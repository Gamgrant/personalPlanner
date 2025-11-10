import os
import json
from typing import List, Dict, Any, Optional

from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import get_google_service  # centralized auth

# Model comes from .env (utils/env_loader has already been called earlier)
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")
JOB_SEARCH_SPREADSHEET_ID = os.environ.get("JOB_SEARCH_SPREADSHEET_ID").strip()

# Scopes: read/write Sheets + Drive listing
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# -------------------------------
# Service factories
# -------------------------------
def get_sheets_service() -> object:
    return get_google_service("sheets", "v4", SCOPES, "BIGQUERY_SHEETS")

def get_drive_service() -> object:
    return get_google_service("drive", "v3", SCOPES, "BIGQUERY_DRIVE")

# -------------------------------
# Tools (AFC-friendly signatures)
# -------------------------------
def list_spreadsheets(max_results: Optional[int] = None) -> List[str]:
    """
    List all spreadsheets the user can access (across My Drive and Shared Drives).
    If max_results is provided and > 0, truncates to that many; otherwise returns all.
    """
    drive = get_drive_service()
    try:
        files: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            page_size = 1000
            if max_results and max_results > 0:
                remaining = max_results - len(files)
                if remaining <= 0:
                    break
                page_size = min(page_size, remaining)

            params = {
                "q": "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
                "fields": "nextPageToken, files(id,name,modifiedTime,webViewLink)",
                "orderBy": "modifiedTime desc",
                "pageSize": page_size,
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
            }
            if page_token:
                params["pageToken"] = page_token

            resp = drive.files().list(**params).execute()
            files.extend(resp.get("files", []) or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        if not files:
            return ["No spreadsheets found."]

        return [
            f"{f.get('name','(untitled)')} — ID: {f.get('id')} — "
            f"Modified: {f.get('modifiedTime','?')} — Link: {f.get('webViewLink','-')}"
            for f in files
        ]
    except HttpError as e:
        raise ValueError(f"Failed to list spreadsheets: {e}")

def get_spreadsheet_info(spreadsheet_id: str) -> str:
    """Return spreadsheet title and sheet names/sizes."""
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
        raise ValueError(f"Failed to get spreadsheet info: {e}")

def read_sheet_values(spreadsheet_id: str, range_name: str = "") -> List[str]:
    """
    Read values from a range. If range_name is empty or just a sheet name,
    returns all used values from that sheet (no 50-row caps).
    Examples: "", "Sheet1", "Sheet1!A1:Z1000"
    """
    sheets = get_sheets_service()
    try:
        # If empty range, the API supports using only the sheet name to get entire used grid.
        # If even that is empty, try the spreadsheet's first sheet name.
        effective_range = range_name
        if not effective_range:
            meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets(properties(title))").execute()
            first = (meta.get("sheets") or [{}])[0]
            title = (first.get("properties") or {}).get("title", "Sheet1")
            effective_range = title  # entire sheet used range

        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=effective_range
        ).execute()
        values = result.get("values", []) or []
        if not values:
            return [f"No data found in range '{effective_range}'."]
        base_len = len(values[0])
        lines: List[str] = []
        for i, row in enumerate(values, 1):
            padded = row + [""] * max(0, base_len - len(row))
            lines.append(f"Row {i:2d}: {padded}")
        return lines
    except HttpError as e:
        raise ValueError(f"Failed to read values: {e}")

def write_sheet_values(
    spreadsheet_id: str,
    range_name: str,
    values_json: str,
    value_input_option: str = "USER_ENTERED",
) -> str:
    """Write a 2D JSON array into a range (no silent caps)."""
    sheets = get_sheets_service()
    try:
        try:
            data_2d = json.loads(values_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"'values_json' must be valid JSON: {e}")

        if not isinstance(data_2d, list) or any(not isinstance(r, list) for r in data_2d):
            raise ValueError("'values_json' must decode to a 2D list, e.g. [[...],[...]]")

        result = sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheets_id if False else spreadsheet_id,  # keep key stable
            range=range_name,
            valueInputOption=value_input_option,
            body={"values": data_2d},
        ).execute()

        return (
            f"Updated '{range_name}'. "
            f"Cells: {result.get('updatedCells', 0)}, "
            f"Rows: {result.get('updatedRows', 0)}, "
            f"Columns: {result.get('updatedColumns', 0)}."
        )
    except HttpError as e:
        raise ValueError(f"Failed to write values: {e}")

