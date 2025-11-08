import os
import base64
import re
import mimetypes
import io
from typing import Optional, List, Any, Dict
from datetime import datetime
from zoneinfo import ZoneInfo
from tzlocal import get_localzone
from email.message import EmailMessage

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from google.adk.agents import Agent
from google.genai import types

# Import Gmail and Drive service helpers. These wrappers return
# authenticated services using centralized credential handling in
# utils.google_service_helpers.
from utils.google_service_helpers import (
    get_gmail_service as _get_gmail_service,
    get_gmail_drive_service as _get_gmail_drive_service,
)

# Import centralized time helper to unify time context across modules.
from utils.time_utils import get_time_context

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")
SCOPES = ["https://mail.google.com/", "https://www.googleapis.com/auth/drive.readonly"]

# ======================================================
# Authentication Bootstrap
# ======================================================

# The local _load_credentials function has been removed in favor of
# centralized credential handling via utils.google_service_helpers. We now
# expose thin wrappers that call into helpers to obtain pre-authenticated
# services. These helpers automatically respect GOOGLE_OAUTH_* env vars and
# refresh tokens as needed.

def get_gmail_service() -> object:
    """
    Return an authenticated Gmail service.

    This delegates credential handling to utils.google_service_helpers.
    It simply returns the result of _get_gmail_service(), which manages
    OAuth and token refresh under the hood.
    """
    return _get_gmail_service()


def get_drive_service() -> object:
    """
    Return an authenticated Drive service for file attachments via Gmail.

    This delegates to utils.google_service_helpers.get_gmail_drive_service,
    which uses the same scopes as Gmail to access Drive files.
    """
    return _get_gmail_drive_service()

# ======================================================
# Time Context Helper
# ======================================================

def make_time_context(preferred_tz: Optional[str] = None) -> dict:
    """
    Return a time context dictionary for Gmail.

    This wrapper delegates to utils.time_utils.get_time_context to compute the
    current date/time information, then maps it into the structure
    originally expected by this agent. It returns keys:
        - current_time: formatted as 12-hour clock with AM/PM
        - current_date: formatted as Weekday, Month Day, Year
        - timezone: the IANA timezone string
        - iso_timestamp: ISO 8601 timestamp including timezone offset

    Args:
        preferred_tz: Optional IANA timezone string. If provided, the
            context is based on that timezone. Otherwise, the local
            timezone is used.

    Returns:
        A dictionary containing formatted time and date values.
    """
    ctx = get_time_context(preferred_tz)
    # Parse the ISO timestamp to format current_time and current_date
    try:
        dt = datetime.fromisoformat(ctx["datetime"])
        current_time = dt.strftime("%I:%M %p")
        current_date = dt.strftime("%A, %B %d, %Y")
    except Exception:
        # Fallback to context fields if parsing fails
        current_time = ctx.get("time", "")
        current_date = ctx.get("date", "")
    return {
        "current_time": current_time,
        "current_date": current_date,
        "timezone": ctx["timezone"],
        "iso_timestamp": ctx["datetime"],
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

def search_messages(query: str, max_results: Optional[int] = None) -> list[str]:
    """
    Search Gmail messages by query string and return a list of summaries.

    The Gmail API may return many results, so this implementation fetches
    messages in batches and continues until either all matching messages
    are retrieved or ``max_results`` is reached.  If ``max_results`` is
    ``None`` or non-positive, all available messages are returned.

    Args:
        query: A Gmail search query (e.g., "from:boss@example.com subject:report").
        max_results: Optional maximum number of messages to return.  If
            ``None`` or <= 0, the function returns all matching messages.

    Returns:
        A list of formatted strings summarizing each message (subject, sender,
        date, and snippet). If no messages match, returns a single-item list
        indicating that no messages were found.
    """
    service = get_gmail_service()
    try:
        messages: list[dict[str, Any]] = []  # type: ignore[assignment]
        page_token: str | None = None
        # Fetch messages until we exhaust results or hit max_results
        while True:
            page_size = 500  # Gmail API supports up to 500 per page
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
            if not page_token or (max_results and max_results > 0 and len(messages) >= max_results):
                break

        if not messages:
            return [f"No messages found for query: {query}"]

        formatted_results: list[str] = []
        for m in messages[: (max_results or len(messages))]:
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


gmail_agent: Agent =  Agent(
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
__all__ = ["gmail_agent"]