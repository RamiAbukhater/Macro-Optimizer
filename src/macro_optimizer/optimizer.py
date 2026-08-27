"""The ILP optimization engine, extracted from the notebook.

Given a food candidate DataFrame (see database.py) and a set of macro/budget
targets, finds the cheapest combination of servings that clears every macro
floor within budget. Falls back to a soft-constraint version that minimizes
cost plus a penalty for whatever goals it couldn't fully reach.

Also carries the anti-repetition guard IDEAS.md calls for: a pure
cost-minimizing ILP has no reason to ever vary its answer, so without this a
run against the same database and goals returns the same handful of
protein-per-dollar items every time. See history.py for where the penalty
and rotation-cap inputs come from.
"""

from typing import Optional, Tuple

import pandas as pd
import pulp

from .database import apply_dietary_filters
from .history import PickHistory

MAX_SERVINGS_DEFAULT = 3
PENALTY_PER_GRAM = 50  # penalty weight for unmet macro in soft-constraint fallback


def run_optimizer(
    food_df: pd.DataFrame,
    goals: dict,
    budget_cap: float,
    filters: Optional[dict] = None,
    history: Optional[PickHistory] = None,
    recency_window_days: int = 7,
    recency_penalty_weight: float = 1.0,
    rotation_cap: Optional[int] = None,
    time_budget_minutes: Optional[float] = None,
    preference_bonus: Optional[dict] = None,
    pinned_items: Optional[list] = None,
    verbose: bool = True,
) -> Tuple[pd.DataFrame, dict]:
    """Run the ILP optimizer. Returns (plan_df, summary_dict).

    ``history``, when given, feeds the anti-repetition guard: items picked
    ``rotation_cap`` or more times in the trailing ``recency_window_days``
    are excluded outright, and every other item's objective cost is bumped
    by ``recency_penalty_weight`` dollars per recent occurrence, so the
    optimizer drifts toward variety instead of a fixed rotation without
    ever being blocked from a genuinely necessary repeat.

    ``time_budget_minutes``, when given, caps total prep + cook time across
    the whole plan, per menu_planner.py's day-by-day scheduling.

    ``preference_bonus`` is a {name: dollars} discount applied to an item's
    objective cost, a soft nudge toward foods the user actually likes rather
    than whatever is purely cheapest, without ever excluding the true
    optimum. ``pinned_items`` is the hard version, at least one serving of
    each named item is forced into the plan (per IDEAS.md's "manually
    preferred recipes").
    """
    if filters:
        food_df = apply_dietary_filters(food_df, filters)

    if history is not None and rotation_cap is not None:
        excluded = set(history.rotation_cap_exclusions(recency_window_days, rotation_cap))
        if excluded:
            food_df = food_df[~food_df["name"].isin(excluded)].copy()

    if food_df.empty:
        raise ValueError("No food items remain after applying dietary filters and rotation cap.")

    pinned_items = pinned_items or []
    missing_pins = [name for name in pinned_items if name not in food_df["name"].values]
    if missing_pins:
        raise ValueError(f"pinned item(s) not available after filtering: {missing_pins}")

    penalties = (
        history.recency_penalty(recency_window_days, recency_penalty_weight)
        if history is not None
        else {}
    )
    bonuses = preference_bonus or {}

    idx = list(food_df.index)

    prob = pulp.LpProblem("Macro_Optimizer", pulp.LpMinimize)
    x = {
        i: pulp.LpVariable(
            f"x_{i}",
            lowBound=1 if food_df.loc[i, "name"] in pinned_items else 0,
            upBound=int(food_df.loc[i, "max_daily"]),
            cat="Integer",
        )
        for i in idx
    }

    def effective_cost(i):
        name = food_df.loc[i, "name"]
        return food_df.loc[i, "price"] + penalties.get(name, 0.0) - bonuses.get(name, 0.0)

    prob += pulp.lpSum(effective_cost(i) * x[i] for i in idx), "Total_Cost_With_Variety_Penalty"

    prob += (pulp.lpSum(food_df.loc[i, "calories"] * x[i] for i in idx) >= goals["calories"], "min_calories")
    prob += (pulp.lpSum(food_df.loc[i, "protein"] * x[i] for i in idx) >= goals["protein"], "min_protein")
    prob += (pulp.lpSum(food_df.loc[i, "carbs"] * x[i] for i in idx) >= goals["carbs"], "min_carbs")
    prob += (pulp.lpSum(food_df.loc[i, "fat"] * x[i] for i in idx) >= goals["fat"], "min_fat")
    prob += (pulp.lpSum(food_df.loc[i, "price"] * x[i] for i in idx) <= budget_cap, "budget_cap")
    if time_budget_minutes is not None:
        prob += (
            pulp.lpSum(food_df.loc[i, "total_time_minutes"] * x[i] for i in idx) <= time_budget_minutes,
            "time_budget",
        )

    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=30)
    prob.solve(solver)
    feasible = pulp.LpStatus[prob.status] == "Optimal"

    if not feasible:
        if verbose:
            print(f"Hard-constraint problem: {pulp.LpStatus[prob.status]}.")
            print("Running soft-constraint fallback (goals become targets, not hard limits)...")
        return _soft_fallback(
            food_df, goals, budget_cap, penalties, bonuses, pinned_items, time_budget_minutes, verbose=verbose
        )

    plan_df = _extract_plan(food_df, x, idx)
    summary = _build_summary(plan_df, goals, budget_cap, feasible=True)
    return plan_df, summary


