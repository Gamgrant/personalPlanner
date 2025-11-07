import os
import base64
import re
import mimetypes
import io
from typing import Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo
from tzlocal import get_localzone
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from google.adk.agents import Agent
from google.genai import types

MODEL = "gemini-2.5-flash"
SCOPES = ["https://mail.google.com/", "https://www.googleapis.com/auth/drive.readonly"]

# ======================================================
# Authentication Bootstrap
# ======================================================

def _load_credentials():
    """Helper to load OAuth credentials shared between Gmail and Drive."""
    creds = None
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))

    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")
    if not credentials_rel or not token_rel:
        raise EnvironmentError("[GMAIL] Missing GOOGLE_OAUTH_* environment variables.")

    credentials_path = os.path.join(project_root, credentials_rel)
    token_path = os.path.join(project_root, token_rel)

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

    return creds


def get_gmail_service():
    """Return authenticated Gmail service."""
    creds = _load_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_drive_service():
    """Return authenticated Drive service (for file attachments)."""
    creds = _load_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)

# ======================================================
# Time Context Helper
# ======================================================

def make_time_context(preferred_tz: Optional[str] = None) -> dict:
    """
    Returns a dictionary with current time, date, and timezone context.
    Useful for timestamping emails or temporal reasoning in the agent.
    """
    try:
        tz = ZoneInfo(preferred_tz) if preferred_tz else ZoneInfo(str(get_localzone()))
    except Exception:
        tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    return {
        "current_time": now.strftime("%I:%M %p"),
        "current_date": now.strftime("%A, %B %d, %Y"),
        "timezone": str(tz),
        "iso_timestamp": now.isoformat(),
    }

# ======================================================
# Helpers
# ======================================================

def _extract_header(headers: list[dict[str, str]], name: str) -> Optional[str]:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def _build_mime_message(
    to: List[str],
    subject: str,
    body_text: str,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    attachments: Optional[List[str]] = None,
) -> EmailMessage:
    """Build RFC5322 email with optional local attachments."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg.set_content(body_text or "")

    # Local file attachments (if any)
    if attachments:
        for path in attachments:
            if not os.path.exists(path):
                continue
            ctype, _ = mimetypes.guess_type(path)
            if ctype is None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            with open(path, "rb") as f:
                data = f.read()
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(path))
    return msg


def _encode_message(msg: EmailMessage) -> dict[str, str]:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}

# ======================================================
# Gmail Tools
# ======================================================

def list_labels() -> list[str]:
    service = get_gmail_service()
    resp = service.users().labels().list(userId="me").execute()
    labels = resp.get("labels", [])
    return [f"{l.get('name')} (id: {l.get('id')})" for l in labels]


def send_email(
    to: List[str],
    subject: str,
    body_text: str,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    attachments: Optional[List[str]] = None,
    drive_file_ids: Optional[List[str]] = None,
) -> str:
    """
    Send an email. Supports:
      - local file attachments
      - Google Drive file attachments (downloaded temporarily)
    """
    gmail = get_gmail_service()
    drive = get_drive_service()
    msg = _build_mime_message(to, subject, body_text, cc=cc, bcc=bcc, attachments=attachments)

    # Add attachments from Google Drive
    if drive_file_ids:
        for fid in drive_file_ids:
            try:
                meta = drive.files().get(fileId=fid, fields="id,name,mimeType").execute()
                name = meta.get("name", f"drive_file_{fid}")
                mime = meta.get("mimeType", "application/octet-stream")
                maintype, subtype = mime.split("/", 1)
                req = drive.files().get_media(fileId=fid)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, req)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                data = fh.getvalue()
                msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
            except Exception as e:
                print(f"[GMAIL] Failed to attach Drive file {fid}: {e}")

    encoded = _encode_message(msg)
    sent = gmail.users().messages().send(userId="me", body=encoded).execute()
    return f"Email sent successfully. Message ID: {sent.get('id')}"

# ======================================================
# Search Messages
# ======================================================

def search_messages(query: str, max_results: int = 5) -> list[str]:
    """
    Search Gmail messages by query string.
    Examples:
        query="from:boss@example.com subject:report"
        query="after:2024/01/01 before:2024/12/31"
    """
    service = get_gmail_service()
    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = results.get("messages", [])
        if not messages:
            return [f"No messages found for query: {query}"]

        formatted_results = []
        for m in messages:
            msg = service.users().messages().get(userId="me", id=m["id"], format="metadata").execute()
            headers = msg.get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(no subject)")
            sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "(unknown sender)")
            date = next((h["value"] for h in headers if h["name"].lower() == "date"), "")
            snippet = msg.get("snippet", "")[:100]
            formatted_results.append(
                f"📧 {subject}\nFrom: {sender}\nDate: {date}\nSnippet: {snippet}"
            )
        return formatted_results
    except HttpError as e:
        raise ValueError(f"Failed to search Gmail messages: {e}")

# ======================================================
# Agent Definition
# ======================================================

gmail_agent_instruction_text = """
You are a Gmail agent capable of sending, reading, searching, and organizing emails.
You can also attach files from Google Drive by passing drive_file_ids=['<file_id>'].

Example:
  send_email(
      to=['someone@example.com'],
      subject='Project Update',
      body_text='Please find attached the PDF.',
      drive_file_ids=['1AbCdEfGhIjKlMnOpQrStUvWxYz']
  )

You can also search messages, e.g.:
  search_messages("from:alice@example.com subject:meeting")
Rules:
- Use Drive file IDs from the Google Drive agent or list_drive_files().
- Never expose raw credentials.
""".strip()


def build_agent():
    return Agent(
        model=MODEL,
        name="google_gmail_agent",
        description="Gmail assistant that can send, search, and organize messages, and attach Google Drive files.",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[
            list_labels,
            send_email,
            search_messages,  # ✅ now defined properly
            make_time_context,
        ],
    )