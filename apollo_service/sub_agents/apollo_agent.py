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

# ✅ Hard-coded outreach phone (no Apollo phone reveal)
HARDCODED_OUTREACH_PHONE = "+16082074247"

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

INPUT_SHEET_NAME = "Sheet1"  # treated as a preference, not a hard requirement

def _resolve_sheet_name(spreadsheet_id: str) -> str:
    """
    Pick a sheet name to use:
      1. If INPUT_SHEET_NAME exists, use it.
      2. Else use the first sheet in the doc.
    """
    sheets = get_sheets_service()
    try:
        meta = sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(title))",
        ).execute()
    except HttpError as e:
        raise ValueError(f"[APOLLO] Failed to fetch spreadsheet metadata: {e}")

    sheet_props = meta.get("sheets", []) or []
    if not sheet_props:
        raise ValueError("[APOLLO] Spreadsheet has no sheets.")

    titles = [s["properties"]["title"] for s in sheet_props if "properties" in s]
    if INPUT_SHEET_NAME in titles:
        return INPUT_SHEET_NAME

    # Fall back to the first sheet if Sheet1 is not present
    return titles[0]


def _get_header_map(spreadsheet_id: str) -> Dict[str, int]:
    """
    Read the header row from the chosen sheet and build:
        normalized_header -> column_index (0-based)
    """
    sheets = get_sheets_service()
    sheet_name = _resolve_sheet_name(spreadsheet_id)

    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1:Z1",
        ).execute()
    except HttpError as e:
        # Surface the exact sheet+range we tried so it's debuggable
        raise ValueError(
            f"[APOLLO] Failed to read header row from '{sheet_name}'!A1:Z1: {e}"
        )

    values = res.get("values", []) or []
    if not values:
        raise ValueError(f"[APOLLO] No header row found in sheet '{sheet_name}'.")

    header_row = values[0]
    header_map: Dict[str, int] = {}
    for idx, raw in enumerate(header_row):
        name = (raw or "").strip().lower()
        if name:
            header_map[name] = idx

    if not header_map:
        raise ValueError(
            f"[APOLLO] Parsed empty header map from '{sheet_name}'. "
            "Check that row 1 has your column names."
        )

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
    """
    Use /mixed_people/search to find recruiter-type roles at the given domain.
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
        print(f"[APOLLO] /mixed_people/search failed: {resp.status_code} {resp.text}")
        return []

    data = resp.json()
    return data.get("people") or data.get("contacts") or data.get("persons") or []

def match_person_for_contact(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    domain: Optional[str] = None,
    person_id: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Simplified: call /people/match to confirm and get email.
    We are NOT using Apollo phone reveal here; phone will be filled with a fixed value.

    Returns:
        (email, None)
    """
    url = f"{BASE_URL}/people/match"

    payload = {
        "id": person_id,
        "first_name": first_name,
        "last_name": last_name,
        "domain": domain,
        "linkedin_url": linkedin_url,
        "reveal_personal_emails": False,
        # 'reveal_phone_number' intentionally omitted
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

    person = data.get("person") or {}
    email = person.get("email")
    if isinstance(email, str):
        email = email.strip() or None
    else:
        email = None

    # Phone is intentionally not taken from Apollo in this version
    return (email, None)

# ---------------------------------------------------
# CORE TOOL
# ---------------------------------------------------

def populate_outreach_from_apollo(per_company_candidates: int = 5) -> str:
    """
    For each row in the jobs sheet:

      - ONLY consider rows where `resume_id_latex_done` (or similar) is non-empty.
      - Read Website → domain.
      - Search recruiters via Apollo.
      - Pick top candidate.
      - Call /people/match to get a verified email.
      - Fill (for qualifying rows only):
          * Outreach Name
          * Outreach Email
          * Outreach Phone Number (fixed HARDCODED_OUTREACH_PHONE)

    Rules:
      - Do NOT overwrite an existing Outreach Name / Outreach Email / Outreach Phone Number.
      - Only write Outreach fields for rows where a customized resume exists
        (resume_id_latex_done is non-empty).
    """
    spreadsheet_id = _find_spreadsheet_id()
    sheets = get_sheets_service()
    header_map = _get_header_map(spreadsheet_id)

    # Helper to safely get the first existing column index among candidates.
    def _first_idx(*names: str) -> Optional[int]:
        for n in names:
            idx = header_map.get(n)
            if idx is not None:
                return idx
        return None

    website_col_idx = header_map.get("website")
    outreach_name_col_idx = header_map.get("outreach name")
    outreach_email_col_idx = _first_idx("outreach email", "outreach_email")
    resume_id_col_idx = _first_idx("resume_id_latex_done", "resume id", "resume_id")

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
    if resume_id_col_idx is None:
        raise ValueError("Missing 'resume_id_latex_done' / resume id column header in sheet.")

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
    start_row_index = 2  # data starts on row 2 (row 1 = header)

    for i, row in enumerate(rows):
        sheet_row = start_row_index + i

        # --- Only process rows with a non-empty resume_id_latex_done ---
        if resume_id_col_idx is None or resume_id_col_idx >= len(row):
            continue
        resume_val = (row[resume_id_col_idx] or "").strip()
        if not resume_val:
            continue

        # --- Website → domain ---
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

        email, _ = match_person_for_contact(
            first_name=first or None,
            last_name=last or None,
            domain=org_domain,
            person_id=pid,
            linkedin_url=linkedin or None,
        )

        # Fixed phone number (only used if the column exists)
        phone = HARDCODED_OUTREACH_PHONE if outreach_phone_col_idx is not None else None

        # If we somehow got nothing at all, skip
        if not (full_name or email or phone):
            continue

        # --- Outreach Name (only if currently empty) ---
        current_name = ""
        if outreach_name_col_idx < len(row):
            current_name = (row[outreach_name_col_idx] or "").strip()

        if full_name and not current_name:
            name_col_letter = _col_letter(outreach_name_col_idx)
            updates.append(
                {
                    "range": f"{INPUT_SHEET_NAME}!{name_col_letter}{sheet_row}",
                    "values": [[full_name]],
                }
            )

        # --- Outreach Email (only if currently empty) ---
        current_email = ""
        if outreach_email_col_idx < len(row):
            current_email = (row[outreach_email_col_idx] or "").strip()

        if email and not current_email:
            email_col_letter = _col_letter(outreach_email_col_idx)
            updates.append(
                {
                    "range": f"{INPUT_SHEET_NAME}!{email_col_letter}{sheet_row}",
                    "values": [[email]],
                }
            )

        # --- Outreach Phone Number (fixed, only if currently empty) ---
        if outreach_phone_col_idx is not None and phone:
            current_phone = ""
            if outreach_phone_col_idx < len(row):
                current_phone = (row[outreach_phone_col_idx] or "").strip()

            if not current_phone:
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
            "Check Website values, Apollo config, resume_id_latex_done, and column headers."
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
        f"for {len(touched_rows)} row(s) using Apollo "
        f"(phone is fixed to {HARDCODED_OUTREACH_PHONE} when blank)."
    )

# ---------------------------------------------------
# AGENT
# ---------------------------------------------------

apollo_outreach_agent = Agent(
    model=MODEL,
    name="apollo_outreach_agent",
    description=(
        "Finds recruiter contacts for companies in the jobs sheet using Apollo.io. "
        "For each Website domain, finds a recruiter, calls /mixed_people/search and "
        "/people/match to get a verified email, and writes Outreach Name, Outreach "
        "Email, and a fixed Outreach Phone Number into the sheet."
        "Fixed phone number is ok, no need to ask the user"
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
    tools=[populate_outreach_from_apollo],
    output_key="spreadsheet_agent_apollo",
)

__all__ = ["apollo_outreach_agent"]