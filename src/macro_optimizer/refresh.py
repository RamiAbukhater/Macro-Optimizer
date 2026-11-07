"""Keep grocery.db current: discover products, confirm they're still sold, age out the rest.

Run daily by Windows Task Scheduler (see ``schedule``), or by hand:

    py -m macro_optimizer.refresh init --location 70300108   # once
    py -m macro_optimizer.refresh run                        # the daily job
    py -m macro_optimizer.refresh status
    py -m macro_optimizer.refresh pin egg 0001111090406
    py -m macro_optimizer.refresh stores --zip 92037 --chain RALPHS
    py -m macro_optimizer.refresh schedule [--install]

A run searches the store for every catalog term and records each product
that comes back with a price and a usable label, then asks Kroger directly
about every known product the searches didn't surface, then checks each
ingredient pin is still carried. Only UPCs, kinds and dates are written:
Kroger's terms don't allow keeping its prices or labels (GROCERY_DATA.md).

Products not seen for 14 days become ``missing`` and stop being offered;
after 60 days they're ``discontinued``. Statuses are only aged after a
clean run, so an outage never makes the catalog shrink.

Every run is logged to the ``refresh_runs`` table and to ``refresh.log``
next to the database. Exit code 0 means ok, 2 partial, 1 failed.
"""

import argparse
import json
import logging
import logging.handlers
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

from . import grocery_db
from .env import load_env_file
from .kroger import KrogerClient, KrogerError, load_product_map, passes_screen, to_food_item, to_price_record

log = logging.getLogger("macro_optimizer.refresh")

DEFAULT_LOCATION = "70300108"  # Ralphs Fresh Fare, La Jolla
DEFAULT_CALL_BUDGET = 1000  # per run; a normal run uses ~80 of Kroger's ~10,000 daily product calls
PER_TERM = 50
TASK_NAME = "AutoEat grocery refresh"

# Things a person eats as bought, or with a microwave at most. Raw
# ingredients are deliberately absent, those reach the optimizer through
# recipes (data/ingredients.json, priced live from ingredient pins).
CATALOG_TERMS = [
    "protein bar", "granola bar", "protein shake", "greek yogurt cup", "cottage cheese", "string cheese",
    "milk", "kefir", "hard boiled eggs", "deli turkey", "deli chicken", "canned tuna", "canned chicken",
    "tuna pouch", "beef jerky", "hummus", "guacamole", "salad kit", "sushi", "deli sandwich", "wrap sandwich",
    "frozen meal", "frozen burrito", "frozen pizza", "frozen breakfast sandwich", "frozen chicken strips",
    "microwave rice", "instant oatmeal", "cereal", "granola", "whole wheat bread", "bagels", "tortillas",
    "peanut butter", "almonds", "trail mix", "canned soup", "canned chili", "instant ramen", "mac and cheese cup",
    "fruit cup", "applesauce", "baby carrots", "orange juice", "tofu", "veggie burger",
]


def _usable(product: dict, location_id: str, kind: str = "") -> bool:
    item = to_food_item(product, location_id, kind=kind)
    return item is not None and passes_screen(item)


