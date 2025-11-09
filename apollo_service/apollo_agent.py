#!/usr/bin/env python3
import os
import re
from typing import Dict, Any, List, Optional

import requests
from googleapiclient.errors import HttpError

from utils.google_service_helpers import get_google_service
from google.adk.agents import Agent
from google.genai import types

# ---------------------------------------------------
# CONFIG
# ---------------------------------------------------

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

APOLLO_API_KEY = "" # set this in your env
if not APOLLO_API_KEY:
    # allow override for local quick testing, but don't hardcode in prod
    APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY_HARDCODE", "")

if not APOLLO_API_KEY:
    # fail early so it's obvious
    raise EnvironmentError("APOLLO_API_KEY is not set. Please configure it in your environment.")

BASE_URL = "https://api.apollo.io/api/v1"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# We’ll try these names in order and use the first that exists
CANDIDATE_SPREADSHEET_NAMES = [
    "jobs_search_database",
    "job_search_spredsheet",
]

# Sheet that contains the jobs + website column
INPUT_SHEET_NAME = "Input"

# We will:
# - detect "Website" column dynamically
# - detect "Outreach Name" and "Outreach email" columns dynamically


# ---------------------------------------------------
# GOOGLE HELPERS
# ---------------------------------------------------

def get_sheets_service():
    return get_google_service("sheets", "v4", SCOPES, "SHEETS")


def get_drive_service():
    return get_google_service("drive", "v3", SCOPES, "SHEETS/DRIVE")


def _find_spreadsheet_id() -> Optional[str]:
    """Find the first matching spreadsheet by known candidate names."""
    drive = get_drive_service()
    try:
        for name in CANDIDATE_SPREADSHEET_NAMES:
            q = (
                "mimeType='application/vnd.google-apps.spreadsheet' "
                f"and name = '{name}' and trashed = false"
            )
            resp = drive.files().list(
                q=q,
                pageSize=1,
                fields="files(id,name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files = resp.get("files", []) or []
            if files:
                return files[0]["id"]
        return None
    except HttpError as e:
        raise ValueError(f"Failed to locate jobs spreadsheet: {e}")


def _get_header_map(spreadsheet_id: str) -> Dict[str, int]:
    """
    Read the header row from Input!A1:Z1 and build a map of
    normalized header -> column index (0-based).
    """
    sheets = get_sheets_service()
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{INPUT_SHEET_NAME}!A1:Z1",
        ).execute()
        values = res.get("values", []) or []
        if not values:
            raise ValueError("No header row found in Input sheet.")
        header_row = values[0]
    except HttpError as e:
        raise ValueError(f"Failed to read header row: {e}")

    header_map: Dict[str, int] = {}
    for idx, raw in enumerate(header_row):
        name = (raw or "").strip().lower()
        if not name:
            continue
        header_map[name] = idx
    return header_map


def _normalize_domain(website: str) -> Optional[str]:
    """
    Extract domain from a website URL or domain-ish string.
    e.g. https://www.stripe.com/careers -> stripe.com
    """
    if not website:
        return None
    w = website.strip().lower()
    # Strip protocol
    w = re.sub(r"^https?://", "", w)
    # Strip leading www.
    w = re.sub(r"^www\.", "", w)
    # Take up to first slash
    w = w.split("/")[0].strip()
    return w or None


# ---------------------------------------------------
# APOLLO HELPERS
# ---------------------------------------------------

def _headers() -> Dict[str, str]:
    """Shared headers using x-api-key auth."""
    return {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "x-api-key": APOLLO_API_KEY,
    }


def search_recruiters_at_company(domain: str, per_page: int = 5) -> List[Dict[str, Any]]:
    """
    /mixed_people/search for recruiter-type roles at the given company domain.
    This does NOT unlock new emails; it's just candidate discovery.
    """
    url = f"{BASE_URL}/mixed_people/search"

    payload = {
        "q_organization_domains_list": [domain],
        "person_titles": [
            "recruiter",
            "technical recruiter",
            "university recruiter",
            "campus recruiter",
            "talent acquisition",
            "talent acquisition specialist",
            "recruiting manager",
            "talent acquisition partner",
        ],
        "include_similar_titles": True,
        "person_seniorities": ["entry", "senior", "manager", "director", "head"],
        "contact_email_status": ["verified"],
        "page": 1,
        "per_page": per_page,
    }

    resp = requests.post(url, headers=_headers(), json=payload)
    if not resp.ok:
        # soft fail: just return no candidates; caller decides
        return []

    data = resp.json()
    people = data.get("people") or data.get("contacts") or data.get("persons") or []
    return people


