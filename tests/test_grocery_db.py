from datetime import datetime, timedelta, timezone

import pytest

from macro_optimizer import grocery_db


@pytest.fixture
def conn(tmp_path):
    conn = grocery_db.connect(tmp_path / "grocery.db")
    yield conn
    conn.close()


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def test_default_path_is_outside_the_repo_and_overridable(monkeypatch, tmp_path):
    monkeypatch.delenv(grocery_db.DB_ENV_VAR, raising=False)
    monkeypatch.setattr(grocery_db.Path, "home", lambda: tmp_path)
    assert grocery_db.default_path() == tmp_path / ".autoeat" / "grocery.db"
    monkeypatch.setenv(grocery_db.DB_ENV_VAR, str(tmp_path / "elsewhere.db"))
    assert grocery_db.default_path() == tmp_path / "elsewhere.db"


def test_record_seen_reports_new_products_and_keeps_the_first_kind(conn):
    assert grocery_db.record_seen(conn, "001", "peanut butter") is True
    assert grocery_db.record_seen(conn, "001", "almonds") is False
    assert grocery_db.products(conn)[0]["kind"] == "peanut butter"


def test_record_seen_never_moves_last_seen_backwards(conn):
    grocery_db.record_seen(conn, "001", "milk", _iso(1))
    grocery_db.record_seen(conn, "001", "milk", _iso(30))
    assert grocery_db.products(conn)[0]["last_seen"] == _iso(1)


def test_products_age_from_active_to_missing_to_discontinued(conn):
    grocery_db.record_seen(conn, "fresh", "milk", _iso(1))
    grocery_db.record_seen(conn, "stale", "milk", _iso(grocery_db.MISSING_AFTER_DAYS + 1))
    grocery_db.record_seen(conn, "gone", "milk", _iso(grocery_db.DISCONTINUED_AFTER_DAYS + 1))
    assert grocery_db.update_statuses(conn) == {"missing": 1, "discontinued": 1}
    assert grocery_db.status_counts(conn) == {"active": 1, "missing": 1, "discontinued": 1}
    # Rows are kept, and a product seen again comes straight back.
    grocery_db.record_seen(conn, "stale", "milk")
    assert {p["upc"] for p in grocery_db.products(conn)} == {"fresh", "stale"}


def test_pins_round_trip_in_price_ingredients_shape(conn):
    grocery_db.set_pin(conn, "banana", "4011", "bananas", each_grams=180)
    grocery_db.set_pin(conn, "egg", "0001111090406")
    assert grocery_db.pins(conn) == {
        "banana": {"upc": "4011", "term": "bananas", "each_grams": 180},
        "egg": {"upc": "0001111090406", "term": ""},
    }
    grocery_db.mark_pin_verified(conn, "egg", "ok")
    grocery_db.set_pin(conn, "egg", "0002")  # re-pinning clears the old verification
    egg = next(p for p in grocery_db.pin_rows(conn) if p["ingredient_id"] == "egg")
    assert egg["upc"] == "0002" and egg["verified_status"] is None


def test_upsert_store_keeps_known_details_when_given_blanks(conn):
    grocery_db.upsert_store(conn, "70300108", "RALPHS", "Ralphs La Jolla", "8657 Villa La Jolla Dr")
    grocery_db.upsert_store(conn, "70300108")
    assert grocery_db.active_store(conn)["name"] == "Ralphs La Jolla"


def test_runs_record_status_counts_and_errors(conn):
    run_id = grocery_db.start_run(conn, "70300108")
    assert grocery_db.recent_runs(conn)[0]["status"] == "running"
    grocery_db.finish_run(conn, run_id, "partial", 12, {"seen": 3}, ["search 'milk': boom"])
    run = grocery_db.recent_runs(conn)[0]
    assert (run["status"], run["api_calls"], run["counts"], run["errors"]) == ("partial", 12, {"seen": 3}, ["search 'milk': boom"])
    assert grocery_db.last_successful_run(conn)["id"] == run_id