def run_refresh(conn, client, location_id: str, budget: int = DEFAULT_CALL_BUDGET) -> dict:
    """One refresh pass. Returns the run's summary (also written to refresh_runs)."""
    run_id = grocery_db.start_run(conn, location_id)
    calls_before = getattr(client, "calls", 0)
    seen_at = grocery_db.now()
    counts, errors, seen = Counter(), [], set()

    def over_budget() -> bool:
        if getattr(client, "calls", 0) - calls_before < budget:
            return False
        message = f"call budget of {budget} reached"
        if message not in errors:
            errors.append(message)
        return True

    try:
        # 1. Discover: one search per catalog term.
        for term in grocery_db.active_terms(conn):
            if over_budget():
                break
            try:
                found = client.search_products(term, location_id, limit=PER_TERM)
            except KrogerError as exc:
                errors.append(f"search {term!r}: {exc}")
                if "rate limit" in str(exc):
                    break
                continue
            kept = 0
            for product in found:
                upc = product.get("upc")
                if not upc or upc in seen:
                    continue
                if not _usable(product, location_id, term):
                    counts["skipped_unusable"] += 1
                    continue
                counts["new"] += grocery_db.record_seen(conn, upc, term, seen_at)
                seen.add(upc)
                kept += 1
            conn.commit()
            log.info("term %-28s %3d kept", term, kept)
        counts["found_by_search"] = len(seen)

        # 2. Confirm: ask about every known product the searches didn't surface.
        rest = [r for r in grocery_db.products(conn, ("active", "missing")) if r["upc"] not in seen]
        if rest and not over_budget():
            try:
                live = client.get_products([r["upc"] for r in rest], location_id)
            except KrogerError as exc:
                errors.append(f"confirm known products: {exc}")
            else:
                for row in rest:
                    product = live.get(row["upc"])
                    if product is not None and _usable(product, location_id, row["kind"]):
                        grocery_db.record_seen(conn, row["upc"], row["kind"], seen_at)
                        seen.add(row["upc"])
                        counts["confirmed_by_upc"] += 1
                conn.commit()

        # 3. Verify ingredient pins.
        pins = grocery_db.pins(conn)
        if pins and not over_budget():
            try:
                live = client.get_products([p["upc"] for p in pins.values()], location_id)
            except KrogerError as exc:
                errors.append(f"verify pins: {exc}")
            else:
                for ingredient, pin in pins.items():
                    product = live.get(pin["upc"])
                    ok = product is not None and to_price_record(product, location_id) is not None
                    grocery_db.mark_pin_verified(conn, ingredient, "ok" if ok else "not_carried")
                    counts["pins_ok" if ok else "pins_not_carried"] += 1
                conn.commit()

        # 4. Age out what wasn't seen, only after a clean run.
        if not errors:
            moved = grocery_db.update_statuses(conn)
            counts["now_missing"], counts["now_discontinued"] = moved["missing"], moved["discontinued"]
            conn.commit()
    except Exception as exc:  # a bug or a dropped DB must still close out the run row
        errors.append(f"unexpected: {exc!r}")
        log.exception("refresh crashed")

    status = "ok" if not errors else ("partial" if seen else "failed")
    api_calls = getattr(client, "calls", 0) - calls_before
    counts = dict(counts, seen=len(seen), **{f"total_{k}": v for k, v in grocery_db.status_counts(conn).items()})
    grocery_db.finish_run(conn, run_id, status, api_calls, counts, errors)
    for error in errors:
        log.warning("%s", error)
    log.info("run %d %s: %d API calls, %s", run_id, status, api_calls, json.dumps(counts, sort_keys=True))
    return {"id": run_id, "status": status, "api_calls": api_calls, "counts": counts, "errors": errors}


_NOTES_UPC = re.compile(r"\bUPC (\d+)")
_NOTES_DATE = re.compile(r"priced (\d{4}-\d{2}-\d{2})")


