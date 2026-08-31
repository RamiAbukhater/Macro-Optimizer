"""One-off: load the pre-accounts webapp/data/{plan,history,deviations}.json
files into a single user's SQLite blobs, so the real local data that
predates the account system isn't just lost when the app stops reading
those files.

Run once, by hand, from the repo root:

    py scripts/migrate_json_to_sqlite.py you@example.com yourname yourpassword

Creates the account if it doesn't exist yet (using that email/username/
password), or logs into an existing one with those credentials. Refuses to
overwrite a blob that's already non-empty for that user, so re-running it is
safe.
"""

import json
import sys
from pathlib import Path

WEBAPP_DIR = Path(__file__).parent.parent / "webapp"
sys.path.insert(0, str(WEBAPP_DIR))

import auth  # noqa: E402
import db  # noqa: E402

DATA_DIR = WEBAPP_DIR / "data"
SOURCE_FILES = {
    "plan": DATA_DIR / "plan.json",
    "plan_previous": DATA_DIR / "plan_previous.json",
    "history": DATA_DIR / "history.json",
    "deviations": DATA_DIR / "deviations.json",
    "pantry": DATA_DIR / "pantry.json",
}


def get_or_create_user(email: str, username: str, password: str) -> dict:
    existing = db.get_user_by_identifier(email)
    if existing is not None:
        if not auth.verify_password(password, existing["password_hash"]):
            raise SystemExit(f"an account for {email!r} already exists but that password doesn't match")
        print(f"Using existing account: {existing['username']} <{existing['email']}> (id={existing['id']})")
        return existing

    user_id = db.create_user(email, username, auth.hash_password(password))
    print(f"Created account: {username} <{email}> (id={user_id})")
    return db.get_user_by_id(user_id)


def migrate(user_id: int) -> None:
    for kind, path in SOURCE_FILES.items():
        if not path.exists():
            print(f"  {kind}: no {path.name} on disk, skipping")
            continue

        existing = db.get_blob(user_id, kind)
        if existing:
            print(f"  {kind}: already has data for this account, skipping (delete it first to re-migrate)")
            continue

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        db.set_blob(user_id, kind, data)
        print(f"  {kind}: migrated from {path.name}")


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(f"usage: py {sys.argv[0]} <email> <username> <password>")
    email, username, password = sys.argv[1:4]

    db.init_db()
    user = get_or_create_user(email, username, password)
    migrate(user["id"])
    print("Done.")


if __name__ == "__main__":
    main()
