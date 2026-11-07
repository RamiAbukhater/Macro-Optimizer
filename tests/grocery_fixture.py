"""A synthetic grocery catalog for tests, shaped like Kroger Products API responses.

Every product here is made up: invented brands, round-number labels and
prices in a realistic range. No real Kroger data is committed (their terms
restrict storing it, see GROCERY_DATA.md). The products go through the same
``to_food_item`` path live Kroger products do, so tests exercise the real
parsing, serving math and dietary screens.

Each kind comes in three brands with slightly different prices and labels,
the shape the optimizer's per-kind cap exists for.
"""

import re

from macro_optimizer import grocery_db

LOCATION_ID = "70300108"
STORE_NAME = "Fixture Market"
BRANDS = ["Northfield", "Sunvale", "Harbor & Pine"]

# kind, product name, category, (cal, protein, carbs, fat, fiber, sodium mg),
# serving (quantity, unit code), package size, regular price, servings per package, ingredients, allergens
G, ML = "GRM", "MLT"
SPECS = [
    # Vegan protein and staples
    ("tofu", "Extra Firm Tofu", "Natural & Organic", (90, 10, 2, 5, 1, 10), (85, G), "14 oz", 2.49, 5,
     "Water, Soybeans, Calcium Sulfate.", ["soybean"]),
    ("veggie burger", "Plant Protein Burger Patties", "Frozen", (130, 15, 8, 4, 4, 380), (71, G), "8 oz", 4.99, 3,
     "Water, Pea Protein, Canola Oil, Rice Starch.", []),
    ("protein shake", "Plant Protein Shake", "Beverages", (160, 20, 8, 5, 3, 300), (330, ML), "4 ct", 9.99, 1,
     "Water, Pea Protein, Sunflower Oil, Cane Sugar.", []),
    ("hummus", "Classic Hummus", "Deli", (70, 2, 4, 5, 1, 130), (28, G), "10 oz", 3.99, 10,
     "Chickpeas, Tahini, Olive Oil, Lemon Juice.", ["sesame"]),
    ("peanut butter", "Creamy Peanut Butter", "Pantry", (190, 7, 7, 16, 2, 140), (32, G), "16 oz", 2.99, 14,
     "Peanuts, Salt.", ["peanuts"]),
    ("almonds", "Roasted Almonds", "Snacks", (170, 6, 6, 15, 3, 0), (28, G), "16 oz", 6.99, 16,
     "Almonds.", ["almonds"]),
    ("instant oatmeal", "Original Instant Oatmeal", "Breakfast", (150, 5, 27, 3, 4, 0), (40, G), "18 oz", 3.49, 13,
     "Whole Grain Rolled Oats.", []),
    ("whole wheat bread", "100% Whole Wheat Bread", "Bakery", (110, 5, 20, 1.5, 3, 180), (43, G), "24 oz", 3.29, 16,
     "Whole Wheat Flour, Water, Yeast, Salt.", ["wheat"]),
    ("tortillas", "Flour Tortillas", "Bakery", (140, 4, 24, 3.5, 1, 330), (45, G), "17.5 oz", 2.99, 11,
     "Enriched Wheat Flour, Water, Vegetable Shortening.", ["wheat"]),
    ("bagels", "Plain Bagels", "Bakery", (270, 10, 53, 1.5, 2, 430), (95, G), "20 oz", 3.49, 6,
     "Enriched Wheat Flour, Water, Sugar, Yeast.", ["wheat"]),
    ("microwave rice", "Microwave Jasmine Rice", "Pantry", (220, 5, 44, 2, 1, 10), (125, G), "8.8 oz", 2.29, 2,
     "Cooked Jasmine Rice, Sunflower Oil.", []),
    ("canned soup", "Lentil Vegetable Soup", "Pantry", (180, 9, 30, 2, 8, 590), (245, G), "18.6 oz", 2.49, 2,
     "Water, Lentils, Carrots, Celery, Tomato Paste.", []),
    ("instant ramen", "Soy Sauce Ramen", "Pantry", (190, 5, 26, 7, 1, 790), (43, G), "3 oz", 0.59, 2,
     "Wheat Flour, Palm Oil, Salt, Soy Sauce.", ["wheat", "soybean"]),
    ("baby carrots", "Baby Carrots", "Produce", (35, 1, 8, 0, 2, 65), (85, G), "16 oz", 1.99, 5,
     "Carrots.", []),
    ("orange juice", "Orange Juice", "Beverages", (110, 2, 26, 0, 0, 0), (240, ML), "52 fl oz", 3.99, 6.5,
     "Orange Juice.", []),
    ("applesauce", "Unsweetened Applesauce Cups", "Pantry", (50, 0, 13, 0, 1, 0), (113, G), "24 oz", 2.79, 6,
     "Apples, Water, Ascorbic Acid.", []),
    ("fruit cup", "Diced Peaches in Juice", "Pantry", (60, 1, 15, 0, 1, 10), (113, G), "16 oz", 2.99, 4,
     "Peaches, Pear Juice, Ascorbic Acid.", []),
    ("cereal", "Toasted Oat Cereal", "Breakfast", (140, 5, 29, 2.5, 4, 190), (39, G), "18 oz", 3.99, 13,
     "Whole Grain Oats, Corn Starch, Sugar, Salt.", []),
    ("granola", "Gluten Free Honey Almond Granola", "Breakfast", (210, 5, 32, 7, 3, 60), (55, G), "12 oz", 4.49, 6,
     "Gluten Free Oats, Honey, Almonds, Canola Oil.", ["almonds"]),
    ("trail mix", "Gluten Free Trail Mix", "Snacks", (160, 5, 13, 10, 2, 45), (30, G), "26 oz", 8.99, 24,
     "Peanuts, Raisins, Almonds, Cashews.", ["peanuts", "almonds", "cashews"]),
    # Vegetarian
    ("greek yogurt cup", "Nonfat Plain Greek Yogurt", "Dairy", (90, 16, 6, 0, 0, 60), (150, G), "5.3 oz", 1.25, 1,
     "Cultured Pasteurized Nonfat Milk.", ["milk"]),
    ("cottage cheese", "Lowfat Cottage Cheese", "Dairy", (90, 12, 5, 2.5, 0, 390), (113, G), "24 oz", 3.99, 6,
     "Cultured Skim Milk, Cream, Salt.", ["milk"]),
    ("string cheese", "Mozzarella String Cheese", "Dairy", (80, 7, 1, 6, 0, 200), (28, G), "10 oz", 5.49, 10,
     "Pasteurized Part-Skim Milk, Cheese Culture, Salt, Enzymes.", ["milk"]),
    ("milk", "2% Reduced Fat Milk", "Dairy", (120, 8, 12, 5, 0, 120), (240, ML), "1 gal", 4.29, 16,
     "Reduced Fat Milk, Vitamin A Palmitate, Vitamin D3.", ["milk"]),
    ("kefir", "Plain Lowfat Kefir", "Dairy", (110, 11, 12, 2, 0, 125), (240, ML), "32 fl oz", 4.49, 4,
     "Cultured Pasteurized Lowfat Milk, Pectin.", ["milk"]),
    ("hard boiled eggs", "Hard Boiled Eggs", "Dairy", (140, 12, 1, 10, 0, 140), (100, G), "6 ct", 3.99, 3,
     "Eggs.", ["eggs"]),
    ("protein bar", "Chocolate Peanut Butter Protein Bars", "Snacks", (200, 20, 22, 7, 5, 210), (60, G), "4 ct", 7.99, 1,
     "Milk Protein Isolate, Peanut Butter, Soluble Corn Fiber, Cocoa.", ["milk", "peanuts"]),
    ("granola bar", "Oats & Honey Granola Bars", "Snacks", (190, 4, 29, 7, 2, 160), (42, G), "8.9 oz", 3.49, 6,
     "Whole Grain Oats, Sugar, Canola Oil, Honey.", []),
    ("frozen burrito", "Bean & Cheese Burrito", "Frozen", (290, 9, 45, 8, 5, 610), (142, G), "5 oz", 1.29, 1,
     "Flour Tortilla (Wheat Flour), Beans, Cheddar Cheese (Milk).", ["wheat", "milk"]),
    ("mac and cheese cup", "Microwave Mac & Cheese Cup", "Pantry", (220, 7, 40, 3.5, 1, 590), (58, G), "4 ct", 4.49, 1,
     "Enriched Macaroni (Wheat Flour), Whey, Milk, Cheese Culture.", ["wheat", "milk"]),
    # Meat, fish and pork
    ("deli turkey", "Oven Roasted Turkey Breast", "Deli", (60, 10, 2, 1, 0, 480), (56, G), "9 oz", 5.99, 4.5,
     "Turkey Breast, Water, Salt, Cane Sugar.", []),
    ("deli chicken", "Rotisserie Seasoned Chicken Breast", "Deli", (60, 11, 1, 1.5, 0, 450), (56, G), "8 oz", 5.49, 4,
     "Chicken Breast, Water, Salt, Spices.", []),
    ("canned tuna", "Chunk Light Tuna in Water", "Pantry", (90, 20, 0, 1, 0, 250), (85, G), "5 oz", 1.29, 1.5,
     "Light Tuna, Water, Salt.", ["fish"]),
    ("canned chicken", "Chunk Chicken Breast in Water", "Pantry", (70, 15, 0, 1, 0, 230), (56, G), "12.5 oz", 4.49, 6,
     "Chicken Breast Meat, Water, Salt.", []),
    ("beef jerky", "Original Beef Jerky", "Snacks", (80, 13, 6, 1, 0, 590), (28, G), "3.25 oz", 6.99, 3,
     "Beef, Water, Sugar, Soy Sauce.", ["soybean"]),
    ("frozen meal", "Grilled Chicken & Rice Bowl", "Frozen", (300, 20, 40, 7, 3, 650), (283, G), "10 oz", 3.49, 1,
     "Cooked Rice, Grilled Chicken, Broccoli, Sauce.", []),
    ("frozen pizza", "Pepperoni Pizza", "Frozen", (320, 13, 34, 15, 2, 720), (128, G), "17 oz", 5.99, 4,
     "Wheat Flour, Mozzarella Cheese (Milk), Pepperoni (Pork).", ["wheat", "milk"]),
    ("frozen breakfast sandwich", "Sausage Egg & Cheese Muffin", "Frozen", (330, 15, 27, 18, 1, 790), (125, G), "4 ct", 6.99, 1,
     "English Muffin (Wheat), Pork Sausage, Egg, Cheese (Milk).", ["wheat", "eggs", "milk"]),
    ("frozen chicken strips", "Breaded Chicken Strips", "Frozen", (190, 13, 13, 9, 1, 520), (84, G), "25 oz", 8.99, 8,
     "Chicken Breast, Wheat Flour, Soybean Oil.", ["wheat"]),
    ("canned chili", "Beef Chili with Beans", "Pantry", (260, 16, 25, 11, 7, 990), (247, G), "15 oz", 2.29, 1.7,
     "Water, Beef, Red Beans, Tomato Paste.", []),
    ("salad kit", "Caesar Salad Kit", "Produce", (140, 3, 8, 11, 2, 270), (85, G), "10.4 oz", 3.99, 3.5,
     "Romaine Lettuce, Dressing (Soybean Oil, Anchovies), Croutons (Wheat).", ["fish", "wheat"]),
    ("deli sandwich", "Ham & Swiss Sandwich", "Deli", (380, 22, 38, 15, 2, 1180), (198, G), "7 oz", 5.99, 1,
     "Wheat Bread, Ham, Swiss Cheese (Milk).", ["wheat", "milk"]),
    ("tuna pouch", "Lemon Pepper Tuna Pouch", "Pantry", (80, 17, 0, 1, 0, 300), (74, G), "2.6 oz", 1.49, 1,
     "Light Tuna, Water, Lemon Pepper Seasoning.", ["fish"]),
]

