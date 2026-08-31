"""The menu planner sketched in IDEAS.md.

Everything else in this package picks food for a single day in isolation.
This module extends that into an actual weekly menu: the same macro
targets, budget, and pantry, but now also aware of how much free time the
user has to cook on a given day, and able to pin specific recipes to
specific days.

The calendar side stays deliberately simple. IDEAS.md notes that Google
Calendar's freebusy.query returns busy intervals, not free ones, so turning
a real calendar into a per-day minute budget is a real integration, not a
free one. Rather than half-build that here, a day's free time is just a
number the caller supplies, the same "first version is just a manual list"
pattern pantry.py already uses. Swapping in a real calendar source later
only means computing that number differently, nothing downstream changes.

Preference learning from usage history is explicitly out of scope here too,
IDEAS.md itself says that needs an app used over time to log picks against,
not something that bolts onto a single planning run. What this module does
implement is the piece IDEAS.md calls "the simplest of the three": manually
pinning specific recipes to specific days.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from .history import PickHistory
from .optimizer import run_optimizer_with_rotation_backoff


@dataclass
class DayPlan:
    """One day's planning inputs. Goals/budget default to the week's, override per day if needed."""

    on: date
    free_minutes: float
    goals: Optional[dict] = None
    budget_cap: Optional[float] = None
    pinned_items: List[str] = field(default_factory=list)


def plan_week(
    food_df: pd.DataFrame,
    days: List[DayPlan],
    default_goals: dict,
    default_budget_cap: float,
    filters: Optional[dict] = None,
    history: Optional[PickHistory] = None,
    recency_window_days: int = 7,
    recency_penalty_weight: float = 1.0,
    rotation_cap: Optional[int] = None,
    tag_rotation_cap: Optional[int] = None,
    item_tags: Optional[Dict[str, str]] = None,
    preference_bonus: Optional[dict] = None,
    verbose: bool = False,
) -> List[dict]:
    """Plan one meal combination per day across ``days``, in order.

    Each day is solved with ``run_optimizer`` against its own time budget,
    then immediately recorded into ``history`` before the next day is
    solved. That's what gives the week real variety, not just the
    cross-run kind: day 3's rotation cap and recency penalty already see
    what days 1 and 2 picked, not just what happened on prior calls to
    this function.

    Returns a list of {day, plan_df, summary} dicts, one per input day. A
    day that's infeasible even after the optimizer's own soft fallback
    still returns its best-effort plan, callers should check
    ``summary['feasible']`` per day rather than assuming success.
    """
    history = history if history is not None else PickHistory()
    results = []

    for day in days:
        goals = day.goals or default_goals
        budget_cap = day.budget_cap if day.budget_cap is not None else default_budget_cap

        plan_df, summary = run_optimizer_with_rotation_backoff(
            rotation_cap,
            recency_window_days,
            food_df=food_df,
            goals=goals,
            budget_cap=budget_cap,
            filters=filters,
            history=history,
            recency_penalty_weight=recency_penalty_weight,
            tag_rotation_cap=tag_rotation_cap,
            item_tags=item_tags,
            as_of=day.on,
            time_budget_minutes=day.free_minutes,
            preference_bonus=preference_bonus,
            pinned_items=day.pinned_items,
            verbose=verbose,
        )

        history.record_plan(plan_df, on=day.on)
        results.append({"day": day.on, "plan_df": plan_df, "summary": summary})

    return results


def week_summary(results: List[dict]) -> dict:
    """Roll a plan_week() result up into totals: cost, macros, and which days missed a goal."""
    total_cost = sum(r["summary"]["achieved"]["cost"] for r in results)
    infeasible_days = [r["day"].isoformat() for r in results if not r["summary"]["feasible"]]
    return {
        "total_cost": total_cost,
        "days_planned": len(results),
        "infeasible_days": infeasible_days,
    }
