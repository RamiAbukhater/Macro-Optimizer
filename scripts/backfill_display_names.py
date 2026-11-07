"""Fill ``display_name`` on the existing Kroger catalog without rebuilding it.

kroger_items.json rows don't carry the product's brand, which the name
cleaner needs, so this looks brands up again by UPC: first through the
same search terms build_kroger_catalog.py uses (one call per term), then
one product lookup per UPC those searches didn't return. Prices, macros
and everything else on each row are left exactly as they are.

    py scripts/backfill_display_names.py --location 70300108

Brands are cached in data_snapshots/kroger_brands.json (git-ignored), so
re-running after a rule change costs no API calls:

    py scripts/backfill_display_names.py --offline

Prints how many names each rule touched and a random before/after sample
to eyeball.
"""

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

# Import the package from this checkout's src/, ahead of any installed copy,
# so the script always runs against the cleaning rules sitting next to it
# (an editable install can point at a different worktree).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macro_optimizer.display_names import clean_display_name, load_overrides  # noqa: E402
from macro_optimizer.kroger import KrogerClient, KrogerError

sys.path.insert(0, str(Path(__file__).parent))
from build_kroger_catalog import CATALOG_PATH, CATALOG_TERMS  # noqa: E402
from pull_kroger_prices import SNAPSHOT_DIR, load_env_file  # noqa: E402

BRAND_CACHE = SNAPSHOT_DIR / "kroger_brands.json"


def upc_of(row: dict) -> str:
    match = re.search(r"UPC (\d+)", row.get("notes", ""))
    return match.group(1) if match else ""


def fetch_brands(upcs: set, location: str, per_term: int) -> dict:
    client = KrogerClient()
    brands = {}
    for term in CATALOG_TERMS:
        for product in client.search_products(term, location, limit=per_term):
            if product.get("upc") in upcs:
                brands[product["upc"]] = product.get("brand") or ""
    missing = sorted(upcs - set(brands))
    print(f"{len(brands)} brands from {len(CATALOG_TERMS)} searches, looking up {len(missing)} more by UPC")
    for upc in missing:
        product = client.get_product(upc, location)
        if product:
            brands[upc] = product.get("brand") or ""
    return brands


def main() -> int:
    load_env_file()
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--location", default="70300108", help="Kroger locationId the catalog was built from")
    parser.add_argument("--per-term", type=int, default=50)
    parser.add_argument("--offline", action="store_true", help="use cached brands only, no API calls")
    parser.add_argument("--sample", type=int, default=40, help="before/after rows to print")
    args = parser.parse_args()

    rows = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    upcs = {upc_of(r) for r in rows} - {""}

    brands = json.loads(BRAND_CACHE.read_text(encoding="utf-8")) if BRAND_CACHE.exists() else {}
    if not args.offline and upcs - set(brands):
        try:
            brands.update(fetch_brands(upcs - set(brands), args.location, args.per_term))
        except KrogerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        BRAND_CACHE.write_text(json.dumps(brands, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    overrides = load_overrides()
    hits, changed = Counter(), []
    for row in rows:
        upc = upc_of(row)
        cleaned, rules = clean_display_name(row["name"], brands.get(upc, ""))
        if upc in overrides:
            cleaned, rules = overrides[upc], ["override"]
        row["display_name"] = cleaned
        hits.update(rules)
        if cleaned != row["name"]:
            changed.append(row)

    CATALOG_PATH.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n{len(rows)} rows, {len(changed)} display names differ from the raw title, "
          f"{sum(1 for r in rows if upc_of(r) not in brands)} with no brand found")
    for rule, count in hits.most_common():
        print(f"  {rule:<16}{count:>5}")
    dupes = Counter(r["display_name"] for r in rows)
    print(f"  {sum(1 for c in dupes.values() if c > 1)} display names shared by more than one product (fine, name stays unique)")
    print("\nSample:")
    for row in random.Random(0).sample(rows, min(args.sample, len(rows))):
        print(f"  {row['name']}\n    -> {row['display_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
