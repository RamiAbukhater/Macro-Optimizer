"""Shopper-facing food names cleaned from raw grocery product titles.

Kroger titles are written to sell a package, not to name a food:
"Frigo® Cheese Heads™ Light Mozzarella String Cheese",
"Guerrero Caseras Flour Tortillas, Soft Taco Size, 10 Count". The menu and
the swipe cards should say "Light Mozzarella String Cheese" and "Caseras
Flour Tortillas". This module turns one into the other with a short list of
deterministic rules, run once at catalog-build time
(kroger.to_food_item), so the result is visible in the catalog JSON diff.

The raw title stays on the row as ``name``. It's the identity key for taste
profiles, pins and rotation history, and two brands of "Creamy Peanut
Butter" would collide without their brands. ``display_name`` is only ever
shown, never matched on.

Rules can't know that "Super Mario" is a licensing deal rather than a
food. Those cases go in data/display_name_overrides.json (UPC -> name),
reviewed by hand and checked in.

Every rule is guarded: a step that would leave the name empty, or strip
the last recognizable food word out of a name that had one, is skipped and
the less-stripped version kept.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

OVERRIDES_PATH = Path(__file__).parent / "data" / "display_name_overrides.json"

# Store brands that show up on a product title even when Kroger's ``brand``
# field names something else (a co-packer, or a more specific line).
HOUSE_BRANDS = [
    "Simple Truth Organic", "Simple Truth", "Private Selection", "Kroger Deli", "Kroger",
    "Heritage Farm", "Home Chef", "Psst",
]

# Parent companies that prefix a title whose ``brand`` field is the product
# line instead ("Kellogg's Frosted Flakes", brand "Frosted Flakes").
MAKER_BRANDS = ["Kellogg's", "General Mills", "Kraft", "Dannon", "Quaker", "Post", "Nabisco", "Nestle", "Hormel"]

TRADEMARK_SYMBOLS = "®™©℠"

# Marketing and packaging phrases that say nothing about the food itself.
FILLER_PHRASES = [
    r"Grab\s*(?:'?N'?|&|and)\s*Go", r"On\s+the\s+Go", r"Shelf[\s-]+Stable",
    r"Microwav(?:e)?able", r"Microwave[\s-]+Safe", r"Handcrafted", r"Hand[\s-]+Crafted",
    r"(?:with\s+|plus\s+|\+\s*)?Calcium\s+(?:and|&)\s+Vitamin\s+D", r"(?:with\s+)?Added\s+Calcium",
    r"BIG\s+Deal!?", r"Value\s+Pack", r"Family\s+Pack", r"Bonus\s+Pack", r"Club\s+Pack",
    r"Variety\s+Pack", r"Multi[\s-]?Pack", r"Snack\s+Pack(?=s?\b)", r"Single[\s-]+Serve",
    r"Ready[\s-]+to[\s-]+Eat", r"Limited\s+Edition", r"(?-i:NEW)!?", r"New!",
]

SIZE_WORDS = (
    r"(?:Soft\s+Tacos?|Street\s+Tacos?|Tacos?|Fajita|Burrito|Family|Party|Snack|Fun|King|Personal|"
    r"Value|Bite|Travel|Sharing|Jumbo|Mini)\s+Size"
)

COUNT_UNITS = (
    r"count|ct|pk|packs?|pack|oz|ounces?|fl\.?\s*oz|lbs?|ml|liters?|l|each|pcs?|pieces?|"
    r"servings?|bars|cups|pouches|bottles|cans"
)

# Trailing package words, only stripped from the very end of a title.
PACKAGING_WORDS = r"Bottles?|Cans?|Jars?|Cartons?|Box(?:es)?|Bags?|Tubs?|Packets?|Pouch(?:es)?|Containers?|Canisters?"

# Words that would be wrong to title-case even though they're all caps.
KEEP_UPPER = {"BBQ", "BLT", "USDA", "PB&J", "PB", "XL", "II", "III", "IPA", "NY", "USA", "B12", "DHA"}

SMALL_WORDS = {"in", "with", "and", "of", "on", "or", "by", "the", "a", "for", "to", "&"}

# Enough food vocabulary for the over-cleaning guard to tell "Hawaiian
# Bagels" (a food) from "Cheese Heads" stripped down to "Light" (not one).
# Singular stems; matched as word prefixes so plurals count.
FOOD_NOUNS = {
    "almond", "apple", "applesauce", "bacon", "bagel", "banana", "bar", "bean", "beef", "beverage",
    "biscuit", "bite", "bowl", "bread", "breakfast", "brisket", "broth", "brownie", "bun", "burger",
    "burrito", "butter", "cake", "carrot", "cashew", "cereal", "cheddar", "cheese", "chicken", "chili",
    "chip", "chocolate", "chowder", "cocktail", "coffee", "cookie", "corn", "cottage", "couscous",
    "cracker", "cream", "crisp", "croissant", "cup", "curry", "dip", "drink", "dumpling", "egg",
    "enchilada", "entree", "falafel", "fig", "fish", "flatbread", "fries", "fruit", "granola", "grape",
    "guacamole", "ham", "hummus", "jerky", "juice", "kefir", "kit", "lasagna", "latte", "lemonade",
    "lentil", "mac", "macaroni", "mango", "meal", "meat", "meatball", "milk", "mix", "mozzarella",
    "muffin", "naan", "noodle", "nut", "oat", "oatmeal", "orange", "pancake", "pasta", "pastrami",
    "pastry", "patty", "peach", "peanut", "pear", "pepperoni", "pie", "pineapple", "pistachio", "pita",
    "pizza", "pork", "potato", "protein", "pudding", "quesadilla", "quinoa", "ramen", "ramyun", "ravioli",
    "rice", "roll", "salad", "salami", "salmon", "salsa", "sandwich", "sauce", "sausage", "seed",
    "shake", "shell", "shrimp", "skyr", "slice", "smoothie", "snack", "soup", "spaghetti", "spinach",
    "spread", "stew", "strawberry", "stick", "sub", "sushi", "taco", "tamale", "tea", "tender", "toast",
    "tofu", "tortilla", "trail", "tuna", "turkey", "veggie", "vegetable", "waffle", "walnut", "water",
    "wing", "wrap", "yogurt",
}

_SYMBOL_RE = re.compile(f"[{TRADEMARK_SYMBOLS}]")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")


def _has_food_noun(text: str) -> bool:
    for word in _WORD_RE.findall(text.lower()):
        word = word.strip("'")
        if any(word.startswith(noun) for noun in FOOD_NOUNS):
            return True
    return False


def _normalize(text: str) -> str:
    """Curly quotes to straight, so "Boar’s Head" matches the brand "Boar's Head"."""
    return text.replace("’", "'").replace("‘", "'").replace(" ", " ")


def _tidy(text: str) -> str:
    text = _SYMBOL_RE.sub("", text).replace("†", "")
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,;:)])", r"\1", text)
    text = re.sub(r"(\()\s+", r"\1", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    text = re.sub(r"\s+-\s*(?=,|$)", "", text)
    text = text.strip(" ,;:-–—&+/")
    # A dangling connector left behind by a stripped phrase ("Juice with").
    text = re.sub(r"(?:\s+(?:with|and|in|by|plus|&|-))+$", "", text, flags=re.I)
    return text.strip(" ,;:-–—&+/")


def _capitalize(text: str) -> str:
    words = text.split(" ")
    letters = re.sub(r"[^A-Za-z]", "", text)
    # A shouted title ("TINA'S RED HOT BEEF BURRITO") gets every word fixed,
    # otherwise only long all-caps words are, so "BBQ" and "XL" survive.
    shouting = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.6
    min_len = 2 if shouting else 4
    out = []
    for i, word in enumerate(words):
        bare = re.sub(r"[^A-Za-z&]", "", word)
        if bare.isupper() and len(bare) >= min_len and bare not in KEEP_UPPER:
            word = word[0] + word[1:].lower() if word[0].isalpha() else word.lower().title()
        if i > 0 and word.lower() in SMALL_WORDS and word[:1].isupper() and not word.isupper():
            word = word.lower()
        out.append(word)
    text = " ".join(out)
    return text[:1].upper() + text[1:]


def _strip_prefix(text: str, prefix: str, require_mark: bool) -> Optional[str]:
    """``text`` with ``prefix`` removed from its start, matching case- and symbol-insensitively."""
    pattern = r"\s*".join(re.escape(part) for part in prefix.split())
    match = re.match(rf"\s*{pattern}(?P<mark>[{TRADEMARK_SYMBOLS}]*)(?:'s|')?(?=[\s,:-]|$)", text, flags=re.I)
    if not match or (require_mark and not match.group("mark")):
        return None
    return text[match.end():]


# Corporate tails on Kroger's ``brand`` field that titles usually leave off
# ("Bush's Best" on "Bush's Kidney Beans", "Marukome USA" on "Marukome ...").
BRAND_TAIL_WORDS = {"best", "foods", "food", "farms", "farm", "co", "co.", "company", "inc", "inc.",
                    "brand", "brands", "naturals", "natural", "snacking", "usa", "mexican", "organic"}


def _brand_candidates(brand: str) -> List[Tuple[str, bool]]:
    """(prefix, needs a ® or ™ after it) pairs to try, most specific first."""
    brand = _normalize(_SYMBOL_RE.sub("", brand or "")).strip()
    if not brand:
        return []
    candidates = [(brand, False)]
    words = brand.split()
    for n in range(len(words) - 1, 0, -1):
        generic_tail = all(w.lower() in BRAND_TAIL_WORDS for w in words[n:])
        candidates.append((" ".join(words[:n]), not generic_tail))
    return candidates


def remove_brand(text: str, brand: str = "", makers_only: bool = False) -> str:
    """Drop the product's brand from the front of the title, plus any store brand.

    Kroger's ``brand`` field is usually the exact prefix ("Boar's Head"),
    but sometimes a longer or corporate form of it ("Simple Truth Organic"
    on a "Simple Truth® ..." title). A shortened brand only matches when the
    title marks it with ® or ™ or the dropped words are corporate filler,
    so a brand like "Fresh Express" can't eat the word "Fresh" off an
    unrelated title. ``makers_only`` drops just the parent company, for
    when the line name is the food ("General Mills Cocoa Puffs™ Cereal").
    """
    # A parent company first, then the line ("General Mills® Cookie Crisp™ Cereal").
    for maker in MAKER_BRANDS:
        stripped = _strip_prefix(text, maker, False)
        if stripped is not None:
            text = stripped
            break
    if makers_only:
        return text
    candidates = _brand_candidates(brand) + [(house, False) for house in HOUSE_BRANDS]
    for prefix, require_mark in candidates:
        stripped = _strip_prefix(text, prefix, require_mark)
        if stripped is not None:
            text = stripped
            break
    return remove_brand_suffix(text, brand)


def remove_brand_suffix(text: str, brand: str = "") -> str:
    """Credited makers ("Shrimp Dumplings by Zenshi®") and a brand repeated at the end ("... Oatmeal - Kroger")."""
    text = re.sub(rf"\s+by\s+[A-Z][\w'.&-]*(?:\s+[A-Z][\w'.&-]*){{0,2}}(?:[{TRADEMARK_SYMBOLS}]+|\s*$)", "", text)
    for name in (_normalize(brand or ""), *HOUSE_BRANDS):
        if name:
            text = re.sub(rf"\s+[-–]\s*{re.escape(name)}[{TRADEMARK_SYMBOLS}]*\s*(?=[-–]|$)", "", text, flags=re.I)
    return text


def remove_trademarks(text: str) -> Tuple[str, int]:
    """Drop each sub-brand, a run of up to 3 capitalized words directly followed by ® or ™.

    "Cheese Heads™ Light Mozzarella" -> "Light Mozzarella". A run is
    skipped (only its symbol goes) when removing it would take the name's
    last food word with it, "Greek Yogurt™" alone is still a food.
    """
    removed = 0
    token = r"(?:[A-Z0-9][\w'.!-]*|'N'?|N|&)"
    run_re = re.compile(rf"(?:(?<=^)|(?<=[\s,(]))(?:{token}\s+){{0,2}}{token}[{TRADEMARK_SYMBOLS}]+")
    while True:
        match = run_re.search(text)
        if not match:
            break
        candidate = text[:match.start()] + text[match.end():]
        # Never strip down to a lone generic word: "Crunch Berries® Cereal"
        # says more than "Cereal".
        too_bare = len(_tidy(candidate).split()) < 2 <= len(_tidy(text).split())
        if _tidy(candidate) and not too_bare and (_has_food_noun(candidate) or not _has_food_noun(text)):
            text = candidate
            removed += 1
        else:
            # Keep the words, drop the mark, and move past it.
            text = text[:match.start()] + _SYMBOL_RE.sub("", match.group()) + text[match.end():]
    return text, removed


def remove_counts_and_sizes(text: str) -> str:
    # "10 Count", "1.9 oz", ".7OZ", and multipacks written "4 - 5 oz Cans".
    text = re.sub(rf",?\s*\(?(?:\b\d+\s*[-x]\s*)?(?:\b\d+(?:\.\d+)?|(?<![\w.])\.\d+)\s*-?\s*(?:{COUNT_UNITS})\b\.?\)?(?:\s+(?:{PACKAGING_WORDS})\b)?", "", text, flags=re.I)
    text = re.sub(r",?\s*\(?\bPack\s+of\s+\d+\)?", "", text, flags=re.I)
    text = re.sub(r",?\s*\b\d+\s*x\s*(?=\s|,|$)", "", text, flags=re.I)
    text = re.sub(rf",?\s*\b{SIZE_WORDS}\b", "", text, flags=re.I)
    # Case-pack codes some suppliers leave on the title ("2/16/4").
    text = re.sub(r"\s+\d+(?:/\d+)+(?=\s|,|$)", "", text)
    text = re.sub(r",?\s*\b(?:Half\s+Gallon|Gallon|Quart|Pint)\s*$", "", text, flags=re.I)
    # Store availability notes ("– Not available before 11:00 AM Daily").
    text = re.sub(r"\s*[-–]\s*Not available\b.*$", "", text, flags=re.I)
    return text


def remove_filler(text: str) -> str:
    for phrase in FILLER_PHRASES:
        text = re.sub(rf"(?:(?<=^)|(?<=[\s,(])){phrase}(?=[\s,)]|$)", "", text, flags=re.I)
    # A phrase said twice in a row ("Frosted Flakes Frosted Flakes").
    text = re.sub(r"\b(\w[\w' ]{3,}?)\s+\1\s*$", r"\1", text.strip())
    return text


def remove_packaging(text: str) -> str:
    return re.sub(rf"\s+(?:{PACKAGING_WORDS})\s*$", "", _tidy(text))


def _rules(brand: str):
    """(rule name, candidate rewrites to try in order) for one product's brand."""
    return [
        ("brand", [lambda t: remove_brand(t, brand), lambda t: remove_brand(t, brand, makers_only=True)]),
        ("trademark", [lambda t: remove_trademarks(t)[0]]),
        ("count_size", [remove_counts_and_sizes]),
        ("filler", [remove_filler]),
        ("packaging", [remove_packaging]),
        # Again at the end: a "- Kroger" suffix is often only exposed once
        # the size after it is gone ("Peanut Butter - Kroger - 16 Oz").
        ("brand", [lambda t: remove_brand_suffix(_tidy(t), brand)]),
    ]


