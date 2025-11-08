from __future__ import annotations

import os
import re
import requests
from typing import List, Optional, Dict, Any

from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import get_google_service

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# -------------------------------
# Google API clients
# -------------------------------

def get_sheets_service() -> object:
    return get_google_service("sheets", "v4", SCOPES, "BACKFILL_SHEETS")


def get_drive_service() -> object:
    return get_google_service("drive", "v3", SCOPES, "BACKFILL_DRIVE")


# -------------------------------
# Spreadsheet discovery helpers
# -------------------------------

def _find_job_search_spreadsheet_id(name: str = "Job_search_Database") -> str:
    """
    Locate the job search spreadsheet by exact name across all accessible drives.
    """
    drive = get_drive_service()
    try:
        resp = drive.files().list(
            q=(
                "mimeType='application/vnd.google-apps.spreadsheet' "
                f"and name='{name}' and trashed=false"
            ),
            pageSize=10,
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Drive API error: {e}")

    files: List[Dict[str, Any]] = resp.get("files", []) or []
    if not files:
        raise RuntimeError(
            f"[BACKFILL] Spreadsheet '{name}' not found. "
            f"Create it or update the name in _find_job_search_spreadsheet_id."
        )
    return files[0]["id"]


def _get_first_sheet_name(spreadsheet_id: str) -> str:
    """
    Return the title of the first sheet/tab in the spreadsheet.
    """
    sheets = get_sheets_service()
    resp = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(title))",
    ).execute()
    sheets_list = resp.get("sheets", []) or []
    if not sheets_list:
        raise RuntimeError("[BACKFILL] Target spreadsheet has no sheets.")
    return sheets_list[0]["properties"]["title"]


# -------------------------------
# HTML / description helpers
# -------------------------------

def _normalize_html_to_text(html: str, max_chars: int = 600) -> str:
    """
    Strip HTML to a concise single-string description.
    Caps length at max_chars on a word boundary.
    """
    if not html:
        return ""
    # Remove scripts/styles
    html = re.sub(
        r"<script[^>]*>.*?</script>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(
        r"<style[^>]*>.*?</style>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    # Truncate
    if len(text) > max_chars:
        cut = text[: max_chars - 3]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut + "..."
    return text


# Optional domain -> Greenhouse board mapping
GH_COMPANY_FROM_DOMAIN: Dict[str, str] = {
    "stripe.com": "stripe",
    "databricks.com": "databricks",
    "asana.com": "asana",
    "anthropic.com": "anthropic",
    # extend as needed
}


def _infer_greenhouse_company_from_url(url: str) -> Optional[str]:
    """
    Best-effort: infer Greenhouse board name for gh_jid URLs.
    """
    url_lower = url.lower()
    for domain, board in GH_COMPANY_FROM_DOMAIN.items():
        if domain in url_lower:
            return board

    m = re.search(r"job-boards\.greenhouse\.io/([^/]+)/jobs/", url_lower)
    if m:
        return m.group(1)

    return None


def _fetch_description_from_url(url: str, max_chars: int = 600) -> str:
    """
    Derive a concise job description from a Website URL.

    - Always uses the URL from the 'Website' column.
    - If URL has gh_jid, tries Greenhouse API using inferred board.
    - Otherwise falls back to fetching HTML and stripping to summary.
    """
    if not url:
        return ""

    # Case 1: URLs like ...?gh_jid=123456
    gh_match = re.search(r"[?&]gh_jid=(\d+)", url)
    if gh_match:
        job_id = gh_match.group(1)
        company = _infer_greenhouse_company_from_url(url)
        if company:
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}?content=true"
            try:
                r = requests.get(api_url, timeout=12)
                r.raise_for_status()
                data = r.json() or {}
                html = data.get("content") or data.get("description") or ""
                text = _normalize_html_to_text(html, max_chars=max_chars)
                if text:
                    return text
            except Exception:
                # Fall through to generic fetch
                pass

    # Case 2: generic: fetch and strip page HTML
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        return _normalize_html_to_text(r.text, max_chars=max_chars)
    except Exception:
        return ""


# -------------------------------
# Core tool: backfill descriptions
# -------------------------------

def backfill_job_descriptions(
    max_rows: Optional[int] = None,
    max_chars: int = 600,
) -> str:
    """
    Backfill the Description column in Job_search_Database.

    Expected layout (first sheet):
        A: Jobs
        B: Website (URL, source of truth)
        C: Company
        D: Location
        E: Date Posted
        F: Description  <-- filled by this tool
        G: Years of Experience

    For each row where:
        - Website (B) has a non-empty URL, and
        - Description (F) is empty,
    fetch a concise description from Website and write it into F.

    Only column F is modified. Other columns are left unchanged.
    """
    sheets = get_sheets_service()
    spreadsheet_id = _find_job_search_spreadsheet_id("Job_search_Database")
    sheet_name = _get_first_sheet_name(spreadsheet_id)

    # Include through column G so we don't accidentally shrink rows
    data_range = f"{sheet_name}!A2:G"

    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=data_range,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Failed to read sheet values: {e}")

    rows: List[List[str]] = result.get("values", []) or []
    if not rows:
        return "[BACKFILL] No rows found."

    # Decide how many rows to inspect
    limit = len(rows) if not max_rows or max_rows <= 0 else min(max_rows, len(rows))

    updates: List[Dict[str, Any]] = []
    updated_count = 0

    for idx in range(limit):
        row = rows[idx]

        # Ensure at least 7 columns (A-G)
        if len(row) < 7:
            row = row + [""] * (7 - len(row))

        website = (row[1] or "").strip()      # B: Website
        description = (row[5] or "").strip()  # F: Description

        if website and not description:
            desc = _fetch_description_from_url(website, max_chars=max_chars)
            if desc:
                row_number = idx + 2  # data starts at row 2
                updates.append({
                    "range": f"{sheet_name}!F{row_number}",
                    "values": [[desc]],
                })
                updated_count += 1

    if not updates:
        return "[BACKFILL] No descriptions updated (all filled or no fetchable content)."

    try:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": updates,
            },
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Failed to write updated descriptions: {e}")

    return f"[BACKFILL] Updated descriptions for {updated_count} job(s) in '{sheet_name}'."


# -------------------------------
# Agent definition
# -------------------------------

backfill_agent_instruction = """
You enrich the existing 'Job_search_Database' Google Sheet.

Behavior:
- Use the first sheet of 'Job_search_Database'.
- Interpret columns as:
    A: Jobs
    B: Website
    C: Company
    D: Location
    E: Date Posted
    F: Description
    G: Years of Experience
- For any row where Description (F) is empty and Website (B) contains a URL:
    - Use that URL as the source of truth.
    - If the URL includes 'gh_jid', infer the Greenhouse board and query the public Greenhouse API.
    - Otherwise, fetch the page HTML and extract a concise, human-readable summary.
- Limit each description to ~2–4 sentences (max ~600 characters).
- Only write to column F. Never change other columns or create new spreadsheets.
""".strip()

description_agent = Agent(
    model=MODEL,
    name="job_description_backfill_agent",
    description=backfill_agent_instruction,
    tools=[backfill_job_descriptions],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)

__all__ = ["description_agent", "backfill_job_descriptions"]