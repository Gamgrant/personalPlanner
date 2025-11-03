# google_sheets_service/agent_google_sheets.py

import os
import os.path
import json
from typing import List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google.adk.agents import Agent
from google.genai import types

MODEL = "gemini-2.5-flash"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# -------------------------------
# Auth bootstrap (same pattern as your Gmail agent)
# -------------------------------
def get_sheets_service():
    """Return an authenticated Google Sheets service (never None)."""
    creds = None

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))

    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel       = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")

    if not credentials_rel or not token_rel:
        raise EnvironmentError(
            "[SHEETS] Expected GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE env vars "
            "(relative to project root)."
        )

    credentials_path = os.path.join(project_root, credentials_rel)
    token_path       = os.path.join(project_root, token_rel)

    print(f"[SHEETS] Looking for credentials at: {credentials_path}")
    print(f"[SHEETS] Looking for token at: {token_path}")

    # Load existing token AS-IS (don’t pass scopes) to avoid re-scoping
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path)
            print("[SHEETS] Existing token.json found and loaded.")
        except (UnicodeDecodeError, ValueError):
            print("[SHEETS] token.json invalid or corrupted. Re-authorizing…")
            try:
                os.remove(token_path)
            except OSError:
                pass
            creds = None

    # Refresh or run OAuth only if necessary
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[SHEETS] Refreshing expired credentials…")
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[SHEETS] Refreshed token.json saved.")
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f"[SHEETS] Missing credentials.json at {credentials_path}")
            print("[SHEETS] Launching browser for new Google OAuth flow…")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[SHEETS] New token.json created successfully.")

    if creds is None:
        raise RuntimeError("[SHEETS] No credentials available after auth flow/refresh.")

    # Build the Sheets API client; never return None.
    try:
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as e:
        raise RuntimeError(f"[SHEETS] Failed to build Sheets service: {e}") from e

    if service is None:
        raise RuntimeError("[SHEETS] googleapiclient.discovery.build returned None.")

    print("[SHEETS] Sheets service initialized successfully.")
    return service


def get_drive_service():
    """Return an authenticated Drive service for listing spreadsheets."""
    creds = None

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))

    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel       = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")

    if not credentials_rel or not token_rel:
        raise EnvironmentError(
            "[SHEETS] Expected GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE env vars "
            "(relative to project root)."
        )

    credentials_path = os.path.join(project_root, credentials_rel)
    token_path       = os.path.join(project_root, token_rel)

    print(f"[SHEETS/DRIVE] Looking for credentials at: {credentials_path}")
    print(f"[SHEETS/DRIVE] Looking for token at: {token_path}")

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path)
            print("[SHEETS/DRIVE] Existing token.json found and loaded.")
        except (UnicodeDecodeError, ValueError):
            print("[SHEETS/DRIVE] token.json invalid or corrupted. Re-authorizing…")
            try:
                os.remove(token_path)
            except OSError:
                pass
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[SHEETS/DRIVE] Refreshing expired credentials…")
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[SHEETS/DRIVE] Refreshed token.json saved.")
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f"[SHEETS/DRIVE] Missing credentials.json at {credentials_path}")
            print("[SHEETS/DRIVE] Launching browser for new Google OAuth flow…")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[SHEETS/DRIVE] New token.json created successfully.")

    if creds is None:
        raise RuntimeError("[SHEETS/DRIVE] No credentials available after auth flow/refresh.")

    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        raise RuntimeError(f"[SHEETS/DRIVE] Failed to build Drive service: {e}") from e

    if service is None:
        raise RuntimeError("[SHEETS/DRIVE] googleapiclient.discovery.build returned None.")

    print("[SHEETS/DRIVE] Drive service initialized successfully.")
    return service
    
# -------------------------------
# Tools (keep input types simple to avoid anyOf)
# -------------------------------
def list_spreadsheets(max_results: int = 25) -> List[str]:
    """List spreadsheets the user can access (Drive)."""
    drive = get_drive_service()
    try:
        resp = drive.files().list(
            q="mimeType='application/vnd.google-apps.spreadsheet'",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()
        files = resp.get("files", []) or []
        if not files:
            return ["No spreadsheets found."]
        return [
            f"{f.get('name','(untitled)')} — ID: {f.get('id')} — Modified: {f.get('modifiedTime','?')} — Link: {f.get('webViewLink','-')}"
            for f in files
        ]
    except HttpError as e:
        raise ValueError(f"Failed to list spreadsheets: {str(e)}")


def get_spreadsheet_info(spreadsheet_id: str) -> str:
    """Get spreadsheet title and sheet list."""
    sheets = get_sheets_service()
    try:
        spreadsheet = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        title = spreadsheet.get("properties", {}).get("title", "Untitled")
        raw_sheets = spreadsheet.get("sheets", []) or []
        lines = [f'Spreadsheet: "{title}" (ID: {spreadsheet_id})', f"Sheets ({len(raw_sheets)}):"]
        for s in raw_sheets:
            props = s.get("properties", {}) or {}
            name = props.get("title", "Sheet")
            sid = props.get("sheetId", "?")
            grid = props.get("gridProperties", {}) or {}
            rows = grid.get("rowCount", "?")
            cols = grid.get("columnCount", "?")
            lines.append(f'  - "{name}" (ID: {sid}) | Size: {rows}x{cols}')
        return "\n".join(lines)
    except HttpError as e:
        raise ValueError(f"Failed to get spreadsheet info: {str(e)}")


def read_sheet_values(spreadsheet_id: str, range_name: str = "A1:Z1000") -> List[str]:
    """Read values in a range. Returns up to first 50 rows as formatted lines."""
    sheets = get_sheets_service()
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range_name
        ).execute()
        values = result.get("values", []) or []
        if not values:
            return [f"No data found in range '{range_name}'."]
        base_len = len(values[0])
        lines = []
        for i, row in enumerate(values[:50], 1):
            padded = row + [""] * max(0, base_len - len(row))
            lines.append(f"Row {i:2d}: {padded}")
        if len(values) > 50:
            lines.append(f"... and {len(values) - 50} more rows")
        return lines
    except HttpError as e:
        raise ValueError(f"Failed to read values: {str(e)}")


