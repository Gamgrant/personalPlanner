import os
import base64
from typing import Optional, List, Any, Dict
from datetime import datetime

from googleapiclient.errors import HttpError
from email.message import EmailMessage

from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import (
    get_gmail_service as _get_gmail_service,
    get_gmail_drive_service as _get_gmail_drive_service,
    get_google_service,
)
from utils.time_utils import get_time_context

# ---------------------------------------------------
# CONFIG
# ---------------------------------------------------

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# Prefer explicit spreadsheet id from env
JOB_SEARCH_SPREADSHEET_ID = (os.environ.get("JOB_SEARCH_SPREADSHEET_ID") or "").strip()

# Optional legacy names as fallback
CANDIDATE_SPREADSHEET_NAMES = [
    "Job_Search_Database",
    "job_search_spreadsheet",
]

# ---------------------------------------------------
# SERVICE HELPERS
# ---------------------------------------------------

def get_gmail_service() -> object:
    return _get_gmail_service()

def get_drive_service() -> object:
    return _get_gmail_drive_service()

def get_sheets_service() -> object:
    return get_google_service("sheets", "v4", SCOPES, "GMAIL_SHEETS")

# ---------------------------------------------------
# TIME CONTEXT
# ---------------------------------------------------

def make_time_context(preferred_tz: Optional[str] = None) -> dict:
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

# ---------------------------------------------------
# GMAIL HELPERS
# ---------------------------------------------------

def _build_mime_message(
    to: List[str],
    subject: str,
    body_text: str,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> EmailMessage:
    """
    Build an EmailMessage with optional attachments.
    attachments: list of { "filename": str, "mime_type": str, "data": bytes }
    """
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)

    msg.set_content(body_text or "")

    if attachments:
        for att in attachments:
            data = att.get("data")
            if not data:
                continue
            filename = att.get("filename") or "attachment"
            mime_type = att.get("mime_type") or "application/octet-stream"
            if "/" in mime_type:
                maintype, subtype = mime_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"

            msg.add_attachment(
                data,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )

    return msg

def _encode_message(msg: EmailMessage) -> Dict[str, str]:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}

# ---------------------------------------------------
# SHEETS HELPERS
# ---------------------------------------------------

