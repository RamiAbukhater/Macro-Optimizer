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
    [line] = result["packaged_items"]
    assert {k: line[k] for k in ("name", "quantity", "unit")} == {
        "name": "Premier Protein Shake", "quantity": 3, "unit": "1 bottle"}
    assert line["estimated_price"] == pytest.approx(13.5)


def test_vendor_specific_market_source_is_still_packaged():
    # market_items.json isn't limited to the literal source "Market", some
    # rows use a vendor-specific string like "Goody's Market". Those still
    # need to show up as something to buy, not get silently dropped into
    # "no shopping needed".
    items = [{"name": "Goody's Breakfast Burrito", "source": "Goody's Market", "servings": 1, "price": 6.49, "serving_unit": "1 burrito"}]
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    assert result["no_shopping_needed"] == []
    [line] = result["packaged_items"]
    assert line["name"] == "Goody's Breakfast Burrito"
    assert line["estimated_price"] == pytest.approx(6.49)


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
        "total_eaten_cost": 0.0,
    }


# --- whole packages ---

BAGELS = {"name": "Northfield Plain Bagels", "source": "Kroger API", "price": 0.58, "upc": "990009",
          "servings_per_package": 6, "package_price": 3.49, "package_size": "20 oz"}


def test_product_rounds_up_to_whole_packages_with_leftovers():
    items = [{**BAGELS, "servings": 2}, {**BAGELS, "servings": 5}]  # 7 bagels across the week
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    [line] = result["packaged_items"]
    assert line["packages"] == 2
    assert line["leftover_servings"] == pytest.approx(5)
    assert line["estimated_price"] == pytest.approx(6.98)  # what the register charges
    assert line["eaten_cost"] == pytest.approx(7 * 0.58)  # what the week eats
    assert result["total_estimated_cost"] == pytest.approx(6.98)
    assert line["package_size"] == "20 oz" and line["name"] == "Northfield Plain Bagels"


def test_one_bagel_still_buys_the_bag():
    result = build_shopping_list([{**BAGELS, "servings": 1}], recipes=RECIPES, ingredient_db=INGREDIENT_DB)
    [line] = result["packaged_items"]
    assert (line["packages"], line["leftover_servings"], line["estimated_price"]) == (1, 5, 3.49)


def test_owned_servings_reduce_packages():
    pantry = Pantry()
    pantry.add_lot("upc:990009", "old", 4)
    result = build_shopping_list([{**BAGELS, "servings": 7}], pantry=pantry, recipes=RECIPES,
                                 ingredient_db=INGREDIENT_DB)
    [line] = result["packaged_items"]
    assert line["have_servings"] == 4 and line["packages"] == 1


def test_this_weeks_own_purchase_does_not_count_as_owned():
    pantry = Pantry()
    pantry.add_lot("upc:990009", "buy:2026-11-08:product:990009", 6)
    result = build_shopping_list([{**BAGELS, "servings": 3}], pantry=pantry, recipes=RECIPES,
                                 ingredient_db=INGREDIENT_DB, exclude_lot_prefix="buy:2026-11-08:",
                                 bought={"product:990009": {}})
    [line] = result["packaged_items"]
    assert line["packages"] == 1 and line["bought"] is True


def test_package_facts_come_from_live_lookup_when_item_lacks_them():
    item = {"name": BAGELS["name"], "source": "Kroger API", "servings": 3, "price": 0.58, "upc": "990009"}
    result = build_shopping_list([item], recipes=RECIPES, ingredient_db=INGREDIENT_DB,
                                 products={"990009": BAGELS})
    assert result["packaged_items"][0]["packages"] == 1


def test_pinned_ingredient_rounds_up_to_package_size():
    items = [{"name": "Chicken & Rice Bowl", "source": "Home Cooking", "servings": 4}]  # 600 g chicken
    packages = {"chicken_breast_raw": {"upc": "1", "title": "Chicken Breasts", "size": "1.5 lb",
                                       "package_quantity": 680.39, "package_price": 7.99}}
    result = build_shopping_list(items, recipes=RECIPES, ingredient_db=INGREDIENT_DB, ingredient_packages=packages)
    chicken = next(i for i in result["ingredients"] if i["ingredient"] == "chicken_breast_raw")
    assert chicken["packages"] == 1
    assert chicken["estimated_price"] == pytest.approx(7.99)
    assert chicken["leftover_quantity"] == pytest.approx(80.4)
    rice = next(i for i in result["ingredients"] if i["ingredient"] == "white_rice_dry")
    assert rice["packages"] is None  # unpinned: stays a loose estimate
    assert rice["estimated_price"] == pytest.approx(0.3)
