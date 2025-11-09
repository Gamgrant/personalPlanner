# jobs_service/__init__.py
from pathlib import Path
import os

# Try to normalize OAuth paths and find the project root
try:
    from utils.routing import ensure_google_oauth_env
    info = ensure_google_oauth_env(__file__)
    ROOT = Path(info["root"])
except Exception:
    # Fallback: assume jobs_service/ is under the project root
    ROOT = Path(__file__).resolve().parents[1]

# Load .env from .creds / .cred / project root
try:
    from dotenv import load_dotenv  # type: ignore

    for _env_path in [
        ROOT / ".creds" / ".env",
        ROOT / ".cred" / ".env",
        ROOT / ".env",
    ]:
        if _env_path.exists():
            load_dotenv(_env_path)
            break
except Exception:
    # dotenv is optional; if missing, env vars must be provided by the OS
    pass
