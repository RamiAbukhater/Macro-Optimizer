from datetime import date, timedelta

import pandas as pd
import pytest

from macro_optimizer.database import load_food_db
from macro_optimizer.history import PickHistory
from macro_optimizer.optimizer import run_optimizer, run_optimizer_with_rotation_backoff
from macro_optimizer.recipes import recipe_food_items
from macro_optimizer.schema import FoodItem

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


def test_run_optimizer_rotation_cap_checks_against_as_of_not_real_today():
    """A future-dated pick (as a rolling week plan records under today+N,
    not real today) must still be excluded when as_of matches that future
    day, not just when as_of happens to be real today. Regression test for
    the "every day after the first repeats the same meals" bug: without
    threading as_of through, this window check silently used real
    date.today() for every call, so a pick recorded for today+1 was invisible
    to the window check when solving today+2 and beyond."""
    df = load_food_db()
    history = PickHistory()
    top_item = df.sort_values("protein_per_dollar", ascending=False).iloc[0]["name"]
    future_day = date.today() + timedelta(days=3)
    for _ in range(3):
        history.add_pick(top_item, on=future_day)

    plan_df, _ = run_optimizer(
        df, GOALS, budget_cap=28.00,
        history=history, recency_window_days=7, rotation_cap=3,
        as_of=future_day, verbose=False,
    )
    assert top_item not in plan_df["name"].values


def test_run_optimizer_time_budget_excludes_slow_recipes():
    df = load_food_db(extra_items=recipe_food_items())
    # Every recipe in the seed set takes at least 10 minutes; a 5-minute
    # budget should force the plan back onto ready-to-eat grocery items.
    plan_df, _ = run_optimizer(df, GOALS, budget_cap=28.00, time_budget_minutes=5, verbose=False)
    assert not plan_df.empty
    assert (plan_df["total_time_minutes"] <= 5).all()


def test_run_optimizer_time_budget_zero_still_feasible_from_ready_to_eat_items():
    df = load_food_db()  # grocery catalog only, all prep_minutes = cook_minutes = 0
    plan_df, summary = run_optimizer(df, GOALS, budget_cap=28.00, time_budget_minutes=0, verbose=False)
    assert summary["feasible"] is True
    assert (plan_df["total_time_minutes"] == 0).all()


def test_run_optimizer_preference_bonus_pulls_in_a_boosted_item():
    df = load_food_db()
    target = next(name for name in df["name"] if "Bagel" in name)

    plan_df, _ = run_optimizer(
        df, GOALS, budget_cap=28.00,
        preference_bonus={target: 50.0},
        verbose=False,
    )
    assert target in plan_df["name"].values


def test_run_optimizer_pinned_items_forced_into_plan():
    df = load_food_db()
    pin = df.sort_values("protein_per_dollar").iloc[0]["name"]  # worst protein per dollar, not picked otherwise
    plan_df, _ = run_optimizer(df, GOALS, budget_cap=28.00, pinned_items=[pin], verbose=False)
    assert pin in plan_df["name"].values
    assert plan_df.loc[plan_df["name"] == pin, "servings"].iloc[0] >= 1


def test_run_optimizer_pinned_item_missing_after_filters_raises():
    df = load_food_db()
    non_vegan_pin = df.loc[~df["vegan"], "name"].iloc[0]
    with pytest.raises(ValueError):
        run_optimizer(df, GOALS, budget_cap=28.00, filters={"vegan": True}, pinned_items=[non_vegan_pin], verbose=False)


def test_run_optimizer_tag_rotation_cap_excludes_every_item_sharing_an_over_capped_tag():
    beef_burger = FoodItem(name="Beef Burger", category="Protein", source="Test", price=1.0,
                            calories=300, protein=30, carbs=0, fat=10, max_daily=5)
    beef_brisket = FoodItem(name="Beef Brisket", category="Protein", source="Test", price=1.0,
                             calories=300, protein=30, carbs=0, fat=10, max_daily=5)
    chicken = FoodItem(name="Chicken Breast", category="Protein", source="Test", price=1.0,
                        calories=200, protein=40, carbs=0, fat=5, max_daily=5)
    df = load_food_db(extra_items=[beef_burger, beef_brisket, chicken], include_market=False)
    item_tags = {"Beef Burger": "beef", "Beef Brisket": "beef"}

    history = PickHistory()
    history.add_pick("Beef Burger", on=date.today() - timedelta(days=2))
    history.add_pick("Beef Brisket", on=date.today() - timedelta(days=1))

    goals = {"calories": 200, "protein": 30, "carbs": 0, "fat": 0}
    plan_df, _ = run_optimizer(
        df, goals, budget_cap=10.0,
        history=history, recency_window_days=7,
        tag_rotation_cap=2, item_tags=item_tags,
        as_of=date.today(), verbose=False,
    )

    assert "Beef Burger" not in plan_df["name"].values
    assert "Beef Brisket" not in plan_df["name"].values
    assert "Chicken Breast" in plan_df["name"].values


def _two_item_db(protein_b=5.0, max_daily_b=5):
    """ItemA is macro-dense and cheap; ItemB alone can't come close to the
    goals below, so excluding ItemA (as a strict rotation cap would, once
    it's already been picked) makes the day infeasible unless the cap relaxes."""
    a = FoodItem(name="ItemA", category="Protein", source="Test", price=1.0,
                 calories=200, protein=50, carbs=0, fat=0, max_daily=5)
    b = FoodItem(name="ItemB", category="Protein", source="Test", price=1.0,
                 calories=50, protein=protein_b, carbs=0, fat=0, max_daily=max_daily_b)
    return load_food_db(extra_items=[a, b], include_market=False)


