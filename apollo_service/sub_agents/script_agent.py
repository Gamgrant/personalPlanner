from __future__ import annotations

import os
import io
from typing import Dict, Any, List, Optional

from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import get_google_service

# -------------------------------
# CONFIG
# -------------------------------

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Prefer explicit spreadsheet id from env (recommended)
JOB_SEARCH_SPREADSHEET_ID = (os.environ.get("JOB_SEARCH_SPREADSHEET_ID") or "").strip()

# Optional fallback names if env var is not set
CANDIDATE_SPREADSHEET_NAMES = [
    "Job_Search_Database",
    "job_search_spreadsheet",
]

# Tab name that holds the jobs
INPUT_SHEET_NAME = "Sheet1"

# -------------------------------
# Google helpers
# -------------------------------

def get_sheets_service():
    return get_google_service("sheets", "v4", SCOPES, "SCRIPT_SHEETS")


def get_drive_service():
    return get_google_service("drive", "v3", SCOPES, "SCRIPT_DRIVE")


def _find_spreadsheet_id() -> str:
    """
    Resolve the Job Search spreadsheet ID.

    Priority:
      1. JOB_SEARCH_SPREADSHEET_ID env var
      2. Fallback: search Drive by known candidate names
    """
    if JOB_SEARCH_SPREADSHEET_ID:
        return JOB_SEARCH_SPREADSHEET_ID

    drive = get_drive_service()
    try:
        for name in CANDIDATE_SPREADSHEET_NAMES:
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
            files = resp.get("files", []) or []
            if files:
                return files[0]["id"]
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Drive search error: {e}")

    raise RuntimeError(
        "[SCRIPT] JOB_SEARCH_SPREADSHEET_ID is not set and no matching "
        "Job_Search_Database/job_search_spreadsheet was found in Drive."
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
    Ensure there is an 'Outreach Email Script' column.
    If missing, append it. Return 0-based index.
    """
    sheets = get_sheets_service()

    for idx, raw in enumerate(header_row):
        name = (raw or "").strip().lower()
        if name in ("outreach email script", "outreach_email_script"):
            return idx

    new_header = list(header_row)
    new_header.append("Outreach Email Script")
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{INPUT_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [new_header]},
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to append Outreach Email Script column: {e}")

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
# Tool 1: Load CV from Google Drive by file ID
# -------------------------------

def load_cv_from_drive_by_id(file_id: str) -> str:
    """
    Load the user's CV text from Google Drive by file ID.

    Supports:
      - Google Docs (export as text/plain)
      - text/plain
      - application/pdf (parsed with pypdf)

    Returns:
      Full plain text for LLM reasoning.
    """
    file_id = (file_id or "").strip()
    if not file_id:
        raise ValueError("[SCRIPT] file_id must be provided.")

    drive = get_drive_service()

    try:
        meta = drive.files().get(fileId=file_id, fields="mimeType").execute()
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to fetch file metadata for CV: {e}")

    mime = meta.get("mimeType", "")

    try:
        if mime == "application/vnd.google-apps.document":
            data = drive.files().export(
                fileId=file_id,
                mimeType="text/plain",
            ).execute()
            return (
                data.decode("utf-8", errors="ignore")
                if isinstance(data, (bytes, bytearray))
                else str(data)
            )

        if mime == "text/plain":
            data = drive.files().get_media(fileId=file_id).execute()
            return (
                data.decode("utf-8", errors="ignore")
                if isinstance(data, (bytes, bytearray))
                else str(data)
            )

        if mime == "application/pdf":
            try:
                # Try to use pypdf if available
                from pypdf import PdfReader  # may not be installed in some environments

                pdf_bytes = drive.files().get_media(fileId=file_id).execute()
                reader = PdfReader(io.BytesIO(pdf_bytes))
                pages_text = []
                for page in reader.pages:
                    pages_text.append(page.extract_text() or "")
                text = "\n".join(pages_text).strip()
                if text:
                    return text
            except Exception:
                # Either pypdf is missing or parsing failed; just fall through
                pass

            # Fallback: no text extracted – return empty text.
            # The email script generator will still work using sheet context only.
            return ""

        # Fallback: try raw bytes as text
        data = drive.files().get_media(fileId=file_id).execute()
        return (
            data.decode("utf-8", errors="ignore")
            if isinstance(data, (bytes, bytearray))
            else str(data)
        )

    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to load CV content: {e}")


# -------------------------------
# Tool 2: Read rows needing email scripts (based on resume file id)
# -------------------------------

def list_rows_for_email_scripts(max_rows: int = 50) -> List[Dict[str, Any]]:
    """
    Return rows ready for an Outreach Email Script.

    A row qualifies if:
      - resume_id_latex_done (or similar) has a file ID (customized resume exists)
      - Outreach Name is present
      - Outreach email is present
      - Outreach Email Script is empty

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
        "resume_file_id": str,
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
    outreach_email_col = col("outreach email", "outreach email ")
    resume_id_col = col("resume_id_latex_done", "resume file id", "resume_id")

    if outreach_name_col is None or outreach_email_col is None:
        raise RuntimeError("[SCRIPT] Missing 'Outreach Name' or 'Outreach email' column.")
    if resume_id_col is None:
        raise RuntimeError("[SCRIPT] Missing 'resume_id_latex_done' (resume file id) column.")

    sheets = get_sheets_service()
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{INPUT_SHEET_NAME}!A2:Z2000",
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[SCRIPT] Failed to read sheet rows: {e}")

    rows = res.get("values", []) or []
    results: List[Dict[str, Any]] = []

    for i, row in enumerate(rows):
        row_number = i + 2  # 1-based + header

        def get(idx: Optional[int]) -> str:
            if idx is None:
                return ""
            return (row[idx] if idx < len(row) else "").strip()

        outreach_name = get(outreach_name_col)
        outreach_email = get(outreach_email_col)
        script_val = get(script_col_idx)
        resume_file_id = get(resume_id_col)

        # Only generate when we have a customized resume (file id) and no script yet
        if outreach_name and outreach_email and resume_file_id and not script_val:
            item = {
                "row_number": row_number,
                "job_title": get(job_col),
                "company": get(company_col),
                "location": get(location_col),
                "description": get(desc_col),
                "degree_req": get(degree_col),
                "yoe_req": get(yoe_col),
                "skills_req": get(skills_col),
                "outreach_name": outreach_name,
                "outreach_email": outreach_email,
                "resume_file_id": resume_file_id,
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
    Write outreach email script into the 'Outreach Email Script' column for a given row.
    Does not touch any other cells.
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

End-to-end responsibility:
- For each job row in the jobs sheet, when a customized resume exists for that row,
  generate a tailored cold outreach email to the recruiter and store it in the
  'Outreach Email Script' column.

Sheet layout (columns of interest):
  Jobs
  Website
  Company
  Location
  Date Posted
  Description
  Degree
  YOE
  Skills
  Good_Match_Yes_No
  customize_now
  resume_id_latex_done        (Google Drive file ID of the customized resume)
  Outreach Name
  Outreach email
  Outreach Phone Number
  Outreach Email Script

Workflow (DO NOT ask for confirmation; just act):

1. Discover target rows:
   - Call list_rows_for_email_scripts(max_rows=...).
   - This returns only rows where:
       • resume_id_latex_done has a file ID
       • Outreach Name is present
       • Outreach email is present
       • Outreach Email Script is currently empty

2. For each returned row:
   - Use resume_file_id to load the candidate's resume:
       • Call load_cv_from_drive_by_id(resume_file_id).
   - Use the CV text + row context:
       • job_title, company, location
       • description, degree_req, yoe_req, skills_req
       • outreach_name (recruiter), outreach_email

3. Generate a personalized outreach email:
   - Concise (aim for <= 200 words).
   - Professional, specific, non-generic.
   - Address the recruiter by name.
   - Mention the company and the role explicitly.
   - Tie 1–3 concrete experiences / skills from the CV directly
     to the role requirements.
   - End with a clear, low-friction call to action
     (e.g., short intro call or async review).

4. Persist the script:
   - Immediately call write_email_script_for_row(row_number, script)
     for each row.
   - Do NOT overwrite an existing Outreach Email Script.
   - IMPORTANT: A single recruiter may appear in multiple rows;
     you SHOULD create distinct scripts per row/role. Do NOT deduplicate
     by email.

5. No sending:
   - This agent never sends emails.
   - It only reads CVs + sheet data, reasons, and writes Outreach Email Scripts.

Use ONLY the provided tools for Drive/Sheets I/O.
Never request extra input from the user inside this workflow.
"""


script_agent = Agent(
    model=MODEL,
    name="script_agent",
    description=script_agent_instruction,
    tools=[
        load_cv_from_drive_by_id,
        list_rows_for_email_scripts,
        write_email_script_for_row,
    ],
    generate_content_config=types.GenerateContentConfig(temperature=0.4),
    output_key="updated_sheet",
)

__all__ = [
    "script_agent",
    "load_cv_from_drive_by_id",
    "list_rows_for_email_scripts",
    "write_email_script_for_row",
]