from datetime import date

from macro_optimizer.adjustments import exclude_and_resolve, resolve_tag_to_item_names
from macro_optimizer.database import load_food_db
from macro_optimizer.menu_planner import DayPlan
from macro_optimizer.schema import FoodItem

GOALS = {"calories": 200, "protein": 50, "carbs": 0, "fat": 0}


def two_item_df(item_a_kwargs=None, item_b_kwargs=None):
    base = dict(category="Protein", source="Test", calories=200, protein=50, carbs=0, fat=0, max_daily=5)
    a = FoodItem(name="Grilled Chicken Breast", price=1.0, **{**base, **(item_a_kwargs or {})})
    b = FoodItem(name="ItemB", price=1.5, **{**base, **(item_b_kwargs or {})})
    return load_food_db(extra_items=[a, b], include_market=False, include_dining=False)


def test_exclude_and_resolve_removes_matched_items_from_the_plan():
    df = two_item_df()  # ItemA (chicken) is strictly cheaper, would normally win
    day = DayPlan(on=date.today(), free_minutes=1000)

    plan_df, summary = exclude_and_resolve(
        df, day, matched_names=["Grilled Chicken Breast"],
        goals=GOALS, budget_cap=10.0,
    )

    assert "Grilled Chicken Breast" not in plan_df["name"].values
    assert set(plan_df["name"]) == {"ItemB"}
    assert summary["feasible"]


def test_exclude_and_resolve_is_a_no_op_when_nothing_matches():
    df = two_item_df()
    day = DayPlan(on=date.today(), free_minutes=1000)

    baseline_df, _ = exclude_and_resolve(df, day, matched_names=[], goals=GOALS, budget_cap=10.0)
    excluded_df, _ = exclude_and_resolve(
        df, day, matched_names=["Nonexistent Item"], goals=GOALS, budget_cap=10.0,
    )

    assert set(baseline_df["name"]) == set(excluded_df["name"])
    assert set(excluded_df["name"]) == {"Grilled Chicken Breast"}  # cheaper item still wins unchanged


def test_resolve_tag_to_item_names_matches_case_insensitive_substring():
    df = two_item_df()
    names = resolve_tag_to_item_names(df, "chicken")
    assert names == ["Grilled Chicken Breast"]


def test_resolve_tag_to_item_names_returns_empty_list_for_unknown_tag():
    df = two_item_df()
    assert resolve_tag_to_item_names(df, "not-a-real-tag") == []


def test_resolve_tag_to_item_names_returns_empty_list_when_tag_has_no_matches_in_df():
    df = two_item_df()
    assert resolve_tag_to_item_names(df, "dairy") == []


def test_resolve_tag_to_item_names_matches_via_allergens_when_text_gives_no_hint():
    # Regression: "Core Power Elite" (category "Protein Shake", no dairy word
    # anywhere in its name/category/notes) survived a "no dairy today"
    # request because only free-text fields were checked. Its curated
    # allergens list is the only place "milk" actually appears.
    shake = FoodItem(
        name="Core Power Elite, Chocolate", price=4.99, category="Protein Shake", source="Test",
        calories=230, protein=42, carbs=8, fat=4.5, max_daily=5, allergens=["milk"],
    )
    df = load_food_db(extra_items=[shake], include_market=False, include_dining=False)
    assert resolve_tag_to_item_names(df, "dairy") == ["Core Power Elite, Chocolate"]


def test_resolve_tag_to_item_names_does_not_confuse_nut_butter_with_dairy():
    # "butter" alone would match "Almond Butter", a nut product with no
    # dairy in it, food_tags.json now uses "dairy butter"/"stick of butter"
    # instead of a bare "butter" synonym for exactly this reason.
    almond_butter = FoodItem(
        name="Almond Butter Packet", price=1.5, category="Nuts", source="Test",
        calories=190, protein=7, carbs=6, fat=16, max_daily=5,
    )
    df = load_food_db(extra_items=[almond_butter], include_market=False, include_dining=False)
    assert resolve_tag_to_item_names(df, "dairy") == []
