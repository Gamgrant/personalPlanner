# orchestrator_client.py
"""
Small helper client for talking to the ADK orchestrator service from Streamlit.

- Automatically locates `.creds/.env` using utils.routing.
- Safe to call create_session() multiple times (no exception if session exists).
- run_orchestrator() sends a user message and returns the events JSON.
"""

import os
import json
import requests
from pathlib import Path

from utils.routing import find_project_root, ensure_google_oauth_env

# Try to load python-dotenv to read `.env`
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


# ---- Locate project root and load .env from .creds ----
_project_root = find_project_root(__file__)
_creds_dir = Path(_project_root) / ".creds"
_env_path = _creds_dir / ".env"

if load_dotenv and _env_path.exists():
    load_dotenv(_env_path)

# Normalize Google OAuth paths (credentials.json, token.json)
ensure_google_oauth_env(__file__)


# ---- Config from environment, with sensible defaults ----
SERVICE_URL = os.environ.get("ORCHESTRATOR_SERVICE_URL")
APP_NAME = os.environ.get("ORCHESTRATOR_APP_NAME")
USER_ID = os.environ.get("ORCHESTRATOR_USER_ID")
SESSION_ID = os.environ.get("ORCHESTRATOR_SESSION_ID")


def pretty_print(obj):
    """Nicely format JSON for logs / debugging."""
    try:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    except Exception:
        print(obj)


def list_apps():
    """Optional helper: list available ADK apps."""
    url = f"{SERVICE_URL}/list-apps"
    resp = requests.get(url)
    resp.raise_for_status()
    apps = resp.json()
    print("Available apps:")
    pretty_print(apps)
    return apps


def create_session(initial_state=None):
    """
    Create or update a session.

    SAFE to call even if the session already exists.
    If backend returns 409 (Conflict), we treat as "session already exists"
    and DO NOT raise an exception.
    """
    if initial_state is None:
        initial_state = {}

    url = f"{SERVICE_URL}/apps/{APP_NAME}/users/{USER_ID}/sessions/{SESSION_ID}"
    resp = requests.post(url, json=initial_state)

    print("\n[create_session]")
    print("Status:", resp.status_code)
    print("Body:", resp.text[:500], "..." if len(resp.text) > 500 else "")

    if resp.status_code in (200, 201):
        try:
            session = resp.json()
            print("\nParsed session JSON:")
            pretty_print(session)
            return session
        except Exception as e:
            print("JSON parse error in create_session:", e)
            return None

    if resp.status_code == 409:
        # Session already exists – treat as success
        print("Session already exists; reusing it.")
        return None

    # Any other error should bubble up
    resp.raise_for_status()
    return None


def get_session():
    """Optional helper to inspect current session (for debugging)."""
    url = f"{SERVICE_URL}/apps/{APP_NAME}/users/{USER_ID}/sessions/{SESSION_ID}"
    resp = requests.get(url)
    resp.raise_for_status()
    session = resp.json()
    print("\n[get_session] Current session:")
    pretty_print(session)
    return session


def run_orchestrator(message_text: str):
    """
    Send a user message to the orchestrator and return the events JSON.

    Here we include session_id again because the /run endpoint expects it
    (otherwise we get a 422 validation error from FastAPI).
    """
    url = f"{SERVICE_URL}/run"
    payload = {
        "app_name": APP_NAME,
        "user_id": USER_ID,
        "session_id": SESSION_ID,  # <-- put this back
        "new_message": {
            "role": "user",
            "parts": [
                {"text": message_text},
            ],
        },
    }
    resp = requests.post(url, json=payload)

    print("\n[run_orchestrator]")
    print("Status:", resp.status_code)
    print("Body:", resp.text[:500], "..." if len(resp.text) > 500 else "")

    resp.raise_for_status()
    try:
        events = resp.json()
        print("\nEvents from orchestrator:")
        pretty_print(events)
        return events
    except Exception as e:
        print("JSON parse error in run_orchestrator:", e)
        return None


def send_to_orchestrator(message_text: str, init_if_needed: bool = True):
    """
    Convenience helper:
    - Optionally create/update the session first
    - Then send the message
    """
    if init_if_needed:
        create_session(initial_state={})
    return run_orchestrator(message_text)


def delete_session():
    """Optional helper to clear the session entirely."""
    url = f"{SERVICE_URL}/apps/{APP_NAME}/users/{USER_ID}/sessions/{SESSION_ID}"
    resp = requests.delete(url)
    print("\n[delete_session]")
    print("Status:", resp.status_code)
    print("Body:", resp.text[:500], "..." if len(resp.text) > 500 else "")
    if resp.status_code not in (200, 204):
        print("Delete returned non-success status.")
