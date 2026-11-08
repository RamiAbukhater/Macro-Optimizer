"""Load API credentials from the repo's gitignored ``.env`` file."""

import os
from pathlib import Path
from typing import Optional

PACKAGE_DIR = Path(__file__).resolve().parent


def find_env_file(start: Path = PACKAGE_DIR) -> Optional[Path]:
    """The nearest ``.env`` at or above ``start``: the repo root's, or the main checkout's from a worktree."""
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env_file(path: Optional[Path] = None) -> Optional[Path]:
    """Read KEY=value lines into the environment; real environment variables win. Returns the file used."""
    path = path or find_env_file()
    if path is None or not Path(path).exists():
        return None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if value:
            os.environ.setdefault(key.strip(), value)
    return Path(path)
