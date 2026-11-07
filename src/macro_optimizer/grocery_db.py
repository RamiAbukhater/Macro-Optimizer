"""The grocery database: which products the planner considers, and our own notes about them.

Kroger's API terms (Section 5e, see GROCERY_DATA.md) forbid keeping copies
of API content, so this file holds only identifiers and data that are ours:

- ``stores``: the Kroger-family stores we plan against.
- ``products``: UPCs worth asking Kroger about, the kind each one counts as
  (what lets the optimizer treat four brands of peanut butter as one food),
  and when our refresh job last saw each one on the shelf.
- ``catalog_terms``: the searches that discover products.
- ``ingredient_pins``: the reviewed product each recipe ingredient is priced from.
- ``refresh_runs``: one row per scheduled refresh, for "prices updated X ago" and debugging.

Prices, names and nutrition labels are fetched live (see market.py) and
never written here.

The file lives outside the repo, by default in ``~/.autoeat``, so OneDrive
never syncs a live SQLite file. Not ``%LOCALAPPDATA%``: Windows redirects
AppData writes from packaged (MSIX) apps into a private folder, so a
database created from one would be invisible to Task Scheduler and the web
app. Set ``AUTOEAT_GROCERY_DB`` to
put it somewhere else (tests point it at a temp file).
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

DB_ENV_VAR = "AUTOEAT_GROCERY_DB"

# A product the refresh hasn't seen on the shelf for this long stops being
# offered (missing), and after the longer window is retired (discontinued).
# Rows are never deleted.
MISSING_AFTER_DAYS = 14
DISCONTINUED_AFTER_DAYS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    location_id TEXT PRIMARY KEY,
    chain       TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    added_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    upc        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'missing', 'discontinued')),
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS products_status ON products(status);
CREATE TABLE IF NOT EXISTS catalog_terms (
    term   TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ingredient_pins (
    ingredient_id    TEXT PRIMARY KEY,
    upc              TEXT NOT NULL,
    search_term      TEXT NOT NULL DEFAULT '',
    each_grams       REAL,
    note             TEXT NOT NULL DEFAULT '',
    last_verified_at TEXT,
    verified_status  TEXT
);
CREATE TABLE IF NOT EXISTS refresh_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    api_calls   INTEGER NOT NULL DEFAULT 0,
    counts      TEXT NOT NULL DEFAULT '{}',
    errors      TEXT NOT NULL DEFAULT '[]'
);
"""