def _soft_fallback(food_df, goals, budget_cap, penalties, bonuses, pinned_items, time_budget_minutes, verbose):
    idx = list(food_df.index)
    prob = pulp.LpProblem("Macro_Soft_Optimizer", pulp.LpMinimize)
    x = {
        i: pulp.LpVariable(
            f"x_{i}",
            lowBound=1 if food_df.loc[i, "name"] in pinned_items else 0,
            upBound=int(food_df.loc[i, "max_daily"]),
            cat="Integer",
        )
        for i in idx
    }
    s_cal = pulp.LpVariable("slack_cal", lowBound=0)
    s_prot = pulp.LpVariable("slack_prot", lowBound=0)
    s_carb = pulp.LpVariable("slack_carb", lowBound=0)
    s_fat = pulp.LpVariable("slack_fat", lowBound=0)

    def effective_cost(i):
        name = food_df.loc[i, "name"]
        return food_df.loc[i, "price"] + penalties.get(name, 0.0) - bonuses.get(name, 0.0)

    P = PENALTY_PER_GRAM
    prob += (
        pulp.lpSum(effective_cost(i) * x[i] for i in idx)
        + P * s_cal + P * s_prot + P * s_carb + P * s_fat
    )

    prob += pulp.lpSum(food_df.loc[i, "calories"] * x[i] for i in idx) + s_cal >= goals["calories"]
    prob += pulp.lpSum(food_df.loc[i, "protein"] * x[i] for i in idx) + s_prot >= goals["protein"]
    prob += pulp.lpSum(food_df.loc[i, "carbs"] * x[i] for i in idx) + s_carb >= goals["carbs"]
    prob += pulp.lpSum(food_df.loc[i, "fat"] * x[i] for i in idx) + s_fat >= goals["fat"]
    prob += pulp.lpSum(food_df.loc[i, "price"] * x[i] for i in idx) <= budget_cap
    if time_budget_minutes is not None:
        prob += pulp.lpSum(food_df.loc[i, "total_time_minutes"] * x[i] for i in idx) <= time_budget_minutes

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=30))

    plan_df = _extract_plan(food_df, x, idx)
    summary = _build_summary(plan_df, goals, budget_cap, feasible=False)
    return plan_df, summary


def _extract_plan(food_df, x, idx):
    plan_rows = []
    for i in idx:
        n = int(round(x[i].varValue or 0))
        if n > 0:
            row = food_df.loc[i].copy()
            row["servings"] = n
            row["total_cost"] = row["price"] * n
            row["total_cal"] = row["calories"] * n
            row["total_prot"] = row["protein"] * n
            row["total_carb"] = row["carbs"] * n
            row["total_fat"] = row["fat"] * n
            plan_rows.append(row)
    return pd.DataFrame(plan_rows)


def _build_summary(plan_df, goals, budget_cap, feasible):
    achieved = {
        "cost": plan_df["total_cost"].sum() if not plan_df.empty else 0,
        "calories": plan_df["total_cal"].sum() if not plan_df.empty else 0,
        "protein": plan_df["total_prot"].sum() if not plan_df.empty else 0,
        "carbs": plan_df["total_carb"].sum() if not plan_df.empty else 0,
        "fat": plan_df["total_fat"].sum() if not plan_df.empty else 0,
    }
    gaps = {k: max(0, goals.get(k, 0) - achieved.get(k, 0)) for k in ["calories", "protein", "carbs", "fat"]}
    return dict(achieved=achieved, gaps=gaps, goals=goals, budget_cap=budget_cap, feasible=feasible)
