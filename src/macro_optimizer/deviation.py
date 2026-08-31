"""Logs what was actually eaten when it differs from the plan.

NLU_ADJUSTMENTS.md's "deviation logging" capability lets a user say, in plain
English, that they skipped a planned item for something else. Once that
sentence is parsed into a planned item and an actual item (nlu.py's job, not
this module's), three things need to happen: log the substitution, correct
that day's achieved macros for it, and carry any resulting shortfall or
overage into tomorrow's goals rather than silently dropping it.

DeviationLog handles the log; recompute_day_macros and carry_forward_goals
are the two pieces of arithmetic NLU_ADJUSTMENTS.md's Capability 2 spells
out, kept as plain functions since neither needs the log to do its job.
"""

import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Union

MACRO_KEYS = ("calories", "protein", "carbs", "fat")


class DeviationLog:
    """A log of {date, planned_item, actual_item} entries, optionally JSON-backed."""

    def __init__(
        self,
        path: Optional[Union[str, Path]] = None,
        entries: Optional[List[dict]] = None,
    ):
        """``entries``, when given, seeds an in-memory-only copy (e.g. a blob
        loaded from a database) without writing anything back to ``path``,
        mirroring PickHistory's ``entries=`` constructor. Callers that want a
        real on-disk log should pass ``path`` alone."""
        self.path = Path(path) if path else None
        if entries is not None:
            self._entries: List[dict] = list(entries)
        elif self.path and self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self._entries = json.load(f)
        else:
            self._entries = []

    def entries(self) -> List[dict]:
        return list(self._entries)

    def record(self, day, planned_item: dict, actual_item: dict, on: Optional[date] = None) -> None:
        # `on` overrides `day` when both are given, matching PickHistory's
        # on-defaults-to-today convention while still taking the day the
        # deviation happened on as the usual first argument.
        on = on or day or date.today()
        self._entries.append({
            "date": on.isoformat(),
            "planned_item": planned_item,
            "actual_item": actual_item,
        })
        self._save()

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, indent=2)


def recompute_day_macros(achieved: dict, planned_item: dict, actual_item: dict) -> dict:
    """A day's achieved macros after swapping ``planned_item`` for ``actual_item``.

    Matches the achieved/gaps shape update.md item #4 added to webapp/app.py:
    one float per macro in MACRO_KEYS.
    """
    return {
        macro: achieved[macro] - planned_item[macro] + actual_item[macro]
        for macro in MACRO_KEYS
    }


def carry_forward_goals(default_goals: dict, delta: dict, cap_fraction: float = 0.2) -> dict:
    """Next day's goals after clamping how much a deviation can shift them.

    Implements NLU_ADJUSTMENTS.md's carry-forward formula: a deviation nudges
    tomorrow's goal by ``delta``, but never by more than ``cap_fraction`` of
    that macro's own default goal in either direction, so one large deviation
    (skipping a 1200-calorie dinner for a 400-calorie one) doesn't demand an
    unreasonable swing the very next day.
    """
    next_goals = {}
    for macro, default_goal in default_goals.items():
        cap = cap_fraction * default_goal
        clamped_delta = max(-cap, min(cap, delta.get(macro, 0)))
        next_goals[macro] = default_goal - clamped_delta
    return next_goals
