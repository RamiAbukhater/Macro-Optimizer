"""Session-cookie auth: password hashing, the login_required gate, and
g.user population. Kept separate from db.py's storage concerns and from
app.py's routing, matching how the core macro_optimizer package stays
framework-agnostic — auth is a webapp-layer concern only.
"""

import os
import secrets
from functools import wraps
from pathlib import Path

from flask import g, jsonify, redirect, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import db

SECRET_KEY_PATH = Path(__file__).parent / "data" / "secret_key"


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return check_password_hash(hashed, password)


def load_secret_key(app) -> None:
    """FLASK_SECRET_KEY env var if set, else a generated key persisted to a
    local gitignored file, so restarts don't invalidate every session."""
    env_key = os.environ.get("FLASK_SECRET_KEY")
    if env_key:
        app.secret_key = env_key
        return

    SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SECRET_KEY_PATH.exists():
        app.secret_key = SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
    else:
        key = secrets.token_hex(32)
        SECRET_KEY_PATH.write_text(key, encoding="utf-8")
        app.secret_key = key


def register_auth(app) -> None:
    """Populates g.user (or None) on every request from the session cookie."""
    @app.before_request
    def _load_current_user():
        user_id = session.get("user_id")
        g.user = db.get_user_by_id(user_id) if user_id else None


def login_required(view):
    """Gate a route on g.user being set. JSON 401 for /api/* routes (except
    /api/auth/* itself, which must stay reachable while logged out);
    redirect to /login for page routes."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required"}), 401
            return redirect("/login")
        return view(*args, **kwargs)
    return wrapped
