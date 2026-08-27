import pytest

from macro_optimizer.pantry import Pantry
from macro_optimizer.recipes import (
    load_ingredient_db,
    load_recipes,
    price_all_recipes,
    price_recipe,
    recipe_food_items,
)
from macro_optimizer.schema import FoodItem

SIMPLE_RECIPE = {
    "name": "Test Rice Bowl",
    "servings": 2,
    "max_daily": 2,
    "ingredients": [
        {"ingredient": "white_rice_dry", "quantity": 200},
        {"ingredient": "chicken_breast_raw", "quantity": 200},
    ],
}


def test_load_recipes_and_ingredients_present():
    recipes = load_recipes()
    ingredients = load_ingredient_db()
    assert len(recipes) > 0
    assert len(ingredients) > 0
    for recipe in recipes:
        for line in recipe["ingredients"]:
            assert line["ingredient"] in ingredients, f"{line['ingredient']} missing from ingredient db"


def test_price_recipe_math_no_pantry():
    ingredient_db = load_ingredient_db()
    rice = ingredient_db["white_rice_dry"]
    chicken = ingredient_db["chicken_breast_raw"]

    priced = price_recipe(SIMPLE_RECIPE, ingredient_db)

    expected_total_cost = rice["price_per_unit"] * 200 + chicken["price_per_unit"] * 200
    expected_total_protein = rice["protein_per_unit"] * 200 + chicken["protein_per_unit"] * 200

    assert priced["price"] == pytest.approx(expected_total_cost / 2, rel=1e-6)
    assert priced["protein"] == pytest.approx(expected_total_protein / 2, rel=1e-6)
    assert priced["source"] == "Home Cooking"
    assert priced["confidence"] == "recipe_estimate"


def test_price_recipe_divides_by_servings():
    ingredient_db = load_ingredient_db()
    priced_2 = price_recipe(SIMPLE_RECIPE, ingredient_db)
    quadruple = {**SIMPLE_RECIPE, "servings": 4, "ingredients": [
        {"ingredient": "white_rice_dry", "quantity": 400},
        {"ingredient": "chicken_breast_raw", "quantity": 400},
    ]}
    priced_4 = price_recipe(quadruple, ingredient_db)
    # Same recipe, doubled ingredients and servings -> same per-serving numbers.
    assert priced_4["price"] == pytest.approx(priced_2["price"], rel=1e-6)
    assert priced_4["calories"] == pytest.approx(priced_2["calories"], rel=1e-6)


def test_price_recipe_pantry_offsets_cost_not_macros():
    ingredient_db = load_ingredient_db()
    priced_no_pantry = price_recipe(SIMPLE_RECIPE, ingredient_db)

    pantry = Pantry()
    pantry.add("chicken_breast_raw", 200)  # fully covers the recipe's chicken need
    priced_with_pantry = price_recipe(SIMPLE_RECIPE, ingredient_db, pantry=pantry)

    assert priced_with_pantry["price"] < priced_no_pantry["price"]
    # Macros are unaffected by what's already owned, you eat the same food either way.
    assert priced_with_pantry["calories"] == pytest.approx(priced_no_pantry["calories"], rel=1e-6)
    assert priced_with_pantry["protein"] == pytest.approx(priced_no_pantry["protein"], rel=1e-6)


def test_price_recipe_full_pantry_coverage_is_free():
    ingredient_db = load_ingredient_db()
    pantry = Pantry()
    pantry.add("white_rice_dry", 200)
    pantry.add("chicken_breast_raw", 200)

    priced = price_recipe(SIMPLE_RECIPE, ingredient_db, pantry=pantry)
    assert priced["price"] == pytest.approx(0.0, abs=1e-9)


def test_price_recipe_dietary_flags_are_conjunction_of_ingredients():
    ingredient_db = load_ingredient_db()
    priced = price_recipe(SIMPLE_RECIPE, ingredient_db)
    # chicken_breast_raw is vegan=False, vegetarian=False -> recipe must inherit that.
    assert priced["vegan"] is False
    assert priced["vegetarian"] is False


def test_price_recipe_unknown_ingredient_raises():
    bad_recipe = {
        "name": "Bad Recipe", "servings": 1, "max_daily": 1,
        "ingredients": [{"ingredient": "unobtainium", "quantity": 10}],
    }
    with pytest.raises(KeyError):
        price_recipe(bad_recipe, load_ingredient_db())


def test_recipe_food_items_are_valid_food_items():
    items = recipe_food_items()
    assert len(items) == len(load_recipes())
    assert all(isinstance(i, FoodItem) for i in items)


def test_price_all_recipes_matches_recipe_count():
    assert len(price_all_recipes()) == len(load_recipes())
