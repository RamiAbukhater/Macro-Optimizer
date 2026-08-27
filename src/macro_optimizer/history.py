"""Tracks what's actually been picked over time.

IDEAS.md flags a real problem with the optimizer as it exists today: a pure
cost-minimizing ILP has no reason to ever change its answer, so given the
same database and goals it picks the same handful of protein-per-dollar
items every single run. The fix has to live in the objective itself, a
recency penalty on repeated items or a hard cap on how many times something
can appear inside a rolling window, not something the user is expected to
notice and override by hand.

PickHistory is the piece that makes that possible, a log of what was chosen
on which day, plus queries optimizer.py uses to build that penalty.
"""

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Union


class PickHistory:
    """A log of {date, item_name, servings} entries, optionally JSON-backed."""

    def __init__(self, path: Optional[Union[str, Path]] = None):
        self.path = Path(path) if path else None
        self._entries: List[dict] = []
        if self.path and self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self._entries = json.load(f)

    def record_plan(self, plan_df, on: Optional[date] = None) -> None:
        """Log every item in a solved plan_df (must have 'name' and 'servings')."""
        on = on or date.today()
        for _, row in plan_df.iterrows():
            self._entries.append({
                "date": on.isoformat(),
                "name": row["name"],
                "servings": int(row["servings"]),
            })
        self._save()

    def add_pick(self, item_name: str, servings: int = 1, on: Optional[date] = None) -> None:
        on = on or date.today()
        self._entries.append({"date": on.isoformat(), "name": item_name, "servings": servings})
        self._save()

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, indent=2)

    def counts_in_window(self, window_days: int, as_of: Optional[date] = None) -> Dict[str, int]:
        """Number of days each item was picked within the trailing window."""
        as_of = as_of or date.today()
        cutoff = as_of - timedelta(days=window_days)
        counts: Counter = Counter()
        for entry in self._entries:
            entry_date = date.fromisoformat(entry["date"])
            if cutoff < entry_date <= as_of:
                counts[entry["name"]] += 1
        return dict(counts)

    def recency_penalty(
        self,
        window_days: int = 7,
        penalty_per_occurrence: float = 1.0,
        as_of: Optional[date] = None,
    ) -> Dict[str, float]:
        """Dollar-equivalent penalty per item, scaled by how often it showed up recently.

        Meant to be added to the optimizer's per-unit cost so repeated items
        look progressively more expensive without ever being excluded outright.
        """
        counts = self.counts_in_window(window_days, as_of=as_of)
        return {name: count * penalty_per_occurrence for name, count in counts.items()}

    def rotation_cap_exclusions(
        self,
        window_days: int = 7,
        max_appearances: int = 3,
        as_of: Optional[date] = None,
    ) -> List[str]:
        """Item names that have hit the hard cap and must sit out this run entirely."""
        counts = self.counts_in_window(window_days, as_of=as_of)
        return [name for name, count in counts.items() if count >= max_appearances]
