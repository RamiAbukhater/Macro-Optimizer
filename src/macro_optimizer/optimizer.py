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

from datetime import date
from typing import Dict, Optional, Tuple

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
    tag_rotation_cap: Optional[int] = None,
    item_tags: Optional[Dict[str, str]] = None,
    as_of: Optional[date] = None,
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

    ``tag_rotation_cap`` and ``item_tags`` extend that same exclusion to
    categories rather than exact names, "beef burger" Monday then "beef
    brisket" Wednesday both count against the "beef" tag, so a category
    that's been picked ``tag_rotation_cap`` or more times in the window
    gets every one of its tagged items excluded, not just repeats of the
    exact item. ``item_tags`` is a {item_name: tag} lookup (see tags.py);
    both must be given together, and an item absent from ``item_tags`` is
    unaffected by this guard. Only the exact-name cap gets a recency
    penalty equivalent, this is exclusion-only.

    ``as_of`` is the day this solve is *for*, defaulting to today. It has to
    be passed explicitly by a multi-day caller (menu_planner.py,
    orchestrator.py) solving a rolling week of *future* dates one at a time:
    PickHistory logs each day under its own real date (today+1, today+2, ...),
    and the window check is ``entry_date <= as_of``, so leaving this at
    today's real date while solving today+2 would silently drop today+1's
    just-recorded pick from the window (its date is in the "future" relative
    to as_of) and only ever compare against today's own entry. Every later
    day would then solve the exact same problem and converge on the same
    picks, look identical to each other past day one.

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
        excluded = set(history.rotation_cap_exclusions(recency_window_days, rotation_cap, as_of=as_of))
        if excluded:
            food_df = food_df[~food_df["name"].isin(excluded)].copy()

    if history is not None and tag_rotation_cap is not None and item_tags is not None:
        excluded_tags = set(
            history.tag_rotation_cap_exclusions(item_tags, recency_window_days, tag_rotation_cap, as_of=as_of)
        )
        if excluded_tags:
            food_df = food_df[~food_df["name"].map(item_tags).isin(excluded_tags)].copy()

    if food_df.empty:
        raise ValueError("No food items remain after applying dietary filters and rotation cap.")

    pinned_items = pinned_items or []
    missing_pins = [name for name in pinned_items if name not in food_df["name"].values]
    if missing_pins:
        raise ValueError(f"pinned item(s) not available after filtering: {missing_pins}")

    penalties = (
        history.recency_penalty(recency_window_days, recency_penalty_weight, as_of=as_of)
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


def run_optimizer_with_rotation_backoff(rotation_cap: Optional[int], recency_window_days: int = 7, **kwargs):
    """Solve one day, relaxing ``rotation_cap`` only as far as feasibility actually requires.

    A hard rotation cap is what guarantees real variety, rotation_cap=1
    means no item repeats anywhere in the window, the strongest form of "every
    day is different." But on a small database that guarantee can run the
    cheap, macro-dense items out before the week is over, forcing a day into
    the soft-constraint fallback even though a single repeated item would
    have cleared every goal. That's a worse trade than the repeat: this
    function tries the requested cap first, and only if that day comes back
    infeasible does it loosen the cap one step at a time (1, 2, 3, ...) until
    either a day is feasible again or the cap is dropped entirely, so the
    guard never silently costs a whole day its macro floors just to avoid a
    repeat this database genuinely can't afford at the given budget.

    Looser isn't always better, though: a day can be infeasible for a reason
    that has nothing to do with rotation at all (the budget is just too tight
    for these goals, full stop), and relaxing the cap in that case doesn't
    fix anything, it just lets the *next* looser cap re-include whatever
    combination is closest to the goals overall. ``_soft_fallback``'s huge
    per-gram macro penalty dwarfs the recency penalty once a day is already
    in soft-fallback territory, so that "closest" combination is nearly
    always the very same one, and if this function kept chasing a smaller
    gap across every cap level, every infeasible day would converge back
    onto it, the "some days are all identical" bug this function exists to
    prevent, just triggered by a real budget/macro shortfall instead of an
    exhausted database. So relaxation here is judged purely by whether it
    recovers full feasibility, not by whether it shrinks the gap: the first
    cap that actually solves (or the tightest cap that produces any result
    at all, if none does) is what's kept once every cap has been tried and
    none came back feasible, a smaller-but-still-missed gap is not worth
    trading away the variety a repeat would have cost.

    A strict-enough cap can exclude every remaining candidate outright,
    which run_optimizer treats as a hard error (``ValueError``), not a
    soft-fallback "infeasible" result, there's nothing left to solve at
    all. That's exactly the case this function exists to relax past, so it's
    caught here and treated the same as "infeasible, try a looser cap" up
    until the cap is fully relaxed; a ValueError that still happens with no
    rotation cap in play at all (e.g. a pinned item dietary filters already
    excluded) is a real caller error, not a rotation problem, and propagates.
    """
    cap = rotation_cap
    # The first cap level that produces any result at all (starting from the
    # tightest, most-varied cap requested and only loosening on a ValueError,
    # i.e. the database has nothing left to offer at that cap). This is what
    # gets returned if no looser cap ever recovers full feasibility, so an
    # already-best-effort day keeps its variety instead of collapsing onto
    # whatever's globally closest to the goals.
    tightest_result = None
    while True:
        try:
            plan_df, summary = run_optimizer(rotation_cap=cap, recency_window_days=recency_window_days, **kwargs)
        except ValueError:
            if cap is None:
                if tightest_result is not None:
                    return tightest_result
                raise
            cap = cap + 1 if cap < recency_window_days else None
            continue

        if tightest_result is None:
            tightest_result = (plan_df, summary)

        if summary["feasible"]:
            return plan_df, summary  # relaxing recovered real feasibility, worth keeping

        if cap is None:
            return tightest_result  # nothing recovers feasibility; keep the most-varied attempt
        cap = cap + 1 if cap < recency_window_days else None


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
