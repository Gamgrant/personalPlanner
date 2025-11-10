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

APOLLO_API_KEY = (
    os.environ.get("APOLLO_API_KEY")
    or os.environ.get("APOLLO_API_KEY_HARDCODE")
)
if not APOLLO_API_KEY:
    raise EnvironmentError("APOLLO_API_KEY is not set. Please configure it in your environment.")

BASE_URL = "https://api.apollo.io/api/v1"

JOB_SEARCH_SPREADSHEET_ID = (os.environ.get("JOB_SEARCH_SPREADSHEET_ID") or "").strip()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

CANDIDATE_SPREADSHEET_NAMES = [
    "Job_Search_Database",
    "job_search_spreadsheet",
]

INPUT_SHEET_NAME = "Sheet1"

# Webhook URL (already includes token)
WEBHOOK_URL = (os.environ.get("APOLLO_WEBHOOK_URL") or "").strip()
if not WEBHOOK_URL:
    raise EnvironmentError(
        "APOLLO_WEBHOOK_URL is not set. It must include the token and point to /apollo-webhook."
    )

# ---------------------------------------------------
# GOOGLE HELPERS
# ---------------------------------------------------

def get_sheets_service():
    return get_google_service("sheets", "v4", SCOPES, "SHEETS")

def get_drive_service():
    return get_google_service("drive", "v3", SCOPES, "SHEETS/DRIVE")

def _find_spreadsheet_id() -> str:
    if JOB_SEARCH_SPREADSHEET_ID:
        return JOB_SEARCH_SPREADSHEET_ID

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
    except HttpError as e:
        raise ValueError(f"Failed to locate jobs spreadsheet: {e}")

    raise ValueError(
        "JOB_SEARCH_SPREADSHEET_ID not set and no matching Job Search spreadsheet found."
    )

def _get_header_map(spreadsheet_id: str) -> Dict[str, int]:
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
        if name:
            header_map[name] = idx
    return header_map

def _normalize_domain(website: str) -> Optional[str]:
    if not website:
        return None
    w = website.strip().lower()
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    w = w.split("/")[0].strip()
    return w or None

def _col_letter(idx_zero_based: int) -> str:
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
    return {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "x-api-key": APOLLO_API_KEY,
    }

def search_recruiters_at_company(domain: str, per_page: int = 5) -> List[Dict[str, Any]]:
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
        print(f"[APOLLO] /mixed_people/search failed: {resp.status_code} {resp.text}")
        return []

    data = resp.json()
    return data.get("people") or data.get("contacts") or data.get("persons") or []

