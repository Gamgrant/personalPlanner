# google_docs_service/agent_google_docs.py

import os
import os.path
import base64
import re
import mimetypes
from typing import Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from tzlocal import get_localzone

from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google.adk.agents import Agent
from google.genai import types

MODEL = "gemini-2.5-flash"

# Keep scopes tight but capable:
# - gmail.modify: read/modify labels, read bodies
# - gmail.send: sending messages
SCOPES = ["https://mail.google.com/"]

# -------------------------------
# Auth & service bootstrap (same pattern as calendar)
# -------------------------------
def get_gmail_service():
    """Return an authenticated Gmail service bound to user 'me' (never None)."""
    creds = None

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))

    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel       = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")

    if not credentials_rel or not token_rel:
        raise EnvironmentError(
            "[GMAIL] Expected GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE env vars "
            "(relative to project root)."
        )

    credentials_path = os.path.join(project_root, credentials_rel)
    token_path       = os.path.join(project_root, token_rel)

    print(f"[GMAIL] Looking for credentials at: {credentials_path}")
    print(f"[GMAIL] Looking for token at: {token_path}")

    # IMPORTANT: load the existing token AS-IS (don’t pass SCOPES) to avoid re-scoping
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path)
            print("[GMAIL] Existing token.json found and loaded.")
        except (UnicodeDecodeError, ValueError):
            print("[GMAIL] token.json invalid or corrupted. Re-authorizing…")
            try:
                os.remove(token_path)
            except OSError:
                pass
            creds = None

    # Refresh or run OAuth only if necessary
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[GMAIL] Refreshing expired credentials…")
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[GMAIL] Refreshed token.json saved.")
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f"[GMAIL] Missing credentials.json at {credentials_path}")
            print("[GMAIL] Launching browser for new Google OAuth flow…")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[GMAIL] New token.json created successfully.")

    if creds is None:
        raise RuntimeError("[GMAIL] No credentials available after auth flow/refresh.")

    # Build the Gmail API client; never return None.
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        # Surface a clear error instead of returning None
        raise RuntimeError(f"[GMAIL] Failed to build Gmail service: {e}") from e

    if service is None:
        # Extremely unlikely, but guard anyway
        raise RuntimeError("[GMAIL] googleapiclient.discovery.build returned None.")

    print("[GMAIL] Gmail service initialized successfully.")
    return service


# -------------------------------
# Helpers
# -------------------------------

def _extract_header(headers: list[dict[str, str]], name: str) -> Optional[str]:
    """Pull a specific header value (case-insensitive) from Gmail API headers array."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None

def _format_local_epoch_ms(epoch_ms: int, tz_str: Optional[str] = None) -> str:
    """
    Format an epoch ms timestamp in a human-friendly local time:
    e.g., "Sun Nov 02, 2025 · 07:56 PM EST"
    If tz_str is None, fall back to the system timezone.
    """
    try:
        zone = ZoneInfo(tz_str) if tz_str else ZoneInfo(str(get_localzone()))
    except Exception:
        zone = ZoneInfo("America/New_York")
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, zone)
    return dt.strftime("%a %b %d, %Y · %I:%M %p %Z")

def _has_explicit_category_or_mailbox(q: str) -> bool:
    """
    Return True if the query already pins scope (category:, in:, label:).
    We then avoid auto-prefixing category:primary.
    """
    ql = (q or "").lower()
    return ("category:" in ql) or (" in:" in ql) or (" label:" in ql)

def _ensure_primary_prefix(q: str) -> str:
    """
    If the user didn't constrain scope, bias search to Primary.
    """
    q = q or ""
    if _has_explicit_category_or_mailbox(q):
        return q
    return f"category:primary {q}".strip()

def _build_mime_message(
     to: list[str],
      subject: str,
      body_text: str,
      cc: Optional[list[str]] = None,
      bcc: Optional[list[str]] = None,
      attachments: Optional[list[str]] = None,
      in_reply_to: Optional[str] = None,
      references: Optional[str] = None,
 ) -> EmailMessage:
    """
    Build an RFC 5322 EmailMessage. Attachments are file paths (optional).
    """
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject

    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        # Bcc is not added as a header sent to recipients, but Gmail honors it if present here.
        msg["Bcc"] = ", ".join(bcc)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    msg.set_content(body_text or "")

    if attachments:
        for path in attachments:
            if not path:
                continue
            ctype, encoding = mimetypes.guess_type(path)
            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            with open(path, "rb") as f:
                data = f.read()
            filename = os.path.basename(path)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

    return msg


def _encode_message(msg: EmailMessage) -> dict[str, str]:
    """Return Gmail API-ready body with base64url encoded MIME message."""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}


# -------------------------------
# Tools (JSON-safe I/O)
# -------------------------------
def list_labels() -> list[str]:
    """List user's Gmail labels."""
    service = get_gmail_service()
    try:
        resp = service.users().labels().list(userId="me").execute()
        labels = resp.get("labels", [])
        return [f"{l.get('name')} (id: {l.get('id')})" for l in labels]
    except HttpError as e:
        raise ValueError(f"Failed to list labels: {str(e)}")


