"""SQLite-backed account store: users, plus one JSON blob per user per kind
of state (pantry, plan, plan_previous, history, deviations, taste_profile).

Mirrors the app's existing persistence pattern (Pantry/PickHistory/
DeviationLog/plan.json: one opaque JSON blob, loaded whole, rewritten whole
on every mutation), just keyed by user instead of by a single global file
path. A fresh, short-lived connection is opened per call rather than a
pooled/long-lived one, matching that same "load fresh, save fresh" spirit.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent / "data" / "app.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_blobs (
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind       TEXT NOT NULL,
                data       TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, kind)
            )
        """)


def create_user(email: str, username: str, password_hash: str) -> int:
    """Insert a new user, returning its id. Raises sqlite3.IntegrityError on
    a duplicate email or username (case-insensitive, per the UNIQUE COLLATE
    NOCASE constraint)."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (email, username, password_hash, _now()),
        )
        return cur.lastrowid


def get_user_by_id(user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_identifier(identifier: str) -> Optional[dict]:
    """Matches against email OR username, for login."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? OR username = ?", (identifier, identifier),
        ).fetchone()
        return dict(row) if row else None


def get_blob(user_id: int, kind: str, default: Any = None) -> Any:
    with _connect() as conn:
        row = conn.execute(
            "SELECT data FROM user_blobs WHERE user_id = ? AND kind = ?", (user_id, kind),
        ).fetchone()
        return json.loads(row["data"]) if row else default


def set_blob(user_id: int, kind: str, data: Any) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_blobs (user_id, kind, data, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, kind) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
            """,
            (user_id, kind, json.dumps(data), _now()),
        )
