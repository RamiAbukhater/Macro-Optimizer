import json
from datetime import datetime, timedelta, timezone

import pytest

import grocery_fixture
from macro_optimizer import grocery_db, refresh
from macro_optimizer.kroger import KrogerError

LOC = grocery_fixture.LOCATION_ID


class FakeKroger:
    """Search returns fixture products whose kind matches the term; UPC lookups return any fixture product."""

    def __init__(self, products=None, fail_terms=(), fail_lookup=False):
        self.products = dict(grocery_fixture.PRODUCTS if products is None else products)
        self.fail_terms, self.fail_lookup = set(fail_terms), fail_lookup
        self.calls = 0

    def search_products(self, term, location_id, limit=50):
        self.calls += 1
        if term in self.fail_terms:
            raise KrogerError("Kroger API unreachable after 3 tries.")
        return [p for p in self.products.values() if p["_kind"] == term][:limit]

    def get_products(self, upcs, location_id):
        self.calls += 1
        if self.fail_lookup:
            raise KrogerError("Kroger API unreachable after 3 tries.")
        return {u: self.products[u] for u in upcs if u in self.products}

    def get_location(self, location_id):
        return {"location_id": location_id, "chain": "RALPHS", "name": "Ralphs Test", "address": "1 Test Way"}


@pytest.fixture
def conn(tmp_path):
    conn = grocery_db.connect(tmp_path / "grocery.db")
    grocery_db.upsert_store(conn, LOC, name="Fixture Market")
    grocery_db.add_terms(conn, sorted({p["_kind"] for p in grocery_fixture.PRODUCTS.values()}))
    yield conn
    conn.close()


def _age(conn, upc, days):
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("UPDATE products SET last_seen = ? WHERE upc = ?", (old, upc))
    conn.commit()


def test_a_first_run_discovers_every_usable_product(conn):
    result = refresh.run_refresh(conn, FakeKroger(), LOC)
    assert result["status"] == "ok"
    assert result["counts"]["new"] == len(grocery_fixture.PRODUCTS)
    assert grocery_db.status_counts(conn) == {"active": len(grocery_fixture.PRODUCTS)}
    assert grocery_db.recent_runs(conn)[0]["status"] == "ok"


def test_only_identifiers_and_dates_are_stored(conn):
    refresh.run_refresh(conn, FakeKroger(), LOC)
    dump = "\n".join(conn.iterdump())
    some = next(iter(grocery_fixture.PRODUCTS.values()))
    assert some["description"] not in dump
    assert str(some["items"][0]["price"]["regular"]) not in dump


def test_products_a_search_misses_are_confirmed_by_upc(conn):
    refresh.run_refresh(conn, FakeKroger(), LOC)
    grocery_db.add_terms(conn, [])
    conn.execute("UPDATE catalog_terms SET active = 0 WHERE term = 'tofu'")
    result = refresh.run_refresh(conn, FakeKroger(), LOC)
    assert result["counts"]["confirmed_by_upc"] == 3
    assert result["status"] == "ok"


def test_unseen_products_age_out_after_a_clean_run(conn):
    refresh.run_refresh(conn, FakeKroger(), LOC)
    gone = next(iter(grocery_fixture.PRODUCTS))
    _age(conn, gone, grocery_db.MISSING_AFTER_DAYS + 1)
    shelf = {u: p for u, p in grocery_fixture.PRODUCTS.items() if u != gone}
    result = refresh.run_refresh(conn, FakeKroger(products=shelf), LOC)
    assert result["counts"]["now_missing"] == 1
    assert gone not in {p["upc"] for p in grocery_db.products(conn)}


def test_an_outage_never_ages_products_out(conn):
    refresh.run_refresh(conn, FakeKroger(), LOC)
    upc = next(iter(grocery_fixture.PRODUCTS))
    _age(conn, upc, grocery_db.MISSING_AFTER_DAYS + 1)
    result = refresh.run_refresh(conn, FakeKroger(fail_lookup=True, fail_terms={"bagels"}), LOC)
    assert result["status"] == "partial"
    assert any("bagels" in e for e in result["errors"])
    assert grocery_db.status_counts(conn) == {"active": len(grocery_fixture.PRODUCTS)}


def test_a_run_with_nothing_seen_is_failed(conn):
    terms = grocery_db.active_terms(conn)
    result = refresh.run_refresh(conn, FakeKroger(fail_terms=terms, fail_lookup=True), LOC)
    assert result["status"] == "failed"


def test_the_call_budget_stops_a_run(conn):
    result = refresh.run_refresh(conn, FakeKroger(), LOC, budget=5)
    assert result["status"] == "partial"
    assert result["api_calls"] == 5
    assert result["errors"] == ["call budget of 5 reached"]


def test_pins_are_verified_against_the_store(conn):
    carried = next(iter(grocery_fixture.PRODUCTS))
    grocery_db.set_pin(conn, "egg", carried)
    grocery_db.set_pin(conn, "olive_oil", "0000000000000")
    result = refresh.run_refresh(conn, FakeKroger(), LOC)
    assert (result["counts"]["pins_ok"], result["counts"]["pins_not_carried"]) == (1, 1)
    status = {p["ingredient_id"]: p["verified_status"] for p in grocery_db.pin_rows(conn)}
    assert status == {"egg": "ok", "olive_oil": "not_carried"}


def test_init_sets_up_store_terms_pins_and_imports_a_legacy_catalog(tmp_path):
    legacy = tmp_path / "kroger_items.json"
    legacy.write_text(json.dumps([
        {"kind": "milk", "notes": "UPC 0001111041700. 1 gal package at $4.29, 16 serving(s), priced 2026-10-10."},
        {"kind": "milk", "notes": "no upc here"},
    ]))
    conn = grocery_db.connect(tmp_path / "grocery.db")
    summary = refresh.init(conn, LOC, FakeKroger(), legacy)
    assert summary["store"]["name"] == "Ralphs Test"
    assert summary["imported"] == 1
    assert summary["terms"] == len(refresh.CATALOG_TERMS)
    assert summary["pins"] > 0
    milk = grocery_db.products(conn)[0]
    assert (milk["upc"], milk["kind"], milk["last_seen"][:10]) == ("0001111041700", "milk", "2026-10-10")
    conn.close()


def test_schedule_command_runs_the_launcher_daily(tmp_path):
    launcher = tmp_path / refresh.LAUNCHER_NAME
    cmd = refresh.schedule_command("05:30", launcher)
    assert cmd[:4] == ["schtasks", "/Create", "/F", "/SC"]
    assert "05:30" in cmd and refresh.TASK_NAME in cmd
    assert cmd[-1].endswith(f'"{launcher}"')


def test_launcher_runs_this_copy_of_the_code(tmp_path):
    launcher = refresh.write_launcher(tmp_path / refresh.LAUNCHER_NAME)
    text = launcher.read_text(encoding="utf-8")
    compile(text, str(launcher), "exec")
    src = str(refresh.Path(refresh.__file__).resolve().parents[1])
    assert repr(src) in text and '"run", "--quiet"' in text