SALE = {"bagels": 2.49}  # one product per kind on sale, to exercise regular-vs-promo pricing


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _product(kind_index: int, brand_index: int, spec) -> dict:
    kind, name, category, macros, serving, size, price, spp, ingredients, allergens = spec
    brand = BRANDS[brand_index]
    cal, protein, carbs, fat, fiber, sodium = macros
    # Brands differ a little: a pricier brand with a bit more of everything.
    price = round(price * (0.9, 1.0, 1.15)[brand_index], 2)
    cal, carbs = cal + 10 * brand_index, carbs + 2.5 * brand_index
    upc = f"99{kind_index:04d}{brand_index:07d}"
    description = f"{brand} {name}"
    unit_names = {G: "Gram", ML: "Milliliter"}
    promo = SALE.get(kind) if brand_index == 0 else 0
    return {
        "productId": upc,
        "upc": upc,
        "brand": brand,
        "description": description,
        "categories": [category],
        "productPageURI": f"/p/{_slug(description)}/{upc}?cid=fixture",
        "items": [{
            "price": {"regular": price, "promo": promo or 0},
            "size": size,
            "soldBy": "UNIT",
            "inventory": {"stockLevel": "HIGH"},
        }],
        "nutritionInformation": [{
            "nutrients": [
                {"code": "ENER-", "quantity": cal}, {"code": "PRO-", "quantity": protein},
                {"code": "CHO-", "quantity": carbs}, {"code": "FAT", "quantity": fat},
                {"code": "FIBTSW", "quantity": fiber}, {"code": "NA", "quantity": sodium},
            ],
            "servingSize": {"quantity": serving[0], "unitOfMeasure": {"code": serving[1], "name": unit_names[serving[1]]}},
            "servingsPerPackage": {"value": spp},
            "ingredientStatement": ingredients,
        }],
        "allergens": [{"levelOfContainmentName": "Contains", "name": a} for a in allergens],
        "images": [{
            "perspective": "front", "featured": True,
            "sizes": [{"size": s, "url": f"https://www.kroger.com/product/images/{s}/front/{upc}"}
                      for s in ("medium", "large")],
        }],
        "_kind": kind,
    }


PRODUCTS = {
    p["upc"]: p
    for k, spec in enumerate(SPECS)
    for p in (_product(k, b, spec) for b in range(len(BRANDS)))
}


def fetcher(upcs, location_id):
    """Stands in for market's live Kroger fetch."""
    return {u: PRODUCTS[u] for u in upcs if u in PRODUCTS}


def seed(conn, seen_at: str = None) -> None:
    """A store plus every fixture product, as a refresh run would have left them."""
    grocery_db.upsert_store(conn, LOCATION_ID, "FIXTURE", STORE_NAME, "1 Test Way")
    for upc, product in PRODUCTS.items():
        grocery_db.record_seen(conn, upc, product["_kind"], seen_at)
    conn.commit()
