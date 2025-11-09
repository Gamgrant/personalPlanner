import os
import re
from typing import Dict, Any, List, Optional, Tuple

import requests
from googleapiclient.errors import HttpError

from utils.google_service_helpers import get_google_service
from google.adk.agents import Agent
from google.genai import types

# ---------------------------------------------------
# CONFIG
# ---------------------------------------------------

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

APOLLO_API_KEY = ""
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

# Candidate spreadsheet names to search for
CANDIDATE_SPREADSHEET_NAMES = [
    "Job_Search_Database",
    "job_search_spreadsheet",
]

# Name of the sheet/tab that holds job rows
INPUT_SHEET_NAME = "Sheet1"


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
    Read the header row from <INPUT_SHEET_NAME>!A1:Z1 and build a map of
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
            raise ValueError(f"No header row found in {INPUT_SHEET_NAME}.")
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
    Extract domain from a website URL or domain-like string.
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


def _col_letter(idx_zero_based: int) -> str:
    """
    Convert 0-based column index to A1-style column letter.
    0 -> A, 1 -> B, ..., 25 -> Z, 26 -> AA, etc.
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
    This does NOT unlock new emails; it's for candidate discovery.
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
        return []

    data = resp.json()
    people = data.get("people") or data.get("contacts") or data.get("persons") or []
    return people


def match_person_for_contact(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    domain: Optional[str] = None,
    person_id: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    /people/match to retrieve work email (and, if available in response) phone number
    for one person.

    Returns:
        (work_email, work_phone) — either may be None.
    """
    url = f"{BASE_URL}/people/match"

    payload = {
        "id": person_id,
        "first_name": first_name,
        "last_name": last_name,
        "domain": domain,
        "linkedin_url": linkedin_url,
        "reveal_personal_emails": False,
        # We do NOT set reveal_phone_number here (that flow requires a webhook).
    }
    payload = {k: v for k, v in payload.items() if v}

    if not payload:
        return (None, None)

    resp = requests.post(url, headers=_headers(), json=payload)
    if not resp.ok:
        return (None, None)

    data = resp.json()
    person = data.get("person") or {}

    # ---- Email selection ----
    direct_email = person.get("email")
    work_email: Optional[str] = None
    if direct_email:
        work_email = direct_email
    else:
        emails = person.get("email_addresses") or []
        for e in emails:
            if not isinstance(e, dict):
                continue
            addr = e.get("email")
            etype = (e.get("type") or "").lower()
            if addr:
                if etype == "work":
                    work_email = addr
                    break
                if not work_email:
                    work_email = addr

    # ---- Phone selection (best-effort from response) ----
    work_phone: Optional[str] = None

    # Some Apollo responses expose phone_numbers / phone_number fields
    phones = person.get("phone_numbers") or person.get("phones") or []
    if isinstance(phones, list):
        for p in phones:
            if not isinstance(p, dict):
                continue
            number = p.get("number") or p.get("phone") or p.get("raw_number")
            ptype = (p.get("type") or "").lower()
            if number:
                if ptype in ("work", "work_direct"):
                    work_phone = number
                    break
                if not work_phone:
                    work_phone = number

    # Fallbacks
    if not work_phone:
        maybe_single = person.get("phone_number") or person.get("phone")
        if isinstance(maybe_single, str) and maybe_single.strip():
            work_phone = maybe_single.strip()

    return (work_email, work_phone)


# ---------------------------------------------------
# CORE TOOL: SEARCH + MATCH + WRITE BACK
# ---------------------------------------------------

