"""
Helper functions for Google API authentication and service construction.

This module centralizes the OAuth credential handling logic used across
different agents (Sheets, Drive, Docs, Calendar, etc.). It ensures
environment variables are set via utils.routing.ensure_google_oauth_env,
loads credentials from disk or refreshes them as needed, and returns
initialized Google API service objects.

Usage:

    from utils.google_service_helpers import get_google_service
    # For Sheets:
    sheets_service = get_google_service(
        api_name="sheets",
        version="v4",
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly"],
        service_label="SHEETS",
    )

    # For Drive (read/write):
    drive_service = get_google_service(
        api_name="drive",
        version="v3",
        scopes=["https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/drive.file"],
        service_label="DRIVE",
    )

    # For Docs:
    docs_service = get_google_service(
        api_name="docs",
        version="v1",
        scopes=["https://www.googleapis.com/auth/documents",
                "https://www.googleapis.com/auth/drive.readonly"],
        service_label="DOCS",
    )

    # For Calendar:
    calendar_service = get_google_service(
        api_name="calendar",
        version="v3",
        scopes=["https://www.googleapis.com/auth/calendar"],
        service_label="CALENDAR",
    )

Notes:
    - This helper intentionally does not pass scopes to
      Credentials.from_authorized_user_file; the scope is only used when a
      new OAuth flow is necessary. This matches the logic used in the
      individual agents before consolidation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Import the helper to set environment variables for credentials. This ensures
# GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE are defined and
# absolute before we attempt to load them.
try:
    from utils.routing import ensure_google_oauth_env
except Exception:
    ensure_google_oauth_env = None  # type: ignore


def _get_service_credentials(service_label: str, scopes: List[str]) -> Credentials:
    """Return authorized Credentials for the given service label and scopes.

    This function handles reading the credentials and token paths from
    environment variables, refreshing them if expired, or running the
    interactive OAuth flow if needed. It relies on utils.routing to set
    environment variables to absolute paths if they aren't already set.

    Args:
        service_label: A short descriptor used in log messages and errors.
        scopes: A list of OAuth scopes required for the service.

    Returns:
        google.oauth2.credentials.Credentials: Authorized credentials for
            interacting with Google APIs.

    Raises:
        EnvironmentError: If the expected environment variables are missing.
        FileNotFoundError: If the credentials file cannot be located.
        RuntimeError: If credentials could not be acquired.
    """
    # Ensure environment variables are set to absolute paths.
    if ensure_google_oauth_env:
        try:
            ensure_google_oauth_env(__file__)
        except Exception:
            pass

    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")

    if not credentials_rel or not token_rel:
        raise EnvironmentError(
            f"[{service_label}] Missing GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE env vars."
        )

    # Both credentials_rel and token_rel should be absolute if ensure_google_oauth_env ran.
    # If not absolute (for robustness), treat them as relative to the project root.
    if os.path.isabs(credentials_rel):
        credentials_path = credentials_rel
    else:
        project_root = Path(__file__).resolve().parents[2]  # fallback: up two levels
        credentials_path = os.path.join(project_root, credentials_rel)

    if os.path.isabs(token_rel):
        token_path = token_rel
    else:
        project_root = Path(__file__).resolve().parents[2]
        token_path = os.path.join(project_root, token_rel)

    creds: Credentials | None = None

    # Attempt to load existing token; do not pass scopes here (mirroring prior logic).
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path)
        except Exception:
            creds = None

    # Refresh or run OAuth if necessary.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print(f"[{service_label}] Refreshing expired credentials…")
            creds.refresh(Request())
            try:
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            except PermissionError:
                # Likely running in a read-only environment (e.g., Cloud Run).
                # That's okay: we can still use the refreshed in-memory credentials.
                print(
                    f"[{service_label}] Warning: cannot write refreshed token to {token_path} "
                    "(read-only filesystem). Continuing with in-memory credentials."
                )

        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"[{service_label}] Missing credentials.json at {credentials_path}"
                )
            print(f"[{service_label}] Launching browser for new OAuth flow…")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
            creds = flow.run_local_server(port=0)
            os.makedirs(Path(token_path).parent, exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

    if creds is None:
        raise RuntimeError(f"[{service_label}] Failed to obtain credentials.")

    return creds


def get_google_service(api_name: str, version: str, scopes: List[str], service_label: str):
    """Construct and return a Google API service.

    Args:
        api_name: The name of the Google API (e.g., 'drive', 'sheets', 'docs').
        version: The version of the API (e.g., 'v3', 'v4').
        scopes: A list of scopes required for this service.
        service_label: A short label used in log messages and exceptions.

    Returns:
        A Google API service instance.

    Raises:
        RuntimeError: If the service could not be built.
    """
    creds = _get_service_credentials(service_label, scopes)
    try:
        service = build(api_name, version, credentials=creds, cache_discovery=False)
    except Exception as e:
        raise RuntimeError(
            f"[{service_label}] Failed to build {api_name.capitalize()} service: {e}"
        ) from e
    return service


# Convenience wrappers for common services. These functions use standard scopes
# as defined in the respective agents. Use these if you don't need custom
# scopes.

def get_sheets_service() -> object:
    """Get the Google Sheets API service with standard read/write scopes."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    return get_google_service("sheets", "v4", scopes, "SHEETS")


def get_drive_service(scopes: List[str] | None = None) -> object:
    """Get the Google Drive API service.

    If no scopes are provided, defaults to read/write scopes for Drive.
    """
    if scopes is None:
        scopes = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.file",
        ]
    return get_google_service("drive", "v3", scopes, "DRIVE")


def get_docs_service() -> object:
    """Get the Google Docs API service."""
    scopes = [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    return get_google_service("docs", "v1", scopes, "DOCS")


def get_calendar_service() -> object:
    """Get the Google Calendar API service."""
    scopes = ["https://www.googleapis.com/auth/calendar"]
    return get_google_service("calendar", "v3", scopes, "CALENDAR")


def get_gmail_service() -> object:
    """Get the Gmail API service."""
    scopes = ["https://mail.google.com/", "https://www.googleapis.com/auth/drive.readonly"]
    return get_google_service("gmail", "v1", scopes, "GMAIL")


def get_gmail_drive_service() -> object:
    """Get the Drive API service using Gmail's scopes for attachments."""
    # When accessing Drive via Gmail, use the same scopes as Gmail.
    scopes = ["https://mail.google.com/", "https://www.googleapis.com/auth/drive.readonly"]
    return get_google_service("drive", "v3", scopes, "GMAIL/DRIVE")