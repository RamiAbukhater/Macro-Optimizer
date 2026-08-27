"""A live adapter for the "grocery and packaged goods" tier of IDEAS.md's data layer.

IDEAS.md ranks three tiers of how solvable arbitrary food data is. Chain
restaurants and grocery/packaged goods are "mostly covered already" by
existing structured databases; independent restaurants and corner stores
are the genuinely open problem. This module builds the grocery tier for
real: Open Food Facts is a free, open, crowdsourced nutrition database
with no API key or account required.

What it deliberately does not solve: Open Food Facts has macros, not
prices. IDEAS.md is explicit that pricing packaged goods is a harder
version of the grocery data problem, not an easier one, real prices need
to come from the retailer, not a nutrition site. Every FoodItem this
module produces needs its price supplied by the caller; treat that price
as something you looked up yourself, not something this module verified.

Data quality note: Open Food Facts is crowdsourced, coverage and accuracy
vary a lot by product and region. Every row produced here carries
``confidence="external_api"`` so callers and the optimizer can tell it
apart from the hand-curated database, same distinction IDEAS.md calls for
in the normalization layer.
"""

from typing import List, Optional

import requests

from .schema import FoodItem

BASE_URL = "https://world.openfoodfacts.org"
USER_AGENT = "MacroOptimizer/0.2 (github.com/RamiAbukhater/Macro-Optimizer)"
REQUEST_TIMEOUT = 10

REQUIRED_NUTRIMENT_FIELDS = ["energy-kcal_100g", "proteins_100g", "carbohydrates_100g", "fat_100g"]

SEARCH_FIELDS = (
    "code,product_name,brands,nutriments,serving_size,"
    "ingredients_analysis_tags,allergens_tags,labels_tags"
)


class OpenFoodFactsError(Exception):
    """A product couldn't be found, or couldn't be normalized into a FoodItem."""


def search_products(query: str, page_size: int = 10) -> List[dict]:
    """Raw Open Food Facts search results for a free-text query. No API key required.

    This hits Open Food Facts' legacy Perl search endpoint, which is being
    phased out in favor of a newer search-a-licious API and, in practice,
    returns occasional 503s under load. ``lookup_by_barcode`` hits the
    current v2 product API instead and has been reliable in testing;
    prefer it when the caller already knows a barcode.
    """
    resp = requests.get(
        f"{BASE_URL}/cgi/search.pl",
        params={"search_terms": query, "json": 1, "page_size": page_size, "fields": SEARCH_FIELDS},
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("products", [])


def lookup_by_barcode(barcode: str) -> Optional[dict]:
    """Raw product dict for one barcode, or None if Open Food Facts has no match."""
    resp = requests.get(
        f"{BASE_URL}/api/v2/product/{barcode}.json",
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 1:
        return None
    return data.get("product")


def _parse_dietary_flags(product: dict) -> dict:
    analysis = set(product.get("ingredients_analysis_tags") or [])
    allergens = set(product.get("allergens_tags") or [])
    labels = set(product.get("labels_tags") or [])

    vegan = "en:vegan" in analysis
    vegetarian = vegan or "en:vegetarian" in analysis
    gluten_free = ("en:no-gluten" in labels or "en:gluten-free" in labels) and "en:gluten" not in allergens
    halal = "en:halal" in labels
    # Open Food Facts doesn't reliably tag pork content specifically;
    # default to False (unknown) rather than guess from category text.
    contains_pork = False

    allergen_names = sorted(a.split(":", 1)[-1] for a in allergens)
    return dict(
        vegetarian=vegetarian, vegan=vegan, gluten_free=gluten_free,
        halal=halal, contains_pork=contains_pork, allergens=allergen_names,
    )


def to_food_item(
    product: dict,
    price: float,
    category: str = "Grocery",
    location: str = "",
    max_daily: int = 3,
) -> FoodItem:
    """Normalize one Open Food Facts product into a FoodItem, per 100g.

    Per-100g rather than per-labeled-serving on purpose: serving_size on
    Open Food Facts is free text ("1 cup (240 ml)", "2 barres (50g)", often
    missing entirely), reliably parsing it is its own small NLP problem,
    the same kind IDEAS.md flags elsewhere for ingredient/recipe text.
    Per-100g is always present and unambiguous; ``price`` should be what
    100g of the product actually costs.
    """
    nutriments = product.get("nutriments") or {}
    missing = [f for f in REQUIRED_NUTRIMENT_FIELDS if nutriments.get(f) is None]
    if missing:
        label = product.get("product_name") or product.get("code") or "product"
        raise OpenFoodFactsError(f"{label!r} is missing required nutriment fields from Open Food Facts: {missing}")

    flags = _parse_dietary_flags(product)
    name = product.get("product_name") or product.get("code") or "Unknown product"
    brand = product.get("brands")
    if brand:
        name = f"{name} ({brand.split(',')[0].strip()})"

    return FoodItem(
        name=name,
        category=category,
        source="Open Food Facts",
        location=location,
        price=price,
        serving_unit="100g",
        calories=round(nutriments["energy-kcal_100g"], 1),
        protein=round(nutriments["proteins_100g"], 2),
        carbs=round(nutriments["carbohydrates_100g"], 2),
        fat=round(nutriments["fat_100g"], 2),
        fiber=round(nutriments.get("fiber_100g") or 0.0, 2),
        # Open Food Facts reports sodium in grams per 100g; this schema uses mg.
        sodium=round((nutriments.get("sodium_100g") or 0.0) * 1000, 1),
        max_daily=max_daily,
        confidence="external_api",
        notes=f"Live Open Food Facts data. Price (${price:.2f} per 100g) supplied by caller, not verified by this module.",
        **flags,
    )


def lookup_and_price(
    query_or_barcode: str, price: float, by_barcode: bool = False, search_pool: int = 10, **kwargs
) -> FoodItem:
    """One-shot: look up a product and normalize it with a caller-supplied price.

    Search results are crowdsourced and uneven, some entries have no
    ``product_name`` at all, just a barcode. Rather than blindly take the
    top hit, this scans up to ``search_pool`` results for the first one
    that both has a real name and has the macro fields to_food_item needs,
    so a well-known query doesn't come back labeled as a 13-digit number.

    Raises OpenFoodFactsError if nothing in the pool clears both bars.
    """
    if by_barcode:
        product = lookup_by_barcode(query_or_barcode)
        if product is None:
            raise OpenFoodFactsError(f"no Open Food Facts product found for barcode {query_or_barcode!r}")
        return to_food_item(product, price=price, **kwargs)

    results = search_products(query_or_barcode, page_size=search_pool)
    for product in results:
        if not product.get("product_name"):
            continue
        try:
            return to_food_item(product, price=price, **kwargs)
        except OpenFoodFactsError:
            continue  # named but missing macro data, try the next candidate

    raise OpenFoodFactsError(
        f"no usable Open Food Facts product found for query {query_or_barcode!r} "
        f"(checked {len(results)} result(s); all were unnamed or missing macro data)"
    )
