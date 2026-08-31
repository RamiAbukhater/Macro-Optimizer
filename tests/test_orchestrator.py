from datetime import date, timedelta

import pytest

from macro_optimizer.channels import ChannelAdapter, CookAtHomeAdapter, PreparedMealAdapter, RestaurantOrderAdapter
from macro_optimizer.menu_planner import DayPlan
from macro_optimizer.orchestrator import confirmation_digest, plan_week_multichannel
from macro_optimizer.schema import FoodItem

GOALS = {"calories": 400, "protein": 30, "carbs": 20, "fat": 10}
TODAY = date(2026, 1, 1)


class TwoItemAdapter(ChannelAdapter):
    """Minimal same-day channel with a strictly-cheaper item and one fallback,
    for deterministic anti-repetition tests (mirrors test_menu_planner.py's
    two_item_df, but as a channel plan_week_multichannel can plan across)."""

    name = "Two Item Test Channel"
    lead_time_days = 0

    def candidates(self):
        base = dict(category="Protein", source="Two Item Test Channel", max_daily=5,
                    calories=400, protein=30, carbs=20, fat=10)
        return [FoodItem(name="ItemA", price=1.0, **base), FoodItem(name="ItemB", price=1.5, **base)]


class EightItemAdapter(ChannelAdapter):
    """Eight same-day items, each on its own individually sufficient to meet
    GOALS, priced in ascending order. Deterministic stand-in for a real
    database, big enough that a strict rotation_cap=1 across a 7-day week
    (GOALS's day count) always has at least one still-unused, still-feasible
    item left to fall back on, so a full week can be both fully unique and
    fully feasible without depending on the real curated database's size."""

    name = "Eight Item Test Channel"
    lead_time_days = 0

    def candidates(self):
        base = dict(category="Protein", source="Eight Item Test Channel", max_daily=5,
                    calories=400, protein=30, carbs=20, fat=10)
        return [FoodItem(name=f"Item{i}", price=1.0 + i * 0.1, **base) for i in range(8)]


def test_plan_week_multichannel_cook_at_home_only_is_feasible():
    days = [DayPlan(on=TODAY, free_minutes=120)]
    results = plan_week_multichannel(
        days, [CookAtHomeAdapter()], GOALS, default_budget_cap=15.0, today=TODAY,
    )
    assert len(results) == 1
    assert results[0]["viable_channels"] == ["Cook at Home"]
    assert results[0]["summary"]["feasible"] is True


def test_plan_week_multichannel_excludes_prepared_meal_within_lead_time():
    channels = [CookAtHomeAdapter(), PreparedMealAdapter()]
    days = [DayPlan(on=TODAY, free_minutes=120)]  # 0 days out, PreparedMeal needs 3
    results = plan_week_multichannel(days, channels, GOALS, default_budget_cap=15.0, today=TODAY)
    assert "Prepared Meal Delivery" not in results[0]["viable_channels"]
    assert "Cook at Home" in results[0]["viable_channels"]


def test_plan_week_multichannel_includes_prepared_meal_beyond_lead_time():
    channels = [CookAtHomeAdapter(), PreparedMealAdapter()]
    far_day = TODAY + timedelta(days=3)
    days = [DayPlan(on=far_day, free_minutes=120)]
    results = plan_week_multichannel(days, channels, GOALS, default_budget_cap=20.0, today=TODAY)
    assert "Prepared Meal Delivery" in results[0]["viable_channels"]


def test_plan_week_multichannel_raises_when_no_channel_viable():
    channels = [PreparedMealAdapter()]  # needs 3 days lead time
    days = [DayPlan(on=TODAY, free_minutes=120)]  # 0 days out
    with pytest.raises(ValueError):
        plan_week_multichannel(days, channels, GOALS, default_budget_cap=20.0, today=TODAY)


def test_plan_week_multichannel_raises_for_a_day_in_the_past():
    days = [DayPlan(on=TODAY - timedelta(days=1), free_minutes=120)]
    with pytest.raises(ValueError):
        plan_week_multichannel(days, [CookAtHomeAdapter()], GOALS, default_budget_cap=15.0, today=TODAY)


