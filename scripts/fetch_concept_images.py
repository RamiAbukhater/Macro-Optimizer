"""Fetch openly licensed photos for the swipe-survey concept cards from Wikimedia Commons.

For each concept in ``src/macro_optimizer/data/taste_concepts.json`` this
finds a photo, either the lead image of the dish's English Wikipedia article
or, when that's missing, unsuitable or already used, the top Commons search
hit for a query. It only accepts files hosted on Commons under CC0, public
domain, CC BY or CC BY-SA, and only JPEG/PNG photos at least MIN_WIDTH wide.

Two steps, so a person can look at every photo before it ships:

    py scripts/fetch_concept_images.py fetch --out <empty staging dir>
        downloads a 640px rendition per concept into the staging dir and
        writes manifest.json there (author, license, source page per file).

    py scripts/fetch_concept_images.py install --staging <dir> [--skip id,id]
        copies the approved files to webapp/static/img/concepts/ and writes
        each concept's ``image`` record into taste_concepts.json. Concepts
        that are skipped or have no photo keep ``image: null`` and fall back
        to the styled placeholder.

``OVERRIDES`` pins a specific Commons file for a concept when the automatic
pick was wrong; it goes through the same license check.
"""

import argparse
import html
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONCEPTS_PATH = ROOT / "src" / "macro_optimizer" / "data" / "taste_concepts.json"
INSTALL_DIR = ROOT / "webapp" / "static" / "img" / "concepts"
# webapp/app.py serves webapp/static/ at the site root (static_url_path="").
PUBLIC_PREFIX = "/img/concepts/"

USER_AGENT = "AutoEat-dev/0.1 (meal planner prototype; concept card photos)"
WIDTH = 640
MIN_WIDTH = 500
PACE_SECONDS = 1.0
ACCEPTED_LICENSE = re.compile(r"^(cc0|cc-zero|public domain|pd\b|pd-|cc[- ]by(-sa)?[- ]\d)", re.I)

