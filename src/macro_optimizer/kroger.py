"""A live adapter for the piece openfoodfacts.py and usda_fdc.py both leave open: price.

Those two modules are nutrition databases, every FoodItem they produce
needs a caller-supplied price. IDEAS.md's own answer to that gap is "go
straight to the retailer." Kroger is the one large US grocer with a free,
self-serve, documented public API that returns real shelf prices for a
specific store: regular price, promo price, package size, UPC, and stock
level. It covers every Kroger banner, Ralphs, Fred Meyer, King Soopers,
Fry's, QFC, Smith's, Harris Teeter and the rest.

Access needs a client id and secret from https://developer.kroger.com
(free, register an application, enable the Products API). Set
``KROGER_CLIENT_ID`` and ``KROGER_CLIENT_SECRET``, or pass them to
``KrogerClient`` directly. Reading prices only needs the app-level
client-credentials token with the ``product.compact`` scope, no shopper
login. The public tier is rate limited per day (documented at 10,000
product calls and 1,600 location calls), plenty for a weekly refresh of an
ingredient list, not for crawling a whole catalog.

Most packaged products also come back with their own nutrition label, so
``to_food_item`` can build a complete, priced FoodItem with no second
lookup. Loose produce and hot deli items usually have no label; for those,
match the generic ingredient to ``usda_fdc`` instead.

Prices are per store, a product search without a ``location_id`` comes
back with no price at all. Call ``find_locations`` first.
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import requests

from .display_names import display_name_for
from .schema import FoodItem

BASE_URL = "https://api.kroger.com/v1"
DATA_DIR = Path(__file__).parent / "data"
CLIENT_ID_ENV_VAR = "KROGER_CLIENT_ID"
CLIENT_SECRET_ENV_VAR = "KROGER_CLIENT_SECRET"
PRODUCT_SCOPE = "product.compact"
REQUEST_TIMEOUT = 10
MAX_PAGE_SIZE = 50  # Kroger rejects filter.limit above 50
MAX_IDS_PER_CALL = 50  # filter.productId takes up to 50 comma-separated UPCs
PARALLEL_CALLS = 4  # batches fetched at once; a whole-catalog fetch is ~34 calls
RETRIES = 3  # attempts per call on a timeout, connection error or 5xx
RETRY_BACKOFF_SECONDS = 2.0  # doubled after each failed attempt

# Macro-per-dollar outliers aren't food a plan should lean on, they're
# almost always a label or package-size mismatch that slipped through.
MAX_PROTEIN_PER_DOLLAR = 60
MAX_CALORIES_PER_DOLLAR = 2500

# Package sizes come back as free text ("16 oz", "1 gal", "5 lb", "12 ct").
# Everything is normalized to the three units data/ingredients.json prices
# in: grams, millilitres, or each.
UNIT_FACTORS = {
    "g": ("g", 1.0), "gram": ("g", 1.0), "grams": ("g", 1.0),
    "kg": ("g", 1000.0),
    "oz": ("g", 28.3495), "ounce": ("g", 28.3495), "ounces": ("g", 28.3495),
    "lb": ("g", 453.592), "lbs": ("g", 453.592), "pound": ("g", 453.592), "pounds": ("g", 453.592),
    "ml": ("ml", 1.0),
    "l": ("ml", 1000.0), "liter": ("ml", 1000.0), "liters": ("ml", 1000.0),
    "fl oz": ("ml", 29.5735), "floz": ("ml", 29.5735),
    "pt": ("ml", 473.176), "pint": ("ml", 473.176),
    "qt": ("ml", 946.353), "quart": ("ml", 946.353),
    "gal": ("ml", 3785.41), "gallon": ("ml", 3785.41),
    "ct": ("each", 1.0), "count": ("each", 1.0), "each": ("each", 1.0), "ea": ("each", 1.0),
    "pk": ("each", 1.0), "pack": ("each", 1.0),
    "dozen": ("each", 12.0), "doz": ("each", 12.0),
}

_SIZE_PATTERN = re.compile(r"(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?)\s*([a-z][a-z ]*?)\s*$")


class KrogerError(Exception):
    """Credentials are missing, or a Kroger response couldn't be used."""


