import pandas as pd
import pytest

from macro_optimizer.database import apply_dietary_filters, load_food_db
from macro_optimizer.schema import FoodItem


def test_load_food_db_combines_market_and_dining():
    df = load_food_db()
    assert len(df) > 0
    assert set(df["source"].unique()) >= {"Market", "Dining Hall"}


def test_load_food_db_derived_columns():
    df = load_food_db()
    row = df.iloc[0]
    assert row["protein_per_dollar"] == pytest.approx(row["protein"] / row["price"])


def test_load_food_db_rejects_missing_required_fields():
    with pytest.raises(ValueError):
        load_food_db(extra_items=[{"name": "Bad Item"}])


def test_load_food_db_keeps_zero_price_items():
    zero_cost_item = FoodItem(
        name="Free Sample", category="Recipe", source="Home Cooking",
        price=0.0, calories=100, protein=5, carbs=10, fat=2,
    )
    df = load_food_db(extra_items=[zero_cost_item])
    assert "Free Sample" in df["name"].values


def test_apply_dietary_filters_vegan():
    df = load_food_db()
    filtered = apply_dietary_filters(df, {"vegan": True})
    assert not filtered.empty
    assert filtered["vegan"].all()


def test_apply_dietary_filters_no_pork():
    df = load_food_db()
    filtered = apply_dietary_filters(df, {"no_pork": True})
    assert not filtered["contains_pork"].any()


def test_apply_dietary_filters_combines_multiple():
    df = load_food_db()
    filtered = apply_dietary_filters(df, {"vegetarian": True, "gluten_free": True})
    assert filtered["vegetarian"].all()
    assert filtered["gluten_free"].all()