# def search_messages(
#     query: str,
#     max_results: int = 10,
#     since_epoch_ms: Optional[int] = None,
#     tz: Optional[str] = None,
# ) -> list[str]:
def search_messages(
    query: str,
    max_results: int = 10,
    since_epoch_ms: Optional[int] = None,
    tz: Optional[str] = None,
    search_scope: Optional[str] = "primary",  # "primary" (default) or "broad"
) -> list[str]:
    """
    Search Gmail. If since_epoch_ms is provided, filter by internalDate >= since_epoch_ms (UTC epoch ms).
    If tz is provided (e.g., from session.state.time_context.tz), use it for display; otherwise use system tz.
    Scope:
      - "primary" (default): implicitly bias to Primary if query doesn't already constrain mailbox/category.
      - "broad": attempt a short sequence of scoped queries in this order until we collect up to max_results:
           1) category:primary
           2) in:starred
           3) in:spam (includeSpamTrash=True)
           4) category:promotions
           5) category:social
           6) category:updates
    """
    service = get_gmail_service()
    if service is None:
        raise RuntimeError("[GMAIL] Service is None (unexpected).")

    try:
        queries: list[dict] = []
        q = query or ""
        if (search_scope or "primary").lower() == "broad":
            # Ordered fallbacks; stop when we’ve gathered enough
            base = q.strip()
            queries = [
                {"q": _ensure_primary_prefix(base), "includeSpamTrash": False},
                {"q": f"in:starred {base}".strip(), "includeSpamTrash": False},
                {"q": f"in:spam {base}".strip(), "includeSpamTrash": True},
                {"q": f"category:promotions {base}".strip(), "includeSpamTrash": False},
                {"q": f"category:social {base}".strip(), "includeSpamTrash": False},
                {"q": f"category:updates {base}".strip(), "includeSpamTrash": False},
            ]
        else:
            # Primary bias unless user already pinned scope
            queries = [{"q": _ensure_primary_prefix(q), "includeSpamTrash": False}]

        results: list[str] = []
        seen_ids: set[str] = set()

        for spec in queries:
            if len(results) >= max_results:
                break
            resp = service.users().messages().list(
                userId="me",
                q=spec["q"],
                maxResults=max_results,
                includeSpamTrash=spec.get("includeSpamTrash", False),
            ).execute()
            messages = resp.get("messages", []) or []
            if not messages:
                continue

            for m in messages:
                if len(results) >= max_results:
                    break
                if m["id"] in seen_ids:
                    continue
                msg = service.users().messages().get(
                    userId="me",
                    id=m["id"],
                    format="metadata",
                    metadataHeaders=["Subject", "From", "Date"]
                ).execute()

                # Filter by internalDate cutoff if requested
                internal_ms = None
                try:
                    internal_ms = int(msg.get("internalDate"))
                except Exception:
                    internal_ms = None
                if since_epoch_ms is not None and internal_ms is not None:
                    if internal_ms < since_epoch_ms:
                        continue

                headers = msg.get("payload", {}).get("headers", [])
                subject = _extract_header(headers, "Subject") or "(no subject)"
                sender  = _extract_header(headers, "From") or "(unknown sender)"

                # Human-readable local time from internalDate (DST-safe)
                if internal_ms is not None:
                    local_when = _format_local_epoch_ms(internal_ms, tz)
                else:
                    local_when = _extract_header(headers, "Date") or "(no date)"

                results.append(f"{subject} — {sender} — {local_when} — ID: {m['id']}")
                seen_ids.add(m["id"])

        return results or ["No messages found."]

    except HttpError as e:
        raise ValueError(f"Failed to search messages: {str(e)}")


