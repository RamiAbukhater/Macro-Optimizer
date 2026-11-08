import pytest

from macro_optimizer.database import load_food_db
from macro_optimizer.kroger import parse_nutrition, servings_in_package, to_food_item
from macro_optimizer.optimizer import KIND_DAILY_CAP, run_optimizer
from macro_optimizer.schema import FoodItem


def _nutrient(code, quantity=None):
    row = {"code": code}
    if quantity is not None:
        row["quantity"] = quantity
    return row


def _label(calories=200, protein=12, carbs=23, fat=8, serving=(52, "GRM", "Gram"), servings_per_package=5, **extra):
    label = {
        "nutrients": [
            _nutrient("ENER-", calories), _nutrient("PRO-", protein), _nutrient("CHO-", carbs),
            _nutrient("FAT", fat), _nutrient("FIBTSW", 5), _nutrient("NA", 190),
        ],
        "servingsPerPackage": {"value": servings_per_package},
        "ingredientStatement": "Dates, Egg Whites, Cashews, Almonds.",
        **extra,
    }
    if serving:
        label["servingSize"] = {"quantity": serving[0], "unitOfMeasure": {"code": serving[1], "name": serving[2]}}
    return label


def _product(label=None, size="9.1 oz", price=10.99, promo=0, description="RXBAR Chocolate Sea Salt Protein Bars", **extra):
    return {
        "upc": "0085777700401",
        "description": description,
        "categories": ["Snacks"],
        "productPageURI": "/p/rxbar-chocolate-sea-salt/0085777700401?cid=tracking",
        "items": [{"price": {"regular": price, "promo": promo}, "size": size}],
        "nutritionInformation": [label if label is not None else _label()],
        **extra,
    }


def test_to_food_item_prices_one_serving_from_package_weight():
    item = to_food_item(_product(), "70300108", "Ralphs La Jolla", kind="protein bar")
    assert isinstance(item, FoodItem)
    # 9.1 oz is 258 g, 4.96 servings of 52 g, not the label's rounded 5.
    assert item.price == pytest.approx(10.99 / (257.98 / 52), abs=0.01)
    assert (item.calories, item.protein, item.carbs, item.fat) == (200, 12, 23, 8)
    assert (item.fiber, item.sodium) == (5, 190)
    assert item.serving_unit == "52 gram"
    assert item.kind == "protein bar"
    assert item.confidence == "external_api"
    assert item.location == "Ralphs La Jolla"
    assert item.source_url == "https://www.kroger.com/p/rxbar-chocolate-sea-salt/0085777700401"
    assert "UPC 0085777700401" in item.notes and "$10.99" in item.notes


def test_to_food_item_keeps_the_raw_title_and_adds_a_clean_display_name():
    item = to_food_item(_product(description="Frigo® Cheese Heads™ Light Mozzarella String Cheese, 12 ct", brand="Frigo"))
    assert item.name == "Frigo® Cheese Heads™ Light Mozzarella String Cheese, 12 ct"
    assert item.display_name == "Light Mozzarella String Cheese"


def test_to_food_item_plans_with_the_regular_price_during_a_sale():
    item = to_food_item(_product(promo=8.99))
    assert item.price == pytest.approx(10.99 / (257.98 / 52), abs=0.01)
    assert "on sale for $8.99 (sale price not used)" in item.notes


def test_to_food_item_uses_the_sale_price_when_asked_and_says_so():
    item = to_food_item(_product(promo=8.99), use_promo=True)
    assert item.price == pytest.approx(8.99 / (257.98 / 52), abs=0.01)
    assert "on sale from $10.99" in item.notes


def test_multipack_labelled_one_serving_is_split_by_count():
    label = _label(serving=None, servings_per_package=1)
    item = to_food_item(_product(label=label, size="4 ct", price=14.99))
    assert item.price == pytest.approx(14.99 / 4, abs=0.01)


def test_servings_fall_back_to_the_label_when_weights_are_not_comparable():
    record = {"unit": "ml", "quantity": 3785.41}
    assert servings_in_package(record, {"serving_grams": None, "servings_per_package": 16}) == 16
    assert servings_in_package(record, {"serving_grams": None, "servings_per_package": None}) is None


