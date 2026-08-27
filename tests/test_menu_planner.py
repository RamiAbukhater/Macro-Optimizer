from datetime import date, timedelta

import pytest

from macro_optimizer.database import load_food_db
from macro_optimizer.history import PickHistory
from macro_optimizer.menu_planner import DayPlan, plan_week, week_summary
from macro_optimizer.schema import FoodItem

GOALS = {"calories": 200, "protein": 50, "carbs": 0, "fat": 0}


def two_item_df(item_a_kwargs=None, item_b_kwargs=None):
    base = dict(category="Protein", source="Test", calories=200, protein=50, carbs=0, fat=0, max_daily=5)
    a = FoodItem(name="ItemA", price=1.0, **{**base, **(item_a_kwargs or {})})
    b = FoodItem(name="ItemB", price=1.5, **{**base, **(item_b_kwargs or {})})
    return load_food_db(extra_items=[a, b], include_market=False, include_dining=False)


def test_plan_week_returns_one_result_per_day():
    df = two_item_df()
    days = [DayPlan(on=date.today(), free_minutes=1000), DayPlan(on=date.today() + timedelta(days=1), free_minutes=1000)]
    results = plan_week(df, days, GOALS, default_budget_cap=10.0)
    assert len(results) == 2
    assert results[0]["day"] == days[0].on
    assert results[1]["day"] == days[1].on


def test_plan_week_rotation_cap_forces_variety_across_days():
    df = two_item_df()  # ItemA is strictly cheaper, always wins without a rotation cap
    d1, d2 = date.today(), date.today() + timedelta(days=1)
    days = [DayPlan(on=d1, free_minutes=1000), DayPlan(on=d2, free_minutes=1000)]

    results = plan_week(
        df, days, GOALS, default_budget_cap=10.0,
        rotation_cap=1, recency_window_days=7,
    )

    day1_names = set(results[0]["plan_df"]["name"])
    day2_names = set(results[1]["plan_df"]["name"])
    assert day1_names == {"ItemA"}
    assert day2_names == {"ItemB"}  # ItemA excluded on day 2, already picked once in the window


def test_plan_week_with_variety_guard_disabled_repeats_the_cheapest_item():
    df = two_item_df()
    d1, d2 = date.today(), date.today() + timedelta(days=1)
    days = [DayPlan(on=d1, free_minutes=1000), DayPlan(on=d2, free_minutes=1000)]

    # recency_penalty_weight=0 turns off the default variety guard entirely,
    # isolating the baseline "always pick the cheapest" behavior.
    results = plan_week(df, days, GOALS, default_budget_cap=10.0, recency_penalty_weight=0.0)

    assert set(results[0]["plan_df"]["name"]) == {"ItemA"}
    assert set(results[1]["plan_df"]["name"]) == {"ItemA"}


def test_plan_week_default_recency_penalty_nudges_variety_without_a_rotation_cap():
    df = two_item_df()
    d1, d2 = date.today(), date.today() + timedelta(days=1)
    days = [DayPlan(on=d1, free_minutes=1000), DayPlan(on=d2, free_minutes=1000)]

    # plan_week's default recency_penalty_weight (1.0) applies even without
    # an explicit rotation_cap, so day 2 already discourages yesterday's pick.
    results = plan_week(df, days, GOALS, default_budget_cap=10.0)

    assert set(results[0]["plan_df"]["name"]) == {"ItemA"}
    assert set(results[1]["plan_df"]["name"]) == {"ItemB"}


def test_plan_week_respects_per_day_time_budget():
    df = two_item_df(
        item_a_kwargs={"cook_minutes": 60},  # cheaper, but too slow for a tight day
        item_b_kwargs={"cook_minutes": 0},
    )
    tight_day = DayPlan(on=date.today(), free_minutes=30)
    results = plan_week(df, [tight_day], GOALS, default_budget_cap=10.0)
    assert set(results[0]["plan_df"]["name"]) == {"ItemB"}


def test_plan_week_pinned_items_forced_on_specific_day():
    df = two_item_df()
    pinned_day = DayPlan(on=date.today(), free_minutes=1000, pinned_items=["ItemB"])
    results = plan_week(df, [pinned_day], GOALS, default_budget_cap=10.0)
    assert "ItemB" in results[0]["plan_df"]["name"].values


def test_plan_week_per_day_goal_override():
    df = two_item_df()
    lighter_goals = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    day = DayPlan(on=date.today(), free_minutes=1000, goals=lighter_goals)
    results = plan_week(df, [day], GOALS, default_budget_cap=10.0)
    # With a zero-goal override the optimizer buys nothing at all.
    assert results[0]["plan_df"].empty


def test_plan_week_records_into_provided_history():
    df = two_item_df()
    history = PickHistory()
    day = DayPlan(on=date.today(), free_minutes=1000)
    plan_week(df, [day], GOALS, default_budget_cap=10.0, history=history)
    counts = history.counts_in_window(7, as_of=date.today())
    assert counts.get("ItemA") == 1


def test_week_summary_totals_cost_and_flags_infeasible_days():
    df = two_item_df()
    days = [DayPlan(on=date.today(), free_minutes=1000)]
    results = plan_week(df, days, GOALS, default_budget_cap=10.0)
    summary = week_summary(results)
    assert summary["days_planned"] == 1
    assert summary["total_cost"] == pytest.approx(results[0]["summary"]["achieved"]["cost"])
    assert summary["infeasible_days"] == []


def test_week_summary_flags_infeasible_day():
    df = two_item_df()
    days = [DayPlan(on=date.today(), free_minutes=1000, budget_cap=0.01)]
    results = plan_week(df, days, GOALS, default_budget_cap=10.0)
    summary = week_summary(results)
    assert summary["infeasible_days"] == [days[0].on.isoformat()]
