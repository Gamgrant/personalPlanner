# google_docs_service/agent_google_gmail.py

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
# Helpers
# ======================================================

def _extract_header(headers: list[dict[str, str]], name: str) -> Optional[str]:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def _format_local_epoch_ms(epoch_ms: int, tz_str: Optional[str] = None) -> str:
    try:
        zone = ZoneInfo(tz_str) if tz_str else ZoneInfo(str(get_localzone()))
    except Exception:
        zone = ZoneInfo("America/New_York")
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, zone)
    return dt.strftime("%a %b %d, %Y · %I:%M %p %Z")


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

    # Send via Gmail
    encoded = _encode_message(msg)
    sent = gmail.users().messages().send(userId="me", body=encoded).execute()
    return f"Email sent successfully. Message ID: {sent.get('id')}"


# ======================================================
# Agent Definition
# ======================================================

gmail_agent_instruction_text = """
You are a Gmail agent capable of sending, reading, and organizing emails.
You can also attach files from Google Drive by passing drive_file_ids=['<file_id>'].
Example:
  send_email(
      to=['someone@example.com'],
      subject='Project Update',
      body_text='Please find attached the PDF.',
      drive_file_ids=['1AbCdEfGhIjKlMnOpQrStUvWxYz']
  )
Rules:
- Use Drive file IDs from the Google Drive agent or list_drive_files().
- Never expose raw credentials.
""".strip()


def build_agent():
    return Agent(
        model=MODEL,
        name="google_gmail_agent",
        description="Gmail assistant that can send/read/manage messages and attach Google Drive files.",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[
            list_labels,
            send_email,
        ],
    )