def get_message(message_id: str, tz: Optional[str] = None, max_chars: int = 200000) -> dict:
    """
    Fetch a single message and return a tiny, structured payload the LLM can reason over.
    We prefer text/html if present; else text/plain. No sanitization/stripping is performed.
    Returns:
      {
        "subject": str,
        "from": str,
        "received_local": str,
        "message_id": str,
        "thread_id": str,
        "content_type": "text/html" | "text/plain",
        "content": str (possibly truncated to max_chars)
      }
    """
    service = get_gmail_service()
    try:
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = msg.get("payload", {}) or {}
        headers = payload.get("headers", []) or []

        subject = _extract_header(headers, "Subject") or "(no subject)"
        from_h  = _extract_header(headers, "From") or "(unknown sender)"

        internal_ms = None
        try:
            internal_ms = int(msg.get("internalDate"))
        except Exception:
            internal_ms = None
        received_local = _format_local_epoch_ms(internal_ms, tz) if internal_ms else "(no date)"

        # Walk the MIME tree to find preferred body (html > plain)
        def walk_parts(p: dict) -> list[dict]:
            if not p:
                return []
            parts = p.get("parts")
            if not parts:
                return [p]
            out: list[dict] = []
            for x in parts:
                out.extend(walk_parts(x))
            return out

        parts = walk_parts(payload)
        chosen_type = None
        chosen_data = None
        # 1) prefer text/html
        for p in parts:
            mime = (p.get("mimeType") or "").lower()
            if mime == "text/html" and p.get("body", {}).get("data"):
                chosen_type = "text/html"
                chosen_data = p["body"]["data"]
                break
        # 2) else text/plain
        if chosen_data is None:
            for p in parts:
                mime = (p.get("mimeType") or "").lower()
                if mime == "text/plain" and p.get("body", {}).get("data"):
                    chosen_type = "text/plain"
                    chosen_data = p["body"]["data"]
                    break
        # 3) else use top-level body if available
        if chosen_data is None and payload.get("body", {}).get("data"):
            chosen_type = (payload.get("mimeType") or "text/plain").lower()
            chosen_data = payload["body"]["data"]

        content = ""
        if chosen_data:
            try:
                content = base64.urlsafe_b64decode(chosen_data.encode("utf-8")).decode("utf-8", errors="replace")
            except Exception:
                content = ""
        if len(content) > max_chars:
            content = content[:max_chars]

        return {
            "subject": subject,
            "from": from_h,
            "received_local": received_local,
            "message_id": msg.get("id"),
            "thread_id": msg.get("threadId"),
            "content_type": chosen_type or "text/plain",
            "content": content,
        }
    except HttpError as e:
        raise ValueError(f"Failed to get message: {str(e)}")
    

def get_thread(thread_id: str) -> dict:
    """
    Fetch a full thread including messages. Returns raw dict (JSON-serializable).
    """
    service = get_gmail_service()
    try:
        thr = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        return thr
    except HttpError as e:
        raise ValueError(f"Failed to get thread: {str(e)}")


def send_email(
     to: list[str],
      subject: str,
      body_text: str,
     cc: Optional[list[str]] = None,
     bcc: Optional[list[str]] = None,
     attachments: Optional[list[str]] = None,
 ) -> str:
    """Send a new email. Returns the Gmail message ID."""
    service = get_gmail_service()
    try:
        # defensive: if the LLM passes a single string by mistake
        if isinstance(to, str):
            to = [to]
        msg = _build_mime_message(to, subject, body_text, cc=cc, bcc=bcc, attachments=attachments)
        body = _encode_message(msg)
        sent = service.users().messages().send(userId="me", body=body).execute()
        return f"Email sent. Message ID: {sent.get('id')}"
    except HttpError as e:
        raise ValueError(f"Failed to send email: {str(e)}")


def reply_to_message(message_id: str, body_text: str, attachments: Optional[list[str]] = None) -> str:
    """
    Reply to a specific message ID in its thread. Preserves Subject/Refs.
    """
    service = get_gmail_service()
    try:
        original = service.users().messages().get(userId="me", id=message_id, format="metadata", metadataHeaders=["Subject", "From", "Message-ID", "References"]).execute()
        headers = original.get("payload", {}).get("headers", [])

        subject = _extract_header(headers, "Subject") or ""
        if not re.match(r"(?i)^re:\s", subject or ""):
            subject = f"Re: {subject}" if subject else "Re: (no subject)"

        in_reply_to = _extract_header(headers, "Message-ID")
        references  = _extract_header(headers, "References")
        if references and in_reply_to:
            references = f"{references} {in_reply_to}"
        elif in_reply_to:
            references = in_reply_to

        # The reply recipient is normally the original "From"
        from_addr = _extract_header(headers, "From")
        if not from_addr:
            raise ValueError("Could not determine original sender address for reply.")

        # Build and send
        msg = _build_mime_message(
            to=[from_addr],
            subject=subject,
            body_text=body_text,
            attachments=attachments,
            in_reply_to=in_reply_to,
            references=references,
        )
        body = _encode_message(msg)
        body["threadId"] = original.get("threadId")  # ensure it stays in the same thread
        sent = service.users().messages().send(userId="me", body=body).execute()
        return f"Reply sent. Message ID: {sent.get('id')}, Thread ID: {sent.get('threadId')}"
    except HttpError as e:
        raise ValueError(f"Failed to reply: {str(e)}")