def parse_size(size: Optional[str]) -> Optional[Tuple[float, str]]:
    """Turn a Kroger size string into ``(quantity, unit)`` with unit in g/ml/each.

    Returns None for anything it can't read confidently ("1 bunch",
    "varies", multi-part sizes like "6 pk / 12 fl oz") rather than guess,
    a wrong package size silently produces a wrong unit price.
    """
    if not size:
        return None
    text = size.strip().lower().rstrip(".").strip()
    match = _SIZE_PATTERN.fullmatch(text)
    if not match:
        return None
    number, unit = match.group(1), match.group(2).strip()
    if "/" in number:
        return None
    factor = UNIT_FACTORS.get(unit)
    if factor is None:
        return None
    quantity = float(number) * factor[1]
    if quantity <= 0:
        return None
    return round(quantity, 2), factor[0]


def to_price_record(product: dict, location_id: str = "", use_promo: bool = True) -> Optional[dict]:
    """Flatten one Kroger product into a price record, or None if it carries no price.

    A product comes back unpriced when it isn't sold at the requested
    store, those are dropped here rather than reported at $0. ``price`` is
    what a shopper pays today (the promo price when one is running),
    ``regular_price`` is kept alongside so a refresh job can tell a
    temporary sale from a real change. With ``use_promo=False``, ``price``
    is the regular price even during a sale (``on_sale`` still says so).
    """
    items = product.get("items") or []
    if not items:
        return None
    item = items[0]
    pricing = item.get("price") or {}
    regular = pricing.get("regular")
    if not regular:
        return None
    promo = pricing.get("promo") or 0
    sale_running = 0 < promo < regular
    price = promo if sale_running and use_promo else regular

    record = {
        "upc": product.get("upc") or product.get("productId") or "",
        "description": product.get("description") or "",
        "brand": product.get("brand") or "",
        "size": item.get("size") or "",
        "price": price,
        "regular_price": regular,
        "on_sale": sale_running,
        "sold_by": item.get("soldBy") or "",
        "stock_level": (item.get("inventory") or {}).get("stockLevel") or "",
        "location_id": location_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quantity": None,
        "unit": None,
        "price_per_unit": None,
    }
    parsed = parse_size(record["size"])
    if parsed:
        quantity, unit = parsed
        record["quantity"] = quantity
        record["unit"] = unit
        record["price_per_unit"] = round(price / quantity, 5)
    return record


def _flatten_store(store: dict) -> dict:
    address = store.get("address") or {}
    parts = (address.get("addressLine1"), address.get("city"), address.get("state"), address.get("zipCode"))
    return {
        "location_id": store.get("locationId", ""),
        "chain": store.get("chain", ""),
        "name": store.get("name", ""),
        "address": ", ".join(part for part in parts if part),
    }


