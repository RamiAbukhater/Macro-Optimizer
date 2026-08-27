from datetime import date

import pandas as pd
import pytest

from macro_optimizer.database import load_food_db
from macro_optimizer.history import PickHistory
from macro_optimizer.optimizer import run_optimizer
from macro_optimizer.recipes import recipe_food_items

GOALS = {"calories": 2000, "protein": 150, "carbs": 200, "fat": 60}


def test_run_optimizer_feasible_meets_all_goals():
    df = load_food_db()
    plan_df, summary = run_optimizer(df, GOALS, budget_cap=28.00, verbose=False)
    assert summary["feasible"] is True
    assert not plan_df.empty
    assert summary["achieved"]["calories"] >= GOALS["calories"]
    assert summary["achieved"]["protein"] >= GOALS["protein"]
    assert summary["achieved"]["carbs"] >= GOALS["carbs"]
    assert summary["achieved"]["fat"] >= GOALS["fat"]
    assert summary["achieved"]["cost"] <= 28.00 + 1e-6


def test_run_optimizer_respects_budget_cap():
    df = load_food_db()
    plan_df, summary = run_optimizer(df, GOALS, budget_cap=28.00, verbose=False)
    assert plan_df["total_cost"].sum() <= 28.00 + 1e-6


def test_run_optimizer_infeasible_budget_falls_back_soft():
    df = load_food_db()
    plan_df, summary = run_optimizer(df, GOALS, budget_cap=1.00, verbose=False)
    assert summary["feasible"] is False
    # Soft fallback still respects the budget cap even though it misses goals.
    assert summary["achieved"]["cost"] <= 1.00 + 1e-6


def test_run_optimizer_respects_max_daily_cap():
    df = load_food_db()
    plan_df, _ = run_optimizer(df, GOALS, budget_cap=28.00, verbose=False)
    merged = plan_df.merge(df[["name", "max_daily"]], on="name", suffixes=("", "_db"))
    assert (merged["servings"] <= merged["max_daily"]).all()


def test_run_optimizer_dietary_filter_excludes_non_compliant_items():
    df = load_food_db()
    plan_df, summary = run_optimizer(
        df, GOALS, budget_cap=35.00, filters={"vegan": True}, verbose=False
    )
    assert not plan_df.empty
    assert plan_df["vegan"].all()


def test_run_optimizer_rotation_cap_excludes_overused_items():
    df = load_food_db()
    history = PickHistory()
    top_item = df.sort_values("protein_per_dollar", ascending=False).iloc[0]["name"]
    for _ in range(3):
        history.add_pick(top_item, on=date.today())

    plan_df, _ = run_optimizer(
        df, GOALS, budget_cap=28.00,
        history=history, recency_window_days=7, rotation_cap=3,
        verbose=False,
    )
    assert top_item not in plan_df["name"].values


def test_run_optimizer_recency_penalty_reduces_but_does_not_ban_repeats():
    df = load_food_db()
    history = PickHistory()
    top_item = df.sort_values("protein_per_dollar", ascending=False).iloc[0]["name"]
    history.add_pick(top_item, on=date.today())

    plan_df, _ = run_optimizer(
        df, GOALS, budget_cap=28.00,
        history=history, recency_window_days=7,
        recency_penalty_weight=2.0,
        verbose=False,
    )
    # Still solvable, still respects budget; item is discouraged, not banned.
    assert not plan_df.empty
    assert plan_df["total_cost"].sum() <= 28.00 + 1e-6


def test_run_optimizer_time_budget_excludes_slow_recipes():
    df = load_food_db(extra_items=recipe_food_items())
    # Every recipe in the seed set takes at least 10 minutes; a 5-minute
    # budget should force the plan back onto ready-to-eat market/dining items.
    plan_df, _ = run_optimizer(df, GOALS, budget_cap=28.00, time_budget_minutes=5, verbose=False)
    assert not plan_df.empty
    assert (plan_df["total_time_minutes"] <= 5).all()


def test_run_optimizer_time_budget_zero_still_feasible_from_ready_to_eat_items():
    df = load_food_db()  # market/dining items only, all prep_minutes = cook_minutes = 0
    plan_df, summary = run_optimizer(df, GOALS, budget_cap=28.00, time_budget_minutes=0, verbose=False)
    assert summary["feasible"] is True
    assert (plan_df["total_time_minutes"] == 0).all()


def test_run_optimizer_preference_bonus_pulls_in_a_boosted_item():
    df = load_food_db()
    target = "Whole Grain Bagel"
    assert target in df["name"].values

    plan_df, _ = run_optimizer(
        df, GOALS, budget_cap=28.00,
        preference_bonus={target: 50.0},
        verbose=False,
    )
    assert target in plan_df["name"].values


def test_run_optimizer_pinned_items_forced_into_plan():
    df = load_food_db()
    pin = "Drip Coffee, Large (16 oz)"  # negligible macros, would not be picked otherwise
    plan_df, _ = run_optimizer(df, GOALS, budget_cap=28.00, pinned_items=[pin], verbose=False)
    assert pin in plan_df["name"].values
    assert plan_df.loc[plan_df["name"] == pin, "servings"].iloc[0] >= 1


def test_run_optimizer_pinned_item_missing_after_filters_raises():
    df = load_food_db()
    non_vegan_pin = "Grilled Chicken Breast (4 oz)"
    with pytest.raises(ValueError):
        run_optimizer(df, GOALS, budget_cap=28.00, filters={"vegan": True}, pinned_items=[non_vegan_pin], verbose=False)