def default_path() -> Path:
    override = os.environ.get(DB_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".autoeat" / "grocery.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (creating if needed) the grocery database, schema included."""
    path = Path(path) if path else default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


def exists(path: Optional[Path] = None) -> bool:
    return (Path(path) if path else default_path()).exists()


# --- stores ------------------------------------------------------------------

def upsert_store(conn, location_id: str, chain: str = "", name: str = "", address: str = "") -> None:
    conn.execute(
        """INSERT INTO stores (location_id, chain, name, address, added_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(location_id) DO UPDATE SET
             chain = COALESCE(NULLIF(excluded.chain, ''), chain),
             name = COALESCE(NULLIF(excluded.name, ''), name),
             address = COALESCE(NULLIF(excluded.address, ''), address)""",
        (location_id, chain, name, address, now()),
    )


def active_store(conn) -> Optional[dict]:
    """The store to plan against: the earliest-added active one."""
    row = conn.execute("SELECT * FROM stores WHERE active = 1 ORDER BY added_at, location_id LIMIT 1").fetchone()
    return dict(row) if row else None


def get_store(conn, location_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM stores WHERE location_id = ?", (location_id,)).fetchone()
    return dict(row) if row else None


# --- products ----------------------------------------------------------------

def record_seen(conn, upc: str, kind: str = "", seen_at: Optional[str] = None) -> bool:
    """Note that a product was on the shelf. Returns True if it was new.

    A product's kind is set when it's first discovered and kept after
    that, so a later search that happens to surface it under another term
    doesn't silently move it between kinds.
    """
    seen_at = seen_at or now()
    is_new = conn.execute("SELECT 1 FROM products WHERE upc = ?", (upc,)).fetchone() is None
    conn.execute(
        """INSERT INTO products (upc, kind, status, first_seen, last_seen) VALUES (?, ?, 'active', ?, ?)
           ON CONFLICT(upc) DO UPDATE SET
             last_seen = MAX(last_seen, excluded.last_seen),
             status = 'active',
             kind = CASE WHEN kind = '' THEN excluded.kind ELSE kind END""",
        (upc, kind, seen_at, seen_at),
    )
    return is_new


def products(conn, statuses: Iterable[str] = ("active",)) -> List[dict]:
    statuses = list(statuses)
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT * FROM products WHERE status IN ({marks}) ORDER BY kind, upc", statuses
    ).fetchall()
    return [dict(r) for r in rows]


def update_statuses(conn, as_of: Optional[datetime] = None) -> Dict[str, int]:
    """Age products out by last_seen: active -> missing -> discontinued. Returns how many moved."""
    as_of = as_of or datetime.now(timezone.utc)
    missing_cut = (as_of - timedelta(days=MISSING_AFTER_DAYS)).isoformat(timespec="seconds")
    gone_cut = (as_of - timedelta(days=DISCONTINUED_AFTER_DAYS)).isoformat(timespec="seconds")
    discontinued = conn.execute(
        "UPDATE products SET status = 'discontinued' WHERE status != 'discontinued' AND last_seen < ?",
        (gone_cut,),
    ).rowcount
    missing = conn.execute(
        "UPDATE products SET status = 'missing' WHERE status = 'active' AND last_seen < ?",
        (missing_cut,),
    ).rowcount
    return {"missing": missing, "discontinued": discontinued}


def status_counts(conn) -> Dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM products GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


# --- catalog terms -----------------------------------------------------------

def add_terms(conn, terms: Iterable[str]) -> None:
    conn.executemany("INSERT OR IGNORE INTO catalog_terms (term) VALUES (?)", [(t,) for t in terms])


def active_terms(conn) -> List[str]:
    return [r["term"] for r in conn.execute("SELECT term FROM catalog_terms WHERE active = 1 ORDER BY term")]


# --- ingredient pins ---------------------------------------------------------

def set_pin(conn, ingredient_id: str, upc: str, search_term: str = "",
            each_grams: Optional[float] = None, note: str = "") -> None:
    conn.execute(
        """INSERT INTO ingredient_pins (ingredient_id, upc, search_term, each_grams, note) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(ingredient_id) DO UPDATE SET
             upc = excluded.upc, search_term = excluded.search_term, each_grams = excluded.each_grams,
             note = excluded.note, last_verified_at = NULL, verified_status = NULL""",
        (ingredient_id, upc, search_term, each_grams, note),
    )


def pins(conn) -> Dict[str, dict]:
    """Pins in the same shape kroger.price_ingredients takes: ``{ingredient: {"upc", "term", "each_grams"}}``."""
    out = {}
    for r in conn.execute("SELECT * FROM ingredient_pins ORDER BY ingredient_id"):
        pin = {"upc": r["upc"], "term": r["search_term"]}
        if r["each_grams"]:
            pin["each_grams"] = r["each_grams"]
        out[r["ingredient_id"]] = pin
    return out


def mark_pin_verified(conn, ingredient_id: str, status: str) -> None:
    conn.execute(
        "UPDATE ingredient_pins SET last_verified_at = ?, verified_status = ? WHERE ingredient_id = ?",
        (now(), status, ingredient_id),
    )


def pin_rows(conn) -> List[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM ingredient_pins ORDER BY ingredient_id")]


# --- refresh runs ------------------------------------------------------------

def start_run(conn, location_id: str) -> int:
    cur = conn.execute("INSERT INTO refresh_runs (location_id, started_at) VALUES (?, ?)", (location_id, now()))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, api_calls: int, counts: dict, errors: List[str]) -> None:
    conn.execute(
        "UPDATE refresh_runs SET finished_at = ?, status = ?, api_calls = ?, counts = ?, errors = ? WHERE id = ?",
        (now(), status, api_calls, json.dumps(counts), json.dumps(errors), run_id),
    )
    conn.commit()


def recent_runs(conn, limit: int = 5) -> List[dict]:
    rows = conn.execute("SELECT * FROM refresh_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        run = dict(r)
        run["counts"] = json.loads(run["counts"])
        run["errors"] = json.loads(run["errors"])
        out.append(run)
    return out


def last_successful_run(conn) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM refresh_runs WHERE status IN ('ok', 'partial') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None