def test_run_optimizer_with_rotation_backoff_relaxes_cap_when_strict_cap_is_infeasible():
    df = _two_item_db()
    goals = {"calories": 200, "protein": 50, "carbs": 0, "fat": 0}
    history = PickHistory()
    history.add_pick("ItemA", on=date.today())  # one prior use -> rotation_cap=1 would exclude it

    plan_df, summary = run_optimizer_with_rotation_backoff(
        1, 7,
        food_df=df, goals=goals, budget_cap=10.0, filters=None,
        history=history, recency_penalty_weight=1.0, as_of=date.today(),
        time_budget_minutes=None, preference_bonus=None, pinned_items=[], verbose=False,
    )

    # ItemB alone can't hit protein=50 (max 5 servings x 5g = 25g), so the
    # strict cap must have relaxed and let ItemA back in to reach feasibility.
    assert summary["feasible"] is True
    assert "ItemA" in plan_df["name"].values


def test_run_optimizer_with_rotation_backoff_keeps_strict_cap_when_already_feasible():
    df = _two_item_db(protein_b=50.0)  # ItemB alone is just as capable here
    goals = {"calories": 200, "protein": 50, "carbs": 0, "fat": 0}
    history = PickHistory()
    history.add_pick("ItemA", on=date.today())

    plan_df, summary = run_optimizer_with_rotation_backoff(
        1, 7,
        food_df=df, goals=goals, budget_cap=10.0, filters=None,
        history=history, recency_penalty_weight=1.0, as_of=date.today(),
        time_budget_minutes=None, preference_bonus=None, pinned_items=[], verbose=False,
    )

    # No relaxation needed here, the strict cap should hold and ItemA should
    # stay excluded.
    assert summary["feasible"] is True
    assert "ItemA" not in plan_df["name"].values


def test_run_optimizer_with_rotation_backoff_falls_back_to_unrestricted_when_still_infeasible():
    df = _two_item_db()
    # Budget so tight even the unrestricted problem (both items available)
    # can't hit the goals, no amount of cap relaxation should fix this.
    goals = {"calories": 200, "protein": 50, "carbs": 0, "fat": 0}
    history = PickHistory()
    history.add_pick("ItemA", on=date.today())

    plan_df, summary = run_optimizer_with_rotation_backoff(
        1, 7,
        food_df=df, goals=goals, budget_cap=0.50, filters=None,
        history=history, recency_penalty_weight=1.0, as_of=date.today(),
        time_budget_minutes=None, preference_bonus=None, pinned_items=[], verbose=False,
    )
    assert summary["feasible"] is False
    assert summary["achieved"]["cost"] <= 0.50 + 1e-6


def test_run_optimizer_with_rotation_backoff_keeps_the_tighter_cap_when_no_cap_recovers_feasibility():
    """Regression test for the "genuinely infeasible days all repeat the same
    meal" bug: when NO amount of rotation relaxation actually recovers full
    feasibility, this must keep the tightest (most-varied) cap's result, not
    whichever cap happens to shrink the macro gap the most. Otherwise every
    already-best-effort day converges back onto the single globally
    closest-to-goal combination, since _soft_fallback's huge per-gram macro
    penalty dwarfs the recency penalty once a day is in soft-fallback
    territory, and real variety silently disappears exactly when the day was
    already falling short.
    """
    goals = {"calories": 0, "protein": 150, "carbs": 0, "fat": 0}
    # ItemA is the clearly macro-superior pick (80g protein max) but was
    # already used once; ItemB is a much weaker fallback (20g protein max).
    # Even both together only reach 100g, short of 150g, so this day can
    # never be feasible no matter how much the rotation cap relaxes.
    a = FoodItem(name="ItemA", category="Protein", source="Test", price=1.0,
                 calories=10, protein=40, carbs=0, fat=0, max_daily=2)
    b = FoodItem(name="ItemB", category="Protein", source="Test", price=1.0,
                 calories=10, protein=10, carbs=0, fat=0, max_daily=2)
    df = load_food_db(extra_items=[a, b], include_market=False)

    history = PickHistory()
    history.add_pick("ItemA", on=date.today())  # rotation_cap=1 excludes it

    plan_df, summary = run_optimizer_with_rotation_backoff(
        1, 7,
        food_df=df, goals=goals, budget_cap=10.0, filters=None,
        history=history, recency_penalty_weight=1.0, as_of=date.today(),
        time_budget_minutes=None, preference_bonus=None, pinned_items=[], verbose=False,
    )

    assert summary["feasible"] is False
    # The buggy version would relax past cap=1 to chase ItemA's smaller
    # protein gap, silently undoing the exclusion on an already-lost day.
    assert "ItemA" not in plan_df["name"].values
    assert "ItemB" in plan_df["name"].values


def test_run_optimizer_with_rotation_backoff_no_cap_does_not_retry():
    df = _two_item_db()
    goals = {"calories": 200, "protein": 50, "carbs": 0, "fat": 0}
    plan_df, summary = run_optimizer_with_rotation_backoff(
        None, 7,
        food_df=df, goals=goals, budget_cap=10.0, filters=None,
        history=None, recency_penalty_weight=1.0, as_of=date.today(),
        time_budget_minutes=None, preference_bonus=None, pinned_items=[], verbose=False,
    )
    assert summary["feasible"] is True