def modify_labels(message_id: str, add_labels: Optional[list[str]] = None, remove_labels: Optional[list[str]] = None) -> str:    
    """
    Add or remove label IDs (e.g., 'UNREAD', 'INBOX', or custom label IDs).
    """
    service = get_gmail_service()
    try:
        body = {
            "addLabelIds": add_labels or [],
            "removeLabelIds": remove_labels or [],
        }
        service.users().messages().modify(userId="me", id=message_id, body=body).execute()
        return "Labels updated."
    except HttpError as e:
        raise ValueError(f"Failed to modify labels: {str(e)}")


def mark_as_read(message_id: str) -> str:
    """Remove UNREAD label."""
    return modify_labels(message_id, remove_labels=["UNREAD"])


def mark_as_unread(message_id: str) -> str:
    """Add UNREAD label."""
    return modify_labels(message_id, add_labels=["UNREAD"])


def archive_message(message_id: str) -> str:
    """
    Archive (remove from INBOX).
    """
    return modify_labels(message_id, remove_labels=["INBOX"])


def trash_message(message_id: str) -> str:
    """Move message to Trash (reversible)."""
    service = get_gmail_service()
    try:
        service.users().messages().trash(userId="me", id=message_id).execute()
        return "Message moved to Trash."
    except HttpError as e:
        raise ValueError(f"Failed to trash message: {str(e)}")


def delete_message(message_id: str) -> str:
    """Permanently delete message (irreversible)."""
    service = get_gmail_service()
    try:
        service.users().messages().delete(userId="me", id=message_id).execute()
        return "Message permanently deleted."
    except HttpError as e:
        raise ValueError(f"Failed to delete message: {str(e)}")


# -------------------------------
# Agent instructions & factory
# -------------------------------
gmail_agent_instruction_text = """
You are a focused Gmail assistant. You can search, read, send, reply, label (read/unread/archive), trash, and delete emails.
Time context:
- ALWAYS read session.state.time_context if present.
- For “since …” queries (e.g., “since 3 PM today”), pass time_context.cutoff_epoch_ms_utc to search_messages(since_epoch_ms=...).
- Display times in time_context.tz.
- Prefer filtering by internalDate >= cutoff_epoch_ms_utc rather than relying on the Date header.

Search scope defaults:
- By default, search only the Primary inbox (implicitly using category:primary).
- If the user says “look for X” and it’s not found in Primary, broaden the scope by calling
  search_messages(..., search_scope="broad"), which will try in order: Primary → Starred → Spam → Promotions → Social → Updates.
- For generic “show unread” style requests, stay in Primary unless the user explicitly asks to broaden.

General Rules:
- ALWAYS assume the user means their Gmail account ('me').
- Prefer concise summaries, not raw JSON.
- Use `search_messages` first to identify a message when the user describes it vaguely (e.g., “that invoice from Alice last week” → query like "from:alice subject:invoice newer_than:10d").
- When the user needs a full message body/headers, call `get_message`.
- When the user needs conversation context, call `get_thread`.

Sending & Replying:
- For new emails: collect "to", subject, body; CC/BCC optional; attachments optional (file paths).
- For replies: call `reply_to_message(message_id, ...)` to preserve threading and headers.
- Keep subject lines short and informative.

Labels & Organization:
- Mark as read/unread using the dedicated tools.
- Archive by removing the INBOX label (use `archive_message`).
- Trash moves to bin (reversible); Delete is permanent — confirm user intent when ambiguous.

Safety:
- Never expose raw OAuth secrets. The token and credentials files are loaded from the same env variables and directories as the Calendar agent.
- If multiple search results match, present the top few lines (Subject — From — Date — ID) and ask which ID to act on.

Output:
- For search: return clean, human-readable lines. Include the message ID for reference.
- For actions (send/reply/label): confirm the action performed and show the message or thread ID.
""".strip()


def build_agent():
    return Agent(
        model=MODEL,
        name="google_gmail_agent",
        description=(
            "Gmail assistant for searching, reading, sending/replying, labeling (read/unread/archive), "
            "trashing, deleting, and listing labels. "
            + gmail_agent_instruction_text
        ),
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[
            list_labels,
            search_messages,
            get_message,
            get_thread,
            send_email,
            reply_to_message,
            mark_as_read,
            mark_as_unread,
            archive_message,
            trash_message,
            delete_message,
            modify_labels,  # exposed in case you want custom label IDs
        ],
    )
