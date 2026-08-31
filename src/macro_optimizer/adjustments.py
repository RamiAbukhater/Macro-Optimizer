"""Corrective feedback for a single day's plan, per NLU_ADJUSTMENTS.md's Capability 1.

The pattern this module formalizes already exists ad hoc in webapp/app.py's
``/api/plan/replace-item`` endpoint: pin everything the user is keeping,
exclude what they rejected from the candidate pool, and re-solve just that
one day with ``run_optimizer``. ``exclude_and_resolve`` is that re-solve,
factored out so it can be driven by more than a single named item, a whole
tag's worth of matches ("no more beef this week"), resolved by
``resolve_tag_to_item_names`` against food_tags.json.

Tag resolution is deliberately dumb: case-insensitive substring matching
against each row's name/category/notes, plus its curated allergens list.
That's enough to catch "chicken" across "Grilled Chicken Breast" and
"Chicken Caesar Wrap" without needing any real NLU, and it fails safe, an
unmatched tag just excludes nothing rather than guessing. The allergens
check exists because name/category/notes alone miss real cases, a dairy
protein shake's category is "Protein Shake" and its notes may never say
"milk," its curated ``allergens: ["milk"]`` is the only place that's true.
"""

import json
from typing import List, Optional, Tuple

import pandas as pd

from .database import DATA_DIR
from .history import PickHistory
from .menu_planner import DayPlan
from .optimizer import run_optimizer

TAGS_PATH_DEFAULT = DATA_DIR / "food_tags.json"


def exclude_and_resolve(
    food_df: pd.DataFrame,
    day: DayPlan,
    matched_names: List[str],
    goals: dict,
    budget_cap: float,
    filters: Optional[dict] = None,
    history: Optional[PickHistory] = None,
    **optimizer_kwargs,
) -> Tuple[pd.DataFrame, dict]:
    """Re-solve ``day`` with ``matched_names`` removed from the candidate pool.

    Same call shape as menu_planner.plan_week()'s per-day run_optimizer
    call, day.free_minutes and day.pinned_items feed the time budget and
    pins the same way, everything else (recency window, rotation cap,
    preference bonus, verbose) passes through ``optimizer_kwargs`` so this
    stays a thin re-solve rather than a second copy of plan_week's defaults.

    Returns the same ``(plan_df, summary)`` tuple run_optimizer does. A day
    with no matches on it is a no-op in effect, nothing is filtered out and
    the day resolves to the same plan it already had.
    """
    filtered_df = food_df[~food_df["name"].isin(matched_names)].copy()

    return run_optimizer(
        filtered_df,
        goals,
        budget_cap,
        filters=filters,
        history=history,
        time_budget_minutes=day.free_minutes,
        pinned_items=day.pinned_items,
        **optimizer_kwargs,
    )


def resolve_tag_to_item_names(food_df: pd.DataFrame, tag: str, tags_path=None) -> List[str]:
    """Expand a tag ("beef", "dairy", ...) into the food_df row names it matches.

    Loads food_tags.json's {tag: [synonyms]} map and does a case-insensitive
    substring match of each synonym against name/category/notes, plus each
    row's ``allergens`` list (schema.py carries this on every FoodItem).
    The allergens check matters on its own, not just as a backstop: a
    protein shake or bar can be genuinely dairy (curated with
    ``allergens: ["milk"]``) without the word "milk", "dairy", or anything
    else dairy-sounding appearing anywhere in its name, category, or notes,
    see NLU_ADJUSTMENTS.md's "Known limitation" entry on Core Power Elite
    surviving a "no dairy today" request for exactly this reason before this
    check existed. An unknown tag or a tag with no hits in ``food_df``
    returns an empty list rather than raising, callers treat "nothing
    matched" as a valid outcome.
    """
    tags_path = tags_path or TAGS_PATH_DEFAULT
    with open(tags_path, encoding="utf-8") as f:
        tags = json.load(f)

    synonyms = tags.get(tag.strip().lower(), [])
    if not synonyms:
        return []

    def _allergens_hit(values, synonym: str) -> bool:
        if not isinstance(values, (list, tuple)):
            return False
        return any(synonym in str(v).lower() for v in values)

    mask = pd.Series(False, index=food_df.index)
    for synonym in synonyms:
        synonym = synonym.lower()
        for col in ("name", "category", "notes"):
            mask |= food_df[col].str.lower().str.contains(synonym, na=False, regex=False)
        if "allergens" in food_df.columns:
            mask |= food_df["allergens"].apply(_allergens_hit, synonym=synonym)

    return food_df.loc[mask, "name"].tolist()
