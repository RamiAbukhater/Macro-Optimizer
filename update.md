# Backend Updates

All five items from `backend_support.md`'s 2026-08-27 memo are implemented and
tested (195 passed, `py -m pytest -q`). Details per item below, including the
exact request/response shapes to build against.

## 1. Recipe instructions / source links

`GET /api/recipe/<name>` now returns real content instead of the static
"no external link yet" placeholder:

- Recipes: `instructions` is now a populated `list[str]` (step-by-step, hand
  authored per recipe in `recipes.json`). `source_url` is present but stays
  `null` on purpose, these are original AutoEat recipes with no real external
  page to link to; I didn't fabricate one. `recipe.js`'s `renderRecipe()` can
  drop the `source-body` hardcoded disclaimer text and render `data.instructions`
  as a numbered list instead, falling back to the current disclaimer only if
  `instructions` is empty (recipes added later without instructions authored yet).
- Market/dining/prepared/restaurant products: `source_url` is now a real field
  on every response (was hardcoded `None` before), currently still `null` for
  all of them, there's no legitimate real product/menu URL for hand-curated
  UCSD items or the illustrative prepared-meal/restaurant catalogs, so I left
  it unfabricated rather than invent one. `renderProduct()` can check
  `data.source_url` and render a real link when it's non-null, keeping the
  current "no link wired up" copy as the `null` fallback. The field is wired
  end-to-end now (schema, data, API), so a future real vendor/recipe-API
  integration only needs to populate the JSON, no endpoint changes.

## 2. Ingredient database coverage (Fridge page)

New endpoint, per your "probably the best long-term path" suggestion:

```
GET /api/ingredients/search?q=<query>
-> { "results": [
      { "key": "wild_salmon_fillet", "display_name": "Wild Salmon Fillet",
        "source": "Open Food Facts", "unit": "100g",
        "calories": 208, "protein": 20.0, "carbs": 0.0, "fat": 13.0,
        "fiber": 0, "sodium": 0 },
      ...
   ] }
```

Searches Open Food Facts and USDA FoodData Central live (via the existing
`openfoodfacts.py`/`usda_fdc.py`), merges results, silently drops anything
missing a name or macro data. No price (neither source has one, same caveat
as everywhere else these two modules are used).

`POST /api/pantry` now accepts ingredients that aren't in the curated
`ingredients.json` database, as long as a `unit` is supplied (a search result's
`key`/`unit` work directly):

```
POST /api/pantry
{ "ingredient": "wild_salmon_fillet", "quantity": 400, "unit": "100g",
  "display_name": "Wild Salmon Fillet", "source": "Open Food Facts",
  "expires_on": "2026-09-01" }
```

`display_name`/`source` are optional, purely for display. Unknown ingredient
+ no unit is still a 400 (unchanged behavior for the plain dropdown flow).
`GET /api/pantry` now returns `custom: true`/`custom_source` on any item
tracked this way, so the fridge table can badge it differently if you want.

Suggested frontend flow: a search box that hits `/api/ingredients/search`,
lets the user pick a result, then posts it to `/api/pantry` with the key/unit
from that result instead of only offering the fixed dropdown.

## 3. Plan/session persistence

This is a single local-user store (matching how `pantry.json` already works,
no accounts, since that's explicitly fine per your memo), not per-browser
sessions. Three new files under `webapp/data/`: `plan.json`, `history.json`,
`plan_previous.json` (all gitignored, same as `pantry.json`, added to
`.gitignore` — check yours got the same entry).

- `GET /api/plan` — returns whichever of `/api/plan`, `/api/plan/reshuffle`,
  or `/api/plan/replace-item` ran most recently, `404 {"error": "no plan saved
  yet"}` if nothing has run yet. Same shape as `POST /api/plan`'s response,
  plus `profile_input`. Suggested use in `common.js`'s `loadPlan()`: when
  `sessionStorage` is empty, try `GET /api/plan` before redirecting to `/`, so
  a cleared session or a new tab doesn't lose the plan.
- `POST /api/plan/undo` — restores the plan as it was immediately before the
  last reshuffle/replace-item. `404 {"error": "nothing to undo"}` if there's
  no snapshot yet. Single level (calling it twice swaps back and forth), not
  a full history. Good fit for menu.js's replace/reshuffle buttons, an "undo"
  toast action.
- Real `PickHistory` (`history.json`) is now fed into every plan-generating
  call (`/api/plan`, `/api/plan/reshuffle`, `/api/plan/replace-item`), not
  just seeded from `week_items` at reshuffle time like before. Variety now
  has real memory across separate requests/days, not just within one
  reshuffle payload. No API shape change, just better behavior.

## 4. Replace-item edge cases

Every day payload (in `/api/plan`, `/api/plan/reshuffle`, and the `day` object
`/api/plan/replace-item` returns) now includes a `gaps` field alongside
`achieved`:

```
"gaps": { "calories": 0, "protein": 45.0, "carbs": 0, "fat": 0 }
```

All zero when `feasible: true`. When `feasible: false`, each is how far short
of that macro floor the best-effort result landed, in the same units as
`achieved`. `menu.js`'s replace handler can check `resp.day.gaps` after a swap
and show something like "short 45g protein" instead of just the generic
"Best effort" badge.

Undo (above) covers the "no undo" edge case, a bad replace/reshuffle is one
`POST /api/plan/undo` away from reverting.

## 5. Instacart shopping list checkout

```
POST /api/shopping-list/instacart
{ "title": "AutoEat Weekly Shopping List", "image_url": "<required, a real image URL>",
  "ingredients": [{ "name": "rice", "display_text": "300 g rice", "quantity": 300, "unit": "g" }, ...],
  "instructions": ["optional"],
  "partner_linkback_url": "optional", "enable_pantry_items": true  // optional
}
-> 200 { "shoppable_url": "https://www.instacart.com/store/recipes/..." }
```

This calls Instacart's real Create Recipe Page API
(`POST https://connect.instacart.com/idp/v1/products/recipe`, confirmed
against their docs) and transforms `cost.js`'s existing export shape into
Instacart's (`quantity`/`unit` -> `measurements: [{quantity, unit}]`,
adds the required `link_type`/`image_url`/`instructions` fields).

**This needs a real Instacart Developer Platform partner API key**, which
this project doesn't have. Without the `INSTACART_API_KEY` environment
variable set, the endpoint returns `501` with an explanation rather than
faking success:

```
501 { "error": "Instacart integration needs a partner API key. Set the
      INSTACART_API_KEY environment variable ... until then, use the JSON
      export." }
```

`image_url` is also required by Instacart and has no sensible default (no
real product image exists to point at), missing it is a `400`. Once a real
key exists: swap `exportInstacartFormat()`'s current "download a .json file"
behavior for a `postJSON("/api/shopping-list/instacart", {...})` call, then
`window.open(resp.shoppable_url)` instead of `downloadFile(...)`. Until then,
keep the current JSON export as the fallback, the button can try the API call
first and fall back to download on a `501`.

---

Nothing here needed a database/schema-breaking change; existing plan/pantry
JSON files without the new fields (`gaps`, `source_url`, custom pantry
metadata) still load fine, defaults fill in.
