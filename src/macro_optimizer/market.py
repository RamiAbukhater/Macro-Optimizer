"""Live grocery data: the products in grocery.db, priced and labeled by Kroger right now.

Kroger's terms don't allow keeping copies of what its API returns (see
GROCERY_DATA.md), so prices, names and nutrition labels are fetched when a
plan needs them and held in this process's memory only, for up to
``CACHE_TTL_SECONDS``. The first plan of the day pays roughly one API call
per 50 products; later plans reuse the answer.

When Kroger can't be reached, or there are no credentials or no grocery
database yet, the grocery catalog comes back empty and ``status()`` says
why. Recipes keep working from the fallback prices in ingredients.json.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import requests

from . import grocery_db
from .env import load_env_file
from .kroger import KrogerClient, KrogerError, _per_ingredient_unit, passes_screen, to_food_item, to_price_record

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 12 * 60 * 60
SALE_PRICES_ENV_VAR = "AUTOEAT_USE_SALE_PRICES"

Fetcher = Callable[[List[str], str], Dict[str, dict]]

_lock = threading.Lock()
_cache: Dict[tuple, tuple] = {}  # (location_id, upc) -> (fetched_monotonic, product or None)
_fetcher: Optional[Fetcher] = None
_client: Optional[KrogerClient] = None
_status = {"ok": None, "error": None, "fetched_at": None}


def use_sale_prices_default() -> bool:
    return os.environ.get(SALE_PRICES_ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def _kroger_fetcher(upcs: List[str], location_id: str) -> Dict[str, dict]:
    global _client
    if _client is None:
        load_env_file()
        _client = KrogerClient()
    return _client.get_products(upcs, location_id)


def set_fetcher(fetcher: Optional[Fetcher]) -> None:
    """Swap where live products come from (tests use a fixture); None restores the Kroger API. Clears the cache."""
    global _fetcher
    with _lock:
        _fetcher = fetcher
        _cache.clear()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def status() -> dict:
    """``{"ok", "error", "fetched_at"}`` from the most recent live fetch, for an "updated X ago" line or a banner."""
    return dict(_status)


def _set_status(ok: bool, error: Optional[str] = None) -> None:
    _status["ok"] = ok
    _status["error"] = error
    if ok:
        _status["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_products(upcs: List[str], location_id: str) -> Dict[str, dict]:
    """Raw Kroger products for these UPCs at one store, from memory when fresh, otherwise live.

    UPCs the store doesn't carry are remembered as absent for the same TTL,
    so they don't cost a call on every plan.
    """
    with _lock:
        clock = time.monotonic()
        stale = [u for u in dict.fromkeys(upcs)
                 if (location_id, u) not in _cache or clock - _cache[(location_id, u)][0] > CACHE_TTL_SECONDS]
        if stale:
            try:
                found = (_fetcher or _kroger_fetcher)(stale, location_id)
            except (KrogerError, requests.RequestException) as exc:
                log.warning("live grocery fetch failed: %s", exc)
                _set_status(False, str(exc))
            else:
                fetched_at = time.monotonic()
                for upc in stale:
                    _cache[(location_id, upc)] = (fetched_at, found.get(upc))
                _set_status(True)
        clock = time.monotonic()
        return {
            u: entry[1] for u in upcs
            if (entry := _cache.get((location_id, u))) and entry[1] is not None and clock - entry[0] <= CACHE_TTL_SECONDS
        }


def _store(conn, location_id: Optional[str]) -> Optional[dict]:
    if location_id:
        return grocery_db.get_store(conn, location_id) or {"location_id": location_id, "name": location_id}
    return grocery_db.active_store(conn)


def market_items(location_id: Optional[str] = None, use_promo: Optional[bool] = None) -> List[dict]:
    """The ready-to-eat grocery catalog as FoodItem dicts, priced live for one store.

    Products come from grocery.db (status ``active``); each is rebuilt from
    its live Kroger record through the same ``to_food_item`` and plausibility
    screen the catalog was discovered with. Items with no price today, no
    usable label, or a name already taken by an earlier item are skipped.
    """
    if not grocery_db.exists():
        _set_status(False, "No grocery database yet: run `py -m macro_optimizer.refresh init`.")
        return []
    use_promo = use_sale_prices_default() if use_promo is None else use_promo
    conn = grocery_db.connect()
    try:
        store = _store(conn, location_id)
        if store is None:
            _set_status(False, "No store set up: run `py -m macro_optimizer.refresh init --location <id>`.")
            return []
        rows = grocery_db.products(conn)
    finally:
        conn.close()

    live = fetch_products([r["upc"] for r in rows], store["location_id"])
    items, names = [], set()
    for row in rows:
        product = live.get(row["upc"])
        if product is None:
            continue
        item = to_food_item(product, store["location_id"], store["name"], kind=row["kind"], use_promo=use_promo)
        if item is None or not passes_screen(item) or item.name in names:
            continue
        names.add(item.name)
        items.append(item.to_dict())
    items.sort(key=lambda d: (d["category"], d["name"]))
    return items


def ingredient_prices(ingredient_db: dict, location_id: Optional[str] = None,
                      use_promo: Optional[bool] = None) -> Dict[str, dict]:
    """Live prices for pinned recipe ingredients: ``{ingredient: {"price_per_unit", "price_source"}}``.

    Only pinned ingredients are priced, a search fallback is never applied
    without a person reviewing it. An ingredient whose pin the store doesn't
    carry today, or whose package unit can't be reconciled, is left out so
    the caller keeps its fallback price.
    """
    if not grocery_db.exists():
        return {}
    use_promo = use_sale_prices_default() if use_promo is None else use_promo
    conn = grocery_db.connect()
    try:
        store = _store(conn, location_id)
        pins = grocery_db.pins(conn)
    finally:
        conn.close()
    pins = {k: v for k, v in pins.items() if k in ingredient_db}
    if store is None or not pins:
        return {}

    live = fetch_products([p["upc"] for p in pins.values()], store["location_id"])
    today = datetime.now().date().isoformat()
    out = {}
    for key, pin in pins.items():
        product = live.get(pin["upc"])
        record = to_price_record(product, store["location_id"], use_promo=use_promo) if product else None
        if record is None:
            continue
        price = _per_ingredient_unit(record, ingredient_db[key]["unit"], pin.get("each_grams"))
        if price is None:
            continue
        sale = " (sale)" if use_promo and record["on_sale"] else ""
        out[key] = {
            "price_per_unit": price,
            "price_source": (f"Kroger live, store {store['location_id']}, {today}, UPC {record['upc']}, "
                             f"{record['size']} at ${record['price']:.2f}{sale}"),
        }
    return out
