"""What's in the fridge, carried day to day while a week is planned.

The optimizer solves one day at a time. To make a week finish what it buys,
this keeps a simulated copy of the fridge while plan_week_multichannel walks
the days:

- before a day: lots past their use-by are dropped, and every owned store
  product becomes a ``stock_credit`` entry (owned servings cost ~$0, see
  optimizer._stock_terms), with an urgency bonus that grows as its use-by
  gets closer. Recipes that use a soon-to-spoil ingredient get the same
  bonus. Recipes already reprice off this simulated pantry on their own,
  because CookAtHomeAdapter prices them against whatever pantry it holds.
- after a day: what it ate comes out of stock (soonest use-by first), and
  anything it had to buy goes in as a whole package, so the leftovers are
  owned on the following days. Monday's one bagel means Tuesday sees five
  free bagels; that's what makes the plan finish the bag.

The real fridge is never touched; this works on a copy.
"""

from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .pantry import Pantry
from .shelf_life import shelf_life_days, use_by
from .shopping_list import HOME_COOKING_SOURCE, packages_needed, product_stock_key

# Extra $/serving for eating owned food that's about to spoil: the full
# amount on its last day, fading to 0 at URGENCY_WINDOW_DAYS out.
URGENCY_MAX = 4.0
URGENCY_WINDOW_DAYS = 4
# Opening a new package costs this fraction of its price in the objective
# (see optimizer._stock_terms), on top of the servings eaten from it.
PACKAGE_FEE = 0.5


def urgency(use_by_iso: Optional[str], on: date) -> float:
    if not use_by_iso:
        return 0.0
    days_left = (date.fromisoformat(use_by_iso) - on).days
    if days_left >= URGENCY_WINDOW_DAYS:
        return 0.0
    return round(URGENCY_MAX * (URGENCY_WINDOW_DAYS - max(days_left, 0)) / URGENCY_WINDOW_DAYS, 3)


class WeekStock:
    def __init__(self, pantry: Optional[Pantry] = None, recipes: Optional[List[dict]] = None,
                 ingredient_packages: Optional[Dict[str, dict]] = None, package_fee: float = PACKAGE_FEE):
        self.package_fee = package_fee
        self.pantry = Pantry(entries=pantry.to_dict() if pantry is not None else {})
        self.recipes = {r["name"]: r for r in (recipes or [])}
        self.ingredient_packages = ingredient_packages or {}

    def _soonest_use_by(self, key: str) -> Optional[str]:
        dates = [l["use_by"] for l in self.pantry.lots(key) if l.get("use_by")]
        return min(dates) if dates else None

    def drop_expired(self, on: date) -> None:
        for key, entry in self.pantry.list_items().items():
            for lot in entry.lots:
                if lot.get("use_by") and lot["use_by"] < on.isoformat():
                    self.pantry.remove_lot(key, lot["id"])

    def prepare_day(self, on: date, candidates) -> Tuple[Dict[str, dict], Dict[str, float]]:
        """(stock_credit for run_optimizer, extra preference bonus for recipes) for one day."""
        self.drop_expired(on)
        credit, bonus = {}, {}
        for item in candidates:
            if item.upc:
                key = product_stock_key(item.upc)
                owned = self.pantry.have(key)
                if owned > 0:
                    credit[item.name] = {"servings": owned, "urgency": urgency(self._soonest_use_by(key), on)}
            recipe = self.recipes.get(item.name)
            if recipe is not None and item.source == HOME_COOKING_SOURCE:
                best = max((urgency(self._soonest_use_by(line["ingredient"]), on)
                            for line in recipe["ingredients"] if self.pantry.have(line["ingredient"]) > 0),
                           default=0.0)
                if best > 0:
                    bonus[item.name] = best
        return credit, bonus

    def record_day(self, on: date, plan_df: pd.DataFrame, candidates) -> pd.DataFrame:
        """Take the day's food out of stock and put in what it implies buying.

        Returns plan_df with a ``from_stock`` column: servings of each row
        that came out of the fridge rather than a new purchase.
        """
        if plan_df.empty:
            return plan_df
        by_name = {c.name: c for c in candidates}
        from_stock = []
        for _, row in plan_df.iterrows():
            servings = float(row["servings"])
            item = by_name.get(row["name"])
            recipe = self.recipes.get(row["name"])
            if item is not None and item.upc:
                key = product_stock_key(item.upc)
                used = min(servings, self.pantry.have(key))
                self.pantry.consume(key, used)
                short = servings - used
                if short > 1e-9 and item.servings_per_package > 0:
                    n = packages_needed(short, item.servings_per_package)
                    days = shelf_life_days(kind=item.kind, category=item.category, name=item.name)
                    self.pantry.add_lot(key, f"plan:{on.isoformat()}:{item.upc}",
                                        n * item.servings_per_package - short,
                                        purchased_on=on.isoformat(), use_by=use_by(on, days))
                from_stock.append(round(used, 2))
            elif recipe is not None and row["source"] == HOME_COOKING_SOURCE:
                scale = servings / recipe["servings"]
                for line in recipe["ingredients"]:
                    ing, qty = line["ingredient"], line["quantity"] * scale
                    used = min(qty, self.pantry.have(ing))
                    self.pantry.consume(ing, used)
                    pkg = self.ingredient_packages.get(ing)
                    short = qty - used
                    if short > 1e-9 and pkg and pkg.get("package_quantity"):
                        n = packages_needed(short, pkg["package_quantity"])
                        self.pantry.add_lot(ing, f"plan:{on.isoformat()}:{ing}",
                                            n * pkg["package_quantity"] - short, purchased_on=on.isoformat(),
                                            use_by=use_by(on, shelf_life_days(ingredient=ing)))
                from_stock.append(0.0)
            else:
                from_stock.append(0.0)
        plan_df = plan_df.copy()
        plan_df["from_stock"] = from_stock
        return plan_df
