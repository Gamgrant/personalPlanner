# verify.py — one-time OAuth to create/refresh token.json (no API calls)
import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

# Base directory: project root (one level up from .creds)
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from either .creds/.env or project root .env
try:
    from dotenv import load_dotenv
    for candidate in [BASE_DIR / ".creds" / ".env", BASE_DIR / ".env"]:
        if candidate.exists():
            load_dotenv(candidate)
            break
except Exception:
    pass

# Paths from .env, with sane defaults into .creds/
CREDENTIALS_FILE = os.environ.get(
    "GOOGLE_OAUTH_CLIENT_FILE",
    str((Path(__file__).resolve().parent / "credentials.json"))
)
TOKEN_FILE = os.environ.get(
    "GOOGLE_OAUTH_TOKEN_FILE",
    str((Path(__file__).resolve().parent / "token.json"))
)

# Scopes for personal Calendar + Gmail (adjust as needed)
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/docs",
    "https://www.googleapis.com/auth/documents",
]

def verify_credentials():
    """
    Run browser OAuth consent for the given SCOPES and write token.json.
    This script is for authentication only—no API calls are made.
    """
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"❌ Error: {CREDENTIALS_FILE} not found. Put your OAuth client here or set GOOGLE_OAUTH_CLIENT_FILE.")
        return

    try:
        print("🔐 Opening browser for Google OAuth consent...")
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        # offline + prompt=consent ensures a refresh token and upgrades scopes if needed
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        print("✅ Authentication successful.")
        print(f"💾 Saved token to: {TOKEN_FILE}")
        print(f"🔎 Scopes granted: {', '.join(SCOPES)}")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")

if __name__ == "__main__":
    verify_credentials()
