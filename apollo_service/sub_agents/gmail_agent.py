import os
import base64
import io
import mimetypes
from typing import Optional, List, Any, Dict
from datetime import datetime

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from email.message import EmailMessage

from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import (
    get_gmail_service as _get_gmail_service,
    get_gmail_drive_service as _get_gmail_drive_service,
    get_google_service,
)
from utils.time_utils import get_time_context

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# -------------------------------------------------------------------
# Service helpers
# -------------------------------------------------------------------

def get_gmail_service() -> object:
    return _get_gmail_service()

def get_drive_service() -> object:
    return _get_gmail_drive_service()

def get_sheets_service() -> object:
    return get_google_service("sheets", "v4", SCOPES, "GMAIL_SHEETS")

# -------------------------------------------------------------------
# Time context
# -------------------------------------------------------------------

def make_time_context(preferred_tz: Optional[str] = None) -> dict:
    """
    Return a time context dict for use in email copy if needed.
    Delegates to utils.time_utils.get_time_context.
    """
    ctx = get_time_context(preferred_tz)
    try:
        dt = datetime.fromisoformat(ctx["datetime"])
        current_time = dt.strftime("%I:%M %p")
        current_date = dt.strftime("%A, %B %d, %Y")
    except Exception:
        current_time = ctx.get("time", "")
        current_date = ctx.get("date", "")
    return {
        "current_time": current_time,
        "current_date": current_date,
        "timezone": ctx["timezone"],
        "iso_timestamp": ctx["datetime"],
    }

# -------------------------------------------------------------------
# Gmail low-level helpers
# -------------------------------------------------------------------

def _build_mime_message(
    to: List[str],
    subject: str,
    body_text: str,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg.set_content(body_text or "")
    return msg

def _encode_message(msg: EmailMessage) -> Dict[str, str]:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}

# Keep your existing tools (list_labels, send_email, search_messages) if needed.
# We'll add draft creation + sheet-driven outreach below.

# -------------------------------------------------------------------
# Spreadsheet helpers (Job_Search_Database)
# -------------------------------------------------------------------

CANDIDATE_SPREADSHEET_NAMES = [
    "Job_Search_Database",
    "job_search_spreadsheet",
]

def _find_jobs_spreadsheet_id() -> str:
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
            raise RuntimeError(f"[GMAIL-OUTREACH] Drive search error: {e}")

        files = resp.get("files", []) or []
        if files:
            return files[0]["id"]

    raise RuntimeError(
        "[GMAIL-OUTREACH] Could not find Job_Search_Database/job_search_spreadsheet in Drive."
    )

def _get_first_sheet_name(spreadsheet_id: str) -> str:
    sheets = get_sheets_service()
    try:
        meta = sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(title))",
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[GMAIL-OUTREACH] Failed to get sheet metadata: {e}")
    sheets_meta = meta.get("sheets", []) or []
    if not sheets_meta:
        raise RuntimeError("[GMAIL-OUTREACH] Spreadsheet has no sheets.")
    return sheets_meta[0]["properties"]["title"]

def _get_header_row(spreadsheet_id: str, sheet_name: str) -> List[str]:
    sheets = get_sheets_service()
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1:Z1",
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[GMAIL-OUTREACH] Failed to read header row: {e}")
    values = res.get("values", []) or []
    if not values:
        raise RuntimeError(f"[GMAIL-OUTREACH] No header row found in {sheet_name}.")
    return values[0]

def _get_header_map(header_row: List[str]) -> Dict[str, int]:
    m: Dict[str, int] = {}
    for idx, raw in enumerate(header_row):
        name = (raw or "").strip().lower()
        if name:
            m[name] = idx
    return m

# -------------------------------------------------------------------
# Core Tool: Create drafts from Outreach email script
# -------------------------------------------------------------------

