import pytest

from macro_optimizer.pantry import Pantry
from macro_optimizer.shopping_list import build_shopping_list

RECIPES = [
    {
        "name": "Chicken & Rice Bowl",
        "servings": 4,
        "ingredients": [
            {"ingredient": "chicken_breast_raw", "quantity": 600},
            {"ingredient": "white_rice_dry", "quantity": 300},
        ],
    }
]

INGREDIENT_DB = {
    "chicken_breast_raw": {"unit": "g", "price_per_unit": 0.01},
    "white_rice_dry": {"unit": "g", "price_per_unit": 0.001},
}


def test_recipe_ingredients_scale_by_servings_ratio():
    items = [{"name": "Chicken & Rice Bowl", "source": "Home Cooking", "servings": 2}]
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    chicken = next(i for i in result["ingredients"] if i["ingredient"] == "chicken_breast_raw")
    # 2 servings out of a 4-serving recipe -> half the ingredient quantities
    assert chicken["needed_quantity"] == pytest.approx(300)


def test_ingredients_aggregate_across_multiple_occurrences():
    items = [
        {"name": "Chicken & Rice Bowl", "source": "Home Cooking", "servings": 2},
        {"name": "Chicken & Rice Bowl", "source": "Home Cooking", "servings": 2},
    ]
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    chicken = next(i for i in result["ingredients"] if i["ingredient"] == "chicken_breast_raw")
    assert chicken["needed_quantity"] == pytest.approx(600)


def test_pantry_offset_reduces_to_buy_quantity():
    items = [{"name": "Chicken & Rice Bowl", "source": "Home Cooking", "servings": 4}]
    pantry = Pantry()
    pantry.add("chicken_breast_raw", 400)
    result = build_shopping_list(items, pantry=pantry, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    chicken = next(i for i in result["ingredients"] if i["ingredient"] == "chicken_breast_raw")
    assert chicken["have_quantity"] == pytest.approx(400)
    assert chicken["to_buy_quantity"] == pytest.approx(200)
    assert chicken["estimated_price"] == pytest.approx(200 * 0.01)


def test_market_items_are_packaged_not_decomposed():
    items = [{"name": "Premier Protein Shake", "source": "Market", "servings": 3, "price": 4.5, "serving_unit": "1 bottle"}]
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    assert result["ingredients"] == []
    assert result["packaged_items"] == [
        {"name": "Premier Protein Shake", "quantity": 3, "unit": "1 bottle", "estimated_price": pytest.approx(13.5)}
    ]


def test_vendor_specific_market_source_is_still_packaged():
    # market_items.json isn't limited to the literal source "Market", some
    # rows use a vendor-specific string like "Goody's Market". Those still
    # need to show up as something to buy, not get silently dropped into
    # "no shopping needed".
    items = [{"name": "Goody's Breakfast Burrito", "source": "Goody's Market", "servings": 1, "price": 6.49, "serving_unit": "1 burrito"}]
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    assert result["no_shopping_needed"] == []
    assert result["packaged_items"] == [
        {"name": "Goody's Breakfast Burrito", "quantity": 1, "unit": "1 burrito", "estimated_price": pytest.approx(6.49)}
    ]


def test_prepared_meal_and_restaurant_items_need_no_shopping():
    items = [
        {"name": "Trifecta Bowl", "source": "Prepared Meal Delivery", "servings": 1},
        {"name": "Chipotle Bowl", "source": "Restaurant Order", "servings": 1},
    ]
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    assert result["ingredients"] == []
    assert result["packaged_items"] == []
    assert len(result["no_shopping_needed"]) == 2


def test_total_estimated_cost_sums_ingredients_and_packaged():
    items = [
        {"name": "Chicken & Rice Bowl", "source": "Home Cooking", "servings": 4},
        {"name": "Premier Protein Shake", "source": "Market", "servings": 2, "price": 4.5, "serving_unit": "1 bottle"},
    ]
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    expected = result["ingredients"][0]["estimated_price"] + result["ingredients"][1]["estimated_price"] + 9.0
    assert result["total_estimated_cost"] == pytest.approx(expected)


def test_empty_items_returns_empty_list():
    result = build_shopping_list([], recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    assert result == {
        "ingredients": [],
        "packaged_items": [],
        "no_shopping_needed": [],
        "total_estimated_cost": 0.0,
    }