def match_person_for_email(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    domain: Optional[str] = None,
    person_id: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Optional[str]:
    """
    /people/match to retrieve a real email for one person.
    This is the call that consumes a credit IF Apollo returns enriched data.
    """
    url = f"{BASE_URL}/people/match"

    payload = {
        "id": person_id,
        "first_name": first_name,
        "last_name": last_name,
        "domain": domain,
        "linkedin_url": linkedin_url,
        "reveal_personal_emails": False,  # stay with work emails only
        # Do NOT send reveal_phone_number -> no webhook requirement
    }
    payload = {k: v for k, v in payload.items() if v}

    if not payload:
        return None

    resp = requests.post(url, headers=_headers(), json=payload)
    if not resp.ok:
        return None

    data = resp.json()
    person = data.get("person") or {}

    # Some responses use person.email directly
    direct_email = person.get("email")
    if direct_email:
        return direct_email

    # Otherwise look in email_addresses[]
    emails = person.get("email_addresses") or []
    work_email = None
    for e in emails:
        if not isinstance(e, dict):
            continue
        addr = e.get("email")
        etype = e.get("type")
        if addr:
            if etype == "work":
                work_email = addr
                break
            if not work_email:
                work_email = addr

    return work_email


# ---------------------------------------------------
# CORE TOOL: SEARCH + MATCH + WRITE BACK
# ---------------------------------------------------

def populate_outreach_from_apollo(
    per_company_candidates: int = 5,
) -> str:
    """
    Workflow:
      1. Locate jobs spreadsheet (jobs_search_database / job_search_spredsheet).
      2. In 'Input' sheet, find:
           - Website column (by header 'website')
           - Outreach Name column (by header 'outreach name')
           - Outreach email column (by header 'outreach email')
      3. For each row with a Website:
           - Extract domain from Website
           - /mixed_people/search for recruiter-type people at that domain
           - Take top 1 candidate
           - /people/match using id + name + domain
           - Write:
                Outreach Name  = matched recruiter's full name
                Outreach email = matched work email
         Existing values in these two columns will be overwritten
         for rows we successfully process.
    """
    spreadsheet_id = _find_spreadsheet_id()
    if not spreadsheet_id:
        raise ValueError(
            "Could not find 'jobs_search_database' or 'job_search_spredsheet' in Drive."
        )

    sheets = get_sheets_service()
    header_map = _get_header_map(spreadsheet_id)

    # Identify relevant columns
    website_col_idx = None
    outreach_name_col_idx = None
    outreach_email_col_idx = None

    for name, idx in header_map.items():
        if name == "website":
            website_col_idx = idx
        elif name == "outreach name":
            outreach_name_col_idx = idx
        elif name == "outreach email":
            outreach_email_col_idx = idx

    if website_col_idx is None:
        raise ValueError("No 'Website' column header found in Input sheet.")

    if outreach_name_col_idx is None or outreach_email_col_idx is None:
        raise ValueError(
            "Missing 'Outreach Name' or 'Outreach email' column header in Input sheet."
        )

    # Read all rows under headers
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{INPUT_SHEET_NAME}!A2:Z1000",
        ).execute()
        rows = res.get("values", []) or []
    except HttpError as e:
        raise ValueError(f"Failed to read job rows: {e}")

    if not rows:
        return "No job rows found under headers."

    updates = []
    start_row_index = 2  # because data starts at row 2

    for i, row in enumerate(rows):
        sheet_row = start_row_index + i

        # Get website value
        website = row[website_col_idx] if website_col_idx < len(row) else ""
        domain = _normalize_domain(website)
        if not domain:
            continue

        # Search for recruiter candidates at this domain
        candidates = search_recruiters_at_company(domain, per_page=per_company_candidates)
        if not candidates:
            continue

        # Take the top candidate
        top = candidates[0]
        first = (top.get("first_name") or "").strip()
        last = (top.get("last_name") or "").strip()
        pid = top.get("id") or top.get("person_id")
        org = top.get("organization") or {}
        org_domain = (
            org.get("primary_domain")
            or org.get("domain")
            or domain
        )
        linkedin = top.get("linkedin_url")
        full_name = (first + " " + last).strip() or ""

        # Match to reveal work email (this is where credits get used)
        email = match_person_for_email(
            first_name=first or None,
            last_name=last or None,
            domain=org_domain,
            person_id=pid,
            linkedin_url=linkedin or None,
        )

        if not (full_name or email):
            continue

        # Ensure row has enough columns to write into
        # We'll build the update as a range like: Input!<col_letter><row>:<col_letter><row>
        def col_letter(idx_zero_based: int) -> str:
            # 0 -> A, 1 -> B, ... 25 -> Z, 26 -> AA ...
            n = idx_zero_based
            letters = ""
            while True:
                n, r = divmod(n, 26)
                letters = chr(ord('A') + r) + letters
                if n == 0:
                    break
                n -= 1
            return letters

        name_col_letter = col_letter(outreach_name_col_idx)
        email_col_letter = col_letter(outreach_email_col_idx)

        rng = f"{INPUT_SHEET_NAME}!{name_col_letter}{sheet_row}:{email_col_letter}{sheet_row}"
        updates.append(
            {
                "range": rng,
                "values": [[full_name, email or ""]],
            }
        )

    if not updates:
        return "No outreach contacts found or written. Check Website values, Apollo filters, and credits."

    # Batch update sheet
    try:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "RAW",
                "data": updates,
            },
        ).execute()
    except HttpError as e:
        raise ValueError(f"Failed to write outreach contacts: {e}")

    return f"Updated Outreach Name & Outreach email for {len(updates)} row(s) using Apollo."


# ---------------------------------------------------
# AGENT DEFINITION
# ---------------------------------------------------

apollo_outreach_agent: Agent = Agent(
    model=MODEL,
    name="apollo_outreach_agent",
    description=(
        "Uses Apollo.io to find recruiter contacts for companies listed in the jobs sheet. "
        "Workflow: read Website column from the Input sheet, search recruiters at that domain, "
        "call /people/match for the top candidate to reveal work email (uses credits), and "
        "write Outreach Name and Outreach email back into the sheet."
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
    tools=[
        populate_outreach_from_apollo,
    ],
)

__all__ = ["apollo_outreach_agent"]