# Grocery data

Status: built (2026-11-04). The first live refresh ran ok: 53 API calls,
1,684 products active at Ralphs Fresh Fare La Jolla (`70300108`).

## Why it looks like this

Kroger's developer Terms of Service (last modified 2019-04-09, Section 5e)
forbid building databases or permanent copies of API content, or caching it
longer than the cache header allows, unless the content owner expressly
permits it. The Products and Locations APIs send no `Cache-Control`,
`Expires` or `ETag` header (checked 2026-11-04). Section 5e also forbids
redistributing or publicly displaying the content, Section 8b requires
deleting stored content when the terms end, and Section 4b says credentials
may not be embedded in open source projects (`.env` is gitignored).

So the data is split by who owns it:

| Data | Where it lives |
|---|---|
| UPCs we track, the kind each counts as, when each was last on the shelf, catalog search terms, ingredient-to-UPC pins, refresh log | ours: `grocery.db` |
| Price (regular and promo), name, size, nutrition label, allergens, stock | Kroger's: fetched live, held in memory only, for up to 12 hours |

One Kroger call returns price and label together, 50 products at a time,
so the whole catalog is ~34 calls (about 8 seconds cold, fetched 4 at a
time) and every tracked product stays covered. USDA FoodData Central and
Open Food Facts were considered for nutrition and dropped: they'd lose
store-brand coverage and need a USDA key, and with no live Kroger price a
grocery item can't be planned anyway.

## Where it lives

- `grocery.db`, a separate SQLite file in `~/.autoeat/`
  (`C:\Users\super\.autoeat`), so OneDrive never syncs a live database.
  Override with `AUTOEAT_GROCERY_DB`. Not `%LOCALAPPDATA%`: Windows
  redirects AppData writes from packaged apps (the Claude desktop app is
  one) into a private folder that Task Scheduler and the web app can't see.
  WAL mode. `refresh.log` (rotating, 5 x 1 MB) sits next to it.
- Kept apart from `webapp/data/app.db`: the catalog can be rebuilt any
  time, accounts can't.
- Never committed. Tests use `tests/grocery_fixture.py`, a synthetic
  catalog of 129 made-up products shaped like Kroger responses.

## Schema (`src/macro_optimizer/grocery_db.py`)

```
stores          location_id PK, chain, name, address, active, added_at
products        upc PK (Kroger's 13-digit id), kind, status, first_seen, last_seen
                status: active | missing | discontinued
catalog_terms   term PK, active
ingredient_pins ingredient_id PK, upc, search_term, each_grams, note,
                last_verified_at, verified_status (ok | not_carried)
refresh_runs    id, location_id, started_at, finished_at,
                status (running | ok | partial | failed), api_calls, counts JSON, errors JSON
```

A product's kind is set when first discovered and never moved, so the
optimizer's per-kind cap (`optimizer.KIND_DAILY_CAP`) stays stable.

## Reading it (`src/macro_optimizer/market.py`)

- `database.load_market_items(location_id=None, use_promo=None)` returns
  the same FoodItem dicts as before: every `active` product, fetched live,
  built with `kroger.to_food_item`, screened by `kroger.passes_screen`
  (implausible macros per dollar), deduped by name.
- `recipes.load_ingredient_db()` reprices each pinned ingredient live and
  sets its `price_source`. Unpinned ingredients, pins the store doesn't
  carry today, and any Kroger outage fall back to the prices in
  `ingredients.json`. `load_ingredient_db(live=False)` skips Kroger.
- **Regular price by default.** Sale prices are used only with
  `use_promo=True` or `AUTOEAT_USE_SALE_PRICES=1`. Notes say when a sale was
  ignored. On the first run, 467 of 1,601 items were on sale.
- If Kroger is unreachable, there are no credentials, or there is no
  database yet, the grocery catalog is empty and `market.status()` says why.
  Recipes keep working.

## Refreshing it (`py -m macro_optimizer.refresh`)

```
init --location 70300108 [--import-catalog kroger_items.json]   # once
run [--budget 1000] [--quiet]                                    # daily
status
pin <ingredient> <upc> [--each-grams N]
stores --zip 92037 --chain RALPHS
schedule [--at 05:00] [--install]
```

Each run:
1. Searches every active catalog term (one call each, 50 results) and
   records products with a price and a usable label.
2. Looks up every other `active` or `missing` product by UPC, and records
   the ones still sold.
3. Verifies each ingredient pin is carried.
4. Only if nothing failed: products unseen for 14 days become `missing`
   (no longer offered), unseen for 60 days `discontinued`. Rows are never
   deleted, and a product seen again becomes `active`.

Calls retry 3 times with backoff on timeouts, dropped connections and
5xx. A 429 (daily limit) stops the run. Each run has a call budget
(default 1,000; a normal run uses ~55 of Kroger's ~10,000 daily product
calls). The run row and the log record status, call count, counts and
errors. Exit code 0 ok, 2 partial, 1 failed.

`schedule --install` writes `run_refresh.pyw` next to the database and
registers a daily 5am Task Scheduler job that runs it with `pythonw`, so no
window opens. The launcher pins the checkout it was installed from, so
re-run `schedule --install` once the code moves (for example after this
branch is merged into main). The job is set to catch up after a missed
start and to run on battery.

## Stores

The schema handles any number of stores; `load_market_items` takes a
`location_id`. One is active for now. Letting each user pick a store comes
later.

## Kroger permission and partner access

The developer catalog (developer.kroger.com/api-products) lists 8 Partner
APIs next to the 5 Public ones, including **Catalog API V2**, whose
endpoints are built for keeping a synced copy of the catalog:

- `GET /catalog/v2/products`: up to 50 UPCs per call, filterable by
  `filter.updatedAt.range` so only changed products are fetched.
- `GET /catalog/v2/products-inventory`: stock, fulfillment and active
  promotions per store, filterable by when they changed.
- `GET /catalog/v2/deleted-items`: products disabled in a date range
  (2-day maximum).

How to get partner access, and whether partners sign different data
terms, isn't stated. The listed contact is APISupport@kroger.com.

Plan: ask APISupport@kroger.com (1) whether a personal project can get
Catalog API V2 or partner Product API access, and (2) for written
permission to keep a private local cache of prices, with history, either
way. If granted: record the date and wording here, add a `price_history`
table (upc, location_id, regular_price, promo_price, stock_level,
first_seen_at, last_seen_at; a row only when the price changes), and
switch the refresh to Catalog V2 delta syncs.

## Leftover Kroger content to delete

`src/macro_optimizer/data/kroger_items.json` (gitignored) and
`data_snapshots/` are stored copies of Kroger content. The UPCs and kinds
have been imported into `grocery.db`, so both can be deleted, along with
the Kroger prices written into the main checkout's `ingredients.json`.