def extract_email_and_phone_simple(data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Minimal extraction:
      - email from person.email
      - phone from person.contact.phone_numbers[0].sanitized_number
    """
    person = data.get("person") or {}

    # Email: just take person.email if present
    email = person.get("email")
    if isinstance(email, str):
        email = email.strip() or None
    else:
        email = None

    # Phone: go straight to person.contact.phone_numbers[0].sanitized_number
    phone = None
    contact = person.get("contact") or {}
    phone_numbers = contact.get("phone_numbers") or []

    if isinstance(phone_numbers, list) and phone_numbers:
        first = phone_numbers[0]
        if isinstance(first, dict):
            sn = first.get("sanitized_number") or first.get("sanitized_phone")
            if isinstance(sn, str) and sn.strip():
                phone = sn.strip()

    return email, phone

def match_person_for_contact(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    domain: Optional[str] = None,
    person_id: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Call /people/match with phone reveal + webhook,
    then:
      - email  = person.email
      - phone  = person.contact.phone_numbers[0].sanitized_number
    """
    url = f"{BASE_URL}/people/match"

    payload = {
        "id": person_id,
        "first_name": first_name,
        "last_name": last_name,
        "domain": domain,
        "linkedin_url": linkedin_url,
        "reveal_personal_emails": False,
        "reveal_phone_number": True,
        "webhook_url": WEBHOOK_URL,
    }
    payload = {k: v for k, v in payload.items() if v}

    if not payload:
        return (None, None)

    resp = requests.post(url, headers=_headers(), json=payload)

    if not resp.ok:
        print(f"[APOLLO] /people/match failed: {resp.status_code} {resp.text}")
        return (None, None)

    try:
        data = resp.json()
    except ValueError:
        print("[APOLLO] /people/match returned non-JSON body.")
        return (None, None)

    return extract_email_and_phone_simple(data)

# ---------------------------------------------------
# CORE TOOL
# ---------------------------------------------------

def populate_outreach_from_apollo(per_company_candidates: int = 5) -> str:
    """
    For each row:
      - read Website → domain
      - search recruiters
      - pick top candidate
      - /people/match with phone reveal
      - fill:
          Outreach Name
          Outreach Email
          Outreach Phone Number
    """
    spreadsheet_id = _find_spreadsheet_id()
    sheets = get_sheets_service()
    header_map = _get_header_map(spreadsheet_id)

    website_col_idx = header_map.get("website")
    outreach_name_col_idx = header_map.get("outreach name")
    outreach_email_col_idx = header_map.get("outreach email") or header_map.get("outreach_email")
    outreach_phone_col_idx = None

    for name, idx in header_map.items():
        if name in (
            "outreach phone number",
            "outreach phone",
            "recruiter phone",
            "recruiter phone number",
        ):
            outreach_phone_col_idx = idx
            break

    if website_col_idx is None:
        raise ValueError("No 'Website' column header found in sheet.")
    if outreach_name_col_idx is None or outreach_email_col_idx is None:
        raise ValueError("Missing 'Outreach Name' or 'Outreach email' column header in sheet.")

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
    start_row_index = 2

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

        if not (full_name or email or phone):
            continue

        # Outreach Name
        if full_name:
            name_col_letter = _col_letter(outreach_name_col_idx)
            updates.append({
                "range": f"{INPUT_SHEET_NAME}!{name_col_letter}{sheet_row}",
                "values": [[full_name]],
            })

        # Outreach Email
        if email:
            email_col_letter = _col_letter(outreach_email_col_idx)
            updates.append({
                "range": f"{INPUT_SHEET_NAME}!{email_col_letter}{sheet_row}",
                "values": [[email]],
            })

        # Outreach Phone Number (from sanitized_number)
        if outreach_phone_col_idx is not None and phone:
            phone_col_letter = _col_letter(outreach_phone_col_idx)
            updates.append({
                "range": f"{INPUT_SHEET_NAME}!{phone_col_letter}{sheet_row}",
                "values": [[phone]],
            })

    if not updates:
        return (
            "No outreach contacts found or written. "
            "Check Website values, Apollo config, webhook, and column headers."
        )

    try:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
    except HttpError as e:
        raise ValueError(f"Failed to write outreach contacts: {e}")

    touched_rows = set()
    for u in updates:
        _, a1 = u["range"].split("!")
        first_cell = a1.split(":")[0]
        row_digits = "".join(ch for ch in first_cell if ch.isdigit())
        if row_digits:
            touched_rows.add(int(row_digits))

    return (
        f"Updated Outreach Name, Outreach email, and Outreach Phone Number "
        f"for {len(touched_rows)} row(s) using Apollo."
    )

# ---------------------------------------------------
# AGENT
# ---------------------------------------------------

apollo_outreach_agent = Agent(
    model=MODEL,
    name="apollo_outreach_agent",
    description=(
        "Finds recruiter contacts for companies in the jobs sheet using Apollo.io. "
        "For each Website domain, finds a recruiter, calls /people/match with "
        "reveal_phone_number + webhook, reads person.email and "
        "person.contact.phone_numbers[0].sanitized_number, and writes them into "
        "Outreach Name, Outreach Email, and Outreach Phone Number."
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
    tools=[populate_outreach_from_apollo],
    output_key="spreadsheet_agent_apollo",
)

__all__ = ["apollo_outreach_agent"]