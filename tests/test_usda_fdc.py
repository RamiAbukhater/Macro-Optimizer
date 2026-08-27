import os
from unittest.mock import MagicMock, patch

import pytest

from macro_optimizer.schema import FoodItem
from macro_optimizer.usda_fdc import (
    DEMO_KEY,
    UsdaFdcError,
    _resolve_api_key,
    lookup_and_price,
    search_foods,
    to_food_item,
)

CHICKEN_BREAST = {
    "fdcId": 174608,
    "description": "Chicken breast, roll, oven-roasted",
    "dataType": "SR Legacy",
    "foodNutrients": [
        {"nutrientId": 1008, "value": 134.0},  # Energy, kcal
        {"nutrientId": 1062, "value": 562.0},  # Energy, kJ (should be ignored)
        {"nutrientId": 1003, "value": 14.59},  # Protein
        {"nutrientId": 1004, "value": 7.65},  # Fat
        {"nutrientId": 1005, "value": 1.79},  # Carbs
        {"nutrientId": 1079, "value": 0.0},  # Fiber
        {"nutrientId": 1093, "value": 883.0},  # Sodium
    ],
}

UNNAMED_FOOD = {"fdcId": 1, "description": "", "foodNutrients": []}

INCOMPLETE_FOOD = {
    "fdcId": 2,
    "description": "Mystery Ingredient",
    "foodNutrients": [
        {"nutrientId": 1008, "value": 100.0},
        # protein/carbs/fat missing entirely
    ],
}


def test_to_food_item_maps_macros_by_nutrient_id_not_name():
    item = to_food_item(CHICKEN_BREAST, price=2.00)
    assert isinstance(item, FoodItem)
    assert item.calories == 134.0  # picks kcal (1008), not kJ (1062)
    assert item.protein == 14.59
    assert item.fat == 7.65
    assert item.carbs == 1.79
    assert item.fiber == 0.0
    assert item.sodium == 883.0


def test_to_food_item_name_includes_data_type_in_source():
    item = to_food_item(CHICKEN_BREAST, price=2.00)
    assert item.source == "USDA FoodData Central (SR Legacy)"
    assert item.serving_unit == "100g"


def test_to_food_item_sets_external_api_confidence():
    item = to_food_item(CHICKEN_BREAST, price=2.00)
    assert item.confidence == "external_api"


def test_to_food_item_dietary_flags_default_false_unless_overridden():
    item = to_food_item(CHICKEN_BREAST, price=2.00)
    assert item.vegetarian is False
    assert item.vegan is False
    assert item.halal is False

    overridden = to_food_item(CHICKEN_BREAST, price=2.00, halal=True)
    assert overridden.halal is True


def test_to_food_item_raises_on_missing_required_macro():
    with pytest.raises(UsdaFdcError):
        to_food_item(INCOMPLETE_FOOD, price=1.00)


def test_resolve_api_key_prefers_explicit_param():
    assert _resolve_api_key("explicit-key") == "explicit-key"


def test_resolve_api_key_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("USDA_FDC_API_KEY", "env-key")
    assert _resolve_api_key(None) == "env-key"


def test_resolve_api_key_falls_back_to_demo_key(monkeypatch):
    monkeypatch.delenv("USDA_FDC_API_KEY", raising=False)
    assert _resolve_api_key(None) == DEMO_KEY


@patch("macro_optimizer.usda_fdc.requests.get")
def test_search_foods_hits_expected_endpoint(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"foods": [CHICKEN_BREAST]}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    results = search_foods("chicken breast", page_size=5, api_key="test-key")

    assert results == [CHICKEN_BREAST]
    called_url = mock_get.call_args[0][0]
    assert "foods/search" in called_url
    params = mock_get.call_args[1]["params"]
    assert params["query"] == "chicken breast"
    assert params["pageSize"] == 5
    assert params["api_key"] == "test-key"
    assert params["dataType"] == ["Foundation", "SR Legacy"]


@patch("macro_optimizer.usda_fdc.requests.get")
def test_search_foods_raises_helpful_error_on_rate_limit(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_get.return_value = mock_response

    with pytest.raises(UsdaFdcError, match="rate limit|429"):
        search_foods("chicken breast")


@patch("macro_optimizer.usda_fdc.requests.get")
def test_lookup_and_price_end_to_end(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"foods": [CHICKEN_BREAST]}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    item = lookup_and_price("chicken breast", price=2.50)
    assert item.name == "Chicken breast, roll, oven-roasted"
    assert item.price == 2.50


@patch("macro_optimizer.usda_fdc.requests.get")
def test_lookup_and_price_skips_unnamed_and_incomplete_results(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"foods": [UNNAMED_FOOD, INCOMPLETE_FOOD, CHICKEN_BREAST]}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    item = lookup_and_price("chicken", price=2.50)
    assert item.name == "Chicken breast, roll, oven-roasted"


@patch("macro_optimizer.usda_fdc.requests.get")
def test_lookup_and_price_raises_when_nothing_usable(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"foods": [UNNAMED_FOOD, INCOMPLETE_FOOD]}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    with pytest.raises(UsdaFdcError):
        lookup_and_price("nonexistent thing", price=1.00)


def test_to_food_item_feeds_directly_into_load_food_db():
    from macro_optimizer.database import load_food_db

    item = to_food_item(CHICKEN_BREAST, price=2.00)
    df = load_food_db(extra_items=[item], include_market=False, include_dining=False)
    assert len(df) == 1
    assert df.iloc[0]["source"] == "USDA FoodData Central (SR Legacy)"