def populate_outreach_from_apollo(
    per_company_candidates: int = 5,
) -> str:
    """
    Workflow:
      1. Locate jobs spreadsheet.
      2. In INPUT_SHEET_NAME, detect columns:
           - 'Website'
           - 'Outreach Name'
           - 'Outreach email'
           - 'Outreach Phone Number' (optional; may also match 'Outreach phone'/'Recruiter phone')
      3. For each row with a Website:
           - Extract company domain.
           - /mixed_people/search for recruiter-type people at that domain.
           - Take the top candidate.
           - /people/match to retrieve work email (and any available phone).
           - Write:
                Outreach Name         = "<First> <Last>"
                Outreach email        = work email (if found)
                Outreach Phone Number = work phone (if found and column exists)
         Only rows with successful matches are updated.
    """
    spreadsheet_id = _find_spreadsheet_id()
    if not spreadsheet_id:
        raise ValueError(
            "Could not find 'Job_Search_Database' or 'job_search_spreadsheet' in Drive."
        )

    sheets = get_sheets_service()
    header_map = _get_header_map(spreadsheet_id)

    website_col_idx = None
    outreach_name_col_idx = None
    outreach_email_col_idx = None
    outreach_phone_col_idx = None

    for name, idx in header_map.items():
        if name == "website":
            website_col_idx = idx
        elif name == "outreach name":
            outreach_name_col_idx = idx
        elif name in ("outreach email", "outreach_email"):
            outreach_email_col_idx = idx
        elif name in (
            "outreach phone number",
            "outreach phone",
            "recruiter phone",
            "recruiter phone number",
        ):
            outreach_phone_col_idx = idx

    if website_col_idx is None:
        raise ValueError("No 'Website' column header found in sheet.")

    if outreach_name_col_idx is None or outreach_email_col_idx is None:
        raise ValueError(
            "Missing 'Outreach Name' or 'Outreach email' column header in sheet."
        )

    # Read data rows
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

    updates: List[Dict[str, Any]] = []
    start_row_index = 2  # data starts from row 2

    for i, row in enumerate(rows):
        sheet_row = start_row_index + i

        website = row[website_col_idx] if website_col_idx < len(row) else ""
        domain = _normalize_domain(website)
        if not domain:
            continue

        candidates = search_recruiters_at_company(domain, per_page=per_company_candidates)
        if not candidates:
            continue

        top = candidates[0]
        first = (top.get("first_name") or "").strip()
        last = (top.get("last_name") or "").strip()
        full_name = (first + " " + last).strip()

        pid = top.get("id") or top.get("person_id")
        org = top.get("organization") or {}
        org_domain = (
            org.get("primary_domain")
            or org.get("domain")
            or domain
        )
        linkedin = top.get("linkedin_url")

        email, phone = match_person_for_contact(
            first_name=first or None,
            last_name=last or None,
            domain=org_domain,
            person_id=pid,
            linkedin_url=linkedin or None,
        )

        # If we got nothing meaningful, skip
        if not (full_name or email or phone):
            continue

        # Build per-cell updates so we don't require contiguous columns
        # Outreach Name
        if full_name:
            name_col_letter = _col_letter(outreach_name_col_idx)
            updates.append(
                {
                    "range": f"{INPUT_SHEET_NAME}!{name_col_letter}{sheet_row}",
                    "values": [[full_name]],
                }
            )

        # Outreach email
        if email:
            email_col_letter = _col_letter(outreach_email_col_idx)
            updates.append(
                {
                    "range": f"{INPUT_SHEET_NAME}!{email_col_letter}{sheet_row}",
                    "values": [[email]],
                }
            )

        # Outreach Phone Number (only if column exists and we have a value)
        if outreach_phone_col_idx is not None and phone:
            phone_col_letter = _col_letter(outreach_phone_col_idx)
            updates.append(
                {
                    "range": f"{INPUT_SHEET_NAME}!{phone_col_letter}{sheet_row}",
                    "values": [[phone]],
                }
            )

    if not updates:
        return (
            "No outreach contacts found or written. "
            "Check Website values, Apollo filters, credits, and column headers."
        )

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

    return (
        f"Updated Outreach Name, Outreach email, and Outreach Phone Number (when available) "
        f"for {len(set(u['range'].split('!')[1].rstrip('0123456789') + str(u['range'].split('!')[1].lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ')) for u in updates))} row(s) using Apollo."
    )


# ---------------------------------------------------
# AGENT DEFINITION
# ---------------------------------------------------

apollo_outreach_agent = Agent(
    model=MODEL,
    name="apollo_outreach_agent",
    description=(
        "Uses Apollo.io to find recruiter contacts for companies listed in the jobs sheet. "
        "Workflow: read the Website column, search recruiters at that domain, call /people/match "
        "for the top candidate to retrieve work email and any available phone number, and write "
        "Outreach Name, Outreach email, and Outreach Phone Number back into the sheet."
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
    tools=[populate_outreach_from_apollo],
    output_key="spreadsheet_agent_apollo",
)

__all__ = ["apollo_outreach_agent"]