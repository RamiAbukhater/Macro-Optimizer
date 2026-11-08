"""Rough shelf life, for "use by" hints and for the planner to finish what spoils first.

Deliberately coarse: a small editable table (data/shelf_life.json) keyed by
product kind, ingredient id, a few name keywords and store category. A lot
bought today gets ``use_by = purchased_on + days``. Nothing here claims a
real expiry date; it's a nudge, and the user can overwrite any date on the
Fridge page.
"""

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA_PATH = Path(__file__).parent / "data" / "shelf_life.json"


@lru_cache(maxsize=1)
def load_table() -> dict:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def shelf_life_days(kind: str = "", category: str = "", ingredient: str = "", name: str = "",
                    table: Optional[dict] = None) -> int:
    """Days a fresh purchase lasts, by the first rule in the table that matches."""
    table = table or load_table()
    if kind and kind in table.get("by_kind", {}):
        return int(table["by_kind"][kind])
    if ingredient and ingredient in table.get("by_ingredient", {}):
        return int(table["by_ingredient"][ingredient])
    text = f"{kind} {ingredient} {name}".lower().replace("_", " ")
    for word, days in table.get("keywords", {}).items():
        if word in text:
            return int(days)
    if category and category in table.get("by_category", {}):
        return int(table["by_category"][category])
    return int(table.get("default", 30))


def use_by(purchased_on: date, days: int) -> str:
    return (purchased_on + timedelta(days=days)).isoformat()
