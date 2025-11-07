import os
import json
from typing import List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from tzlocal import get_localzone

# Import centralized time utilities. We use get_time_context to obtain
# current time data in a unified way across all modules. This allows
# consistent timezone handling and formatting throughout the project.
from utils.time_utils import get_time_context

from googleapiclient.errors import HttpError

from google.adk.agents import Agent
from google.genai import types

# Import centralized helper for Google API authentication and service construction.
from utils.google_service_helpers import get_google_service

# Load the model name from environment variables if available. Defaults to
# 'gemini-2.5-flash' when not provided. Centralizing this variable allows
# configuration via .env without editing code in multiple places.
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ------------------------------------------
# Auth Bootstrap
# ------------------------------------------
def get_docs_service() -> object:
    """
    Return an authenticated Google Docs service.

    This function delegates credential handling to the centralized helper
    defined in utils.google_service_helpers. By using get_google_service,
    we avoid duplicating OAuth flow logic here. The SCOPES constant
    specifies the permissions needed for Docs operations.
    """
    return get_google_service("docs", "v1", SCOPES, "DOCS")


def get_drive_service() -> object:
    """
    Return an authenticated Google Drive service with the same scopes
    used for Docs. This is used to list and retrieve documents from
    Google Drive when the user asks to view or search for Docs files.
    """
    return get_google_service("drive", "v3", SCOPES, "DOCS/DRIVE")


