from __future__ import annotations

import os
import io
from typing import Dict, Any, List, Optional

from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import get_google_service

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

CANDIDATE_SPREADSHEET_NAMES = [
    "Job_Search_Database",
    "job_search_spreadsheet",
]

# Update this if your actual sheet/tab name differs
INPUT_SHEET_NAME = "Sheet1"


# -------------------------------
# Google helpers
# -------------------------------

def get_sheets_service():
    return get_google_service("sheets", "v4", SCOPES, "SCRIPT_SHEETS")


def get_drive_service():
    return get_google_service("drive", "v3", SCOPES, "SCRIPT_DRIVE")


def _find_spreadsheet_id() -> str:
    drive = get_drive_service()
    for name in CANDIDATE_SPREADSHEET_NAMES:
        try:
            resp = drive.files().list(
                q=(
                    "mimeType='application/vnd.google-apps.spreadsheet' "
                    f"and name = '{name}' and trashed = false"
                ),
                pageSize=1,
                fields="files(id,name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
        except HttpError as e:
            raise RuntimeError(f"[SCRIPT] Drive search error: {e}")

        files = resp.get("files", []) or []
        if files:
            return files[0]["id"]
    raise RuntimeError(
        "[SCRIPT] Could not find Job_Search_Database. "
        "Expected one of: Job_Search_Database, job_search_spreadsheet."
    )


def _get_header_row(spreadsheet_id: str) -> List[str]:
    sheets = get_sheets_service()
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{INPUT_SHEET_NAME}!A1:Z1",
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to read header row: {e}")

    values = res.get("values", []) or []
    if not values:
        raise RuntimeError(f"[SCRIPT] No header row found in {INPUT_SHEET_NAME}.")
    return values[0]


def _get_header_map(spreadsheet_id: str) -> Dict[str, int]:
    header_row = _get_header_row(spreadsheet_id)
    header_map: Dict[str, int] = {}
    for idx, raw in enumerate(header_row):
        name = (raw or "").strip().lower()
        if name:
            header_map[name] = idx
    return header_map


def _ensure_email_script_column(spreadsheet_id: str, header_row: List[str]) -> int:
    """
    Ensure there is an 'Outreach email script' column.
    If missing, append it. Return 0-based index.
    """
    sheets = get_sheets_service()

    for idx, raw in enumerate(header_row):
        name = (raw or "").strip().lower()
        if name in ("outreach email script",):
            return idx

    new_header = list(header_row)
    new_header.append("Outreach email script")
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{INPUT_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [new_header]},
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to append Outreach email script column: {e}")

    return len(new_header) - 1


def _col_letter(idx_zero_based: int) -> str:
    """
    Convert 0-based column index to A1 notation.
    """
    n = idx_zero_based
    letters = ""
    while True:
        n, r = divmod(n, 26)
        letters = chr(ord("A") + r) + letters
        if n == 0:
            break
        n -= 1
    return letters


# -------------------------------
# Tool 1: Load CV from Google Drive
# -------------------------------

def load_cv_from_drive(cv_file_name: str) -> str:
    """
    Load the user's CV text from Google Drive by file name.

    Supports:
      - Google Docs (export as text/plain)
      - text/plain
      - application/pdf (parsed with pypdf)

    Returns:
      Full plain text for LLM reasoning.
    """
    if not cv_file_name.strip():
        raise ValueError("[SCRIPT] cv_file_name must be provided.")

    drive = get_drive_service()

    q_common = (
        "("
        "mimeType='application/vnd.google-apps.document' or "
        "mimeType='text/plain' or "
        "mimeType='application/pdf'"
        ") and trashed=false"
    )

    try:
        # Exact name first
        resp = drive.files().list(
            q=f"name='{cv_file_name}' and {q_common}",
            pageSize=10,
            fields="files(id,name,mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = resp.get("files", []) or []

        # Fallback: contains/starts with
        if not files:
            resp = drive.files().list(
                q=f"(name contains '{cv_file_name}' or name starts with '{cv_file_name}') and {q_common}",
                pageSize=10,
                fields="files(id,name,mimeType)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files = resp.get("files", []) or []
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Drive search error for CV: {e}")

    if not files:
        raise RuntimeError(f"[SCRIPT] Could not find CV file named like '{cv_file_name}'.")

    # Prefer Docs, then text, then PDF
    def pick(mime: str) -> Optional[Dict[str, Any]]:
        for f in files:
            if f.get("mimeType") == mime:
                return f
        return None

    file = (
        pick("application/vnd.google-apps.document")
        or pick("text/plain")
        or pick("application/pdf")
        or files[0]
    )
    file_id = file["id"]
    mime = file["mimeType"]

    try:
        if mime == "application/vnd.google-apps.document":
            data = drive.files().export(fileId=file_id, mimeType="text/plain").execute()
            return data.decode("utf-8", errors="ignore") if isinstance(data, (bytes, bytearray)) else str(data)

        if mime == "text/plain":
            data = drive.files().get_media(fileId=file_id).execute()
            return data.decode("utf-8", errors="ignore") if isinstance(data, (bytes, bytearray)) else str(data)

        if mime == "application/pdf":
            from pypdf import PdfReader  # requires pypdf installed
            pdf_bytes = drive.files().get_media(fileId=file_id).execute()
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages_text = []
            for page in reader.pages:
                pages_text.append(page.extract_text() or "")
            text = "\n".join(pages_text).strip()
            if not text:
                raise RuntimeError("[SCRIPT] PDF CV has no extractable text (likely scanned).")
            return text

        # Fallback: try raw bytes -> text
        data = drive.files().get_media(fileId=file_id).execute()
        return data.decode("utf-8", errors="ignore") if isinstance(data, (bytes, bytearray)) else str(data)

    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to load CV content: {e}")


# -------------------------------
# Tool 2: Read rows needing email scripts
# -------------------------------

def list_rows_for_email_scripts(max_rows: int = 50) -> List[Dict[str, Any]]:
    """
    Return rows ready for an Outreach email script.

    A row qualifies if:
      - Outreach Name is present
      - Outreach Email is present
      - Outreach email script is empty

    Each item includes:
      {
        "row_number": int,
        "job_title": str,
        "company": str,
        "location": str,
        "description": str,
        "degree_req": str,
        "yoe_req": str,
        "skills_req": str,
        "outreach_name": str,
        "outreach_email": str,
      }
    """
    spreadsheet_id = _find_spreadsheet_id()
    header_row = _get_header_row(spreadsheet_id)
    header_map = _get_header_map(spreadsheet_id)
    script_col_idx = _ensure_email_script_column(spreadsheet_id, header_row)

    def col(*names: str) -> Optional[int]:
        for n in names:
            idx = header_map.get(n.lower())
            if idx is not None:
                return idx
        return None

    job_col = col("jobs")
    company_col = col("company")
    location_col = col("location")
    desc_col = col("description")
    degree_col = col("degree")
    yoe_col = col("yoe", "years of experience")
    skills_col = col("skills")
    outreach_name_col = col("outreach name")
    outreach_email_col = col("outreach email")
    # phone exists but not required for email script
    # outreach_phone_col = col("outreach phone number")

    if outreach_name_col is None or outreach_email_col is None:
        raise RuntimeError("[SCRIPT] Missing 'Outreach Name' or 'Outreach Email' column.")

    sheets = get_sheets_service()
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{INPUT_SHEET_NAME}!A2:Z1000",
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to read rows: {e}")

    rows = res.get("values", []) or []
    results: List[Dict[str, Any]] = []

    for i, row in enumerate(rows):
        row_number = i + 2

        def get(idx: Optional[int]) -> str:
            if idx is None:
                return ""
            return row[idx] if idx < len(row) else ""

        outreach_name = get(outreach_name_col).strip()
        outreach_email = get(outreach_email_col).strip()
        script_val = get(script_col_idx).strip()

        if outreach_name and outreach_email and not script_val:
            item = {
                "row_number": row_number,
                "job_title": get(job_col).strip(),
                "company": get(company_col).strip(),
                "location": get(location_col).strip(),
                "description": get(desc_col).strip(),
                "degree_req": get(degree_col).strip(),
                "yoe_req": get(yoe_col).strip(),
                "skills_req": get(skills_col).strip(),
                "outreach_name": outreach_name,
                "outreach_email": outreach_email,
            }
            results.append(item)

        if max_rows and len(results) >= max_rows:
            break

    return results


# -------------------------------
# Tool 3: Write email script into sheet
# -------------------------------

def write_email_script_for_row(row_number: int, script: str) -> str:
    """
    Write outreach email script into the 'Outreach email script' column for a given row.
    """
    if row_number < 2:
        raise ValueError("[SCRIPT] row_number must be >= 2 (data rows).")

    spreadsheet_id = _find_spreadsheet_id()
    header_row = _get_header_row(spreadsheet_id)
    script_col_idx = _ensure_email_script_column(spreadsheet_id, header_row)

    col = _col_letter(script_col_idx)
    cell_range = f"{INPUT_SHEET_NAME}!{col}{row_number}"

    sheets = get_sheets_service()
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=cell_range,
            valueInputOption="USER_ENTERED",
            body={"values": [[script.strip()]]},
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to write script to row {row_number}: {e}")

    return f"[SCRIPT] Wrote outreach email script to row {row_number}."


# -------------------------------
# Agent instructions
# -------------------------------

script_agent_instruction = """
You are script_agent.

Goal:
- For each job row in the jobs sheet, generate a tailored cold outreach email
  to the recruiter and store it in the 'Outreach email script' column.

Sheet layout (columns of interest) from {spreadsheet_agent_apollo}:
  Jobs
  Website
  Company
  Location
  Date Posted
  Description
  Degree
  YOE
  Skills
  Matching Score
  Good Match?
  customize now? [user input]
  latex done?
  Outreach Name
  Outreach Email
  Outreach Phone Number
  Outreach email script
  Outreach phone script

Workflow:

1. Obtain the CV
- The orchestrator or UI must provide a `cv_file_name` (e.g., "steven_yeo_cv").
- Call load_cv_from_drive(cv_file_name) to load FULL CV text.
- Use your own reasoning over this CV text to understand:
    - core skills
    - experience level
    - domains/tech stack
    - notable achievements

2. Find rows needing scripts
- Call list_rows_for_email_scripts(max_rows=...) to get rows where:
    - Outreach Name is present
    - Outreach Email is present
    - Outreach email script is EMPTY
- Each item gives you:
    - row_number
    - job_title, company, location
    - description, degree_req, yoe_req, skills_req
    - outreach_name, outreach_email

3. Generate the outreach email (LLM reasoning ONLY)
For each returned row:
- Write a concise (< 200 words), high-quality email that:
    - Addresses the recruiter by name (Outreach Name).
    - Mentions the company and, if present, the specific role (Jobs).
    - Connects the candidate's background (from CV) to:
        • job description
        • degree_req, yoe_req, skills_req (when available).
    - Is specific and non-generic.
    - Includes 1–2 concrete, relevant strengths (no buzzword soup).
    - Has a friendly, professional tone.
    - Ends with a clear, low-friction call to action (e.g., short intro chat).

4. Store the script
- After generating an email for a row:
    - Call write_email_script_for_row(row_number, script).
- Do NOT overwrite an existing 'Outreach email script' unless explicitly instructed.
- Ensure that each recruiter email ends up associated with at most one strong script
  (if multiple rows share the same Outreach Email, you should only create one script).

5. Do NOT send emails.
- Actual sending is handled by a Gmail agent after explicit user confirmation.
- Your sole responsibility is: read CV + sheet, reason, and write the outreach copy.

Use only the provided tools for I/O.
All content generation must use your own reasoning and the given context.
"""

script_agent = Agent(
    model=MODEL,
    name="script_agent",
    description=script_agent_instruction,
    tools=[load_cv_from_drive, list_rows_for_email_scripts, write_email_script_for_row],
    generate_content_config=types.GenerateContentConfig(temperature=0.4),
    output_key = "updated_sheet"
)

__all__ = [
    "script_agent",
    "load_cv_from_drive",
    "list_rows_for_email_scripts",
    "write_email_script_for_row",
]