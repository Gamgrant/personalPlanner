# personalPlanner/utils/routing.py
from __future__ import annotations
import os
from pathlib import Path
from typing import Iterable

# Recognize both `.creds` and `.cred` markers so the project can be configured
# either way.  We include `pyproject.toml` and `.git` as additional markers
# for locating the project root.
_MARKERS: Iterable[str] = (".creds", ".cred", "pyproject.toml", ".git")

def find_project_root(start: str | Path) -> Path:
    """Walk upward from 'start' until we find a marker that identifies the repo root."""
    p = Path(start).resolve()
    for a in [p] + list(p.parents):
        if any((a / m).exists() for m in _MARKERS):
            return a
    # Fallback: 2 levels up usually lands at personalPlanner/ in your tree
    return p.parents[2]

def _first_existing(*paths: Path) -> Path | None:
    """Return the first path in `paths` that exists, or None if none do."""
    for p in paths:
        if p.exists():
            return p
    return None


def ensure_google_oauth_env(start: str | Path) -> dict:
    """
    Set GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE environment variables to
    absolute paths within the project's credential directory.

    This helper prefers the `.creds` directory if it exists, but will fall back
    to `.cred` if that exists instead.  By normalizing these variables to
    absolute paths, downstream code that constructs credential paths via
    ``os.path.join(project_root, credentials_rel)`` will correctly ignore
    ``project_root`` when ``credentials_rel`` is already absolute.

    Args:
        start: A path within the project (typically ``__file__`` from the caller).

    Returns:
        A dict with the project root and resolved credential/token paths for
        diagnostic purposes.
    """
    root = find_project_root(start)
    # Prefer .creds over .cred; fall back to whichever exists.
    creds_dir = _first_existing(root / ".creds", root / ".cred")
    # If neither directory exists, pick .creds as default.  This way the error
    # message will point to .creds even if it hasn't been created yet.
    if creds_dir is None:
        creds_dir = root / ".creds"

    default_client = creds_dir / "credentials.json"
    default_token = creds_dir / "token.json"

    # Only set the variables if they are not already defined (allows overrides)
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_FILE", str(default_client))
    os.environ.setdefault("GOOGLE_OAUTH_TOKEN_FILE", str(default_token))

    # Normalize to absolute paths (even if previously set relative in .env)
    os.environ["GOOGLE_OAUTH_CLIENT_FILE"] = str(Path(os.environ["GOOGLE_OAUTH_CLIENT_FILE"]).resolve())
    os.environ["GOOGLE_OAUTH_TOKEN_FILE"] = str(Path(os.environ["GOOGLE_OAUTH_TOKEN_FILE"]).resolve())

    return {
        "root": str(root),
        "credentials": os.environ["GOOGLE_OAUTH_CLIENT_FILE"],
        "token": os.environ["GOOGLE_OAUTH_TOKEN_FILE"],
    }