# concept id -> (Wikipedia article for the lead image, Commons search fallback)
SOURCES = {
    "pancakes": ("Pancake", "pancakes stack syrup"),
    "french_toast": ("French toast", "french toast plate"),
    "scrambled_eggs_toast": ("Scrambled eggs", "scrambled eggs toast"),
    "bacon_and_eggs": ("Bacon and eggs", "bacon and eggs breakfast plate"),
    "breakfast_sandwich": ("Breakfast sandwich", "egg sausage breakfast sandwich"),
    "breakfast_burrito": ("Breakfast burrito", "breakfast burrito"),
    "yogurt_parfait": ("Parfait", "yogurt parfait granola"),
    "oatmeal_fruit": ("Porridge", "oatmeal berries bowl"),
    "cereal_milk": ("Breakfast cereal", "cereal bowl milk"),
    "avocado_toast": ("Avocado toast", "avocado toast"),
    "bagel_cream_cheese": ("Bagel and cream cheese", "bagel cream cheese"),
    "smoothie_bowl": ("Smoothie bowl", "smoothie bowl fruit"),
    "shakshuka": ("Shakshouka", "shakshuka pan"),
    "cheeseburger": ("Cheeseburger", "cheeseburger"),
    "grilled_chicken_veg": (None, "grilled chicken breast vegetables plate"),
    "bbq_ribs": ("Spare ribs", "barbecue pork ribs"),
    "fried_chicken": ("Fried chicken", "fried chicken"),
    "buffalo_wings": ("Buffalo wing", "buffalo wings"),
    "chicken_nuggets_fries": ("Chicken nugget", "chicken nuggets fries"),
    "steak_potatoes": ("Steak frites", "steak potatoes plate"),
    "meatloaf": ("Meatloaf", "meatloaf mashed potatoes"),
    "mac_and_cheese": ("Macaroni and cheese", "macaroni and cheese"),
    "grilled_cheese_soup": ("Cheese sandwich", "grilled cheese tomato soup"),
    "turkey_sandwich": ("Turkey sandwich", "turkey sandwich"),
    "tuna_sandwich": ("Tuna fish sandwich", "tuna salad sandwich"),
    "blt": ("BLT", "BLT sandwich"),
    "pulled_pork": ("Pulled pork", "pulled pork sandwich"),
    "hot_dog": ("Hot dog", "hot dog mustard"),
    "chicken_noodle_soup": ("Chicken soup", "chicken noodle soup"),
    "cobb_salad": ("Cobb salad", "cobb salad"),
    "chicken_caesar": ("Caesar salad", "chicken caesar salad"),
    "salmon_rice": (None, "salmon fillet rice plate"),
    "veggie_burger": ("Veggie burger", "veggie burger"),
    "rice_and_beans": ("Rice and beans", "rice and beans plate"),
    "chicken_tacos": ("Taco", "chicken tacos"),
    "fish_tacos": ("Fish taco", "fish tacos"),
    "beef_burrito": ("Burrito", "beef burrito"),
    "bean_cheese_burrito": (None, "bean burrito"),
    "burrito_bowl": (None, "burrito bowl rice beans"),
    "quesadilla": ("Quesadilla", "quesadilla"),
    "enchiladas": ("Enchilada", "enchiladas"),
    "nachos": ("Nachos", "nachos cheese"),
    "veggie_fajitas": ("Fajita", "vegetable fajitas peppers"),
    "beef_chili": ("Chili con carne", "chili con carne bowl"),
    "spaghetti_meatballs": ("Spaghetti and meatballs", "spaghetti meatballs"),
    "margherita_pizza": ("Pizza Margherita", "pizza margherita"),
    "pepperoni_pizza": (None, "pepperoni pizza"),
    "lasagna": ("Lasagna", "lasagna"),
    "fettuccine_alfredo": ("Fettuccine Alfredo", "fettuccine alfredo"),
    "chicken_parmesan": ("Chicken parmigiana", "chicken parmesan"),
    "pesto_pasta": (None, "pasta pesto"),
    "minestrone": ("Minestrone", "minestrone soup"),
    "italian_sub": ("Submarine sandwich", "italian sub sandwich"),
    "chicken_stir_fry": (None, "chicken stir fry vegetables"),
    "tofu_stir_fry": (None, "tofu stir fry"),
    "ramen": ("Ramen", "ramen bowl"),
    "pho": ("Pho", "pho bowl"),
    "sushi_rolls": ("Makizushi", "sushi rolls maki"),
    "poke_bowl": ("Poke (dish)", "poke bowl"),
    "pad_thai": ("Pad thai", "pad thai"),
    "chicken_curry": ("Chicken tikka masala", "chicken tikka masala"),
    "lentil_curry": ("Dal", "dal lentil curry"),
    "fried_rice": ("Fried rice", "egg fried rice"),
    "teriyaki_bowl": (None, "teriyaki chicken rice"),
    "orange_chicken": ("Orange chicken", "orange chicken"),
    "beef_broccoli": ("Beef and broccoli", "beef broccoli"),
    "bulgogi": ("Bulgogi", "bulgogi"),
    "dumplings": ("Jiaozi", "pork dumplings jiaozi"),
    "greek_salad": ("Greek salad", "greek salad"),
    "chicken_shawarma": ("Shawarma", "chicken shawarma wrap"),
    "falafel_wrap": ("Falafel", "falafel wrap"),
    "beef_kofta": ("Kofta", "kofta kebab"),
    "lentil_soup": ("Lentil soup", "lentil soup"),
    "grain_bowl": (None, "quinoa salad bowl"),
    "grilled_fish_lemon": (None, "grilled fish lemon plate"),
    "hummus_pita": ("Hummus", "hummus pita"),
    "chips_guacamole": ("Guacamole", "guacamole tortilla chips"),
    "apple_peanut_butter": (None, "apple slices peanut butter"),
    "mixed_nuts": ("Mixed nuts", "mixed nuts bowl"),
    "cheese_crackers": (None, "cheese and crackers plate"),
    "veggies_dip": ("Crudités", "crudites vegetables dip"),
    "fresh_fruit": (None, "fresh fruit bowl"),
    "jerky": ("Jerky", "beef jerky"),
    "hard_boiled_eggs": ("Boiled egg", "hard boiled eggs halved"),
    "cookies": ("Chocolate chip cookie", "chocolate chip cookies"),
    "potato_chips": ("Potato chip", "potato chips bowl"),
    "cottage_cheese_fruit": ("Cottage cheese", "cottage cheese fruit"),
    "protein_smoothie": ("Smoothie", "smoothie glass"),
    "glass_of_milk": ("Milk", "glass of milk"),
    "frozen_meals": ("TV dinner", "microwave meal tray"),
    "cook_scratch": (None, "chopping vegetables cutting board kitchen"),
    "grab_and_go": (None, "deli sandwiches display case"),
    "bars_shakes": ("Protein bar", "protein bars"),
    "canned_instant": (None, "canned soup can"),
}

