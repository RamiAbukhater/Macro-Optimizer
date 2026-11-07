import pytest

import grocery_fixture
from macro_optimizer import grocery_db, market
from macro_optimizer.database import load_market_items
from macro_optimizer.kroger import KrogerError
from macro_optimizer.recipes import load_ingredient_db


@pytest.fixture
def counting_fetcher():
    """The fixture fetcher, recording which UPCs each live call asked for. Restores the default after."""
    calls = []

    def fetch(upcs, location_id):
        calls.append(list(upcs))
        return grocery_fixture.fetcher(upcs, location_id)

    market.set_fetcher(fetch)
    yield calls
    market.set_fetcher(grocery_fixture.fetcher)


def test_catalog_is_built_live_from_the_products_in_the_database():
    items = load_market_items()
    assert len(items) == len(grocery_fixture.PRODUCTS)
    assert {i["source"] for i in items} == {"Kroger API"}
    assert all(i["kind"] and i["location"] == grocery_fixture.STORE_NAME for i in items)


def test_live_answers_are_cached_in_memory(counting_fetcher):
    load_market_items()
    load_market_items()
    assert len(counting_fetcher) == 1


def test_cache_expires_after_the_ttl(counting_fetcher, monkeypatch):
    load_market_items()
    monkeypatch.setattr(market, "CACHE_TTL_SECONDS", -1)
    load_market_items()
    assert len(counting_fetcher) == 2


def test_regular_price_by_default_sale_price_on_request():
    def bagel(items):
        return next(i for i in items if i["name"] == "Northfield Plain Bagels")

    regular, sale = bagel(load_market_items()), bagel(load_market_items(use_promo=True))
    assert sale["price"] < regular["price"]
    assert "sale price not used" in regular["notes"]
    assert "on sale from" in sale["notes"]


def test_sale_prices_can_be_turned_on_by_environment(monkeypatch):
    monkeypatch.setenv(market.SALE_PRICES_ENV_VAR, "1")
    bagel = next(i for i in load_market_items() if i["name"] == "Northfield Plain Bagels")
    assert "on sale from" in bagel["notes"]


def test_products_the_store_does_not_carry_today_are_left_out(counting_fetcher):
    dropped = next(iter(grocery_fixture.PRODUCTS))
    market.set_fetcher(lambda upcs, loc: {u: p for u, p in grocery_fixture.fetcher(upcs, loc).items() if u != dropped})
    items = load_market_items()
    assert len(items) == len(grocery_fixture.PRODUCTS) - 1
    assert grocery_fixture.PRODUCTS[dropped]["description"] not in {i["name"] for i in items}


def test_kroger_outage_gives_an_empty_catalog_and_says_why(counting_fetcher):
    def down(upcs, loc):
        raise KrogerError("Kroger API unreachable after 3 tries.")

    market.set_fetcher(down)
    assert load_market_items() == []
    assert market.status() == {**market.status(), "ok": False, "error": "Kroger API unreachable after 3 tries."}


def test_no_database_yet_gives_an_empty_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv(grocery_db.DB_ENV_VAR, str(tmp_path / "missing.db"))
    assert load_market_items() == []
    assert "refresh init" in market.status()["error"]
    assert not (tmp_path / "missing.db").exists()


def test_pinned_ingredients_are_priced_live_and_the_rest_keep_their_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv(grocery_db.DB_ENV_VAR, str(tmp_path / "grocery.db"))
    conn = grocery_db.connect()
    grocery_fixture.seed(conn)
    tofu_upc = next(u for u, p in grocery_fixture.PRODUCTS.items() if p["_kind"] == "tofu")
    grocery_db.set_pin(conn, "white_rice_dry", tofu_upc)  # any product sold by weight will do
    grocery_db.set_pin(conn, "egg", "0000000000000")  # not carried
    conn.commit()
    conn.close()
    market.clear_cache()

    fallback = load_ingredient_db(live=False)
    live = load_ingredient_db()
    tofu = grocery_fixture.PRODUCTS[tofu_upc]["items"][0]
    assert live["white_rice_dry"]["price_per_unit"] == pytest.approx(tofu["price"]["regular"] / 396.89, abs=1e-5)
    assert live["white_rice_dry"]["price_source"].startswith("Kroger live, store 70300108")
    assert live["egg"] == fallback["egg"]
    assert live["chicken_breast_raw"] == fallback["chicken_breast_raw"]
