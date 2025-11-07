# google_sheets_service/agent_google_sheets.py

import os
import os.path
import json
from typing import List, Optional

from googleapiclient.errors import HttpError

from utils.google_service_helpers import get_google_service

from google.adk.agents import Agent
from google.genai import types

# Load the model name from environment variables if available. Defaults
# to 'gemini-2.5-flash'. Centralizing model configuration allows you to
# change the model across all agents via .env.

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# -------------------------------------------------------------------
# Service constructors (delegated to utils.google_service_helpers)
#
# We rely on the centralized helper to build Google API services. The SCOPES
# constant defined above specifies the scopes needed for both Sheets and the
# Drive API calls used by this agent. When obtaining a Drive service for
# listing spreadsheets, we pass these same scopes to get_google_service.

def get_sheets_service():
    """Return an authenticated Google Sheets service (never None)."""
    return get_google_service("sheets", "v4", SCOPES, "SHEETS")


def get_drive_service():
    """Return an authenticated Drive service for listing spreadsheets."""
    return get_google_service("drive", "v3", SCOPES, "SHEETS/DRIVE")

# -------------------------------
# Tools (keep input types simple to avoid anyOf)
# -------------------------------
from typing import List, Optional  # make sure this line exists

def list_spreadsheets(max_results: Optional[int] = None) -> List[str]:
    """
    List spreadsheets the user can access (Drive).
    """
    drive = get_drive_service()
    try:
        files = []
        page_token = None
        while True:
            params = {
                "q": "mimeType='application/vnd.google-apps.spreadsheet'",
                "pageSize": 1000,
                "fields": "nextPageToken, files(id,name,modifiedTime,webViewLink)",
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
            }
            if page_token:
                params["pageToken"] = page_token

            resp = drive.files().list(**params).execute()
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        if not files:
            return ["No spreadsheets found."]
        return [
            f"{f['name']} — ID: {f['id']} — Modified: {f.get('modifiedTime','?')} — Link: {f.get('webViewLink','-')}"
            for f in files
        ]
    except HttpError as e:
        raise ValueError(f"Failed to list spreadsheets: {e}")



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
    """
    Read all values in a range and return each row as a formatted string.

    The Google Sheets API does not impose a limit on the number of rows returned.
    This implementation returns every row without truncation.

    Args:
        spreadsheet_id: The ID of the spreadsheet to read from.
        range_name: The A1-notation range to retrieve (e.g., "Sheet1!A1:Z1000").

    Returns:
        A list of strings, one per row, with values padded to the width of
        the first row.  If the range is empty, a single-item list is returned
        describing the lack of data.
    """
    sheets = get_sheets_service()
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range_name
        ).execute()
        values = result.get("values", []) or []
        if not values:
            return [f"No data found in range '{range_name}'."]
        base_len = len(values[0])
        lines: List[str] = []
        for i, row in enumerate(values, 1):
            padded = row + [""] * max(0, base_len - len(row))
            lines.append(f"Row {i:2d}: {padded}")
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


google_sheets_agent : Agent = Agent(
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

__all__ = ["google_sheets_agent"]