# concept id -> exact Commons file title, for when the automatic pick was wrong.
# Picked by eye from Commons search results (scripts review, Nov 2026): the automatic
# pick was the wrong dish, a duplicate, or showed a brand. Concepts with no usable
# photo (apple_peanut_butter, canned_instant, grab_and_go) are left to --skip.
OVERRIDES: dict = {
    "grilled_chicken_veg": "File:Liat Portal for Foodie Disorder - Homemade Grilled Chicken Dinner.jpg",
    "turkey_sandwich": "File:Turkey and cheese sandwich.jpg",
    "fish_tacos": "File:Fish tacos food cabbage tortillas.jpg",
    "burrito_bowl": "File:Burrito Bowl - La Casa Restaurant - Sarah Stierch - 2021-09-29.jpg",
    "pepperoni_pizza": "File:Pepperoni Pizza from Fellini’s Pizza.jpg",
    "tofu_stir_fry": "File:Tofu stir-fry.jpeg",
    "poke_bowl": "File:Salmon Poke Bowl (S) with Spicy mayo sauce - Kitokito.jpg",
    "pho": "File:Beef Pho (M) - Milk No Sugar.jpg",
    "lentil_curry": "File:Dal Tadka Masala.png",
    "grain_bowl": "File:Quinoa Salad (5045982815).jpg",
    "cheese_crackers": "File:Cheddar cheese and Japanese rice crackers with black sesame and soy sauce - Massachusetts.jpg",
    "fresh_fruit": "File:Bananas on black background 02.jpg",
    "smoothie_bowl": "File:Açaí na tigela - Acai bowl.jpg",
    "yogurt_parfait": "File:Granola and yogurt - Bubbles + Brunch 2018 - Sarah Stierch 02.jpg",
    "salmon_rice": "File:Grilled plated salmon fillet (cropped).jpg",
}


def _open(url: str, timeout: int = 30):
    """urlopen with Wikimedia's User-Agent, pacing, and backoff on 429/503."""
    for attempt in range(6):
        time.sleep(PACE_SECONDS)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == 5:
                raise
            wait = float(e.headers.get("Retry-After") or 0) or 10 * 2 ** attempt
            print(f"    rate limited, waiting {wait:.0f}s")
            time.sleep(wait)


def _get(url: str, params: dict) -> dict:
    with _open(url + "?" + urllib.parse.urlencode(params)) as resp:
        return json.load(resp)


def _lead_image(article: str):
    data = _get("https://en.wikipedia.org/w/api.php", {
        "action": "query", "format": "json", "redirects": 1, "prop": "pageimages",
        "piprop": "name", "titles": article,
    })
    for page in data.get("query", {}).get("pages", {}).values():
        if page.get("pageimage"):
            return "File:" + page["pageimage"]
    return None


def _search(query: str, limit: int = 8) -> list:
    data = _get("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "list": "search", "srnamespace": 6,
        "srsearch": f"{query} filetype:bitmap", "srlimit": limit,
    })
    return [hit["title"] for hit in data.get("query", {}).get("search", [])]


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def _file_info(title: str):
    """Commons metadata for a file if it is an acceptably licensed photo, else None."""
    data = _get("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "titles": title, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": WIDTH,
    })
    for page in data.get("query", {}).get("pages", {}).values():
        if "missing" in page or not page.get("imageinfo"):
            return None  # not on Commons (e.g. an enwiki-only non-free file)
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})
        license_name = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        if info.get("mime") not in ("image/jpeg", "image/png") or info.get("width", 0) < MIN_WIDTH:
            return None
        if not ACCEPTED_LICENSE.match(license_name):
            return None
        # Artist fields sometimes run on ("X from Chicago; hometown of ..."): keep the name.
        author = _strip_html(meta.get("Artist", {}).get("value", "")).split(";")[0].strip() or "Unknown author"
        return {
            "file": page["title"],
            "thumb": info.get("thumburl") or info["url"],
            "page": info["descriptionurl"],
            "author": author[:80],
            "license": license_name,
            "license_url": meta.get("LicenseUrl", {}).get("value", ""),
            "source": "Wikimedia Commons",
        }
    return None