class KrogerClient:
    """Thin client for Kroger's public Locations and Products APIs.

    Holds the app-level token and refreshes it when it expires (Kroger
    issues 30-minute tokens), so a refresh job pricing a few dozen
    ingredients authenticates once.
    """

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id = client_id or os.environ.get(CLIENT_ID_ENV_VAR)
        self.client_secret = client_secret or os.environ.get(CLIENT_SECRET_ENV_VAR)
        if not self.client_id or not self.client_secret:
            raise KrogerError(
                f"Kroger API credentials missing. Register an application at "
                f"https://developer.kroger.com and set {CLIENT_ID_ENV_VAR} and {CLIENT_SECRET_ENV_VAR}."
            )
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self.calls = 0  # API requests made, so a refresh run can report and budget them
        self._lock = threading.Lock()  # get_products fetches batches from several threads

    def _get_token(self) -> str:
        with self._lock:
            return self._token_locked()

    def _token_locked(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        resp = requests.post(
            f"{BASE_URL}/connect/oauth2/token",
            data={"grant_type": "client_credentials", "scope": PRODUCT_SCOPE},
            auth=(self.client_id, self.client_secret),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code in (400, 401):
            raise KrogerError("Kroger rejected the client id/secret, check both values and that the Products API is enabled for the app.")
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        # Refresh a minute early so a token never expires mid-request.
        self._token_expires_at = time.time() + body.get("expires_in", 1800) - 60
        return self._token

    def _request(self, path: str, params: dict):
        """GET with retries on timeouts, dropped connections and 5xx, backing off between tries.

        A 429 is not retried: Kroger's limits are per day, so waiting
        seconds won't help, and hammering a limit is how an app gets cut off.
        """
        delay = RETRY_BACKOFF_SECONDS
        for attempt in range(1, RETRIES + 1):
            with self._lock:
                self.calls += 1
            try:
                resp = requests.get(
                    f"{BASE_URL}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {self._get_token()}", "Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT,
                )
            except (requests.ConnectionError, requests.Timeout):
                if attempt == RETRIES:
                    raise KrogerError(f"Kroger API unreachable after {RETRIES} tries.")
            else:
                if resp.status_code == 429:
                    raise KrogerError("Kroger daily rate limit reached, try again tomorrow.")
                if resp.status_code < 500 or attempt == RETRIES:
                    return resp
            time.sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")

    def _get(self, path: str, params: dict) -> dict:
        resp = self._request(path, params)
        resp.raise_for_status()
        return resp.json()

    def find_locations(
        self, zip_code: str, radius_miles: int = 10, chain: Optional[str] = None, limit: int = 10
    ) -> List[dict]:
        """Stores near a zip code, nearest first. ``chain`` narrows to one banner, e.g. "RALPHS"."""
        params = {
            "filter.zipCode.near": zip_code,
            "filter.radiusInMiles": radius_miles,
            "filter.limit": limit,
        }
        if chain:
            params["filter.chain"] = chain
        return [_flatten_store(s) for s in self._get("/locations", params).get("data", [])]

    def get_location(self, location_id: str) -> Optional[dict]:
        """One store by locationId, same shape find_locations returns, or None if there's no such store."""
        try:
            store = self._get(f"/locations/{location_id}", {}).get("data")
        except requests.HTTPError:
            return None
        return _flatten_store(store) if store else None

    def search_products(self, term: str, location_id: str, limit: int = 10, start: int = 0) -> List[dict]:
        """Raw Kroger product dicts for a search term, priced for one store."""
        params = {
            "filter.term": term,
            "filter.locationId": location_id,
            "filter.limit": min(limit, MAX_PAGE_SIZE),
        }
        if start:
            params["filter.start"] = start
        return self._get("/products", params).get("data", [])

    def get_product(self, product_id: str, location_id: str) -> Optional[dict]:
        """One raw Kroger product by UPC/productId, priced for one store, or None if Kroger has no such product."""
        resp = self._request(f"/products/{product_id}", {"filter.locationId": location_id})
        if resp.status_code in (400, 404):
            return None
        resp.raise_for_status()
        return resp.json().get("data") or None

    def get_products(self, product_ids: List[str], location_id: str) -> dict:
        """Raw Kroger products for many UPCs at one store, ``{upc: product}``.

        Asks for up to 50 UPCs per call. UPCs Kroger doesn't know are
        simply absent from the result; ones it knows but the store doesn't
        sell come back without a price (``to_price_record`` drops those).
        """
        ids = list(dict.fromkeys(product_ids))
        chunks = [ids[i:i + MAX_IDS_PER_CALL] for i in range(0, len(ids), MAX_IDS_PER_CALL)]

        def fetch(chunk):
            return self._get("/products", {"filter.productId": ",".join(chunk), "filter.locationId": location_id})

        if len(chunks) > 1:
            with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
                bodies = list(pool.map(fetch, chunks))
        else:
            bodies = [fetch(c) for c in chunks]
        found = {}
        for body in bodies:
            for product in body.get("data") or []:
                upc = product.get("upc") or product.get("productId")
                if upc:
                    found[upc] = product
        return found

    def price_search(self, term: str, location_id: str, limit: int = 10) -> List[dict]:
        """Price records for a search term at one store, unpriced products dropped."""
        records = (to_price_record(p, location_id) for p in self.search_products(term, location_id, limit))
        return [r for r in records if r is not None]

    def cheapest_per_unit(self, term: str, location_id: str, unit: str, limit: int = 20) -> Optional[dict]:
        """The lowest unit-price match for a term among results sold in ``unit`` (g, ml, or each).

        Search relevance is Kroger's, not this module's, a search for
        "onion" can surface onion rings. Treat the result as a candidate
        to review, not a verified match.
        """
        candidates = [r for r in self.price_search(term, location_id, limit) if r["unit"] == unit]
        if not candidates:
            return None
        return min(candidates, key=lambda r: r["price_per_unit"])


def load_product_map() -> dict:
    """The reviewed ingredient -> Kroger product pins shipped in data/kroger_products.json."""
    with open(DATA_DIR / "kroger_products.json", encoding="utf-8") as f:
        return json.load(f)


def _per_ingredient_unit(record: dict, unit: str, each_grams: Optional[float]) -> Optional[float]:
    """A record's unit price expressed in the ingredient's own unit, or None if they can't be reconciled."""
    if record["price_per_unit"] is None:
        return None
    if record["unit"] == unit:
        return record["price_per_unit"]
    # Loose produce is sold by the pound but recipes count it by the piece
    # (a banana); each_grams is the reviewed as-sold weight of one piece.
    if unit == "each" and record["unit"] == "g" and each_grams:
        return round(record["price"] / record["quantity"] * each_grams, 5)
    return None


def price_ingredients(
    client: KrogerClient, location_id: str, ingredient_db: dict, product_map: Optional[dict] = None
) -> dict:
    """Price every ingredient in ``ingredient_db`` at one store.

    Each ingredient is priced from its pinned UPC in the product map when
    there is one, a specific product a person looked at and confirmed is
    the right thing. Only when a pin is missing, or the store doesn't
    carry it, does this fall back to the cheapest search result, and that
    result is marked ``"pinned": False`` so nothing unreviewed gets applied
    silently (search for "banana" and the cheapest "each" is a plantain).

    Returns ``{ingredient: {"price_per_unit", "pinned", "product", "candidates"}}``;
    ``price_per_unit`` is None when nothing usable was found.
    """
    product_map = load_product_map() if product_map is None else product_map
    results = {}
    for key, info in ingredient_db.items():
        pin = product_map.get(key, {})
        unit, each_grams = info["unit"], pin.get("each_grams")
        term = pin.get("term") or key.replace("_", " ")

        chosen, price, pinned = None, None, False
        if pin.get("upc"):
            raw = client.get_product(pin["upc"], location_id)
            record = to_price_record(raw, location_id) if raw else None
            if record:
                price = _per_ingredient_unit(record, unit, each_grams)
                if price is not None:
                    chosen, pinned = record, True

        candidates = []
        if chosen is None:
            for record in client.price_search(term, location_id, limit=30):
                unit_price = _per_ingredient_unit(record, unit, each_grams)
                if unit_price is not None:
                    candidates.append((unit_price, record))
            candidates.sort(key=lambda pair: pair[0])
            if candidates:
                price, chosen = candidates[0]

        results[key] = {
            "price_per_unit": price,
            "pinned": pinned,
            "product": chosen,
            "candidates": [record for _, record in candidates[:5]],
        }
    return results


# --- Products as FoodItems -------------------------------------------------
#
# Kroger's product payload carries the package's own nutrition label
# (``nutritionInformation``), so a priced product can become a complete
# FoodItem with no second lookup. Coverage is good but not total, roughly
# four in five priced products have a usable label; the rest (hot deli,
# loose produce, some store-made items) are skipped by to_food_item rather
# than given guessed macros.

# INFOODS tagnames Kroger uses on each nutrient row.
NUTRIENT_CODES = {
    "calories": "ENER-",
    "protein": "PRO-",
    "carbs": "CHO-",
    "fat": "FAT",
    "fiber": "FIBTSW",
    "sodium": "NA",  # milligrams
}
REQUIRED_NUTRIENTS = ["calories", "protein", "carbs", "fat"]
SERVING_UNIT_GRAMS = {"GRM": 1.0, "ONZ": 28.3495, "KGM": 1000.0, "LBR": 453.592}

_MEAT_WORDS = (
    "chicken", "beef", "pork", "turkey", "bacon", "ham", "sausage", "pepperoni", "salami", "prosciutto",
    "lamb", "veal", "duck", "meat", "steak", "brisket", "carnitas", "chorizo", "gelatin", "lard", "tallow",
    "fish", "tuna", "salmon", "cod", "tilapia", "shrimp", "crab", "surimi", "anchovy", "anchovies", "oyster", "clam",
    "lobster", "scallop", "sardine", "pollock", "bonito", "jerky", "broth", "rennet",
)
_PORK_WORDS = ("pork", "bacon", "ham", "pepperoni", "salami", "prosciutto", "sausage", "chorizo", "carnitas", "lard")
_ANIMAL_PRODUCT_WORDS = (
    "milk", "cream", "butter", "cheese", "whey", "casein", "yogurt", "lactose", "egg", "honey", "ghee",
)


def _mentions(text: str, words) -> bool:
    """Whole-word match, plurals included, so "ham" doesn't fire on "graham" or "cod" on "codex"."""
    return re.search(r"\b(?:%s)(?:s|es)?\b" % "|".join(map(re.escape, words)), text) is not None


def parse_nutrition(product: dict) -> Optional[dict]:
    """Per-serving macros from a product's nutrition label, or None if it has no usable one.

    A nutrient row with no ``quantity`` is how Kroger renders a 0 on the
    label (plain oats list sodium and added sugar that way), so a present
    row without a quantity reads as zero. A nutrient with no row at all is
    genuinely unknown, and a missing calorie/protein/carb/fat row rejects
    the label. Labels whose macros can't plausibly add up to their stated
    calories are rejected too, a data-entry error upstream shouldn't reach
    the optimizer as a miracle food.
    """
    labels = product.get("nutritionInformation") or []
    if not labels:
        return None
    label = labels[0]
    rows = {row.get("code"): row for row in label.get("nutrients") or []}

    macros = {}
    for field, code in NUTRIENT_CODES.items():
        row = rows.get(code)
        if row is None:
            if field in REQUIRED_NUTRIENTS:
                return None
            macros[field] = 0.0
        else:
            macros[field] = float(row.get("quantity") or 0.0)
    if rows[NUTRIENT_CODES["calories"]].get("quantity") is None:
        return None

    from_macros = 4 * macros["protein"] + 4 * macros["carbs"] + 9 * macros["fat"]
    if macros["calories"] >= 50 and not (0.5 * macros["calories"] <= from_macros <= 1.6 * macros["calories"]):
        return None

    serving = label.get("servingSize") or {}
    serving_qty = serving.get("quantity")
    unit = serving.get("unitOfMeasure") or {}
    grams_factor = SERVING_UNIT_GRAMS.get(unit.get("code"))
    macros["serving_grams"] = serving_qty * grams_factor if serving_qty and grams_factor else None
    macros["serving_label"] = f"{serving_qty:g} {(unit.get('name') or '').lower()}".strip() if serving_qty else ""
    macros["servings_per_package"] = (label.get("servingsPerPackage") or {}).get("value")
    macros["ingredients"] = label.get("ingredientStatement") or ""
    return macros


def servings_in_package(record: dict, nutrition: dict) -> Optional[float]:
    """How many label servings one purchased package holds.

    Package weight over serving weight when both are known, since that's
    arithmetic rather than a typed-in field. Otherwise the label's own
    servings-per-package, corrected for multipacks: a "4 ct" box of bars
    whose label says 1 serving per package is describing one bar.
    """
    if record.get("unit") == "g" and nutrition.get("serving_grams"):
        return record["quantity"] / nutrition["serving_grams"]
    servings = nutrition.get("servings_per_package")
    if not servings:
        return None
    if record.get("unit") == "each" and record["quantity"] > 1 and servings == 1:
        return record["quantity"]
    return float(servings)


def _dietary_flags(product: dict, ingredients: str) -> dict:
    """Dietary flags inferred from the ingredient statement and allergen declarations.

    Kroger publishes no vegetarian/vegan/halal fields, so these are keyword
    screens, deliberately one-sided: a product is only called vegetarian or
    vegan when it has an ingredient statement and nothing in it (or its
    name) looks animal-derived. Unknown stays False. Halal is only ever
    taken from the product's own name.
    """
    name = (product.get("description") or "").lower()
    text = f"{name} {ingredients.lower()}"
    contains = sorted({
        a["name"].lower() for a in product.get("allergens") or []
        if a.get("name") and (a.get("levelOfContainmentName") or "").lower() == "contains"
    })
    allergen_text = " ".join(contains)

    has_meat = _mentions(text, _MEAT_WORDS) or "fish" in allergen_text or "crustacean" in allergen_text
    vegetarian = bool(ingredients) and not has_meat
    vegan = vegetarian and not _mentions(f"{text} {allergen_text}", _ANIMAL_PRODUCT_WORDS)
    return dict(
        vegetarian=vegetarian, vegan=vegan,
        gluten_free="gluten free" in name or "gluten-free" in name,
        halal="halal" in name,
        contains_pork=_mentions(text, _PORK_WORDS),
        allergens=contains,
    )


# Kroger's own size names, largest first. "large" is ~500px, plenty for a
# swipe card without pulling the multi-megabyte xlarge shot.
_IMAGE_SIZE_PREFERENCE = ("large", "medium", "xlarge", "small", "thumbnail")


def product_image_url(product: dict, perspective: str = "front") -> str:
    """The product's photo URL from its ``images`` array, or "" if it has none.

    Prefers the ``perspective`` shot (the package front), then whichever
    image Kroger marks ``featured``, then any image at all; within that
    image, the size in ``_IMAGE_SIZE_PREFERENCE`` order.
    """
    images = [img for img in (product.get("images") or []) if img.get("sizes")]
    if not images:
        return ""
    images.sort(key=lambda img: (img.get("perspective") != perspective, not img.get("featured")))
    sizes = {s.get("size"): s.get("url") for s in images[0]["sizes"] if s.get("url")}
    for size in _IMAGE_SIZE_PREFERENCE:
        if sizes.get(size):
            return sizes[size]
    return next(iter(sizes.values()), "")


def to_food_item(
    product: dict, location_id: str = "", location_name: str = "", max_daily: int = 2, kind: str = "",
    use_promo: bool = False,
) -> Optional[FoodItem]:
    """One priced, labeled Kroger product as a FoodItem for one serving, or None if it can't be built honestly.

    ``price`` is the package price divided by the servings in it. That's
    the cost of the food eaten, not the cash needed at the register: a
    plan that uses one serving from a 30-serving canister of oats still
    means buying the canister. The package price is kept in ``notes``.

    The regular shelf price is used unless ``use_promo`` is set: a sale
    ends, and a plan built on it quietly gets more expensive next week.
    """
    record = to_price_record(product, location_id)
    nutrition = parse_nutrition(product)
    if record is None or nutrition is None or nutrition["calories"] <= 0:
        return None
    servings = servings_in_package(record, nutrition)
    if not servings or servings < 0.75:
        return None

    categories = product.get("categories") or []
    page = (product.get("productPageURI") or "").split("?")[0]
    package_price = record["price"] if use_promo else record["regular_price"]
    if not record["on_sale"]:
        sale = ""
    elif use_promo:
        sale = f", on sale from ${record['regular_price']:.2f}"
    else:
        sale = f", on sale for ${record['price']:.2f} (sale price not used)"
    return FoodItem(
        name=record["description"],
        category=categories[0] if categories else "Grocery",
        source="Kroger API",
        location=location_name or location_id,
        price=round(package_price / servings, 2),
        serving_unit=nutrition["serving_label"] or "1 serving",
        calories=nutrition["calories"],
        protein=nutrition["protein"],
        carbs=nutrition["carbs"],
        fat=nutrition["fat"],
        fiber=nutrition["fiber"],
        sodium=nutrition["sodium"],
        max_daily=max_daily,
        confidence="external_api",
        notes=(
            f"UPC {record['upc']}. {record['size']} package at ${package_price:.2f}{sale}, "
            f"{round(servings, 1):g} serving(s), priced {record['fetched_at'][:10]}."
        ),
        source_url=f"https://www.kroger.com{page}" if page else "",
        kind=kind,
        display_name=display_name_for(record["description"], record["brand"], record["upc"]),
        image_url=product_image_url(product),
        **_dietary_flags(product, nutrition["ingredients"]),
    )


def passes_screen(item: FoodItem) -> bool:
    """False for an item whose macros per dollar are implausible, a label or size mismatch upstream."""
    if item.price <= 0:
        return False
    return (item.protein / item.price <= MAX_PROTEIN_PER_DOLLAR
            and item.calories / item.price <= MAX_CALORIES_PER_DOLLAR)
