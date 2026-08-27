import pytest

from macro_optimizer.channels import CookAtHomeAdapter, PreparedMealAdapter, RestaurantOrderAdapter
from macro_optimizer.database import load_dining_items, load_market_items
from macro_optimizer.recipes import load_recipes
from macro_optimizer.schema import FoodItem


def test_cook_at_home_lead_time_is_zero():
    assert CookAtHomeAdapter.lead_time_days == 0


def test_cook_at_home_candidates_include_market_dining_and_recipes():
    adapter = CookAtHomeAdapter()
    candidates = adapter.candidates()
    expected_count = len(load_market_items()) + len(load_dining_items()) + len(load_recipes())
    assert len(candidates) == expected_count
    assert all(isinstance(c, FoodItem) for c in candidates)


def test_cook_at_home_pantry_reduces_recipe_cost():
    from macro_optimizer.pantry import Pantry

    no_pantry = {c.name: c.price for c in CookAtHomeAdapter().candidates()}
    pantry = Pantry()
    pantry.add("white_rice_dry", 1000)
    pantry.add("chicken_breast_raw", 1000)
    with_pantry = {c.name: c.price for c in CookAtHomeAdapter(pantry=pantry).candidates()}

    assert with_pantry["Chicken & Rice Bowl"] < no_pantry["Chicken & Rice Bowl"]


def test_prepared_meal_lead_time_is_three_days():
    assert PreparedMealAdapter.lead_time_days == 3


def test_prepared_meal_candidates_are_clearly_marked_illustrative():
    candidates = PreparedMealAdapter().candidates()
    assert len(candidates) > 0
    assert all(c.confidence == "illustrative_example" for c in candidates)
    assert all(c.prep_minutes == 0 and c.cook_minutes == 0 for c in candidates)


def test_restaurant_order_lead_time_is_zero():
    assert RestaurantOrderAdapter.lead_time_days == 0


def test_restaurant_order_candidates_are_clearly_marked_illustrative():
    candidates = RestaurantOrderAdapter().candidates()
    assert len(candidates) > 0
    assert all(c.confidence == "illustrative_example" for c in candidates)
