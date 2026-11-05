from unittest.mock import MagicMock, patch

import pytest

from macro_optimizer.kroger import (
    KrogerClient,
    KrogerError,
    load_product_map,
    parse_size,
    price_ingredients,
    to_price_record,
)

CHICKEN = {
    "productId": "0001111097970",
    "upc": "0001111097970",
    "brand": "Kroger",
    "description": "Kroger Boneless Skinless Chicken Breasts",
    "items": [
        {
            "itemId": "0001111097970",
            "inventory": {"stockLevel": "HIGH"},
            "price": {"regular": 8.97, "promo": 0},
            "size": "3 lb",
            "soldBy": "UNIT",
        }
    ],
}

RICE_ON_SALE = {
    "productId": "0001111085319",
    "upc": "0001111085319",
    "brand": "Kroger",
    "description": "Kroger Long Grain White Rice",
    "items": [{"price": {"regular": 2.49, "promo": 1.99}, "size": "32 oz", "soldBy": "UNIT"}],
}

NOT_SOLD_HERE = {"productId": "1", "upc": "1", "description": "Regional Item", "items": [{"size": "1 lb"}]}


def _response(json_body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    return resp


TOKEN_RESPONSE = _response({"access_token": "tok", "expires_in": 1800})


@pytest.mark.parametrize(
    "size, expected",
    [
        ("3 lb", (1360.78, "g")),
        ("32 oz", (907.18, "g")),
        ("500 g", (500.0, "g")),
        ("1 gal", (3785.41, "ml")),
        ("16.9 fl oz", (499.79, "ml")),
        ("12 ct", (12.0, "each")),
        ("1 dozen", (12.0, "each")),
        ("16 OZ", (453.59, "g")),
    ],
)
def test_parse_size_normalizes_to_grams_ml_or_each(size, expected):
    assert parse_size(size) == expected


@pytest.mark.parametrize("size", [None, "", "1 bunch", "varies", "6 pk / 12 fl oz", "1/2 gal", "0 oz"])
def test_parse_size_returns_none_rather_than_guessing(size):
    assert parse_size(size) is None


def test_to_price_record_computes_unit_price():
    record = to_price_record(CHICKEN, location_id="70300022")
    assert record["price"] == 8.97
    assert record["on_sale"] is False
    assert record["unit"] == "g"
    assert record["price_per_unit"] == pytest.approx(8.97 / 1360.78, abs=1e-5)
    assert record["location_id"] == "70300022"
    assert record["stock_level"] == "HIGH"


def test_to_price_record_uses_promo_price_and_keeps_regular():
    record = to_price_record(RICE_ON_SALE)
    assert record["price"] == 1.99
    assert record["regular_price"] == 2.49
    assert record["on_sale"] is True


def test_to_price_record_drops_products_with_no_price():
    assert to_price_record(NOT_SOLD_HERE) is None
    assert to_price_record({"description": "no items"}) is None


def test_to_price_record_keeps_price_when_size_is_unreadable():
    product = {**CHICKEN, "items": [{"price": {"regular": 4.0}, "size": "1 bunch"}]}
    record = to_price_record(product)
    assert record["price"] == 4.0
    assert record["price_per_unit"] is None


def test_client_requires_credentials(monkeypatch):
    monkeypatch.delenv("KROGER_CLIENT_ID", raising=False)
    monkeypatch.delenv("KROGER_CLIENT_SECRET", raising=False)
    with pytest.raises(KrogerError, match="developer.kroger.com"):
        KrogerClient()


def test_client_reads_credentials_from_environment(monkeypatch):
    monkeypatch.setenv("KROGER_CLIENT_ID", "id")
    monkeypatch.setenv("KROGER_CLIENT_SECRET", "secret")
    client = KrogerClient()
    assert (client.client_id, client.client_secret) == ("id", "secret")


def test_price_search_authenticates_once_and_filters_by_location():
    client = KrogerClient("id", "secret")
    products = _response({"data": [CHICKEN, NOT_SOLD_HERE]})
    with patch("macro_optimizer.kroger.requests.post", return_value=TOKEN_RESPONSE) as post, \
         patch("macro_optimizer.kroger.requests.get", return_value=products) as get:
        first = client.price_search("chicken breast", "70300022")
        client.price_search("chicken breast", "70300022")

    assert [r["upc"] for r in first] == ["0001111097970"]
    assert post.call_count == 1
    assert post.call_args.kwargs["auth"] == ("id", "secret")
    assert post.call_args.kwargs["data"]["scope"] == "product.compact"
    params = get.call_args.kwargs["params"]
    assert params["filter.term"] == "chicken breast"
    assert params["filter.locationId"] == "70300022"
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_bad_credentials_raise_a_clear_error():
    client = KrogerClient("id", "wrong")
    with patch("macro_optimizer.kroger.requests.post", return_value=_response({}, status=401)):
        with pytest.raises(KrogerError, match="rejected"):
            client.price_search("rice", "70300022")


def test_rate_limit_raises_a_clear_error():
    client = KrogerClient("id", "secret")
    with patch("macro_optimizer.kroger.requests.post", return_value=TOKEN_RESPONSE), \
         patch("macro_optimizer.kroger.requests.get", return_value=_response({}, status=429)):
        with pytest.raises(KrogerError, match="rate limit"):
            client.price_search("rice", "70300022")


def test_find_locations_flattens_address_and_passes_chain():
    client = KrogerClient("id", "secret")
    stores = _response({"data": [{
        "locationId": "70300022", "chain": "RALPHS", "name": "Ralphs - La Jolla",
        "address": {"addressLine1": "8657 Villa La Jolla Dr", "city": "La Jolla", "state": "CA", "zipCode": "92037"},
    }]})
    with patch("macro_optimizer.kroger.requests.post", return_value=TOKEN_RESPONSE), \
         patch("macro_optimizer.kroger.requests.get", return_value=stores) as get:
        found = client.find_locations("92037", chain="RALPHS")

    assert found == [{
        "location_id": "70300022", "chain": "RALPHS", "name": "Ralphs - La Jolla",
        "address": "8657 Villa La Jolla Dr, La Jolla, CA, 92037",
    }]
    assert get.call_args.kwargs["params"]["filter.chain"] == "RALPHS"
    assert get.call_args.kwargs["params"]["filter.zipCode.near"] == "92037"


def test_cheapest_per_unit_ignores_results_in_the_wrong_unit():
    client = KrogerClient("id", "secret")
    pricey = {**RICE_ON_SALE, "upc": "2", "items": [{"price": {"regular": 5.0}, "size": "16 oz"}]}
    liquid = {**RICE_ON_SALE, "upc": "3", "items": [{"price": {"regular": 0.5}, "size": "1 gal"}]}
    products = _response({"data": [pricey, RICE_ON_SALE, liquid]})
    with patch("macro_optimizer.kroger.requests.post", return_value=TOKEN_RESPONSE), \
         patch("macro_optimizer.kroger.requests.get", return_value=products):
        best = client.cheapest_per_unit("white rice", "70300022", unit="g")
        assert client.cheapest_per_unit("white rice", "70300022", unit="each") is None

    assert best["upc"] == "0001111085319"


class FakeClient:
    """Stands in for KrogerClient: a fixed catalog keyed by UPC, searchable as a whole."""

    def __init__(self, products):
        self.products = {p["upc"]: p for p in products}

    def get_product(self, product_id, location_id):
        return self.products.get(product_id)

    def price_search(self, term, location_id, limit=10):
        return [r for r in (to_price_record(p, location_id) for p in self.products.values()) if r]


BANANAS = {"upc": "4011", "description": "Bananas", "items": [{"price": {"regular": 0.69}, "size": "1 lb", "soldBy": "WEIGHT"}]}
PLANTAIN = {"upc": "4235", "description": "Plantain", "items": [{"price": {"regular": 0.99}, "size": "1 ea"}]}


def test_price_ingredients_uses_the_pinned_product_over_a_cheaper_search_hit():
    pricey_rice = {**RICE_ON_SALE, "upc": "9", "items": [{"price": {"regular": 6.0}, "size": "32 oz"}]}
    client = FakeClient([RICE_ON_SALE, pricey_rice])
    result = price_ingredients(client, "70300108", {"white_rice_dry": {"unit": "g"}}, {"white_rice_dry": {"upc": "9"}})

    assert result["white_rice_dry"]["pinned"] is True
    assert result["white_rice_dry"]["product"]["upc"] == "9"
    assert result["white_rice_dry"]["price_per_unit"] == pytest.approx(6.0 / 907.18, abs=1e-5)


def test_price_ingredients_converts_by_the_pound_produce_to_each():
    client = FakeClient([BANANAS, PLANTAIN])
    product_map = {"banana": {"upc": "4011", "each_grams": 180}}
    result = price_ingredients(client, "70300108", {"banana": {"unit": "each"}}, product_map)

    assert result["banana"]["pinned"] is True
    assert result["banana"]["price_per_unit"] == pytest.approx(0.69 / 453.59 * 180, abs=1e-4)


def test_price_ingredients_flags_search_fallbacks_as_unpinned():
    client = FakeClient([CHICKEN])
    for product_map in ({}, {"chicken_breast_raw": {"upc": "not-carried-here"}}):
        result = price_ingredients(client, "70300108", {"chicken_breast_raw": {"unit": "g"}}, product_map)
        assert result["chicken_breast_raw"]["pinned"] is False
        assert result["chicken_breast_raw"]["product"]["upc"] == CHICKEN["upc"]
        assert result["chicken_breast_raw"]["candidates"]


def test_price_ingredients_reports_no_match_rather_than_a_wrong_unit():
    client = FakeClient([CHICKEN])
    result = price_ingredients(client, "70300108", {"olive_oil": {"unit": "ml"}}, {})
    assert result["olive_oil"]["price_per_unit"] is None
    assert result["olive_oil"]["product"] is None


def test_shipped_product_map_only_pins_known_ingredients():
    from macro_optimizer.recipes import load_ingredient_db

    assert set(load_product_map()) <= set(load_ingredient_db())


def test_get_product_returns_none_for_an_unknown_upc():
    client = KrogerClient("id", "secret")
    with patch("macro_optimizer.kroger.requests.post", return_value=TOKEN_RESPONSE), \
         patch("macro_optimizer.kroger.requests.get", return_value=_response({}, status=404)):
        assert client.get_product("0000000000000", "70300108") is None