def _find_jobs_spreadsheet_id() -> str:
    """
    Resolve the Job Search spreadsheet ID.

    Priority:
      1. JOB_SEARCH_SPREADSHEET_ID env var
      2. Fallback: search Drive by legacy names
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
        raise RuntimeError(f"[GMAIL-OUTREACH] Drive search error: {e}")

    raise RuntimeError(
        "[GMAIL-OUTREACH] JOB_SEARCH_SPREADSHEET_ID is not set and no matching "
        "Job_Search_Database/job_search_spreadsheet was found in Drive."
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

# ---------------------------------------------------
# DRIVE / RESUME ATTACHMENT HELPER
# ---------------------------------------------------

def _get_resume_attachment_from_id(file_id: str) -> Optional[Dict[str, Any]]:
    """
    Given a Drive file ID, fetch bytes + metadata to attach to an email.
    Returns:
      {
        "filename": str,
        "mime_type": str,
        "data": bytes,
      }
    or None if anything fails.
    """
    file_id = (file_id or "").strip()
    if not file_id:
        return None

    drive = get_drive_service()

    try:
        meta = drive.files().get(
            fileId=file_id,
            fields="name,mimeType",
            supportsAllDrives=True,
        ).execute()
        name = meta.get("name") or "resume.pdf"
        mime_type = meta.get("mimeType") or "application/pdf"

        data = drive.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        ).execute()

        if not isinstance(data, (bytes, bytearray)):
            # In weird cases, convert to bytes-ish
            data = bytes(str(data), "utf-8")

        return {
            "filename": name,
            "mime_type": mime_type,
            "data": data,
        }

    except HttpError as e:
        print(f"[GMAIL-OUTREACH] Failed to fetch resume file {file_id}: {e}")
        return None

# ---------------------------------------------------
# CORE TOOL: CREATE PERSONALIZED DRAFTS (WITH RESUME ATTACHMENT)
# ---------------------------------------------------

def create_drafts_from_outreach_scripts(max_drafts: int = 50) -> str:
    """
    Auto behavior (no confirmation, no extra queries):

    For each row in the jobs sheet:
      - Uses:
          Jobs (job title)
          Company
          Outreach Name
          Outreach Email
          resume_id_latex_done (Drive file ID for customized resume)
          Outreach Email Script (body text generated by script_agent)
      - If:
          • Outreach Email exists
          • Outreach Email Script exists (non-empty)
          • resume_id_latex_done exists
        Then:
          - Fetch resume file by ID from Drive.
          - Attach that resume to the email.
          - Create a Gmail DRAFT to Outreach Email with:
              * Subject based on job title + company.
              * Body from Outreach Email Script.
              * Attached resume file.

    Rules:
      - Never send emails, only create drafts.
      - One draft per row that satisfies the conditions.
      - Does NOT modify the sheet.
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

    jobs_idx = col_index("jobs", "job", "job title", "role")
    company_idx = col_index("company")
    outreach_name_idx = col_index("outreach name")
    outreach_email_idx = col_index("outreach email", "outreach_email")
    script_idx = col_index("outreach email script", "outreach_email_script")
    resume_id_idx = col_index("resume_id_latex_done", "resume id", "resume_id")

    if outreach_email_idx is None or script_idx is None:
        raise RuntimeError(
            "[GMAIL-OUTREACH] Missing required columns: 'Outreach Email' or 'Outreach Email Script'."
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

    created = 0

    for row in rows:
        if created >= max_drafts:
            break

        def get(idx: Optional[int]) -> str:
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").strip()

        job_title = get(jobs_idx)
        company = get(company_idx)
        recruiter_name = get(outreach_name_idx)
        recruiter_email = get(outreach_email_idx)
        script_body = get(script_idx)
        resume_file_id = get(resume_id_idx)

        # Require recruiter email, script, and resume id
        if not recruiter_email or not script_body or not resume_file_id:
            continue

        # Build subject
        if job_title and company:
            subject = f"Application for {job_title} at {company}"
        elif job_title:
            subject = f"Application for {job_title}"
        elif company:
            subject = f"Exploring opportunities at {company}"
        else:
            subject = "Application for relevant opportunities"

        # Fallback greeting
        greeting_name = recruiter_name or "there"

        # If the script body doesn't start with greeting, we can leave as-is;
        # assume script_agent already formatted it. Just trust the script.
        body_text = script_body.strip()

        # Get resume attachment
        attachments: List[Dict[str, Any]] = []
        att = _get_resume_attachment_from_id(resume_file_id)
        if att:
            attachments.append(att)

        msg = _build_mime_message(
            to=[recruiter_email],
            subject=subject,
            body_text=body_text,
            attachments=attachments,
        )
        encoded = _encode_message(msg)

        try:
            gmail.users().drafts().create(
                userId="me",
                body={"message": encoded},
            ).execute()
            created += 1
        except HttpError as e:
            print(f"[GMAIL-OUTREACH] Failed to create draft for {recruiter_email}: {e}")

    if created == 0:
        return (
            "[GMAIL-OUTREACH] No drafts created. "
            "Ensure 'Outreach Email Script' and 'resume_id_latex_done' are populated for target rows."
        )

    return f"[GMAIL-OUTREACH] Created {created} draft(s) with attached resumes."

# ---------------------------------------------------
# UTILITIES
# ---------------------------------------------------

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

# ---------------------------------------------------
# AGENT DEFINITION
# ---------------------------------------------------

gmail_outreach_instruction = """
You are the gmail_outreach_agent.

Behavior:
- Do NOT ask the user for confirmation.
- When invoked, you should directly call create_drafts_from_outreach_scripts().

create_drafts_from_outreach_scripts():
- Reads the Job Search sheet.
- For each row:
    - Uses:
        • Jobs (job title)
        • Company
        • Outreach Name
        • Outreach Email
        • Outreach Email Script
        • resume_id_latex_done (Google Drive file ID of the customized resume)
    - If Outreach Email, Outreach Email Script, and resume_id_latex_done are all present:
        • Fetches the resume file by ID from Drive.
        • Attaches that resume to the email.
        • Uses the Outreach Email Script as the body.
        • Generates a clear subject line from job title + company.
        • Creates a Gmail DRAFT (never sends).

Rules:
- Never send emails automatically.
- Never modify the spreadsheet.
- Never expose credentials.
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
    "google_gmail_agent",
    "create_drafts_from_outreach_scripts",
    "list_labels",
    "search_messages",
    "make_time_context",
]