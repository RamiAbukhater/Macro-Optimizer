"""Shared food item schema.

Every candidate the optimizer can choose (a market item, a dining hall item, a
priced recipe, eventually a chain-restaurant or grocery item pulled from an
external API) is normalized into this one shape before it reaches
``run_optimizer``. This is the "normalization layer" sketched in IDEAS.md:
one schema, one adapter per source, so the optimizer never has to know where
a row came from.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional

# Fields every food row must carry. Kept as a plain list (rather than only
# living on the dataclass) so database.py can validate raw dicts/JSON before
# they're turned into FoodItem instances.
FIELDS = [
    "name", "category", "source", "location",
    "price", "serving_unit",
    "calories", "protein", "carbs", "fat", "fiber", "sodium",
    "allergens", "vegetarian", "vegan", "gluten_free", "halal", "contains_pork",
    "max_daily", "notes", "prep_minutes", "cook_minutes", "source_url", "kind", "image_url",
    "display_name", "upc", "servings_per_package", "package_price", "package_size",
]

REQUIRED_FIELDS = [
    "name", "category", "source", "price",
    "calories", "protein", "carbs", "fat",
]


@dataclass
class FoodItem:
    name: str
    category: str
    source: str
    price: float
    calories: float
    protein: float
    carbs: float
    fat: float
    location: str = ""
    serving_unit: str = "1 serving"
    fiber: float = 0.0
    sodium: float = 0.0
    allergens: List[str] = field(default_factory=list)
    vegetarian: bool = False
    vegan: bool = False
    gluten_free: bool = False
    halal: bool = False
    contains_pork: bool = False
    max_daily: int = 1
    notes: str = ""
    # Distinguishes hand-curated/database rows from anything sourced from an
    # external API, a recipe decomposition, or free-text user entry, per the
    # "source and confidence field" IDEAS.md calls for in the normalization
    # layer. Not used to filter today; the UI/optimizer can surface it later.
    confidence: str = "curated"
    # Active time cost, not wall-clock (overnight oats has cook_minutes=0
    # even though it sits in the fridge for hours). Defaults to 0 for
    # market/dining items, which are already ready to eat. Feeds
    # menu_planner.py's per-day time budget; see IDEAS.md's "Schedule aware
    # planning" section.
    prep_minutes: float = 0.0
    cook_minutes: float = 0.0
    # A real product page, menu URL, or recipe source, when one legitimately
    # exists. Defaults to "" (unknown/none), never fabricated, per the
    # "recipe instructions / source links" gap IDEAS.md and backend_support.md
    # flag: hand-curated market/dining rows and illustrative vendor catalogs
    # have no real URL to attach yet, this field exists so one can be filled
    # in per-row without a schema change once a real source is available.
    source_url: str = ""
    # What sort of food this is, at the level a person means by "I already
    # had peanut butter today", for catalogs big enough to carry the same
    # thing under many brands and sizes. The optimizer caps a day's servings
    # per kind (see optimizer.KIND_DAILY_CAP). "" means no grouping, which is
    # right for recipes and one-of-a-kind items.
    kind: str = ""
    # What to show a person, when ``name`` is a raw product title full of
    # brand and package noise ("Kroger® Hawaiian Bagels" -> "Hawaiian
    # Bagels"). Filled at catalog-build time by display_names.py. ``name``
    # stays the raw, unique title because it's the identity key for taste
    # profiles, pins and history; "" means show ``name`` as is.
    display_name: str = ""
    # A product photo URL straight from the source (Kroger's CDN for
    # catalog rows). Only the URL is stored, never the image bytes: Kroger's
    # API terms forbid keeping permanent copies of API content, so the
    # browser loads it from Kroger directly. "" means no photo; the swipe
    # card then draws a styled placeholder instead.
    image_url: str = ""
    # What one purchase of this item actually is, for a store product:
    # the UPC, how many of the servings above one package holds, its shelf
    # price and its size as printed ("20 oz"). ``price`` is per serving, so
    # the shopping list needs these to round a week's servings up to whole
    # packages. Filled live from Kroger (kroger.to_food_item) and never
    # persisted. Empty/0 for recipes and anything not bought as a package.
    upc: str = ""
    servings_per_package: float = 0.0
    package_price: float = 0.0
    package_size: str = ""

    def __post_init__(self):
        if self.price < 0:
            raise ValueError(f"{self.name!r}: price cannot be negative")
        for macro in ("calories", "protein", "carbs", "fat", "fiber", "sodium"):
            if getattr(self, macro) < 0:
                raise ValueError(f"{self.name!r}: {macro} cannot be negative")
        if self.max_daily < 1:
            raise ValueError(f"{self.name!r}: max_daily must be at least 1")
        if self.prep_minutes < 0 or self.cook_minutes < 0:
            raise ValueError(f"{self.name!r}: time costs cannot be negative")

    @property
    def total_time_minutes(self) -> float:
        return self.prep_minutes + self.cook_minutes

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FoodItem":
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise ValueError(f"food item missing required fields: {missing}")
        known = {k: v for k, v in data.items() if k in _FIELD_NAMES}
        return cls(**known)


_FIELD_NAMES = set(FoodItem.__dataclass_fields__.keys())