# -------------------------------
# Helpers for Job_search_Database
# -------------------------------
def _find_job_search_spreadsheet_id(name: str = "Job_Search_Database") -> str:
    """
    Resolve the Job_Search_Database spreadsheet ID.

    Priority:
      1) Hardcoded/env JOB_SEARCH_SPREADSHEET_ID (explicit and fastest)
      2) Search Google Drive for a Google Sheet with matching name,
         optionally restricted to JOB_SEARCH_FOLDER_ID.
    """
    # 1) Explicit ID (primary)
    if JOB_SEARCH_SPREADSHEET_ID:
        return JOB_SEARCH_SPREADSHEET_ID

    drive = get_drive_service()

    target_lower = name.lower()
    candidates: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    folder_id = os.environ.get("JOB_SEARCH_FOLDER_ID")
    base_query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    if folder_id:
        base_query += f" and '{folder_id}' in parents"

    while True:
        resp = drive.files().list(
            q=base_query,
            pageSize=1000,
            fields="nextPageToken, files(id,name,webViewLink)",
            orderBy="modifiedTime desc",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageToken=page_token,
        ).execute()
        files = resp.get("files", []) or []
        candidates.extend(files)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not candidates:
        raise ValueError(
            f"No spreadsheets available to search. "
            f"Ensure Drive access and sharing permissions are set, "
            f"or set JOB_SEARCH_SPREADSHEET_ID."
        )

    for f in candidates:
        if (f.get("name") or "").lower() == target_lower:
            return f["id"]
    for f in candidates:
        if (f.get("name") or "").lower().startswith(target_lower):
            return f["id"]
    for f in candidates:
        if target_lower in (f.get("name") or "").lower():
            return f["id"]

    raise ValueError(
        f"Spreadsheet named like '{name}' not found in Drive. "
        f"Either rename the sheet or set JOB_SEARCH_SPREADSHEET_ID."
    )

def _get_first_sheet_name(spreadsheet_id: str) -> str:
    sheets = get_sheets_service()
    resp = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(title))",
    ).execute()
    sheets_list = resp.get("sheets", []) or []
    if not sheets_list:
        raise ValueError("Target spreadsheet has no sheets.")
    return sheets_list[0]["properties"]["title"]

def append_jobs_to_job_search_database(jobs_result: List[Dict[str, Any]]) -> str:
    """
    Append job results (title, url, company, location, date_posted, description)
    into the first sheet of 'Job_search_Database'.

    Layout (first sheet):
      A: Jobs
      B: Website
      C: Company
      D: Location
      E: Date Posted
      F: Description
    """
    if not jobs_result:
        return "No jobs to append (jobs_result is empty)."

    rows: List[List[Any]] = []
    for j in jobs_result:
        if not isinstance(j, dict):
            continue
        rows.append(
            [
                j.get("title", ""),
                j.get("url", ""),
                j.get("company", ""),
                j.get("location", ""),
                j.get("date_posted", ""),
                j.get("description", ""),  # <-- NEW: write description into column F
            ]
        )

    if not rows:
        return "No valid job records found in jobs_result."

    spreadsheet_id = _find_job_search_spreadsheet_id("Job_Search_Database")
    sheet_name = _get_first_sheet_name(spreadsheet_id)
    sheets = get_sheets_service()

    # A..F (6 columns) now matches the 6 values above
    result = sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A2:F",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

    updated = result.get("updates", {}).get("updatedRows") or len(rows)
    return f"Appended {updated} job rows to '{sheet_name}' in 'Job_Search_Database'."

# -------------------------------
# Agents
# -------------------------------


job_sheets_agent_instruction = """
You take structured job results from the ATS Jobs Agent (output_key='jobs_result')
and append them into the existing 'Job_Search_Database' Google Sheet.
""".strip()

database_agent = Agent(
    model=MODEL,
    name="job_search_sheets_agent",
    description=(
        "Appends ATS job search results into the 'Job_Search_Database' spreadsheet. "
        + job_sheets_agent_instruction
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0),
    tools=[append_jobs_to_job_search_database],
    output_key="spreadsheet",
)
