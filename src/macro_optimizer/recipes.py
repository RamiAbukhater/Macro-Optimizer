"""The recipe layer sketched in IDEAS.md.

A recipe with a known ingredient list and a known yield reduces to the same
shape as any other row in the food database: sum each ingredient's price
and macros scaled to the quantity used, offset whatever's already in the
pantry, divide by servings. The result is one more FoodItem-shaped
candidate, run_optimizer doesn't need to change at all, it just gets more
rows to choose from.

``prep_minutes`` / ``cook_minutes`` carry straight through into the
FoodItem schema, so menu_planner.py can schedule a recipe against a day's
actual free time (see IDEAS.md's "Schedule aware planning" section).
Market/dining items default to 0 on both, they're already ready to eat.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from .pantry import Pantry
from .schema import FoodItem

DATA_DIR = Path(__file__).parent / "data"


def load_ingredient_db() -> Dict[str, dict]:
    with open(DATA_DIR / "ingredients.json", encoding="utf-8") as f:
        return json.load(f)


def load_recipes() -> List[dict]:
    with open(DATA_DIR / "recipes.json", encoding="utf-8") as f:
        return json.load(f)


def price_recipe(
    recipe: dict,
    ingredient_db: Dict[str, dict],
    pantry: Optional[Pantry] = None,
) -> dict:
    """Decompose one recipe into a per-serving FoodItem-shaped dict.

    Macros are computed off the full ingredient quantities, you eat the
    same nutrition whether or not you paid for it this time. Cost is
    computed off whatever the pantry doesn't already cover, so a recipe
    built entirely from owned ingredients prices out at $0/serving.
    """
    servings = recipe["servings"]
    if servings <= 0:
        raise ValueError(f"{recipe['name']!r}: servings must be positive")

    totals = {"cost": 0.0, "calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "fiber": 0.0, "sodium": 0.0}
    allergens = set()
    flags = {"vegetarian": True, "vegan": True, "gluten_free": True, "halal": True}
    contains_pork = False
    missing = []

    for line in recipe["ingredients"]:
        name = line["ingredient"]
        qty = line["quantity"]
        ing = ingredient_db.get(name)
        if ing is None:
            missing.append(name)
            continue

        buy_qty = pantry.offset(name, qty) if pantry is not None else qty

        totals["cost"] += ing["price_per_unit"] * buy_qty
        totals["calories"] += ing["calories_per_unit"] * qty
        totals["protein"] += ing["protein_per_unit"] * qty
        totals["carbs"] += ing["carbs_per_unit"] * qty
        totals["fat"] += ing["fat_per_unit"] * qty
        totals["fiber"] += ing["fiber_per_unit"] * qty
        totals["sodium"] += ing["sodium_per_unit"] * qty

        allergens.update(ing["allergens"])
        for flag in flags:
            flags[flag] = flags[flag] and ing[flag]
        contains_pork = contains_pork or ing["contains_pork"]

    if missing:
        raise KeyError(f"{recipe['name']!r}: unknown ingredients {missing}")

    per_serving = {
        "name": recipe["name"],
        "category": recipe.get("category", "Recipe"),
        "source": "Home Cooking",
        "location": "Home",
        "price": round(totals["cost"] / servings, 4),
        "serving_unit": f"1 of {servings} servings",
        "calories": round(totals["calories"] / servings, 1),
        "protein": round(totals["protein"] / servings, 2),
        "carbs": round(totals["carbs"] / servings, 2),
        "fat": round(totals["fat"] / servings, 2),
        "fiber": round(totals["fiber"] / servings, 2),
        "sodium": round(totals["sodium"] / servings, 1),
        "allergens": sorted(allergens),
        "vegetarian": flags["vegetarian"],
        "vegan": flags["vegan"],
        "gluten_free": flags["gluten_free"],
        "halal": flags["halal"],
        "contains_pork": contains_pork,
        "max_daily": recipe.get("max_daily", 2),
        "notes": recipe.get("notes", ""),
        "confidence": "recipe_estimate",
        "prep_minutes": recipe.get("prep_minutes", 0),
        "cook_minutes": recipe.get("cook_minutes", 0),
    }
    return per_serving


def price_all_recipes(
    recipes: Optional[List[dict]] = None,
    ingredient_db: Optional[Dict[str, dict]] = None,
    pantry: Optional[Pantry] = None,
) -> List[dict]:
    """Price every recipe in the database (or a given subset) for the optimizer."""
    recipes = recipes if recipes is not None else load_recipes()
    ingredient_db = ingredient_db if ingredient_db is not None else load_ingredient_db()
    return [price_recipe(r, ingredient_db, pantry=pantry) for r in recipes]


def recipe_food_items(
    recipes: Optional[List[dict]] = None,
    ingredient_db: Optional[Dict[str, dict]] = None,
    pantry: Optional[Pantry] = None,
) -> List[FoodItem]:
    """Same as price_all_recipes, but as validated FoodItem instances ready for load_food_db()."""
    priced = price_all_recipes(recipes, ingredient_db, pantry)
    return [FoodItem.from_dict(row) for row in priced]
