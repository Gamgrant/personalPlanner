"""
Google Drive Agent

This module provides an ADK-compatible agent for interacting with Google Drive.
It can:
- List, search, and read Drive files
- Upload new files (text or from URL)
- Check sharing permissions
- Retrieve file modification timestamps
- Recursively traverse folders
"""

import os
import io
from datetime import datetime
from typing import List, Dict

from tzlocal import get_localzone
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

from google.adk.agents import Agent
from google.genai import types

import httpx

from utils.google_service_helpers import get_google_service

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# Full read/write + listing scopes
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]

# ------------------------------------------
# Auth Bootstrap
# ------------------------------------------
def get_drive_service() -> object:
    """Return an authenticated Google Drive service."""
    return get_google_service("drive", "v3", SCOPES, "DRIVE")

# ------------------------------------------
# Internal helpers
# ------------------------------------------
def _paginate_files(drive, q: str, page_size: int = 1000) -> List[Dict]:
    """Fetch ALL matching files across all drives."""
    items: List[Dict] = []
    page_token = None
    while True:
        params = {
            "q": q,
            "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink,parents)",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "pageSize": page_size,
            "orderBy": "modifiedTime desc",
            "corpora": "allDrives",   # <-- key to see everything you can access
        }
        if page_token:
            params["pageToken"] = page_token
        resp = drive.files().list(**params).execute()
        items.extend(resp.get("files", []) or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items

# ------------------------------------------
# Tools (AFC-friendly: no unions/Optionals)
# ------------------------------------------
def list_drive_files(max_results: int = 0, folder_id: str = "", mime_type: str = "") -> List[str]:
    """
    List files/folders across all drives. If folder_id is provided, list direct
    children of that folder. If mime_type is provided, filter by that type.
      - max_results: 0 means unlimited.
      - folder_id: "" means search globally.
      - mime_type: "" means any type; e.g. 'application/vnd.google-apps.folder' for folders.
    """
    drive = get_drive_service()
    try:
        q_parts = ["trashed=false"]
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        if mime_type:
            q_parts.append(f"mimeType='{mime_type}'")
        items = _paginate_files(drive, " and ".join(q_parts))

        if max_results > 0:
            items = items[:max_results]

        if not items:
            return ["No files found in Drive."]
        return [
            f"{f.get('name','(untitled)')} — ID: {f['id']} — Type: {f['mimeType']} — "
            f"Modified: {f.get('modifiedTime','?')} — Link: {f.get('webViewLink','-')}"
            for f in items
        ]
    except HttpError as e:
        raise ValueError(f"Failed to list Drive files: {e}")
def list_drive_pdfs_in_folder(folder_id: str, max_results: int = 0) -> List[str]:
    """
    List PDF files inside a specific folder.
      - folder_id: ID of the folder to search in.
      - max_results: 0 means unlimited.
    """
    if not folder_id:
        return ["folder_id is required."]
    return list_drive_files(
        max_results=max_results,
        folder_id=folder_id,
        mime_type="application/pdf"
    )

def list_drive_folders(max_results: int = 0, folder_id: str = "") -> List[str]:
    """List folders (optionally inside a specific parent folder)."""
    folder_mime = "application/vnd.google-apps.folder"
    return list_drive_files(max_results=max_results, folder_id=folder_id, mime_type=folder_mime)

def list_drive_files_recursive(start_folder_id: str, max_results: int = 0, mime_type: str = "") -> List[str]:
    """
    Recursively traverse folders starting at start_folder_id and list all matching items.
      - start_folder_id: required
      - max_results: 0 = unlimited
      - mime_type: "" = any; e.g., folder mime to list only folders
    """
    if not start_folder_id:
        return ["start_folder_id is required."]

    drive = get_drive_service()
    try:
        results: List[Dict] = []
        queue: List[str] = [start_folder_id]
        folder_mime = "application/vnd.google-apps.folder"

        while queue:
            parent = queue.pop(0)
            q_parts = [f"'{parent}' in parents", "trashed=false"]
            if mime_type:
                q_parts.append(f"mimeType='{mime_type}'")
            children = _paginate_files(drive, " and ".join(q_parts))
            results.extend(children)

            # Always discover subfolders to recurse deeper
            subfolders = [c for c in children if c.get("mimeType") == folder_mime]
            queue.extend([sf["id"] for sf in subfolders])

            if max_results > 0 and len(results) >= max_results:
                results = results[:max_results]
                break

        if not results:
            return [f"No items found under folder {start_folder_id}."]
        return [
            f"{f.get('name','(untitled)')} — ID: {f['id']} — Type: {f['mimeType']} — "
            f"Modified: {f.get('modifiedTime','?')} — Link: {f.get('webViewLink','-')}"
            for f in results
        ]
    except HttpError as e:
        raise ValueError(f"Failed to recursively list Drive files: {e}")

def find_drive_items_by_name(name: str, exact: bool = True, mime_type: str = "", in_folder_id: str = "") -> List[str]:
    """
    Search by name (exact or contains). Optionally filter by mime_type or restrict to a folder.
      - exact=True uses name = '...'
      - exact=False uses name contains '...'
    """
    if not name:
        return ["name is required."]

    drive = get_drive_service()
    try:
        q_parts = ["trashed=false"]
        if in_folder_id:
            q_parts.append(f"'{in_folder_id}' in parents")
        if mime_type:
            q_parts.append(f"mimeType='{mime_type}'")
        if exact:
            q_parts.append(f"name = '{name}'")
        else:
            q_parts.append(f"name contains '{name}'")

        items = _paginate_files(drive, " and ".join(q_parts))
        if not items:
            return [f"No items found matching name: {name}"]
        return [
            f"{f.get('name','(untitled)')} — ID: {f['id']} — Type: {f['mimeType']} — "
            f"Modified: {f.get('modifiedTime','?')} — Link: {f.get('webViewLink','-')}"
            for f in items
        ]
    except HttpError as e:
        raise ValueError(f"Failed to search by name: {e}")

def get_drive_file_content(file_id: str) -> str:
    """Download file content as text, if possible (handles Google Docs, Sheets, and PDFs)."""
    drive = get_drive_service()
    try:
        meta = drive.files().get(
            fileId=file_id,
            fields="id, name, mimeType, webViewLink"
        ).execute()
        mime = meta["mimeType"]

        export_map = {
            "application/vnd.google-apps.document": "text/plain",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "text/plain",
        }

        # 1) Build request (export vs raw download)
        if mime in export_map:
            request = drive.files().export_media(fileId=file_id, mimeType=export_map[mime])
        else:
            request = drive.files().get_media(fileId=file_id)

        # 2) Download into memory
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        content = fh.getvalue()

        # 3) Special handling for PDFs
        if mime == "application/pdf":
            try:
                import PyPDF2  # make sure PyPDF2 is installed
                fh.seek(0)
                reader = PyPDF2.PdfReader(fh)
                pages = []
                for page in reader.pages:
                    pages.append(page.extract_text() or "")
                text = "\n".join(pages).strip()
                if not text:
                    text = f"[Parsed PDF but no extractable text found — file may be scanned images. Size={len(content)} bytes]"
            except Exception as e:
                text = f"[Unable to parse PDF text: {e}. Raw size={len(content)} bytes]"
        else:
            # 4) Default: assume it's text-ish
            try:
                text = content.decode("utf-8", errors="replace")
            except Exception as e:
                text = f"[Binary or unsupported text encoding — {len(content)} bytes; error={e}]"

        return f"File: {meta['name']} (ID: {file_id}, Type: {mime})\n\n{text}"
    except HttpError as e:
        raise ValueError(f"Failed to read Drive file: {e}")

def create_drive_file(file_name: str, content: str = "", folder_id: str = "root") -> str:
    """Create a text file in Google Drive."""
    drive = get_drive_service()
    try:
        metadata = {"name": file_name, "parents": [folder_id]}
        media = None
        if content:
            media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")

        file = (
            drive.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id, name, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        return f"Created '{file_name}' — ID: {file['id']} — Link: {file['webViewLink']}"
    except HttpError as e:
        raise ValueError(f"Failed to create Drive file: {e}")

def upload_drive_file_from_url(url: str, file_name: str, folder_id: str = "root") -> str:
    """Download a file from URL and upload to Google Drive."""
    drive = get_drive_service()
    try:
        with httpx.Client(follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.content
            mime = resp.headers.get("Content-Type", "application/octet-stream")

        metadata = {"name": file_name, "parents": [folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=True)

        file = (
            drive.files()
            .create(body=metadata, media_body=media, fields="id, name, webViewLink", supportsAllDrives=True)
            .execute()
        )
        return f"Uploaded '{file_name}' from URL — ID: {file['id']} — Link: {file['webViewLink']}"
    except Exception as e:
        raise ValueError(f"Failed to upload file from URL: {e}")

def get_drive_file_permissions(file_id: str) -> str:
    """Check file permissions and sharing status."""
    drive = get_drive_service()
    try:
        meta = drive.files().get(
            fileId=file_id,
            fields="id, name, webViewLink, shared, permissions",
            supportsAllDrives=True,
        ).execute()

        output = [
            f"File: {meta['name']} (ID: {file_id})",
            f"Shared: {meta.get('shared', False)}",
            f"View Link: {meta.get('webViewLink', '-')}",
            "",
            "Permissions:",
        ]
        perms = meta.get("permissions", [])
        if not perms:
            output.append("  (Private file)")
        else:
            for p in perms:
                typ = p.get("type")
                role = p.get("role")
                email = p.get("emailAddress", "")
                output.append(f"  - {typ} ({role}) {email}")
        return "\n".join(output)
    except HttpError as e:
        raise ValueError(f"Failed to get permissions: {e}")

def get_drive_file_modified_time(file_id: str) -> dict:
    """Retrieve structured last-modified timestamp of a Drive file."""
    drive = get_drive_service()
    try:
        meta = drive.files().get(
            fileId=file_id,
            fields="id, name, mimeType, modifiedTime, webViewLink",
        ).execute()

        modified_str = meta.get("modifiedTime")
        if not modified_str:
            raise ValueError("No modifiedTime found for file.")

        modified_dt = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
        local_tz = get_localzone()
        modified_local = modified_dt.asctime if False else modified_dt.astimezone(local_tz)

        return {
            "file_id": file_id,
            "file_name": meta.get("name", "(untitled)"),
            "mime_type": meta.get("mimeType"),
            "datetime": modified_local.isoformat(),
            "date": modified_local.strftime("%Y-%m-%d"),
            "time": modified_local.strftime("%H:%M:%S"),
            "weekday": modified_local.strftime("%A"),
            "timezone": str(local_tz),
            "summary": modified_local.strftime("Last modified on %A, %b %d %Y at %I:%M %p %Z"),
            "link": meta.get("webViewLink"),
        }
    except HttpError as e:
        raise ValueError(f"Failed to retrieve modified time: {e}")

# ------------------------------------------
# Agent Definition
# ------------------------------------------
drive_agent_instruction_text = """
You are a Google Drive assistant. You can:
- List files and folders (across all drives)
- Read file contents (including PDFs, Google Docs, Sheets, and Presentations)
- Upload or create new files
- Check sharing permissions
- Retrieve modification times for Drive files
- Recursively traverse a folder tree

Special behavior for resumes and skill scoring:
- If the user asks you to evaluate or score resumes in a specific folder against a list
  of skills, follow this pattern:
  1) Use `list_drive_pdfs_in_folder(folder_id=...)` (or `list_drive_files` with
     mime_type='application/pdf') to get all PDF resumes in that folder.
  2) For each resume, call `get_drive_file_content(file_id=...)` to read its text.
  3) Using your own reasoning (no extra tools), compare the resume content to the
     user-provided skill list.
  4) For each resume, compute a score from 0–100% and identify:
       - What is strong / well-covered in the resume.
       - What is weak or missing relative to the skill list.

Rules:
- Use file IDs from list_drive_files()/list_drive_pdfs_in_folder()/find_drive_items_by_name().
- Never expose credentials.
- Keep text responses concise.
- If a query is ambiguous, list the closest matches and ask which one to use.

### JSON output for resume scoring
When (and only when) you are evaluating or scoring resumes in a Drive folder against a list
of skills, your final answer MUST be valid JSON with the following structure:

[
  {
    "name": "<resume file name>",
    "id": "<drive file id>",
    "score": <integer between 0 and 100>,
    "what_is_good": "<A VERY SHORT summary (1–2 sentences, max ~35–40 words total) of the main strengths. Do NOT list every single skill; just give a high-level, condensed overview.>",
    "what_is_missing": "<A MUCH LONGER explanation (at least 5–8 sentences) focusing on gaps: which skills from the list are missing, weak, only implied, not quantified, or not prominent enough. Explicitly name the missing/weak skills and give concrete suggestions for how the resume could show them more clearly.>"
  },
  ...
]

Additional rules for this JSON output:
- The array MUST be sorted from highest to lowest "score" (descending order).
- Do NOT include any extra commentary or text outside the JSON array.
- **Relative length requirement**:
  - "what_is_good" MUST be noticeably shorter than "what_is_missing" (1–2 short sentences, no long enumerations).
  - "what_is_missing" MUST be the main, detailed section (5–8 sentences), and should enumerate specific missing or weak skills from the provided list.
- When there are few true gaps, still use "what_is_missing" to suggest improvements:
  - e.g., making certain skills more explicit, adding metrics, clarifying tools, or separating data engineering vs analysis vs modeling responsibilities.

For all other Google Drive requests (listing, reading, permissions, uploads, etc.),
you may respond in normal natural language.
""".strip()

google_drive_agent: Agent = Agent(
    model=MODEL,
    name="google_drive_agent",
    description=(
        "Google Drive assistant for listing, reading, uploading, and managing Drive files. "
        + drive_agent_instruction_text
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    tools=[
        list_drive_pdfs_in_folder, 
        list_drive_files,
        list_drive_folders,
        list_drive_files_recursive,
        find_drive_items_by_name,
        get_drive_file_content,
        create_drive_file,
        upload_drive_file_from_url,
        get_drive_file_permissions,
        get_drive_file_modified_time,
    ],
)

__all__ = ["google_drive_agent"]
