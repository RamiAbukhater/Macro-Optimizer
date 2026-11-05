# Grocery data: design

Status: agreed design, not built yet (2026-11-04).

## Why it looks like this

Kroger's developer Terms of Service (last modified 2019-04-09, Section 5e)
forbid building databases or permanent copies of API content, or caching it
longer than the cache header allows, unless the content owner expressly
permits it. The Products and Locations APIs send no `Cache-Control`,
`Expires` or `ETag` header (checked 2026-11-04). Section 5e also forbids
redistributing or publicly displaying the content, and Section 8b requires
deleting stored content when the terms end.

So the design splits the data by who owns it:

| Data | Source | Stored? |
|---|---|---|
| Ingredient-to-UPC pins, product kinds, stores we use, refresh log | ours | yes, in `grocery.db` |
| Nutrition, product name, brand, size, allergens | USDA FoodData Central (public domain) and Open Food Facts (ODbL, attribution required) | yes, in `grocery.db`, with history |
| Price (regular and promo), stock level | Kroger Products API | **no**: fetched live, cached in memory only |

We are also asking Kroger for written permission to cache prices for
personal use (see "Kroger permission" below). If they grant it, the
`price_history` table below gets turned on and nothing else changes shape.

## Where it lives

- A separate SQLite file, `grocery.db`, in `%LOCALAPPDATA%\AutoEat\`, so
  OneDrive never syncs a live database. Override with `AUTOEAT_GROCERY_DB`.
- WAL mode. Kept apart from `webapp/data/app.db`: the catalog can be rebuilt
  from its sources at any time, while accounts can't.
- Never committed. Tests use a small synthetic fixture.

## Schema

```
stores           location_id PK, chain, name, address, active
products         gtin PK (14-digit, check digit included), name, brand,
                 size_text, size_qty, size_unit, category, servings_per_pkg,
                 nutrition JSON, allergens JSON, vegetarian, vegan,
                 gluten_free, halal, contains_pork,
                 nutrition_source ('usda' | 'off'), source_ref,
                 first_seen, last_seen, status ('active' | 'missing' | 'discontinued')
nutrition_history gtin, source, nutrition JSON, observed_at
                 (a row only when the label changes)
product_kinds    gtin, kind            -- many-to-many, the dedupe point
catalog_terms    kind, search_term, daily_cap, active
ingredient_pins  ingredient_id, gtin, location_id (NULL = any store), note,
                 last_verified_at, verified_status
refresh_runs     id, started_at, finished_at, status ('ok' | 'partial' | 'failed'),
                 calls_by_source JSON, counts JSON, errors JSON

-- Disabled until Kroger grants permission:
price_history    gtin, location_id, regular_price, promo_price, stock_level,
                 first_seen_at, last_seen_at   -- a row only on change