# ------------------------------------------
# Tools
# ------------------------------------------
def list_docs(max_results: int | None = None) -> List[str]:
    """
    List Google Docs files accessible to the user.

    This function retrieves all documents by paging through the Drive API
    rather than limiting to a fixed number of results.  If
    ``max_results`` is provided and positive, at most that many
    documents are returned; otherwise, all documents are returned.

    Args:
        max_results: Optional maximum number of documents to return.  If
            ``None`` or <= 0, the function returns every document.

    Returns:
        A list of formatted strings describing each document.
    """
    drive = get_drive_service()
    try:
        files: List[dict] = []
        page_token: str | None = None
        while True:
            page_size = 1000
            if max_results and max_results > 0:
                remaining = max_results - len(files)
                if remaining <= 0:
                    break
                page_size = min(page_size, remaining)
            params = {
                "q": "mimeType='application/vnd.google-apps.document' and trashed=false",
                "fields": "nextPageToken, files(id,name,modifiedTime,webViewLink)",
                "orderBy": "modifiedTime desc",
                "pageSize": page_size,
                # Include documents from shared drives and shared resources
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = drive.files().list(**params).execute()
            files.extend(resp.get("files", []) or [])
            page_token = resp.get("nextPageToken")
            if not page_token or (max_results and max_results > 0 and len(files) >= max_results):
                break
        if not files:
            return ["No Google Docs found."]
        return [
            f"{f.get('name','(untitled)')} — ID: {f.get('id')} — Modified: {f.get('modifiedTime','?')} — Link: {f.get('webViewLink','-')}"
            for f in files
        ]
    except HttpError as e:
        raise ValueError(f"Failed to list Google Docs: {str(e)}")


def get_doc_content(document_id: str) -> str:
    """Retrieve plain text content of a Google Doc."""
    docs = get_docs_service()
    try:
        doc = docs.documents().get(documentId=document_id).execute()
        title = doc.get("title", "Untitled Document")
        body = doc.get("body", {}).get("content", [])
        lines = [f"Document: {title} (ID: {document_id})", "-" * 40]

        for el in body:
            if "paragraph" in el:
                for e in el["paragraph"].get("elements", []):
                    text_run = e.get("textRun", {})
                    if "content" in text_run:
                        lines.append(text_run["content"].strip())
        return "\n".join(lines)
    except HttpError as e:
        raise ValueError(f"Failed to read document: {str(e)}")


def create_doc(title: str) -> str:
    """Create a new Google Doc and return its ID and link."""
    docs = get_docs_service()
    try:
        doc = docs.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId")
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        return f"Created new doc '{title}'. ID: {doc_id} | URL: {url}"
    except HttpError as e:
        raise ValueError(f"Failed to create document: {str(e)}")


def append_doc_text(document_id: str, text: str) -> str:
    """Append text to the end of a Google Doc."""
    docs = get_docs_service()
    try:
        requests = [{"insertText": {"location": {"index": 1_000_000}, "text": f"\n{text}\n"}}]
        docs.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute()
        return f"Appended text to document {document_id}."
    except HttpError as e:
        raise ValueError(f"Failed to append text: {str(e)}")


def get_doc_modified_time(document_id: str) -> dict:
    """
    Retrieve the last modified time of a Google Doc in structured form.
    """
    drive = get_drive_service()
    try:
        file_metadata = drive.files().get(
            fileId=document_id,
            fields="name, modifiedTime, webViewLink",
        ).execute()

        modified_str = file_metadata.get("modifiedTime")
        if not modified_str:
            raise ValueError("No modifiedTime found in file metadata.")

        modified_dt = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
        local_tz = get_localzone()
        modified_local = modified_dt.astimezone(local_tz)

        return {
            "document_id": document_id,
            "document_name": file_metadata.get("name", "(untitled)"),
            "datetime": modified_local.isoformat(),
            "date": modified_local.strftime("%Y-%m-%d"),
            "time": modified_local.strftime("%H:%M:%S"),
            "weekday": modified_local.strftime("%A"),
            "timezone": str(local_tz),
            "summary": modified_local.strftime(
                f"Last modified on %A, %b %d %Y at %I:%M %p %Z"
            ),
            "link": file_metadata.get("webViewLink"),
        }
    except HttpError as e:
        raise ValueError(f"Failed to retrieve modified time: {str(e)}")


# ------------------------------------------
# Time Context Tool
# ------------------------------------------
def make_time_context(preferred_tz: Optional[str] = None) -> dict:
    """
    Return structured current time context.

    This wrapper calls utils.time_utils.get_time_context to obtain a base
    dictionary containing date/time components. It then adds a human-readable
    summary and returns the enhanced context. By reusing get_time_context,
    we ensure consistent timezone handling across all agents.

    Args:
        preferred_tz: Optional IANA timezone string. If provided, the
            context is based on that timezone. Otherwise, the local
            timezone is used.

    Returns:
        A dictionary containing ISO timestamp, date, time, weekday,
        timezone, UTC offset, and a formatted summary string.
    """
    ctx = get_time_context(preferred_tz)
    # Compute a summary in the format "Weekday, Month Day Year, HH:MM AM TZ".
    try:
        dt = datetime.fromisoformat(ctx["datetime"])
        summary = dt.strftime("%A, %b %d %Y, %I:%M %p %Z")
    except Exception:
        # Fallback: build summary from individual context fields
        summary = f"{ctx.get('weekday', '')}, {ctx.get('date', '')} {ctx.get('time', '')} {ctx.get('timezone', '')}"
    ctx["summary"] = summary
    return ctx


# ------------------------------------------
# Agent Definition
# ------------------------------------------
docs_agent_instruction_text = """
You are a specialized Google Docs assistant. You can:
- List or search Google Docs
- Retrieve document text
- Create new documents
- Append or write text
- Get document modification times

Rules:
- Use document IDs from the list function.
- When writing, confirm changes with the document ID or title.
- Never expose raw credentials or tokens.
""".strip()


google_docs_agent: Agent  = Agent(
        model=MODEL,
        name="google_docs_agent",
        description=(
            "Google Docs assistant for listing, reading, creating, and editing Google Docs. "
            + docs_agent_instruction_text
        ),
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[
            list_docs,
            get_doc_content,
            create_doc,
            append_doc_text,
            make_time_context,
            get_doc_modified_time,
        ],
    )
__all__ = ["google_docs_agent"]