def write_sheet_values(
    spreadsheet_id: str,
    range_name: str,
    values_json: str,
    value_input_option: str = "USER_ENTERED",  # or "RAW"
) -> str:
    """
    Write/update a range with a JSON string representing a 2D array.
    Example values_json: "[[\"Task\",\"Done\"],[\"Migrate\",\"Yes\"]]"
    """
    sheets = get_sheets_service()
    try:
        try:
            data_2d = json.loads(values_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"'values_json' must be valid JSON: {e}")

        if not isinstance(data_2d, list) or any(not isinstance(r, list) for r in data_2d):
            raise ValueError("'values_json' must decode to a 2D list, e.g. [[...],[...]]")

        result = sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheets_id if False else spreadsheet_id,  # keep linter quiet if you like
            range=range_name,
            valueInputOption=value_input_option,
            body={"values": data_2d},
        ).execute()

        return (
            f"Updated '{range_name}'. "
            f"Cells: {result.get('updatedCells', 0)}, Rows: {result.get('updatedRows', 0)}, Columns: {result.get('updatedColumns', 0)}."
        )
    except HttpError as e:
        raise ValueError(f"Failed to write values: {str(e)}")


def clear_sheet_values(spreadsheet_id: str, range_name: str) -> str:
    """Clear values in a range."""
    sheets = get_sheets_service()
    try:
        result = sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=range_name
        ).execute()
        cleared = result.get("clearedRange", range_name)
        return f"Cleared range '{cleared}'."
    except HttpError as e:
        raise ValueError(f"Failed to clear values: {str(e)}")


def create_spreadsheet(title: str) -> str:
    """Create a new spreadsheet. Returns ID and URL."""
    sheets = get_sheets_service()
    try:
        resp = sheets.spreadsheets().create(body={"properties": {"title": title}}).execute()
        return f"Created spreadsheet '{title}'. ID: {resp.get('spreadsheetId')} | URL: {resp.get('spreadsheetUrl')}"
    except HttpError as e:
        raise ValueError(f"Failed to create spreadsheet: {str(e)}")


def create_sheet(spreadsheet_id: str, sheet_name: str) -> str:
    """Add a sheet (tab) to an existing spreadsheet."""
    sheets = get_sheets_service()
    try:
        body = {"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}
        resp = sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        sid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        return f"Created sheet '{sheet_name}' (ID: {sid})."
    except HttpError as e:
        raise ValueError(f"Failed to create sheet: {str(e)}")


# -------------------------------
# Agent instructions & factory
# -------------------------------
sheets_agent_instruction_text = """
You are a focused Google Sheets assistant. You can list spreadsheets, inspect sheet metadata,
read ranges, write/update ranges (with a JSON string), clear ranges, create spreadsheets, and add sheets (tabs).

Rules:
- Use A1 notation (e.g., 'Sheet1!A1:D10').
- For writing, pass `values_json` as a JSON-encoded 2D array, e.g. "[[\"Task\",\"Done\"],[\"Migrate\",\"Yes\"]]".
- 'USER_ENTERED' respects formulas/locale; 'RAW' writes literal values.
- Confirm actions with affected range or created IDs/URLs.
""".strip()


def build_agent():
    return Agent(
        model=MODEL,
        name="google_sheets_agent",
        description=(
            "Google Sheets assistant for listing spreadsheets, reading/writing ranges, "
            "clearing ranges, creating spreadsheets, and adding sheets. "
            + sheets_agent_instruction_text
        ),
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[
            list_spreadsheets,
            get_spreadsheet_info,
            read_sheet_values,
            write_sheet_values,   # <-- uses values_json: str (no Union/Optional)
            clear_sheet_values,   # <-- separate clear tool
            create_spreadsheet,
            create_sheet,
        ],
    )