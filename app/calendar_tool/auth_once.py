# app/calendar_tool/auth_once.py
from __future__ import annotations
import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Paths
BASE_DIR   = Path(__file__).parent
CREDS_DIR  = BASE_DIR / "creds"
CREDS_DIR.mkdir(parents=True, exist_ok=True)

CLIENT_SECRET_PATH = CREDS_DIR / "credentials.json"  # <-- your OAuth client JSON
TOKEN_PATH         = CREDS_DIR / "token.json"

# Single full-access scope: read, write, freebusy, etc.
SCOPES = ["https://www.googleapis.com/auth/calendar"]

def main():
    # If a valid token exists with the right scope, keep it
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        # If token is valid, nothing to do
        if creds and creds.valid:
            print("Existing token is valid. Nothing to do.")
            return
        # Refresh if possible
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
            print("Token refreshed and saved.")
            return

    # Otherwise start the OAuth flow
    if not CLIENT_SECRET_PATH.exists():
        raise FileNotFoundError(
            f"Missing {CLIENT_SECRET_PATH}. Put your OAuth client JSON there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_PATH),
        scopes=SCOPES,
    )
    # Opens a browser to localhost callback; if the port is busy, it picks another.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    # Save the new token
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    # Print a quick summary
    data = json.loads(creds.to_json())
    granted = data.get("scopes") or []
    print("✅ New token saved to:", TOKEN_PATH)
    print("   Granted scopes:")
    for s in granted:
        print("   -", s)

if __name__ == "__main__":
    main()