def clean_display_name(title: str, brand: str = "") -> Tuple[str, List[str]]:
    """A shopper-facing name for one raw product title, and the rules that changed it.

    Rules run in order; each one is checked before it's kept. If a step
    would leave nothing, leave a lone generic word, or leave a name with
    no food word when it had one, that step is undone (or its milder
    fallback tried) and the next rule runs on the less-stripped text. The
    returned list names the rules that actually changed the title (plus
    "capitalization" and "tidy" for cosmetic fixes), which is what the
    catalog backfill reports.
    """
    original = _normalize(title or "").strip()
    text = original
    hits: List[str] = []
    had_food = _has_food_noun(original)

    def accept(candidate: str) -> bool:
        tidied = _tidy(candidate)
        # "Cap'n Crunch® Cereal" stays as is, "Cereal" alone says nothing.
        too_bare = len(tidied.split()) < 2 <= len(_tidy(text).split())
        return bool(tidied) and not too_bare and (_has_food_noun(tidied) or not had_food)

    for rule, attempts in _rules(brand):
        for attempt in attempts:
            candidate = attempt(text)
            if candidate != text and accept(candidate):
                if _tidy(candidate) != _tidy(text) and rule not in hits:
                    hits.append(rule)
                text = candidate
                break

    tidied = _tidy(text)
    if tidied != text.strip():
        if "tidy" not in hits and _SYMBOL_RE.sub("", text).strip() != tidied:
            hits.append("tidy")
    capitalized = _capitalize(tidied)
    if capitalized != tidied:
        hits.append("capitalization")
    return capitalized or original, hits


@lru_cache(maxsize=1)
def load_overrides() -> Dict[str, str]:
    """Hand-reviewed UPC -> display name fixes for titles the rules can't clean."""
    if not OVERRIDES_PATH.exists():
        return {}
    data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {str(upc): name for upc, name in data.items() if not upc.startswith("_")}


def display_name_for(title: str, brand: str = "", upc: str = "") -> str:
    """The display name a catalog row should carry: a curated override if one exists, else the cleaned title."""
    override = load_overrides().get(str(upc)) if upc else None
    if override:
        return override
    return clean_display_name(title, brand)[0]


def label(item) -> str:
    """What to show for a food row (dict, Series, or FoodItem): its display name, else its raw name."""
    if isinstance(item, dict) or hasattr(item, "get"):
        shown, name = item.get("display_name"), item.get("name")
    else:
        shown, name = getattr(item, "display_name", ""), getattr(item, "name", "")
    # A DataFrame row mixing old and new sources can carry NaN, not "".
    if isinstance(shown, str) and shown.strip():
        return shown
    return name if isinstance(name, str) else ""