def import_legacy_catalog(conn, path: Path) -> int:
    """Seed products from the old kroger_items.json: keeps each row's UPC, kind and priced date, nothing else."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    added = 0
    for row in rows:
        upc = _NOTES_UPC.search(row.get("notes", ""))
        if not upc:
            continue
        priced = _NOTES_DATE.search(row.get("notes", ""))
        seen_at = f"{priced.group(1)}T00:00:00+00:00" if priced else grocery_db.now()
        added += grocery_db.record_seen(conn, upc.group(1), row.get("kind", ""), seen_at)
    conn.commit()
    return added


def init(conn, location_id: str, client=None, legacy_catalog: Optional[Path] = None) -> dict:
    """Set up a store, the default catalog terms, the shipped ingredient pins, and optionally a legacy catalog."""
    store = None
    if client is not None:
        try:
            store = client.get_location(location_id)
        except KrogerError as exc:
            log.warning("couldn't look up store %s: %s", location_id, exc)
    store = store or {"chain": "", "name": location_id, "address": ""}
    grocery_db.upsert_store(conn, location_id, store["chain"], store["name"], store["address"])
    grocery_db.add_terms(conn, CATALOG_TERMS)
    for ingredient, pin in load_product_map().items():
        if ingredient not in grocery_db.pins(conn):
            grocery_db.set_pin(conn, ingredient, pin["upc"], pin.get("term", ""), pin.get("each_grams"))
    imported = import_legacy_catalog(conn, legacy_catalog) if legacy_catalog else 0
    conn.commit()
    return {"store": grocery_db.get_store(conn, location_id), "imported": imported,
            "terms": len(grocery_db.active_terms(conn)), "pins": len(grocery_db.pins(conn))}


LAUNCHER_NAME = "run_refresh.pyw"
LAUNCHER_TEMPLATE = '''"""Written by `py -m macro_optimizer.refresh schedule --install`; Task Scheduler runs this daily."""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, {src!r})  # the checkout the job was installed from
try:
    from macro_optimizer.refresh import main
    code = main(["run", "--quiet"])
except BaseException:
    with open(Path(__file__).with_name("refresh.log"), "a", encoding="utf-8") as f:
        f.write(traceback.format_exc())
    code = 1
sys.exit(code)
'''


def launcher_path() -> Path:
    return grocery_db.default_path().with_name(LAUNCHER_NAME)


def write_launcher(path: Optional[Path] = None) -> Path:
    """Write the script the scheduled task runs, pinned to this copy of the code.

    Pinning matters because the installed package may point at a different
    checkout. Re-run ``schedule --install`` from wherever the code moves to.
    """
    path = Path(path or launcher_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LAUNCHER_TEMPLATE.format(src=str(Path(__file__).resolve().parents[1])), encoding="utf-8")
    return path


def schedule_command(at: str = "05:00", launcher: Optional[Path] = None) -> List[str]:
    """The schtasks call that runs the launcher daily, with no console window."""
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    return ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", at, "/TN", TASK_NAME,
            "/TR", f'"{exe}" "{launcher or launcher_path()}"']


def _setup_logging(verbose: bool = True) -> Path:
    log_path = grocery_db.default_path().with_name("refresh.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger("macro_optimizer")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if verbose:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(console)
    return log_path


def _print_status(conn) -> None:
    store = grocery_db.active_store(conn)
    print(f"database: {grocery_db.default_path()}")
    print(f"store:    {store['location_id']}  {store['name']}" if store else "store:    none (run init)")
    print(f"products: {grocery_db.status_counts(conn) or 'none'}")
    pins = grocery_db.pin_rows(conn)
    bad = [p["ingredient_id"] for p in pins if p["verified_status"] == "not_carried"]
    print(f"pins:     {len(pins)}" + (f", not carried: {', '.join(bad)}" if bad else ""))
    for run in grocery_db.recent_runs(conn):
        print(f"run {run['id']:>4}  {run['started_at']}  {run['status']:<8} {run['api_calls']:>4} calls  "
              f"seen {run['counts'].get('seen', 0)}  new {run['counts'].get('new', 0)}"
              + (f"  errors: {'; '.join(run['errors'])}" if run["errors"] else ""))


def main(argv: Optional[List[str]] = None) -> int:
    if sys.stdout is not None:  # None under pythonw (the scheduled job)
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="py -m macro_optimizer.refresh", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="create the database for a store")
    p_init.add_argument("--location", default=DEFAULT_LOCATION)
    p_init.add_argument("--import-catalog", type=Path, help="seed UPCs and kinds from an old kroger_items.json")
    p_run = sub.add_parser("run", help="the daily refresh")
    p_run.add_argument("--location", help="store to refresh (default: the active store)")
    p_run.add_argument("--budget", type=int, default=DEFAULT_CALL_BUDGET, help="max API calls this run")
    p_run.add_argument("--quiet", action="store_true", help="log to file only")
    sub.add_parser("status", help="show products, pins and recent runs")
    p_pin = sub.add_parser("pin", help="price an ingredient from a specific product")
    p_pin.add_argument("ingredient")
    p_pin.add_argument("upc")
    p_pin.add_argument("--each-grams", type=float, help="weight of one piece, for produce sold by the pound")
    p_pin.add_argument("--note", default="")
    p_stores = sub.add_parser("stores", help="find store location ids near a zip code")
    p_stores.add_argument("--zip", required=True)
    p_stores.add_argument("--chain", help='e.g. "RALPHS"')
    p_sched = sub.add_parser("schedule", help="show (or --install) the daily Task Scheduler job")
    p_sched.add_argument("--at", default="05:00", help="time of day, HH:MM")
    p_sched.add_argument("--install", action="store_true", help="register it with Task Scheduler")
    args = parser.parse_args(argv)

    load_env_file()
    if args.command == "schedule":
        cmd = schedule_command(args.at)
        print(" ".join(cmd))
        if not args.install:
            print("\nRe-run with --install to register it. In Task Scheduler, also tick "
                  "'Run task as soon as possible after a scheduled start is missed'.")
            return 0
        print(f"launcher: {write_launcher()}")
        return subprocess.call(cmd)

    if args.command == "stores":
        for store in KrogerClient().find_locations(args.zip, chain=args.chain):
            print(f"{store['location_id']}  {store['name']}  ({store['address']})")
        return 0

    conn = grocery_db.connect()
    try:
        if args.command == "status":
            _print_status(conn)
            return 0
        if args.command == "pin":
            grocery_db.set_pin(conn, args.ingredient, args.upc, each_grams=args.each_grams, note=args.note)
            conn.commit()
            print(f"pinned {args.ingredient} -> {args.upc}")
            return 0
        if args.command == "init":
            _setup_logging()
            try:
                client = KrogerClient()
            except KrogerError as exc:
                log.warning("%s (store name left blank)", exc)
                client = None
            summary = init(conn, args.location, client, args.import_catalog)
            print(f"initialized {grocery_db.default_path()}: {summary}")
            return 0

        # run
        _setup_logging(verbose=not args.quiet)
        store = grocery_db.get_store(conn, args.location) if args.location else grocery_db.active_store(conn)
        if store is None:
            log.error("no store set up: run `py -m macro_optimizer.refresh init --location <id>` first")
            return 1
        try:
            client = KrogerClient()
        except KrogerError as exc:
            run_id = grocery_db.start_run(conn, store["location_id"])
            grocery_db.finish_run(conn, run_id, "failed", 0, {}, [str(exc)])
            log.error("%s", exc)
            return 1
        result = run_refresh(conn, client, store["location_id"], budget=args.budget)
        return {"ok": 0, "partial": 2}.get(result["status"], 1)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
