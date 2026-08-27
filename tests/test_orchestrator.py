from datetime import date, timedelta

import pytest

from macro_optimizer.channels import CookAtHomeAdapter, PreparedMealAdapter, RestaurantOrderAdapter
from macro_optimizer.menu_planner import DayPlan
from macro_optimizer.orchestrator import confirmation_digest, plan_week_multichannel

GOALS = {"calories": 400, "protein": 30, "carbs": 20, "fat": 10}
TODAY = date(2026, 1, 1)


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


def test_confirmation_digest_is_a_dry_run_and_totals_cost():
    days = [DayPlan(on=TODAY, free_minutes=120)]
    results = plan_week_multichannel(days, [CookAtHomeAdapter()], GOALS, default_budget_cap=15.0, today=TODAY)
    digest = confirmation_digest(results)
    assert "DRY RUN" in digest
    assert "nothing has been ordered or charged" in digest
    total = results[0]["summary"]["achieved"]["cost"]
    assert f"${total:.2f}" in digest
