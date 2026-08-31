# Backend support needed

**All five items below are done as of 2026-08-27** — see `update.md` for the
exact endpoints/shapes to build against. Left the original notes in place
below for context on what was gapped and why.

Notes for whoever picks up backend work next. The webapp (Flask + the
`macro_optimizer` package) was extended to support four new pages —
Weekly Menu, Cost Analysis, Fridge/Inventory, and a Recipe/Product
breakdown page — reusing and lightly extending the existing package
(`pantry.py`, `recipes.py`, a new `shopping_list.py`) wherever that was
enough to make the feature real. Everything below is what's still a stub,
a placeholder, or a real gap, grouped by page. When you land something
here, add a note to `update.md` describing what's now available — the web
agent will pick it up and wire the frontend to it.

## Instacart / real shopping list checkout (Cost Analysis page) — DONE, see update.md #5

The Cost Analysis page (`webapp/static/cost.js`) exports a shopping list
in a format shaped to match Instacart's Create Recipe Page API ingredient
schema (`{name, display_text, quantity, unit}`, see
[docs.instacart.com/developer_platform_api](https://docs.instacart.com/developer_platform_api/guide/tutorials/create_a_recipe_page)),
but it only downloads a `.json` file. No live API call is made.

Needed to make this real:
- An Instacart Developer Platform API key/partner account.
- A backend endpoint (e.g. `POST /api/shopping-list/instacart`) that POSTs
  the ingredient list to Instacart's Create Recipe Page API and returns
  the resulting shoppable page URL.
- The frontend button would then open that URL in a new tab instead of
  downloading a file.
- Same shape of work applies to Walmart's Recipes/Bundle + AddToCart APIs
  if a second retailer is wanted (see `IDEAS.md`'s "Shopping list export"
  section).

## Recipe instructions / source links (Recipe breakdown page) — DONE, see update.md #1

`recipes.json` only carries an ingredient list, servings, prep/cook time,
and a short `notes` field, there's no step-by-step instructions and no
source URL. `GET /api/recipe/<name>` (see `webapp/app.py`) always returns
`"source_url": null` for both recipe and product lookups, and the recipe
page shows a static "no external link yet" message.

Needed to make this real:
- Either author actual instructions into `recipes.json` per recipe, or
  pull them from a recipe API (Spoonacular, Edamam) and store a
  `source_url`/`instructions` field per recipe, per the recipe layer
  sketched in `IDEAS.md`.
- For non-recipe items (Market/Dining Hall/Prepared Meal/Restaurant Order),
  a real product page or menu URL would need to be added to those JSON
  data files (`src/macro_optimizer/data/*.json`), there's currently no
  field for it in the schema at all (see `schema.py`).

## Ingredient database coverage (Fridge page) — DONE, see update.md #2

The Fridge page's "add an item" dropdown is populated from
`GET /api/ingredients`, which is just the 16 entries in
`ingredients.json`. That's fine for what the recipe database currently
uses, but it means a user can only track pantry items that happen to be a
recipe ingredient already, they can't log "I have 2 lbs of salmon" if no
recipe uses salmon.

Needed to make this real:
- A larger ingredient database (more raw ingredients with price/macro
  data), or
- Letting the pantry track arbitrary free-text ingredients with
  user-entered macros/price (lower quality, but unblocks tracking
  anything), or
- Wiring `openfoodfacts.py`/`usda_fdc.py` (both already exist and work,
  see `src/macro_optimizer/openfoodfacts.py` and `usda_fdc.py`) into the
  Fridge page as a "search a real product to add" flow instead of the
  fixed dropdown, this is probably the best long-term path and doesn't
  need new backend, just a new endpoint that proxies those two modules'
  existing `search_products`/`usda_search_foods` functions to the
  frontend.

## Plan/session persistence — DONE, see update.md #3

The whole app is currently stateless server-side except for the pantry
(`webapp/data/pantry.json`, gitignored, single fridge for whoever's
running the app locally). The weekly plan itself lives only in the
browser's `sessionStorage` (see `webapp/static/js/common.js`'s
`loadPlan`/`savePlan`), set once at onboarding and mutated in place by
the Weekly Menu page's replace/reshuffle actions.

This is fine for a single local user, but has real limits:
- Refreshing after clearing `sessionStorage`, or opening the dashboard in
  a new tab, loses the plan (redirects back to onboarding).
- There's no multi-user support, no accounts, one pantry file total.
- `PickHistory` (`history.py`) is never persisted across requests, so the
  rotation-cap/recency-penalty variety guard only ever sees what's seeded
  in-request (see `api_plan_reshuffle` in `webapp/app.py`), a real
  install would want `PickHistory(path=...)` persisted per user and fed
  every time a plan is generated or a meal is actually eaten, not just at
  reshuffle time.

Needed to make this real: a real user/session model (even just one
persisted JSON blob per browser session id, no auth needed yet) covering
plan + pick history + pantry together, instead of pantry alone.

**Update:** accounts now exist (`webapp/db.py`, `webapp/auth.py`) —
real signup/login with hashed passwords and a session cookie, not the
placeholder session-id idea above. Every kind of state (pantry, plan,
plan_previous, history, deviations, taste_profile) is scoped to
`users.id` via the `user_blobs` table in `webapp/data/app.db`
(gitignored), the same one-blob-per-kind shape the old JSON files had,
just keyed by user instead of by a single global path. `sessionStorage`
is still used client-side as a cache (`common.js`'s `loadPlan`/
`savePlan`), but the server-side source of truth is per-account now, so
a cleared `sessionStorage` or a new tab falls back to `GET /api/plan`
for that logged-in user instead of losing the plan.

## Replace-item edge cases (Weekly Menu page) — DONE, see update.md #4

`POST /api/plan/replace-item` (`webapp/app.py`) pins every other item on
that day and excludes only the replaced one, then re-solves. This works
well in the common case but has two rough edges worth knowing about:

- If removing one item makes the day infeasible (no combination of the
  remaining candidates plus pinned items can hit the macro floors within
  budget/time), the endpoint returns whatever `run_optimizer`'s own
  soft-constraint fallback produces (goals become best-effort), it
  doesn't tell the user *why* the swap came back under target. The
  frontend currently just re-renders whatever comes back; a nicer version
  would surface "couldn't fully replace this without missing your macros"
  explicitly.
- There's no undo, a bad swap means clicking replace again and hoping for
  something closer to the original, since the whole point is variety, the
  replacement isn't guaranteed to be similar in cost or macros to what it
  replaced.
