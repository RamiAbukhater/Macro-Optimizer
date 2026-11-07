"""The decision engine sketched in AUTOMATION.md, plus its confirm-before-purchase layer.

For each day, only the channels whose lead time has already been cleared
are viable, a prepared meal ordered today can't land tomorrow if its lead
time is 3 days, so the near-term days on a rolling weekly plan lose that
option and lean on same-day channels instead, while the far-out days can
still lock in the slower, cheaper ones. That's AUTOMATION.md's "lead time
is the real new constraint" idea: the planner runs on a rolling basis,
committing the early-cutoff channels first.

Once the viable channels for a day are known, picking the cheapest
combination across all of them is exactly the problem run_optimizer
already solves, no new solver logic needed, the decision engine's whole
job is deciding which candidate rows are even allowed to compete that day.

Nothing in this module places a real order or spends real money.
AUTOMATION.md is explicit that deciding a plan and acting on it are
different problems, and that the realistic path is a confirm step before
anything gets purchased. confirmation_digest() is that step, a plain-text,
nothing-happens-until-approved summary; wiring an approved digest to an
actual channel's checkout API is a separate integration this project
doesn't have vendor access to build yet.
"""

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from .channels import ChannelAdapter
from .database import load_food_db
from .history import PickHistory
from .menu_planner import DayPlan
from .optimizer import run_optimizer_with_rotation_backoff


def plan_week_multichannel(
    days: List[DayPlan],
    channels: List[ChannelAdapter],
    default_goals: dict,
    default_budget_cap: float,
    today: Optional[date] = None,
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
    """Plan one meal combination per day, choosing freely across every channel viable that day.

    Returns a list of dicts: {day, days_out, viable_channels, plan_df,
    summary}, in the same shape menu_planner.plan_week() returns plus the
    two new fields, so downstream code (history, confirmation_digest) can
    treat both the same way.
    """
    today = today or date.today()
    history = history if history is not None else PickHistory()
    results = []

    for day in days:
        days_out = (day.on - today).days
        if days_out < 0:
            raise ValueError(f"day {day.on} is in the past relative to today={today}")

        viable = [c for c in channels if days_out >= c.lead_time_days]
        if not viable:
            names = [c.name for c in channels]
            raise ValueError(
                f"no channel is viable for {day.on} ({days_out} days out); "
                f"available channels need at least {min(c.lead_time_days for c in channels)} "
                f"days lead time: {names}"
            )

        candidates = []
        for channel in viable:
            candidates.extend(channel.candidates())

        food_df = load_food_db(extra_items=candidates, include_market=False)

        goals = day.goals or default_goals
        budget_cap = day.budget_cap if day.budget_cap is not None else default_budget_cap

        plan_df, summary = run_optimizer_with_rotation_backoff(
            rotation_cap, recency_window_days,
            food_df=food_df, goals=goals, budget_cap=budget_cap, filters=filters,
            history=history, recency_penalty_weight=recency_penalty_weight,
            tag_rotation_cap=tag_rotation_cap, item_tags=item_tags,
            as_of=day.on, time_budget_minutes=day.free_minutes, preference_bonus=preference_bonus,
            pinned_items=day.pinned_items, verbose=verbose,
        )
        history.record_plan(plan_df, on=day.on)

        results.append({
            "day": day.on,
            "days_out": days_out,
            "viable_channels": [c.name for c in viable],
            "plan_df": plan_df,
            "summary": summary,
        })

    return results


def confirmation_digest(results: List[dict]) -> str:
    """A plain-text, nothing-happens-until-approved summary of a multichannel plan.

    This is the confirm step AUTOMATION.md calls for, not an order placer.
    """
    lines = ["Weekly plan, DRY RUN: nothing has been ordered or charged.", ""]
    total = 0.0
    for r in results:
        cost = r["summary"]["achieved"]["cost"]
        total += cost
        status = "OK" if r["summary"]["feasible"] else "BEST-EFFORT"
        if r["plan_df"].empty:
            items = "(nothing selected)"
        else:
            items = ", ".join(
                f"{row['name']} x{int(row['servings'])} [{row['source']}]"
                for _, row in r["plan_df"].iterrows()
            )
        lines.append(f"{r['day'].isoformat()}  [{status}]  ${cost:.2f}  {items}")
    lines.append("")
    lines.append(f"Total: ${total:.2f}")
    lines.append("Reply to approve before any channel places a real order.")
    return "\n".join(lines)