def create_drafts_from_outreach_scripts(max_drafts: int = 50) -> str:
    """
    Reads Job_Search_Database, and for each row that has:
      - Outreach Name
      - Outreach Email
      - Outreach email script
    creates a Gmail draft (NOT sent) addressed to that recruiter.

    Rules:
    - One draft per unique Outreach Email (avoid spamming the same recruiter).
    - Subject is generated from Jobs + Company when available.
    - Body is primarily the Outreach email script from the sheet.
    - Uses LLM reasoning (this agent) to lightly refine subject/body if needed,
      but does not modify the sheet here.
    """
    gmail = get_gmail_service()
    sheets = get_sheets_service()

    spreadsheet_id = _find_jobs_spreadsheet_id()
    sheet_name = _get_first_sheet_name(spreadsheet_id)
    header_row = _get_header_row(spreadsheet_id, sheet_name)
    header_map = _get_header_map(header_row)

    def col_index(*candidates: str) -> Optional[int]:
        for c in candidates:
            idx = header_map.get(c.lower())
            if idx is not None:
                return idx
        return None

    jobs_idx = col_index("jobs")
    company_idx = col_index("company")
    outreach_name_idx = col_index("outreach name")
    outreach_email_idx = col_index("outreach email")
    script_idx = col_index("outreach email script")

    if outreach_email_idx is None or script_idx is None:
        raise RuntimeError(
            "[GMAIL-OUTREACH] Missing required columns: 'Outreach Email' or 'Outreach email script'."
        )

    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A2:Z10000",
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[GMAIL-OUTREACH] Failed to read sheet rows: {e}")

    rows = res.get("values", []) or []
    if not rows:
        return "[GMAIL-OUTREACH] No data rows found."

    seen_emails = set()
    created = 0

    for row in rows:
        if created >= max_drafts:
            break

        def get(idx: Optional[int]) -> str:
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").strip()

        recruiter_name = get(outreach_name_idx)
        recruiter_email = get(outreach_email_idx)
        script_body = get(script_idx)
        job_title = get(jobs_idx)
        company = get(company_idx)

        if not recruiter_email or not script_body:
            continue

        # One draft per recruiter email
        if recruiter_email.lower() in seen_emails:
            continue

        seen_emails.add(recruiter_email.lower())

        # Subject generation (LLM-guided but simple here)
        if job_title and company:
            subject = f"Interest in {job_title} opportunities at {company}"
        elif company:
            subject = f"Exploring opportunities at {company}"
        else:
            subject = "Exploring potential opportunities"

        # The script_body is assumed to already be a well-formed email body
        # generated by script_agent. We don't alter it here beyond stripping.
        body_text = script_body.strip()

        # Build draft MIME message
        msg = _build_mime_message(
            to=[recruiter_email],
            subject=subject,
            body_text=body_text,
        )
        encoded = _encode_message(msg)

        try:
            gmail.users().drafts().create(
                userId="me",
                body={"message": encoded}
            ).execute()
            created += 1
        except HttpError as e:
            # Soft-fail: skip this recruiter, continue with others
            print(f"[GMAIL-OUTREACH] Failed to create draft for {recruiter_email}: {e}")

    if created == 0:
        return (
            "[GMAIL-OUTREACH] No drafts created. "
            "Ensure 'Outreach Email' and 'Outreach email script' are populated."
        )
    return f"[GMAIL-OUTREACH] Created {created} draft(s) from Outreach email scripts."

# -------------------------------------------------------------------
# (Optional) Keep search + labels if you still want them
# -------------------------------------------------------------------

def list_labels() -> List[str]:
    service = get_gmail_service()
    resp = service.users().labels().list(userId="me").execute()
    labels = resp.get("labels", [])
    return [f"{l.get('name')} (id: {l.get('id')})" for l in labels]

def search_messages(query: str, max_results: Optional[int] = None) -> List[str]:
    service = get_gmail_service()
    try:
        messages: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:
            page_size = 500
            if max_results and max_results > 0:
                remaining = max_results - len(messages)
                if remaining <= 0:
                    break
                page_size = min(page_size, remaining)

            response = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=page_size,
                pageToken=page_token,
            ).execute()
            messages.extend(response.get("messages", []) or [])
            page_token = response.get("nextPageToken")
            if not page_token or (max_results and len(messages) >= max_results):
                break

        if not messages:
            return [f"No messages found for query: {query}"]

        results: List[str] = []
        for m in messages[: (max_results or len(messages))]:
            msg = service.users().messages().get(
                userId="me", id=m["id"], format="metadata"
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(no subject)")
            sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "(unknown sender)")
            date = next((h["value"] for h in headers if h["name"].lower() == "date"), "")
            snippet = msg.get("snippet", "")[:100]
            results.append(f"📧 {subject}\nFrom: {sender}\nDate: {date}\nSnippet: {snippet}")
        return results
    except HttpError as e:
        raise ValueError(f"Failed to search Gmail messages: {e}")

# -------------------------------------------------------------------
# Agent Definition
# -------------------------------------------------------------------

gmail_outreach_instruction = """
You are the gmail_outreach_agent.

High-level behavior:
1. First, confirm with the user:
   "Would you like me to create email drafts to the recruiters based on your Outreach email scripts?"

2. If the user says YES:
   - Call create_drafts_from_outreach_scripts().
   - This will:
       • Read the Job_Search_Database (or job_search_spreadsheet) from Drive.
       • For each row with both 'Outreach Email' and 'Outreach email script' filled,
         create a Gmail DRAFT (not send) to that recruiter.
       • Use one draft per unique recruiter email to avoid spamming.
       • Use the sheet script as the email body and generate a clear subject line
         from the Jobs + Company fields when available.

3. Never send emails automatically in this agent.
   - You ONLY create drafts.
   - Actual sending should be done by another step/agent after explicit user confirmation.

You can also:
- list_labels()
- search_messages(query)
- make_time_context() for including friendly time context if needed.

Never expose credentials. Never modify the spreadsheet directly from this agent.
Use only your tools for Gmail + read-only Sheets/Drive.
"""

google_gmail_agent = Agent(
    model=MODEL,
    name="gmail_outreach_agent",
    description=gmail_outreach_instruction,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    tools=[
        list_labels,
        search_messages,
        make_time_context,
        create_drafts_from_outreach_scripts,
    ],
)

__all__ = [
    "gmail_outreach_agent",
    "create_drafts_from_outreach_scripts",
    "list_labels",
    "search_messages",
    "make_time_context",
]