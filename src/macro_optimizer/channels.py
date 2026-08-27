"""Fulfillment channel adapters, per AUTOMATION.md's architecture sketch.

AUTOMATION.md's core idea is that a meal slot shouldn't be locked to one
fulfillment path. The system should be free to cook from what's on hand,
use a prepared meal that was already scheduled, or place a same-day
restaurant order, whichever is cheapest and easiest that day, and each
channel needs an adapter that turns whatever it offers into the same shape
the optimizer already uses, plus one field nothing else here needs yet:
lead time.

Only "Cook at Home" is a real, live adapter, it's exactly the market/
dining/recipe database already built. "Prepared Meal Delivery" and
"Restaurant Order" are illustrative catalogs (see their data files'
``confidence: "illustrative_example"`` and per-item notes), standing in
for a real Trifecta-style or DoorDash-style integration that needs actual
vendor API access this project doesn't have. They exist so the decision
engine in orchestrator.py has real cost/macro/lead-time tradeoffs to
choose between, not to claim any real vendor's menu or pricing.

AUTOMATION.md explicitly folds "grocery cart" into cook-at-home: reusing
the shopping list export sketched in IDEAS.md still requires cooking
afterward, so it's a fulfillment detail on top of this channel, not a
separate cost/macro outcome, and doesn't get its own adapter here.
"""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from .database import load_dining_items, load_market_items
from .pantry import Pantry
from .recipes import recipe_food_items
from .schema import FoodItem

DATA_DIR = Path(__file__).parent / "data"


class ChannelAdapter(ABC):
    """Common interface every fulfillment channel implements.

    ``lead_time_days`` is how many days before the meal a decision has to
    be locked in. 0 means it can be decided the same day; orchestrator.py
    uses this to filter which channels are still viable for a given day.
    """

    name: str
    lead_time_days: int

    @abstractmethod
    def candidates(self) -> List[FoodItem]:
        """Priced, macro'd options this channel can currently offer."""
        raise NotImplementedError


class CookAtHomeAdapter(ChannelAdapter):
    """The channel this whole project already builds: market + dining + recipes.

    Cheapest by far, costs real prep time (reflected in each candidate's
    prep_minutes/cook_minutes), and can be decided the same day.
    """

    name = "Cook at Home"
    lead_time_days = 0

    def __init__(self, pantry: Optional[Pantry] = None):
        self.pantry = pantry

    def candidates(self) -> List[FoodItem]:
        items = [FoodItem.from_dict(d) for d in load_market_items()]
        items += [FoodItem.from_dict(d) for d in load_dining_items()]
        items += recipe_food_items(pantry=self.pantry)
        return items


class PreparedMealAdapter(ChannelAdapter):
    """Illustrative stand-in for a Trifecta-style prepared meal service.

    Fastest path to actual zero effort, fixed price, known macros, no prep
    time, but per AUTOMATION.md has to be selected days ahead of delivery,
    not decided the morning of.
    """

    name = "Prepared Meal Delivery"
    lead_time_days = 3

    def candidates(self) -> List[FoodItem]:
        return _load_food_items(DATA_DIR / "prepared_meals.json")


class RestaurantOrderAdapter(ChannelAdapter):
    """Illustrative stand-in for a DoorDash-style agent-placed restaurant order.

    The same-day option for whenever nothing else got planned in time.
    Most expensive per meal, but per AUTOMATION.md the only channel with
    effectively no lead time.
    """

    name = "Restaurant Order"
    lead_time_days = 0

    def candidates(self) -> List[FoodItem]:
        return _load_food_items(DATA_DIR / "restaurant_orders.json")


def _load_food_items(path: Path) -> List[FoodItem]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [FoodItem.from_dict(d) for d in raw]
