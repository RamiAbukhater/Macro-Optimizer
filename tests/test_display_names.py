import json

import pytest

from macro_optimizer.display_names import (
    clean_display_name, display_name_for, label, load_overrides, OVERRIDES_PATH,
)
from macro_optimizer.schema import FIELDS, FoodItem


@pytest.mark.parametrize("title, brand, expected", [
    # Brand from Kroger's own brand field, and the house brands.
    ("Kroger® Hawaiian Bagels", "Kroger", "Hawaiian Bagels"),
    ("Boar's Head Grab N Go Handcrafted Pastrami Swiss on Rye Sandwich", "Boar's Head", "Pastrami Swiss on Rye Sandwich"),
    ("Boar’s Head Golden Classic Chicken Breast", "Boar's Head", "Golden Classic Chicken Breast"),
    ("Simple Truth® Slivered Blanched Almonds", "Simple Truth Organic", "Slivered Blanched Almonds"),
    ("Private Selection® Grilled Shrimp Dumplings by Zenshi™", "Private Selection", "Grilled Shrimp Dumplings"),
    ("Thomas' Pumpkin Spice Bagels", "Thomas", "Pumpkin Spice Bagels"),
    ("Bush's Kidney Beans in a Mild Chili Sauce 16 oz", "Bush's Best", "Kidney Beans in a Mild Chili Sauce"),
    ("Chicken of the Sea® Old Bay Seasoning Wild Caught Light Tuna Packet", "Chicken of the Sea",
     "Old Bay Seasoning Wild Caught Light Tuna"),
    # Trademarked sub-brands.
    ("Frigo® Cheese Heads™ Light Mozzarella String Cheese", "Frigo", "Light Mozzarella String Cheese"),
    ("Dole® Fruit Bowls® Mandarin Oranges In 100% Fruit Juice Cups", "Dole", "Mandarin Oranges in 100% Fruit Juice Cups"),
    ("ACTUAL VEGGIES® California Garden Veggie Burger", "Actual Veggies", "California Garden Veggie Burger"),
    # Counts, sizes, packaging, marketing filler.
    ("Guerrero Caseras Flour Tortillas, Soft Taco Size, 10 Count", "Guerrero", "Caseras Flour Tortillas"),
    ("Tropicana Light Orange Juice Beverage, No Pulp, Calcium and Vitamin D", "Tropicana", "Light Orange Juice Beverage, No Pulp"),
    ("Chicken of the Sea Chunk Light Tuna In Water 4 - 5 oz Cans", "Chicken of the Sea", "Chunk Light Tuna in Water"),
    ("Kroger® 2% Reduced Fat Milk 32 fl. oz. Bottle", "Kroger", "2% Reduced Fat Milk"),
    ("Kroger® Peanut Butter Creamy BIG Deal!", "Kroger", "Peanut Butter Creamy"),
    ("Horizon Organic Shelf Stable 1% Lowfat Chocolate Milk", "Horizon Organic", "1% Lowfat Chocolate Milk"),
    ("Creamy Peanut Butter - Kroger - 16 Oz", "Kroger", "Creamy Peanut Butter"),
    ("Ralphs® Skim Milk Gallon", "Ralphs", "Skim Milk"),
    # Shouted titles.
    ("TINA'S® RED HOT BEEF BURRITO 2/16/4", "Tina's", "Red Hot Beef Burrito"),
    # Words that look like filler but aren't: "New England" keeps "New".
    ("Progresso™ Rich & Hearty New England Clam Chowder Canned Soup", "Progresso",
     "Rich & Hearty New England Clam Chowder Canned Soup"),
])
def test_cleans_real_catalog_titles(title, brand, expected):
    assert clean_display_name(title, brand)[0] == expected


@pytest.mark.parametrize("title, brand, expected", [
    # The brand is the food; stripping it would leave a bare "Cereal".
    ("Cap'n Crunch® Cereal", "Cap'N Crunch", "Cap'n Crunch Cereal"),
    ("Fruity Pebbles Cereal", "Fruity Pebbles", "Fruity Pebbles Cereal"),
    # Only the parent company goes when the line name is what the food is called.
    ("General Mills Cocoa Puffs™ Cereal", "Cocoa Puffs", "Cocoa Puffs Cereal"),
    # The whole title is the brand.
    ("Mike's Hot Honey", "Mike's Hot Honey", "Mike's Hot Honey"),
    # A shortened brand needs a (R)/TM mark to match, so "Fresh" survives here.
    ("Fresh Spinach Salad Kit", "Fresh Express", "Fresh Spinach Salad Kit"),
])
def test_guards_against_over_cleaning(title, brand, expected):
    assert clean_display_name(title, brand)[0] == expected


def test_reports_which_rules_fired():
    _, hits = clean_display_name("Guerrero Caseras Flour Tortillas, Soft Taco Size, 10 Count", "Guerrero")
    assert hits == ["brand", "count_size"]
    assert clean_display_name("Plain Kefir", "")[1] == []


def test_overrides_win_and_are_well_formed():
    overrides = load_overrides()
    assert overrides, "overrides file should load"
    raw = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    assert all(k.startswith("_") or k.isdigit() for k in raw)
    assert all(isinstance(v, str) and v.strip() for v in overrides.values())
    # Kraft Super Mario Mac & Cheese: a licensing name no rule can know about.
    assert display_name_for("Kraft Super Mario Microwavable Mac & Cheese Cups", "Kraft", "0002100005317") == "Mac & Cheese Cups"
    assert display_name_for("Kraft Super Mario Microwavable Mac & Cheese Cups", "Kraft") == "Super Mario Mac & Cheese Cups"


def test_display_name_is_a_schema_field_with_fallback():
    assert "display_name" in FIELDS
    item = FoodItem(name="Kroger® Hawaiian Bagels", category="Bakery", source="Kroger API",
                    price=0.5, calories=1, protein=1, carbs=1, fat=0)
    assert item.display_name == ""
    assert label(item) == "Kroger® Hawaiian Bagels"
    assert label({"name": "Raw", "display_name": "Clean"}) == "Clean"
    assert label({"name": "Overnight Oats"}) == "Overnight Oats"


def test_catalog_rows_all_carry_a_display_name():
    from macro_optimizer.database import load_market_items
    rows = load_market_items()
    assert all(row.get("display_name") for row in rows)
    # Raw titles stay the unique identity key.
    assert len({row["name"] for row in rows}) == len(rows)
    assert not any(sym in row["display_name"] for row in rows for sym in "®™")
