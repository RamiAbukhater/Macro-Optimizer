"""Macro Optimizer: cost-minimizing meal planning against macro and budget targets."""

from .schema import FoodItem, FIELDS
from .database import load_food_db, apply_dietary_filters
from .optimizer import run_optimizer
from .profile import Profile, compute_goals, bmr, tdee
from .recipes import load_recipes, load_ingredient_db, price_recipe, recipe_food_items
from .pantry import Pantry
from .history import PickHistory
from .menu_planner import DayPlan, plan_week, week_summary
from .channels import ChannelAdapter, CookAtHomeAdapter, PreparedMealAdapter, RestaurantOrderAdapter
from .orchestrator import plan_week_multichannel, confirmation_digest
from .openfoodfacts import (
    OpenFoodFactsError,
    search_products,
    lookup_by_barcode,
    to_food_item,
    lookup_and_price,
)
from .usda_fdc import (
    UsdaFdcError,
    search_foods as usda_search_foods,
    to_food_item as usda_to_food_item,
    lookup_and_price as usda_lookup_and_price,
)

__all__ = [
    "FoodItem",
    "FIELDS",
    "load_food_db",
    "apply_dietary_filters",
    "run_optimizer",
    "Profile",
    "compute_goals",
    "bmr",
    "tdee",
    "load_recipes",
    "load_ingredient_db",
    "price_recipe",
    "recipe_food_items",
    "Pantry",
    "PickHistory",
    "DayPlan",
    "plan_week",
    "week_summary",
    "ChannelAdapter",
    "CookAtHomeAdapter",
    "PreparedMealAdapter",
    "RestaurantOrderAdapter",
    "plan_week_multichannel",
    "confirmation_digest",
    "OpenFoodFactsError",
    "search_products",
    "lookup_by_barcode",
    "to_food_item",
    "lookup_and_price",
    "UsdaFdcError",
    "usda_search_foods",
    "usda_to_food_item",
    "usda_lookup_and_price",
]