def _candidates(cid: str):
    if cid in OVERRIDES:
        yield OVERRIDES[cid]
        return
    article, query = SOURCES[cid]
    if article:
        lead = _lead_image(article)
        if lead:
            yield lead
    yield from _search(query)


def cmd_fetch(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    if manifest_path.exists():  # resume an interrupted fetch
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    elif any(out.iterdir()):
        sys.exit(f"{out} is not empty and has no manifest.json; give an empty staging directory")
    else:
        manifest = {}
    concepts = json.loads(CONCEPTS_PATH.read_text(encoding="utf-8"))
    ids = [c["id"] for c in concepts["dishes"] + concepts["formats"]]
    missing = sorted(set(ids) - set(SOURCES))
    if missing:
        sys.exit(f"no SOURCES entry for: {missing}")
    used = {entry["file"] for entry in manifest.values()}
    for cid in ids:
        if cid in manifest:
            continue
        picked = None
        for title in _candidates(cid):
            if title in used:
                continue
            info = _file_info(title)
            if info:
                picked = info
                break
        if not picked:
            print(f"  {cid}: no acceptable photo")
            continue
        suffix = ".png" if picked["thumb"].lower().endswith(".png") else ".jpg"
        dest = out / f"{cid}{suffix}"
        with _open(picked["thumb"], timeout=60) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        used.add(picked["file"])
        manifest[cid] = {**picked, "local": dest.name}
        manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  {cid}: {picked['file']} [{picked['license']}]")
    print(f"{len(manifest)}/{len(ids)} photos in {out}")


def _compress(src: Path, dest: Path) -> None:
    """Re-encode as a JPEG at most WIDTH wide, transparency flattened onto white.

    Pillow comes in with matplotlib (requirements.txt); imported here so
    the fetch step doesn't need it.
    """
    from PIL import Image

    with Image.open(src) as im:
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            flat = Image.new("RGB", im.size, "white")
            flat.paste(im, mask=im.getchannel("A"))
            im = flat
        else:
            im = im.convert("RGB")
        if im.width > WIDTH:
            im = im.resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS)
        im.save(dest, "JPEG", quality=78, optimize=True, progressive=True)


def cmd_install(staging: Path, skip: set) -> None:
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    concepts = json.loads(CONCEPTS_PATH.read_text(encoding="utf-8"))
    installed = 0
    for concept in concepts["dishes"] + concepts["formats"]:
        entry = manifest.get(concept["id"])
        if not entry or concept["id"] in skip:
            concept["image"] = None
            continue
        name = f"{concept['id']}.jpg"
        _compress(staging / entry["local"], INSTALL_DIR / name)
        concept["image"] = {
            "url": PUBLIC_PREFIX + name,
            "page": entry["page"],
            "author": entry["author"],
            "license": entry["license"],
            "license_url": entry["license_url"],
            "source": entry["source"],
            "file": entry["file"],
        }
        installed += 1
    # One concept per line, like the hand-written file, so tags stay easy to scan and diff.
    rows = lambda key: ",\n".join("  " + json.dumps(c, ensure_ascii=False) for c in concepts[key])
    text = (
        "{\n"
        f' "_about": {json.dumps(concepts["_about"], ensure_ascii=False)},\n'
        f' "dishes": [\n{rows("dishes")}\n ],\n'
        f' "formats": [\n{rows("formats")}\n ]\n'
        "}\n"
    )
    json.loads(text)
    CONCEPTS_PATH.write_text(text, encoding="utf-8")
    print(f"installed {installed} photos into {INSTALL_DIR}")


def main() -> None:
    # Commons file names are often non-ASCII; don't die on a cp1252 console.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", type=Path, required=True)
    i = sub.add_parser("install")
    i.add_argument("--staging", type=Path, required=True)
    i.add_argument("--skip", default="", help="comma-separated concept ids to leave on the placeholder")
    args = ap.parse_args()
    if args.cmd == "fetch":
        cmd_fetch(args.out)
    else:
        cmd_install(args.staging, {s for s in args.skip.split(",") if s})


if __name__ == "__main__":
    main()
