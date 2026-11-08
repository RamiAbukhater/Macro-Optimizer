"""Loads the curated food database and builds the combined candidate DataFrame."""

from pathlib import Path
from typing import Iterable, List, Optional, Union

import pandas as pd

from .schema import FoodItem

DATA_DIR = Path(__file__).parent / "data"


def load_market_items(location_id: Optional[str] = None, use_promo: Optional[bool] = None) -> List[dict]:
    """The ready-to-eat grocery catalog: real products from one supermarket,
    priced and labeled live by Kroger (see market.py). The product list
    comes from grocery.db, kept current by ``py -m macro_optimizer.refresh``.
    Empty when Kroger can't be reached; ``market.status()`` says why."""
    from .market import market_items

    return market_items(location_id=location_id, use_promo=use_promo)


def load_food_db(
    extra_items: Optional[Iterable[Union[dict, FoodItem]]] = None,
    include_market: bool = True,
) -> pd.DataFrame:
    """Build the combined candidate food DataFrame.

    ``extra_items`` lets callers fold in rows from another source, priced
    recipes (see recipes.py), a live scrape, a future chain-restaurant
    adapter, using the same shape everything else here uses.
    """
    items: List[dict] = []
    if include_market:
        items.extend(load_market_items())
    if extra_items:
        for item in extra_items:
            items.append(item.to_dict() if isinstance(item, FoodItem) else item)

    if not items:
        raise ValueError("no food items to build a database from")

    # Validate every row conforms to the shared schema before it can reach
    # the optimizer.
    validated = [FoodItem.from_dict(item).to_dict() for item in items]

    df = pd.DataFrame(validated)
    df = df.dropna(subset=["price", "calories", "protein", "carbs", "fat"])
    # >= 0, not > 0: a pantry-offset recipe (see recipes.py) can legitimately
    # cost $0 once everything it needs is already owned, and that's exactly
    # the row the optimizer should prefer, not one to silently drop.
    df = df[df["price"] >= 0].reset_index(drop=True)

    df["protein_per_dollar"] = df["protein"] / df["price"]
    df["calories_per_dollar"] = df["calories"] / df["price"]
    df["fat_per_dollar"] = df["fat"] / df["price"]
    df["carbs_per_dollar"] = df["carbs"] / df["price"]
    df["cal_per_gram_protein"] = df.apply(
        lambda r: r["calories"] / r["protein"] if r["protein"] > 0 else float("inf"),
        axis=1,
    )
    df["total_time_minutes"] = df["prep_minutes"] + df["cook_minutes"]
    return df


def apply_dietary_filters(food_df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """Return the subset of food_df that passes all active dietary filters."""
    mask = pd.Series(True, index=food_df.index)
    if filters.get("vegetarian"):
        mask &= food_df["vegetarian"].astype(bool)
    if filters.get("vegan"):
        mask &= food_df["vegan"].astype(bool)
    if filters.get("no_pork"):
        mask &= ~food_df["contains_pork"].astype(bool)
    if filters.get("halal"):
        mask &= food_df["halal"].astype(bool)
    if filters.get("gluten_free"):
        mask &= food_df["gluten_free"].astype(bool)
    return food_df[mask].copy()
