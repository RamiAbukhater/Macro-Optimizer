from macro_optimizer.kroger import product_image_url, to_food_item

from test_kroger_catalog import _product

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
