"""Pull real shelf prices for every ingredient in data/ingredients.json from one Kroger-family store.

Needs KROGER_CLIENT_ID and KROGER_CLIENT_SECRET (free, from
https://developer.kroger.com), either as environment variables or in a
.env file at the repo root. Run from the repo root:

    py scripts/pull_kroger_prices.py --zip 92037 --chain RALPHS     # list nearby stores
    py scripts/pull_kroger_prices.py --location 70300108            # pull and compare
    py scripts/pull_kroger_prices.py --location 70300108 --apply    # also write into ingredients.json

Each ingredient is priced from the product pinned for it in
data/kroger_products.json. An ingredient with no pin, or whose pin the
store doesn't carry, falls back to the cheapest search result and is
flagged "unpinned": product search is fuzzy ("banana" also matches
plantains and banana peppers), so --apply skips those. To fix one, pick
the right UPC from the candidates saved in the snapshot and pin it.

Every run writes a snapshot to data_snapshots/kroger_<location>_<date>.json.
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from macro_optimizer.kroger import KrogerClient, KrogerError, price_ingredients

REPO_ROOT = Path(__file__).parent.parent
INGREDIENTS_PATH = REPO_ROOT / "src" / "macro_optimizer" / "data" / "ingredients.json"
SNAPSHOT_DIR = REPO_ROOT / "data_snapshots"


def load_env_file(path: Path = REPO_ROOT / ".env") -> None:
    """Read KEY=value lines from the repo's .env into the environment, real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if value:
            os.environ.setdefault(key.strip(), value)


def apply_prices(text: str, prices: dict) -> str:
    """Rewrite price_per_unit (and its price_source note) for each key in ingredients.json's text.

    Edits the text in place rather than re-dumping the JSON so the file's
    hand-laid-out formatting survives a refresh and the diff shows only
    the numbers that actually moved.
    """
    for key, (price, source) in prices.items():
        block = re.search(r'"%s": \{.*?\n  \}' % re.escape(key), text, flags=re.DOTALL)
        if not block:
            continue
        body = block.group(0)
        body = re.sub(r'\n\s*"price_source": "[^"]*",', "", body)
        body = re.sub(
            r'("price_per_unit": )[-0-9.eE+]+,',
            lambda m: '%s%s,\n    "price_source": %s,' % (m.group(1), format(price, ".5f").rstrip("0"), json.dumps(source)),
            body,
            count=1,
        )
        text = text[: block.start()] + body + text[block.end():]
    return text


def main() -> int:
    load_env_file()
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--zip", help="list stores near this zip code and exit")
    parser.add_argument("--chain", help='narrow --zip to one banner, e.g. "RALPHS"')
    parser.add_argument("--location", help="Kroger locationId to pull prices from")
    parser.add_argument("--apply", action="store_true", help="write pinned matches into ingredients.json")
    args = parser.parse_args()
    if not args.zip and not args.location:
        parser.error("pass --zip to find a store, or --location to pull prices")

    try:
        client = KrogerClient()
        if not args.location:
            for store in client.find_locations(args.zip, chain=args.chain):
                print(f"{store['location_id']}  {store['name']}  ({store['address']})")
            return 0
        ingredients = json.loads(INGREDIENTS_PATH.read_text(encoding="utf-8"))
        results = price_ingredients(client, args.location, ingredients)
    except KrogerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    today = date.today().isoformat()
    to_apply = {}
    print(f"{'ingredient':<26}{'current':>10}{'kroger':>10}  match")
    for key, result in results.items():
        current, price, product = ingredients[key]["price_per_unit"], result["price_per_unit"], result["product"]
        if product is None:
            print(f"{key:<26}{current:>10.5f}{'-':>10}  no match")
            continue
        flag = "" if result["pinned"] else "  [unpinned, review]"
        sale = " sale" if product["on_sale"] else ""
        print(f"{key:<26}{current:>10.5f}{price:>10.5f}  {product['description']} ({product['size']}, ${product['price']:.2f}{sale}){flag}")
        if result["pinned"]:
            source = f"Kroger API, store {args.location}, {today}, UPC {product['upc']}, {product['size']} at ${product['price']:.2f}"
            to_apply[key] = (price, source)

    SNAPSHOT_DIR.mkdir(exist_ok=True)
    out_path = SNAPSHOT_DIR / f"kroger_{args.location}_{today}.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path.relative_to(REPO_ROOT)}")

    if args.apply:
        text = INGREDIENTS_PATH.read_text(encoding="utf-8")
        INGREDIENTS_PATH.write_text(apply_prices(text, to_apply), encoding="utf-8")
        skipped = len(results) - len(to_apply)
        print(f"applied {len(to_apply)} price(s) to {INGREDIENTS_PATH.relative_to(REPO_ROOT)}" + (f", skipped {skipped} unpinned" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
