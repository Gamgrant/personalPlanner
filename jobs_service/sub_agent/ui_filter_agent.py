# manager_agent/sub_agent/job_description_backfill_agent.py

from __future__ import annotations


import os
import os.path
import re
import json
from typing import List, Optional

import requests
from googleapiclient.errors import HttpError

from utils.google_service_helpers import get_google_service

from google.adk.agents import Agent
from google.genai import types

# Load the model name from environment variables if available. Defaults to
# 'gemini-2.5-flash' when unspecified. Centralizing this variable allows
# configuration via .env without editing code in multiple places.
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# The scopes required for accessing the Job_search_Database sheet. We pass
# these to get_google_service when constructing Sheets and Drive services.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# -------------------------------------------------------------------
# Service constructors (delegated to utils.google_service_helpers)
#
# The Job description backfill agent requires access to both Sheets and Drive.
# We use the centralized get_google_service helper for both.

def get_sheets_service() -> object:
    return get_google_service("sheets", "v4", SCOPES, "BACKFILL_SHEETS")


def get_drive_service() -> object:
    return get_google_service("drive", "v3", SCOPES, "BACKFILL_DRIVE")

# ---------- Sheet discovery ----------

def _find_job_search_spreadsheet_id(name: str = "Job_search_Database") -> str:
    """
    Locate the job search spreadsheet by name across all accessible drives.

    The Drive API call includes ``supportsAllDrives`` and ``includeItemsFromAllDrives``
    so that spreadsheets stored in shared drives or shared with the user are
    discovered.  If no matching spreadsheet is found, a RuntimeError is raised.

    Args:
        name: The exact name of the spreadsheet to find.

    Returns:
        The ID of the spreadsheet.
    """
    drive = get_drive_service()
    try:
        # Use a large page size and include shared drives to find the file
        resp = drive.files().list(
            q=(
                "mimeType='application/vnd.google-apps.spreadsheet' "
                f"and name='{name}' and trashed=false"
            ),
            pageSize=1000,
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Drive API error: {e}")
    files = resp.get("files", []) or []
    if not files:
        raise RuntimeError(
            f"[BACKFILL] Spreadsheet '{name}' not found. "
            f"Create it or adjust the name in _find_job_search_spreadsheet_id."
        )
    return files[0]["id"]


def _get_first_sheet_name(spreadsheet_id: str) -> str:
    sheets = get_sheets_service()
    resp = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(title))",
    ).execute()
    sheets_list = resp.get("sheets", []) or []
    if not sheets_list:
        raise RuntimeError("[BACKFILL] Target spreadsheet has no sheets.")
    return sheets_list[0]["properties"]["title"]

# ---------- Fetch & normalize descriptions ----------

def _normalize_html_to_text(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_description_from_url(url: str) -> str:
    if not url:
        return ""

    # 1) Greenhouse official API if matches pattern
    m = re.search(r"boards\.greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if m:
        company = m.group(1)
        job_id = m.group(2)
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}?content=true"
        try:
            r = requests.get(api_url, timeout=15)
            r.raise_for_status()
            data = r.json() or {}
            html = data.get("content") or data.get("description") or ""
            text = _normalize_html_to_text(html)
            if text:
                return text
        except Exception:
            # fall through to generic fetch
            pass

    # 2) Generic fallback: fetch page HTML and strip to text
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return _normalize_html_to_text(r.text)
    except Exception:
        return ""

# ---------- Core tool ----------
def backfill_job_descriptions(max_rows: Optional[int] = None) -> str:
    """
    Look at 'Job_search_Database' (first sheet):

    - Column B: Website
    - Column E: Description

    For each row where Description is empty and Website has a URL,
    fetch a description from the URL and write it back into column E.
    """
    sheets = get_sheets_service()
    spreadsheet_id = _find_job_search_spreadsheet_id("Job_search_Database")
    sheet_name = _get_first_sheet_name(spreadsheet_id)

    data_range = f"{sheet_name}!A2:F"  # A:Jobs, B:Website, C:Company, D:Location, E:Description, F:Date Posted
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=data_range,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Failed to read sheet values: {e}")

    rows = result.get("values", []) or []
    if not rows:
        return "[BACKFILL] No rows found in Job_search_Database."

    updated_rows: List[List[str]] = []
    updated_count = 0

    # Determine how many rows to process. If max_rows is None or non-positive,
    # process all rows. Otherwise, process only up to max_rows.
    total_to_process = len(rows) if not max_rows or max_rows <= 0 else min(len(rows), max_rows)
    for idx, row in enumerate(rows[:total_to_process], start=2):  # logical row number (A2=2)
        # pad row to 6 columns
        if len(row) < 6:
            row = row + [""] * (6 - len(row))

        website = row[1].strip() if len(row) > 1 else ""
        description = row[4].strip() if len(row) > 4 else ""

        if website and not description:
            desc = _fetch_description_from_url(website)
            if desc:
                row[4] = desc
                updated_count += 1

        updated_rows.append(row)

    # If we processed fewer than existing rows, keep the tail unchanged
    if len(rows) > total_to_process:
        updated_rows.extend(rows[total_to_process:])

    if updated_count == 0:
        return "[BACKFILL] No descriptions updated (all filled or no valid URLs)."

    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=data_range,
            valueInputOption="USER_ENTERED",
            body={"values": updated_rows},
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Failed to write updated descriptions: {e}")

    return f"[BACKFILL] Updated descriptions for {updated_count} job(s) in '{sheet_name}'."

# ---------- Agent ----------
####Add some new tools here,
#1. Year of experience
#2. Salary
#3. Degree
#3. Required skills/Desirable skills

####Can add the curator agent,
backfill_agent_instruction = """
You update the 'Description' column in the existing 'Job_search_Database' Google Sheet.

Behavior:
- Use the first sheet of 'Job_search_Database'.
- Treat column B as 'Website' and column E as 'Description'.
- For any row where Description is empty and Website has a URL:
    * If it's a Greenhouse job URL, call the official Greenhouse boards API.
    * Otherwise, fetch the page HTML and extract a short plain-text description.
- Write descriptions back into column E in-place.
- Do not create new spreadsheets or modify other columns.
"""

filter_ui_agent = Agent(
    model=MODEL,
    name="job_description_backfill_agent",
    description="Backfills missing job descriptions in Job_search_Database from the Website column.",
    tools=[backfill_job_descriptions],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)
__all__ = ["filter_ui_agent"]

