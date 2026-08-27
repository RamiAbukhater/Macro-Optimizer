"""A second live adapter for IDEAS.md's "grocery and packaged goods" data-layer tier.

USDA FoodData Central is the other database IDEAS.md names alongside Open
Food Facts. Where Open Food Facts is strongest on branded packaged goods,
FoodData Central's Foundation and SR Legacy data types are lab-analyzed,
government-sourced macros for generic raw ingredients, chicken breast,
rolled oats, black beans, the exact kind of thing recipes.py's
hand-typed ``data/ingredients.json`` currently estimates by hand. This
module is that same normalization pattern (search live, screen out bad
candidates, map into FoodItem), aimed at a different, complementary slice
of the data.

Unlike Open Food Facts, FoodData Central is not fully anonymous: it needs
an ``api_key`` query parameter. Getting a real one is free and instant at
https://api.data.gov/signup, no business relationship required, which is
what separates it from Nutritionix/Edamam/Instacart/DoorDash elsewhere in
this project. Without a key supplied, this module falls back to the
public ``DEMO_KEY``, which works with no signup but is rate-limited hard
(documented at 30 requests/hour, 50/day, per IP, shared across everyone
using the default). Set the ``USDA_FDC_API_KEY`` environment variable, or
pass ``api_key=`` directly, once you have your own.

Same pricing gap as Open Food Facts, and for the same reason: FoodData
Central is a nutrition database, not a pricing one. Every FoodItem this
module produces needs its price supplied by the caller.

FoodData Central also doesn't carry the kind of structured
vegetarian/vegan/halal/allergen tags Open Food Facts does, so those flags
default to False (unknown) here rather than guessed from a food's
description text; pass them explicitly via lookup_and_price()/to_food_item()
kwargs when you know them.
"""

import os
from typing import List, Optional

import requests

from .schema import FoodItem

BASE_URL = "https://api.nal.usda.gov/fdc/v1"
API_KEY_ENV_VAR = "USDA_FDC_API_KEY"
DEMO_KEY = "DEMO_KEY"
REQUEST_TIMEOUT = 10

DEFAULT_DATA_TYPES = ["Foundation", "SR Legacy"]

# USDA FoodData Central nutrient IDs are stable identifiers, safer to match
# on than the human-readable name (which has near-duplicates, "Energy" in
# both kcal and kJ shows up in the same food, distinguished only by unit).
NUTRIENT_IDS = {
    "calories": 1008,  # Energy, KCAL
    "protein": 1003,
    "carbs": 1005,  # Carbohydrate, by difference
    "fat": 1004,  # Total lipid (fat)
    "fiber": 1079,  # Fiber, total dietary
    "sodium": 1093,  # Sodium, Na, MG
}
REQUIRED_MACROS = ["calories", "protein", "carbs", "fat"]


class UsdaFdcError(Exception):
    """A product couldn't be found, was missing macro data, or the API call itself failed."""


def _resolve_api_key(api_key: Optional[str]) -> str:
    return api_key or os.environ.get(API_KEY_ENV_VAR) or DEMO_KEY


def search_foods(
    query: str,
    page_size: int = 10,
    data_types: Optional[List[str]] = None,
    api_key: Optional[str] = None,
) -> List[dict]:
    """Raw FoodData Central search results for a free-text query.

    ``data_types`` defaults to Foundation + SR Legacy, generic lab-analyzed
    ingredients rather than branded products (Open Food Facts already
    covers that tier). Pass ``data_types=["Branded"]`` explicitly if you
    want commercial products instead.
    """
    resp = requests.get(
        f"{BASE_URL}/foods/search",
        params={
            "query": query,
            "pageSize": page_size,
            "dataType": data_types if data_types is not None else DEFAULT_DATA_TYPES,
            "api_key": _resolve_api_key(api_key),
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code in (403, 429):
        raise UsdaFdcError(
            f"USDA FoodData Central rejected the request (HTTP {resp.status_code}), likely a rate "
            f"limit on the shared DEMO_KEY. Get a free instant key at https://api.data.gov/signup "
            f"and pass it as api_key= or set the {API_KEY_ENV_VAR} environment variable."
        )
    resp.raise_for_status()
    return resp.json().get("foods", [])


def _extract_macros(food: dict) -> dict:
    by_id = {n.get("nutrientId"): n.get("value") for n in food.get("foodNutrients", [])}
    return {key: by_id.get(nutrient_id) for key, nutrient_id in NUTRIENT_IDS.items()}


def to_food_item(
    food: dict,
    price: float,
    category: str = "Grocery",
    location: str = "",
    max_daily: int = 3,
    **flag_overrides,
) -> FoodItem:
    """Normalize one FoodData Central search result into a FoodItem, per 100g.

    ``price`` has to be supplied by the caller, see the module docstring.
    Dietary flags aren't reliably available from this API and default to
    False; pass e.g. ``vegetarian=True`` through ``**flag_overrides`` when
    you know them from the ingredient itself.
    """
    macros = _extract_macros(food)
    missing = [m for m in REQUIRED_MACROS if macros.get(m) is None]
    if missing:
        label = food.get("description") or food.get("fdcId") or "food"
        raise UsdaFdcError(f"{label!r} is missing required nutrient fields from FoodData Central: {missing}")

    name = food.get("description") or f"FDC #{food.get('fdcId')}"
    brand = food.get("brandOwner") or food.get("brandName")
    if brand:
        name = f"{name} ({brand})"

    fields = dict(
        name=name,
        category=category,
        source=f"USDA FoodData Central ({food.get('dataType', 'unknown')})",
        location=location,
        price=price,
        serving_unit="100g",
        calories=round(macros["calories"], 1),
        protein=round(macros["protein"], 2),
        carbs=round(macros["carbs"], 2),
        fat=round(macros["fat"], 2),
        fiber=round(macros.get("fiber") or 0.0, 2),
        sodium=round(macros.get("sodium") or 0.0, 1),
        max_daily=max_daily,
        confidence="external_api",
        notes=f"Live USDA FoodData Central data (fdcId {food.get('fdcId')}). "
              f"Price (${price:.2f} per 100g) supplied by caller, not verified by this module.",
    )
    fields.update(flag_overrides)
    return FoodItem(**fields)


def lookup_and_price(
    query: str,
    price: float,
    data_types: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    search_pool: int = 10,
    **kwargs,
) -> FoodItem:
    """One-shot: search FoodData Central and normalize the best match with a caller-supplied price.

    Scans up to ``search_pool`` results for the first one with a real
    description and complete macro data, rather than trusting the top hit
    blindly, same reasoning as openfoodfacts.lookup_and_price.
    """
    results = search_foods(query, page_size=search_pool, data_types=data_types, api_key=api_key)
    for food in results:
        if not food.get("description"):
            continue
        try:
            return to_food_item(food, price=price, **kwargs)
        except UsdaFdcError:
            continue

    raise UsdaFdcError(
        f"no usable FoodData Central result found for query {query!r} "
        f"(checked {len(results)} result(s); all were unnamed or missing macro data)"
    )
