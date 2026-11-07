"""Build the ready-to-eat grocery catalog from one Kroger-family store's real products.

Searches the store for each term in CATALOG_TERMS, keeps every product
that comes back with both a shelf price and a usable nutrition label, and
writes them to src/macro_optimizer/data/kroger_items.json, the catalog
database.load_market_items() serves to the optimizer. Run from the repo
root, with the same credentials as pull_kroger_prices.py:

    py scripts/build_kroger_catalog.py --location 70300108

Each item's ``kind`` is the search term that found it, which is what lets
the optimizer treat four brands of peanut butter as one food.

Re-run it to refresh prices; the file is rebuilt from scratch each time,
so anything the store stopped carrying drops out. One search call per
term, well inside Kroger's daily limit.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from macro_optimizer.kroger import KrogerClient, KrogerError, to_food_item

sys.path.insert(0, str(Path(__file__).parent))
from pull_kroger_prices import REPO_ROOT, load_env_file  # noqa: E402

CATALOG_PATH = REPO_ROOT / "src" / "macro_optimizer" / "data" / "kroger_items.json"

# Things a person eats as bought, or with a microwave at most. Raw
# ingredients are deliberately absent, those reach the optimizer through
# recipes (data/ingredients.json, priced by pull_kroger_prices.py).
CATALOG_TERMS = [
    "protein bar", "granola bar", "protein shake", "greek yogurt cup", "cottage cheese", "string cheese",
    "milk", "kefir", "hard boiled eggs", "deli turkey", "deli chicken", "canned tuna", "canned chicken",
    "tuna pouch", "beef jerky", "hummus", "guacamole", "salad kit", "sushi", "deli sandwich", "wrap sandwich",
    "frozen meal", "frozen burrito", "frozen pizza", "frozen breakfast sandwich", "frozen chicken strips",
    "microwave rice", "instant oatmeal", "cereal", "granola", "whole wheat bread", "bagels", "tortillas",
    "peanut butter", "almonds", "trail mix", "canned soup", "canned chili", "instant ramen", "mac and cheese cup",
    "fruit cup", "applesauce", "baby carrots", "orange juice", "tofu", "veggie burger",
]

# Macro-per-dollar outliers aren't food a plan should lean on, they're
# almost always a label or package-size mismatch that slipped through.
MAX_PROTEIN_PER_DOLLAR = 60
MAX_CALORIES_PER_DOLLAR = 2500


def main() -> int:
    load_env_file()
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--location", required=True, help="Kroger locationId to build the catalog from")
    parser.add_argument("--per-term", type=int, default=50, help="products to request per search term (max 50)")
    args = parser.parse_args()

    try:
        client = KrogerClient()
        store = client.get_location(args.location) or {"name": args.location}
        items, seen_upcs, seen_names, skipped = [], set(), set(), Counter()
        for term in CATALOG_TERMS:
            kept = 0
            for product in client.search_products(term, args.location, limit=args.per_term):
                upc = product.get("upc")
                if upc in seen_upcs:
                    continue
                seen_upcs.add(upc)
                item = to_food_item(product, args.location, store["name"], kind=term)
                if item is None:
                    skipped["no price or usable label"] += 1
                elif item.price <= 0 or item.name in seen_names:
                    skipped["duplicate name or zero price"] += 1
                elif (item.protein / item.price > MAX_PROTEIN_PER_DOLLAR
                      or item.calories / item.price > MAX_CALORIES_PER_DOLLAR):
                    skipped["implausible macros per dollar"] += 1
                else:
                    seen_names.add(item.name)
                    items.append(item.to_dict())
                    kept += 1
            print(f"{term:<28}{kept:>4} kept")
    except KrogerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    items.sort(key=lambda row: (row["category"], row["name"]))
    CATALOG_PATH.write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{len(items)} items from {store['name']} -> {CATALOG_PATH.relative_to(REPO_ROOT)}")
    for reason, count in skipped.items():
        print(f"  skipped {count}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
