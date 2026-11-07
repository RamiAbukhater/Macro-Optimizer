import importlib.util
import sys
from pathlib import Path

from macro_optimizer.database import load_market_items
from macro_optimizer.kroger import product_image_url, to_food_item

from test_kroger_catalog import _product

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("backfill_kroger_images", SCRIPTS / "backfill_kroger_images.py")
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def _image(perspective, sizes, featured=False):
    return {
        "perspective": perspective,
        "featured": featured,
        "sizes": [{"size": s, "url": f"https://img/{perspective}/{s}"} for s in sizes],
    }


def test_product_image_url_prefers_the_front_shot_at_large_size():
    product = {"images": [_image("back", ["large"], featured=True), _image("front", ["xlarge", "large", "small"])]}
    assert product_image_url(product) == "https://img/front/large"


def test_product_image_url_falls_back_to_featured_then_any_size():
    product = {"images": [_image("left", ["thumbnail"]), _image("top", ["small"], featured=True)]}
    assert product_image_url(product) == "https://img/top/small"


def test_product_image_url_is_empty_when_kroger_has_no_photo():
    assert product_image_url({}) == ""
    assert product_image_url({"images": [{"perspective": "front", "sizes": []}]}) == ""


def test_to_food_item_keeps_the_photo_url():
    product = _product()
    product["images"] = [_image("front", ["medium", "large"])]
    assert to_food_item(product).image_url == "https://img/front/large"
    assert to_food_item(_product()).image_url == ""


def test_backfill_matches_rows_to_images_by_the_upc_in_notes():
    rows = [
        {"name": "a", "notes": "UPC 0001. 1 ct package at $1.00."},
        {"name": "b", "notes": "UPC 0002. 1 ct package at $1.00."},
        {"name": "c", "notes": "no upc recorded"},
    ]
    filled = backfill.fill_image_urls(rows, {"0001": "https://img/1"})
    assert filled == 1
    assert rows[0]["image_url"] == "https://img/1"
    assert "image_url" not in rows[1] and "image_url" not in rows[2]


def test_shipped_catalog_links_photos_on_krogers_cdn_rather_than_copies():
    # Kroger's API terms bar keeping permanent copies of API content, so the
    # catalog may only carry links back to Kroger, never re-hosted files.
    urls = [row.get("image_url", "") for row in load_market_items()]
    present = [u for u in urls if u]
    assert len(present) >= 0.9 * len(urls)
    assert all(u.startswith("https://www.kroger.com/product/images/") for u in present)