def test_a_nutrient_row_without_a_quantity_reads_as_zero():
    label = _label(calories=150, protein=5, carbs=27, fat=2.5)
    label["nutrients"] = [row for row in label["nutrients"] if row["code"] != "NA"] + [_nutrient("NA")]
    assert parse_nutrition(_product(label=label))["sodium"] == 0.0


@pytest.mark.parametrize("missing", ["ENER-", "PRO-", "CHO-", "FAT"])
def test_a_label_missing_a_core_macro_is_rejected(missing):
    label = _label()
    label["nutrients"] = [row for row in label["nutrients"] if row["code"] != missing]
    assert parse_nutrition(_product(label=label)) is None
    assert to_food_item(_product(label=label)) is None


def test_a_label_whose_macros_cannot_add_up_to_its_calories_is_rejected():
    assert parse_nutrition(_product(label=_label(calories=200, protein=90, carbs=0, fat=0))) is None
    assert parse_nutrition(_product(label=_label(calories=400, protein=2, carbs=3, fat=0))) is None


def test_products_without_a_label_or_a_price_are_skipped():
    assert to_food_item({**_product(), "nutritionInformation": []}) is None
    assert to_food_item({**_product(), "items": [{"size": "9.1 oz"}]}) is None
    # No package weight and no servings-per-package: nothing to divide the price by.
    assert to_food_item(_product(label=_label(serving=None, servings_per_package=None), size="1 bunch")) is None


def _flags(description, ingredients, allergens=()):
    product = _product(
        label=_label(ingredientStatement=ingredients), description=description,
        allergens=[{"levelOfContainmentName": level, "name": name} for level, name in allergens],
    )
    return to_food_item(product)


def test_dietary_flags_match_whole_words_only():
    yogurt = _flags("Vanilla Greek Yogurt", "Cultured Milk, Graham Cracker Crumble.", [("Contains", "Milk")])
    assert yogurt.vegetarian is True and yogurt.vegan is False
    assert yogurt.contains_pork is False  # "graham" is not "ham"
    assert yogurt.allergens == ["milk"]


def test_dietary_flags_catch_meat_fish_and_pork():
    caesar = _flags("Caesar Salad Kit", "Romaine Lettuce, Dressing (Soybean Oil, Anchovies).")
    assert caesar.vegetarian is False
    pizza = _flags("Pepperoni Pizza", "Wheat Flour, Cheese, Pepperoni.")
    assert pizza.vegetarian is False and pizza.contains_pork is True
    tofu = _flags("Extra Firm Tofu", "Water, Soybeans, Calcium Sulfate.", [("Free from", "Milk")])
    assert tofu.vegetarian is True and tofu.vegan is True
    assert tofu.allergens == []


def test_dietary_flags_stay_false_without_an_ingredient_statement():
    unknown = _flags("Mystery Snack", "")
    assert unknown.vegetarian is False and unknown.vegan is False


def test_kind_cap_limits_servings_across_brands_of_the_same_food():
    def spread(name, kind):
        return FoodItem(name=name, category="Test", source="Test", price=0.10, calories=190,
                        protein=7, carbs=7, fat=16, max_daily=2, kind=kind)

    filler = FoodItem(name="Rice Bowl", category="Test", source="Test", price=2.0, calories=600,
                      protein=40, carbs=80, fat=10, max_daily=6)
    goals = {"calories": 1500, "protein": 60, "carbs": 60, "fat": 60}

    brands = [spread(f"Peanut Butter Brand {n}", "peanut butter") for n in range(4)]
    capped, _ = run_optimizer(load_food_db(extra_items=brands + [filler], include_market=False),
                              goals, budget_cap=30, verbose=False)
    peanut_butter = capped[capped["name"].str.startswith("Peanut Butter")]
    assert peanut_butter["servings"].sum() == KIND_DAILY_CAP

    # The same four items with no kind are unrelated foods, and the cheapest plan leans on all of them.
    loose = [spread(f"Peanut Butter Brand {n}", "") for n in range(4)]
    uncapped, _ = run_optimizer(load_food_db(extra_items=loose + [filler], include_market=False),
                                goals, budget_cap=30, verbose=False)
    assert uncapped[uncapped["name"].str.startswith("Peanut Butter")]["servings"].sum() > KIND_DAILY_CAP


def test_shipped_catalog_rows_carry_a_kind_and_a_real_price():
    df = load_food_db()
    assert (df["kind"] != "").all()
    assert (df["price"] > 0).all()
