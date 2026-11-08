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

Whole packages: in a store you buy the box, not one bagel. Every line is
rounded UP to whole packages wherever the package is known (a store
product's servings_per_package, or a pinned ingredient's package size from
market.ingredient_packages), and says what's left over after the week.
``estimated_price`` / ``total_estimated_cost`` are what you pay at the
register; ``eaten_cost`` / ``total_eaten_cost`` are what the food you
actually eat this week is worth. Lines without a known package (loose
produce, unpinned ingredients) stay per-quantity estimates.
"""

import math
from typing import Dict, List, Optional

from .pantry import Pantry
from .recipes import load_ingredient_db, load_recipes

HOME_COOKING_SOURCE = "Home Cooking"
# Matches app.py's classify_channel(): any source starting with one of these
# is a finished, delivered-or-ordered meal, never something to shop for.
NO_SHOPPING_SOURCE_PREFIXES = ("Prepared Meal Delivery", "Restaurant Order")


def product_stock_key(upc: str) -> str:
    """The pantry key a store product's servings are tracked under."""
    return f"upc:{upc}"


def packages_needed(quantity: float, per_package: float) -> int:
    """Whole packages that cover ``quantity``, rounded up (0 when nothing is needed)."""
    if quantity <= 1e-9:
        return 0
    return math.ceil(round(quantity / per_package, 6))


def line_key(kind: str, ident: str) -> str:
    return f"{kind}:{ident}"


def build_shopping_list(
    items: List[dict],
    pantry: Optional[Pantry] = None,
    recipes: Optional[List[dict]] = None,
    ingredient_db: Optional[Dict[str, dict]] = None,
    ingredient_packages: Optional[Dict[str, dict]] = None,
    products: Optional[Dict[str, dict]] = None,
    exclude_lot_prefix: Optional[str] = None,
    bought: Optional[Dict[str, dict]] = None,
) -> dict:
    """Aggregate a flat list of plan items (across any number of days) into a shopping list.

    Each item in ``items`` needs at least ``name``, ``source``, and
    ``servings``; ``price`` is the per-serving price of a packaged item.
    Package facts for a packaged item come from the item itself
    (``upc``/``servings_per_package``/``package_price``/``package_size``) or
    from ``products``, a live {upc or name: FoodItem dict} lookup.
    ``ingredient_packages`` is market.ingredient_packages()'s
    {ingredient: {upc, title, size, package_quantity, package_price}}.

    ``exclude_lot_prefix`` leaves this week's own purchases out of what
    counts as already owned, so checking a line off doesn't shrink it.
    ``bought`` is this week's {line key: purchase} record; each line's
    ``bought`` flag comes from it.
    """
    recipes = recipes if recipes is not None else load_recipes()
    ingredient_db = ingredient_db if ingredient_db is not None else load_ingredient_db()
    ingredient_packages = ingredient_packages or {}
    products = products or {}
    bought = bought or {}
    recipes_by_name = {r["name"]: r for r in recipes}

    def have(key: str) -> float:
        return pantry.have(key, exclude_prefix=exclude_lot_prefix) if pantry is not None else 0.0

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
            live = products.get(item.get("upc") or "") or products.get(name) or {}
            facts = {**live, **{k: v for k, v in item.items() if v not in (None, "", 0)}}
            bucket = packaged_totals.setdefault(
                name,
                {
                    "name": name,
                    "display_name": facts.get("display_name") or name,
                    "quantity": 0,
                    "unit": facts.get("serving_unit", "1 serving"),
                    "price_each": facts.get("price", 0.0),
                    "upc": facts.get("upc") or "",
                    "servings_per_package": float(facts.get("servings_per_package") or 0),
                    "package_price": float(facts.get("package_price") or 0),
                    "package_size": facts.get("package_size") or "",
                },
            )
            bucket["quantity"] += servings

    register_total = 0.0
    eaten_total = 0.0

    ingredients = []
    for ing_name, qty in sorted(ingredient_totals.items()):
        ing = ingredient_db.get(ing_name)
        if ing is None:
            continue
        owned = have(ing_name)
        to_buy = max(0.0, qty - owned)
        eaten = round(qty * ing["price_per_unit"], 2)
        key = line_key("ingredient", ing_name)
        line = {
            "key": key,
            "ingredient": ing_name,
            "display_name": ing_name.replace("_", " "),
            "unit": ing["unit"],
            "needed_quantity": round(qty, 1),
            "have_quantity": round(owned, 1),
            "to_buy_quantity": round(to_buy, 1),
            "eaten_cost": eaten,
            "packages": None,
            "package_quantity": None,
            "package_price": None,
            "package_size": "",
            "title": "",
            "upc": "",
            "leftover_quantity": 0.0,
            "bought": key in bought,
        }
        pkg = ingredient_packages.get(ing_name)
        if pkg and pkg.get("package_quantity"):
            n = packages_needed(to_buy, pkg["package_quantity"])
            price = round(n * pkg["package_price"], 2)
            line.update({
                "packages": n,
                "package_quantity": pkg["package_quantity"],
                "package_price": pkg["package_price"],
                "package_size": pkg.get("size", ""),
                "title": pkg.get("title", ""),
                "upc": pkg.get("upc", ""),
                "leftover_quantity": round(n * pkg["package_quantity"] - to_buy, 1),
            })
        else:
            price = round(to_buy * ing["price_per_unit"], 2)
        line["estimated_price"] = price
        register_total += price
        eaten_total += eaten
        ingredients.append(line)

    packaged_items = []
    for bucket in packaged_totals.values():
        servings = bucket["quantity"]
        eaten = round(servings * bucket["price_each"], 2)
        key = line_key("product", bucket["upc"] or bucket["name"])
        owned = have(product_stock_key(bucket["upc"])) if bucket["upc"] else 0.0
        to_buy = max(0.0, servings - owned)
        line = {
            "key": key,
            "name": bucket["name"],
            "display_name": bucket["display_name"],
            "quantity": servings,
            "unit": bucket["unit"],
            "upc": bucket["upc"],
            "have_servings": round(owned, 2),
            "to_buy_servings": round(to_buy, 2),
            "eaten_cost": eaten,
            "packages": None,
            "servings_per_package": None,
            "package_price": None,
            "package_size": bucket["package_size"],
            "leftover_servings": 0.0,
            "bought": key in bought,
        }
        spp = bucket["servings_per_package"]
        if spp > 0 and bucket["package_price"] > 0:
            n = packages_needed(to_buy, spp)
            price = round(n * bucket["package_price"], 2)
            line.update({
                "packages": n,
                "servings_per_package": spp,
                "package_price": bucket["package_price"],
                "leftover_servings": round(n * spp - to_buy, 2),
            })
        else:
            price = round(to_buy * bucket["price_each"], 2)
        line["estimated_price"] = price
        register_total += price
        eaten_total += eaten
        packaged_items.append(line)

    return {
        "ingredients": ingredients,
        "packaged_items": packaged_items,
        "no_shopping_needed": no_shopping_needed,
        "total_estimated_cost": round(register_total, 2),
        "total_eaten_cost": round(eaten_total, 2),
    }


def purchase_quantity(line: dict) -> Optional[float]:
    """What checking a shopping line off adds to the fridge.

    The full package contents (servings for a product, ingredient units for
    an ingredient), or the to-buy amount when no package size is known.
    None when there's nothing to buy.
    """
    if line.get("packages"):
        per = line.get("servings_per_package") or line.get("package_quantity")
        return round(line["packages"] * per, 4)
    qty = line.get("to_buy_servings", line.get("to_buy_quantity"))
    return qty if qty else None
