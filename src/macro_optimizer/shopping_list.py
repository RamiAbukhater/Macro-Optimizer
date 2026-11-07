"""Turns a solved plan's item list into an actual shopping list.

A plan_df row is a finished-meal-shaped thing (name, servings, price), not a
shopping list, that's exactly right for the optimizer and for showing the
user what they're eating, but a shopping list needs raw ingredient
quantities. Only "Home Cooking" rows (see recipes.py) decompose that way;
a recipe's ingredient lines scale by servings-chosen / recipe-servings and
aggregate across every day they appear. Any other row inside the Cook at
Home channel, whatever source string the grocery catalog uses, is already
the shoppable unit itself (a specific packaged item bought as-is), so it's
grouped as a packaged item rather than decomposed. The Prepared Meal
Delivery / Restaurant Order channels are delivered directly, there's
nothing to shop for.

Pantry offset (see pantry.py) is applied per aggregated ingredient, not per
recipe occurrence, so owning 500g of rice covers it once across the whole
week's worth of rice-using recipes, not once per recipe.
"""

from typing import Dict, List, Optional

from .pantry import Pantry
from .recipes import load_ingredient_db, load_recipes

HOME_COOKING_SOURCE = "Home Cooking"
# Matches app.py's classify_channel(): any source starting with one of these
# is a finished, delivered-or-ordered meal, never something to shop for.
NO_SHOPPING_SOURCE_PREFIXES = ("Prepared Meal Delivery", "Restaurant Order")


def build_shopping_list(
    items: List[dict],
    pantry: Optional[Pantry] = None,
    recipes: Optional[List[dict]] = None,
    ingredient_db: Optional[Dict[str, dict]] = None,
) -> dict:
    """Aggregate a flat list of plan items (across any number of days) into a shopping list.

    Each item in ``items`` needs at least ``name``, ``source``, and
    ``servings``; ``price`` is used for Market-sourced packaged items.
    Returns ``{ingredients, packaged_items, no_shopping_needed, total_estimated_cost}``.
    """
    recipes = recipes if recipes is not None else load_recipes()
    ingredient_db = ingredient_db if ingredient_db is not None else load_ingredient_db()
    recipes_by_name = {r["name"]: r for r in recipes}

    ingredient_totals: Dict[str, float] = {}
    packaged_totals: Dict[str, dict] = {}
    no_shopping_needed: List[dict] = []

    for item in items:
        name = item["name"]
        source = item.get("source", "")
        servings = item.get("servings", 1)

        recipe = recipes_by_name.get(name)
        if source == HOME_COOKING_SOURCE and recipe is not None:
            scale = servings / recipe["servings"]
            for line in recipe["ingredients"]:
                ingredient_totals[line["ingredient"]] = (
                    ingredient_totals.get(line["ingredient"], 0.0) + line["quantity"] * scale
                )
        elif source.startswith(NO_SHOPPING_SOURCE_PREFIXES):
            no_shopping_needed.append({"name": name, "source": source, "servings": servings})
        else:
            bucket = packaged_totals.setdefault(
                name,
                {
                    "name": name,
                    "quantity": 0,
                    "unit": item.get("serving_unit", "1 serving"),
                    "price_each": item.get("price", 0.0),
                },
            )
            bucket["quantity"] += servings

    total_cost = 0.0

    ingredients = []
    for ing_name, qty in sorted(ingredient_totals.items()):
        ing = ingredient_db.get(ing_name)
        if ing is None:
            continue
        have = pantry.have(ing_name) if pantry is not None else 0.0
        to_buy = max(0.0, qty - have)
        price = round(to_buy * ing["price_per_unit"], 2)
        total_cost += price
        ingredients.append(
            {
                "ingredient": ing_name,
                "display_name": ing_name.replace("_", " "),
                "unit": ing["unit"],
                "needed_quantity": round(qty, 1),
                "have_quantity": round(have, 1),
                "to_buy_quantity": round(to_buy, 1),
                "estimated_price": price,
            }
        )

    packaged_items = []
    for bucket in packaged_totals.values():
        price = round(bucket["quantity"] * bucket["price_each"], 2)
        total_cost += price
        packaged_items.append(
            {
                "name": bucket["name"],
                "quantity": bucket["quantity"],
                "unit": bucket["unit"],
                "estimated_price": price,
            }
        )

    return {
        "ingredients": ingredients,
        "packaged_items": packaged_items,
        "no_shopping_needed": no_shopping_needed,
        "total_estimated_cost": round(total_cost, 2),
    }
