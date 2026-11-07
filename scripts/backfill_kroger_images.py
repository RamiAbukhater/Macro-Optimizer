"""Add each catalog product's photo URL to kroger_items.json without touching anything else.

build_kroger_catalog.py now captures ``image_url`` on every rebuild, but a
rebuild also refreshes every price and can drop or add products. This
fills in just the image URL for the catalog as it stands: it re-runs the
same search terms (one call per term, matching results back by UPC), then
looks up any product still missing a photo one at a time. Run from the
repo root:

    py scripts/backfill_kroger_images.py --location 70300108

Only URLs are written, never image bytes. Kroger's API terms forbid
keeping permanent copies of API content, so the web app hotlinks the
image from Kroger's CDN instead of re-hosting it.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from macro_optimizer.kroger import KrogerClient, KrogerError, product_image_url

sys.path.insert(0, str(Path(__file__).parent))
from build_kroger_catalog import CATALOG_PATH, CATALOG_TERMS  # noqa: E402
from pull_kroger_prices import REPO_ROOT, load_env_file  # noqa: E402

_UPC_PATTERN = re.compile(r"UPC (\d+)")


def row_upc(row: dict) -> str:
    """The UPC build_kroger_catalog.py recorded in a row's ``notes``, or ""."""
    match = _UPC_PATTERN.search(row.get("notes") or "")
    return match.group(1) if match else ""


def fill_image_urls(rows: list, images_by_upc: dict) -> int:
    """Set ``image_url`` on each row whose UPC has a known image. Returns how many rows got one."""
    filled = 0
    for row in rows:
        url = images_by_upc.get(row_upc(row))
        if url:
            row["image_url"] = url
            filled += 1
    return filled


def main() -> int:
    load_env_file()
    # A worktree checkout has no .env of its own; fall back to the main
    # checkout's, which git worktrees share a parent directory with.
    for parent in REPO_ROOT.parents:
        load_env_file(parent / ".env")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--location", required=True, help="Kroger locationId the catalog was built from")
    args = parser.parse_args()

    rows = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    wanted = {row_upc(r) for r in rows} - {""}
    images = {}
    try:
        client = KrogerClient()
        for term in CATALOG_TERMS:
            for product in client.search_products(term, args.location, limit=50):
                upc = product.get("upc") or product.get("productId")
                if upc in wanted and upc not in images:
                    url = product_image_url(product)
                    if url:
                        images[upc] = url
        missing = sorted(wanted - set(images))
        print(f"search pass: {len(images)} of {len(wanted)} products have a photo, looking up {len(missing)} more")
        for upc in missing:
            product = client.get_product(upc, args.location)
            url = product_image_url(product or {})
            if url:
                images[upc] = url
    except KrogerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    filled = fill_image_urls(rows, images)
    CATALOG_PATH.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{filled} of {len(rows)} rows now have image_url -> {CATALOG_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