def test_plan_week_multichannel_restaurant_only_channel_is_used():
    days = [DayPlan(on=TODAY, free_minutes=0)]  # zero time to cook
    results = plan_week_multichannel(
        days, [RestaurantOrderAdapter()], GOALS, default_budget_cap=20.0, today=TODAY,
    )
    assert results[0]["summary"]["feasible"] is True
    assert (results[0]["plan_df"]["source"].str.startswith("Restaurant Order")).all()


def test_plan_week_multichannel_all_three_channels_together():
    far_day = TODAY + timedelta(days=3)
    channels = [CookAtHomeAdapter(), PreparedMealAdapter(), RestaurantOrderAdapter()]
    days = [DayPlan(on=far_day, free_minutes=120)]
    results = plan_week_multichannel(days, channels, GOALS, default_budget_cap=20.0, today=TODAY)
    assert set(results[0]["viable_channels"]) == {"Cook at Home", "Prepared Meal Delivery", "Restaurant Order"}
    assert results[0]["summary"]["feasible"] is True


def test_plan_week_multichannel_does_not_freeze_after_day_two():
    """Same regression as test_menu_planner.py's does_not_freeze_after_day_two,
    but through plan_week_multichannel, the function /api/plan actually calls.
    Confirms the as_of fix reaches the real request path, not just the lower-
    level plan_week helper."""
    days = [DayPlan(on=TODAY + timedelta(days=i), free_minutes=1000) for i in range(4)]
    results = plan_week_multichannel(days, [TwoItemAdapter()], GOALS, default_budget_cap=10.0, today=TODAY)
    names = [set(r["plan_df"]["name"]) for r in results]

    assert names[0] == {"ItemA"}
    assert names[1] == {"ItemB"}
    assert names[2] == {"ItemA"}
    assert names[2] != names[1]
    assert names[3] == {"ItemB"}
    assert names[3] != names[2]


def test_plan_week_multichannel_default_webapp_settings_give_a_fully_unique_week():
    """Regression test for the "every day should be unique" bug report: with
    enough distinct feasible items available, webapp/app.py's actual
    /api/plan settings (rotation_cap=1, recency_penalty_weight=5.0, the full
    7-day window) should mean no item repeats anywhere across the week, not
    just no exact day-to-day duplicate and not the old rotation_cap=3
    "three-day runs". Uses a deterministic 8-item channel (see
    EightItemAdapter) rather than the real curated database, whose size and
    per-item feasibility depend on live data the test shouldn't be coupled to."""
    days = [DayPlan(on=TODAY + timedelta(days=i), free_minutes=1000) for i in range(7)]

    results = plan_week_multichannel(
        days, [EightItemAdapter()], GOALS, default_budget_cap=10.0, today=TODAY,
        rotation_cap=1, recency_penalty_weight=5.0, verbose=False,
    )

    assert all(r["summary"]["feasible"] for r in results)

    seen = set()
    for r in results:
        names = set(r["plan_df"]["name"])
        assert not (names & seen), f"item(s) repeated across the week: {names & seen}"
        seen |= names


def test_plan_week_multichannel_tight_pool_relaxes_rather_than_going_empty():
    """With only two feasible items but a 3-day week, day 3 can't be both
    unique and feasible (ItemA and ItemB are each used up by days 1 and 2),
    so the rotation backoff should relax the cap and still return a real
    plan for day 3, not an empty one."""
    days = [DayPlan(on=TODAY + timedelta(days=i), free_minutes=1000) for i in range(3)]

    results = plan_week_multichannel(
        days, [TwoItemAdapter()], GOALS, default_budget_cap=10.0, today=TODAY,
        rotation_cap=1, recency_penalty_weight=5.0, verbose=False,
    )

    for r in results:
        assert not r["plan_df"].empty
    assert results[2]["summary"]["feasible"] is True  # relaxed cap recovers feasibility


def test_confirmation_digest_is_a_dry_run_and_totals_cost():
    days = [DayPlan(on=TODAY, free_minutes=120)]
    results = plan_week_multichannel(days, [CookAtHomeAdapter()], GOALS, default_budget_cap=15.0, today=TODAY)
    digest = confirmation_digest(results)
    assert "DRY RUN" in digest
    assert "nothing has been ordered or charged" in digest
    total = results[0]["summary"]["achieved"]["cost"]
    assert f"${total:.2f}" in digest