```

### UPC/GTIN join

Kroger's `productId`/`upc` is 13 digits **without** the check digit
(`0007411700094`). USDA `gtinUpc` and Open Food Facts barcodes include it.
Products are keyed by the full 14-digit GTIN: compute the GS1 check digit for
Kroger IDs, zero-pad the others, and convert back when calling Kroger.

## How the catalog is built

The catalog comes from sources we're allowed to store, not from Kroger
search results:

1. For each `catalog_terms` row, search USDA branded foods and Open Food
   Facts, keep products with a usable label, and upsert them by GTIN.
2. Kroger is only asked about GTINs already in the catalog: whether the store
   carries each one and what it costs. That answer lives in memory only.
3. A product the store doesn't carry is still kept (other stores may), but
   it's filtered out when planning for that store.

Expect lower coverage than today's 1,361 Kroger-searched items: some
store-brand products have no USDA/OFF record. The first build reports the
match rate per kind so we can see what got lost.

## Live prices

- `prices.get(location_id, gtins)` calls the Kroger Products API in batches
  by `filter.productId` (batch size to be confirmed against the API docs)
  and returns regular price, promo price, size and stock level.
- An in-process cache with a 12-hour TTL, never written to disk. The first
  plan of the day pays a few dozen calls, and later plans reuse them.
- The optimizer plans with the **regular** price. The promo price is used
  only when the user turns on "use sale prices".
- If Kroger is unreachable, planning still works: it falls back to the
  `price_per_unit` already in `ingredients.json` for recipes, and leaves
  grocery products out, with a banner saying prices are unavailable.

## Scheduler

Windows Task Scheduler runs `py -m macro_optimizer.refresh` daily at about
5am, with "run as soon as possible after a missed start" on.

Each run:
1. Refreshes nutrition and product details from USDA and Open Food Facts
   for every catalog term (searches) and every active product (by GTIN).
2. Verifies each ingredient pin is still carried at its store (live Kroger
   call) and records only `last_verified_at` and `verified_status`.
3. Updates `status`: not found in any source for 14 days becomes `missing`
   (excluded), 60 days becomes `discontinued` (kept, never hard-deleted).
   Terms whose search failed this run don't count toward `missing`.
4. Writes a `refresh_runs` row and appends to a rotating log next to the
   database.

Retries: exponential backoff on 429 and 5xx, 3 tries per call, and a call
budget per source per run. Each term is written in its own transaction, so a
failed run leaves the previous data live.

USDA's default `DEMO_KEY` is too limited for a daily job, so a free
`USDA_FDC_API_KEY` goes in `.env`.

## How the code reads it

- `database.load_market_items(location_id)` returns the same dicts as today:
  catalog rows from `grocery.db` joined with live prices. `channels.py`,
  `webapp/app.py` and the optimizer don't change.
- Ingredient pricing reads pins from `grocery.db` and prices from the live
  cache. The number in `ingredients.json` is a fallback only, so
  `pull_kroger_prices.py --apply` goes away.
- `scripts/build_kroger_catalog.py` and `scripts/pull_kroger_prices.py`
  become `macro_optimizer.refresh` subcommands: `refresh`, `pin`, `status`.
- Tests run against a temporary `grocery.db` seeded from a small synthetic
  fixture, with the Kroger price call stubbed. Tests that count the real
  catalog switch to the fixture.

## Stores

The schema handles any number of stores. One is active for now: Ralphs
Fresh Fare La Jolla, `70300108`. Letting each user pick a store comes later.

## Kroger permission and partner access

The developer catalog (developer.kroger.com/api-products) lists 8 Partner
APIs next to the 5 Public ones, including **Catalog API V2**. Its endpoints
are built for keeping a synced copy of the catalog:

- `GET /catalog/v2/products`: search by up to 50 UPCs per call and a
  `filter.updatedAt.range`, so only changed products are fetched.
- `GET /catalog/v2/products-inventory`: stock level, fulfillment and active
  promotions per store, filterable by when stock or fulfillment changed.
- `GET /catalog/v2/deleted-items`: products disabled in a date range
  (2-day maximum), so discontinued items can be dropped exactly.

How to get partner access, and whether partners sign data terms that differ
from the public Terms of Service, isn't stated on the page. The only contact
listed is APISupport@kroger.com.

Plan: email APISupport@kroger.com asking (1) whether a personal project can
get Catalog API V2 or partner Product API access and (2) for written
permission to keep a private local cache of prices, including history,
either way. If granted: record the date and the wording here, enable
`price_history`, and switch the daily job to Catalog V2 delta syncs
(`updatedAt` for changes, `deleted-items` for discontinued products). The
storage design above already fits that.

## Existing data to clean up

`src/macro_optimizer/data/kroger_items.json` (about 1.2 MB, never committed)
and `data_snapshots/` are stored Kroger content. Once the new catalog is
built, delete them and add `kroger_items.json` to `.gitignore` in the
meantime. Nothing has been deleted yet.
