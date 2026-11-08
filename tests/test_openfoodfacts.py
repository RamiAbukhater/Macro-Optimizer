from unittest.mock import MagicMock, patch

import pytest

from macro_optimizer.openfoodfacts import (
    OpenFoodFactsError,
    lookup_and_price,
    lookup_by_barcode,
    search_products,
    to_food_item,
)
from macro_optimizer.schema import FoodItem

NUTELLA_LIKE_PRODUCT = {
    "code": "3017620422003",
    "product_name": "Nutella",
    "brands": "Nutella, Ferrero, Yum yum",
    "serving_size": None,
    "nutrition_grades": "e",
    "ingredients_analysis_tags": ["en:palm-oil", "en:non-vegan", "en:maybe-vegetarian"],
    "allergens_tags": ["en:milk", "en:nuts", "en:soybeans"],
    "labels_tags": ["en:no-gluten"],
    "nutriments": {
        "energy-kcal_100g": 539,
        "proteins_100g": 6.3,
        "carbohydrates_100g": 57.5,
        "fat_100g": 30.9,
        "fiber_100g": None,
        "sodium_100g": 0.0428,
    },
}

VEGAN_PRODUCT = {
    "code": "1234567890123",
    "product_name": "Oat Milk",
    "brands": "Generic",
    "ingredients_analysis_tags": ["en:vegan", "en:vegetarian"],
    "allergens_tags": [],
    "labels_tags": ["en:gluten-free"],
    "nutriments": {
        "energy-kcal_100g": 47,
        "proteins_100g": 1.0,
        "carbohydrates_100g": 6.6,
        "fat_100g": 1.5,
        "fiber_100g": 0.8,
        "sodium_100g": 0.04,
    },
}

UNNAMED_PRODUCT = {
    "code": "9999999999999",
    "product_name": "",  # crowdsourced entry with no name, just a barcode
    "nutriments": {
        "energy-kcal_100g": 100,
        "proteins_100g": 2.0,
        "carbohydrates_100g": 20,
        "fat_100g": 1.0,
    },
}

NAMED_PRODUCT = {
    "code": "1111111111111",
    "product_name": "Rolled Oats",
    "brands": "",
    "nutriments": {
        "energy-kcal_100g": 389,
        "proteins_100g": 16.9,
        "carbohydrates_100g": 66.3,
        "fat_100g": 6.9,
        "fiber_100g": 10.6,
    },
}

INCOMPLETE_PRODUCT = {
    "code": "0000000000000",
    "product_name": "Mystery Snack",
    "nutriments": {
        "energy-kcal_100g": 200,
        "proteins_100g": None,  # missing, should fail normalization
        "carbohydrates_100g": 20,
        "fat_100g": 5,
    },
}


def test_to_food_item_maps_macros_and_name_with_brand():
    item = to_food_item(NUTELLA_LIKE_PRODUCT, price=3.50)
    assert isinstance(item, FoodItem)
    assert item.name == "Nutella (Nutella)"
    assert item.calories == 539
    assert item.protein == 6.3
    assert item.carbs == 57.5
    assert item.fat == 30.9
    assert item.price == 3.50
    assert item.serving_unit == "100g"


def test_to_food_item_converts_sodium_grams_to_milligrams():
    item = to_food_item(NUTELLA_LIKE_PRODUCT, price=3.50)
    assert item.sodium == pytest.approx(42.8, rel=1e-3)


def test_to_food_item_missing_fiber_defaults_to_zero():
    item = to_food_item(NUTELLA_LIKE_PRODUCT, price=3.50)
    assert item.fiber == 0.0


def test_to_food_item_sets_external_api_confidence():
    item = to_food_item(NUTELLA_LIKE_PRODUCT, price=3.50)
    assert item.confidence == "external_api"


def test_to_food_item_vegan_product_flags():
    item = to_food_item(VEGAN_PRODUCT, price=4.00)
    assert item.vegan is True
    assert item.vegetarian is True  # vegan implies vegetarian
    assert item.gluten_free is True


def test_to_food_item_non_vegan_product_flags():
    item = to_food_item(NUTELLA_LIKE_PRODUCT, price=3.50)
    assert item.vegan is False
    assert item.vegetarian is False  # "maybe-vegetarian" is not a firm "en:vegetarian" tag
    assert "milk" in item.allergens


def test_to_food_item_raises_on_missing_required_macro():
    with pytest.raises(OpenFoodFactsError):
        to_food_item(INCOMPLETE_PRODUCT, price=2.00)


@patch("macro_optimizer.openfoodfacts.requests.get")
def test_search_products_hits_expected_endpoint_and_parses_results(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"products": [NUTELLA_LIKE_PRODUCT]}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    results = search_products("nutella", page_size=5)

    assert results == [NUTELLA_LIKE_PRODUCT]
    called_url = mock_get.call_args[0][0]
    assert "cgi/search.pl" in called_url
    assert mock_get.call_args[1]["params"]["search_terms"] == "nutella"
    assert mock_get.call_args[1]["params"]["page_size"] == 5
    assert "User-Agent" in mock_get.call_args[1]["headers"]


@patch("macro_optimizer.openfoodfacts.requests.get")
def test_lookup_by_barcode_returns_product_when_found(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": 1, "product": NUTELLA_LIKE_PRODUCT}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    product = lookup_by_barcode("3017620422003")
    assert product == NUTELLA_LIKE_PRODUCT
    called_url = mock_get.call_args[0][0]
    assert "3017620422003" in called_url


@patch("macro_optimizer.openfoodfacts.requests.get")
def test_lookup_by_barcode_returns_none_when_not_found(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": 0}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    assert lookup_by_barcode("0000000000000") is None


@patch("macro_optimizer.openfoodfacts.requests.get")
def test_lookup_and_price_end_to_end_via_search(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"products": [NUTELLA_LIKE_PRODUCT]}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    item = lookup_and_price("nutella", price=3.50)
    assert item.name == "Nutella (Nutella)"
    assert item.price == 3.50


@patch("macro_optimizer.openfoodfacts.requests.get")
def test_lookup_and_price_skips_unnamed_results_for_named_ones(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"products": [UNNAMED_PRODUCT, NAMED_PRODUCT]}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    item = lookup_and_price("oats", price=2.50)
    assert item.name.startswith("Rolled Oats")


@patch("macro_optimizer.openfoodfacts.requests.get")
def test_lookup_and_price_raises_when_search_finds_nothing(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"products": []}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    with pytest.raises(OpenFoodFactsError):
        lookup_and_price("some nonexistent product xyz", price=1.00)


@patch("macro_optimizer.openfoodfacts.requests.get")
def test_lookup_and_price_raises_when_barcode_not_found(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": 0}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    with pytest.raises(OpenFoodFactsError):
        lookup_and_price("0000000000000", price=1.00, by_barcode=True)


def test_to_food_item_feeds_directly_into_load_food_db():
    from macro_optimizer.database import load_food_db

    item = to_food_item(NUTELLA_LIKE_PRODUCT, price=3.50)
    df = load_food_db(extra_items=[item], include_market=False)
    assert len(df) == 1
    assert df.iloc[0]["source"] == "Open Food Facts"
