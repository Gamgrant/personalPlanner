"""
Google Drive Agent

This module provides an ADK-compatible agent for interacting with Google Drive.
It can:
- List, search, and read Drive files
- Upload new files (text or from URL)
- Check sharing permissions
- Retrieve file modification timestamps
"""

import os
import io
import json
from datetime import datetime
from typing import List, Optional

from tzlocal import get_localzone
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

from google.adk.agents import Agent
from google.genai import types

import httpx

MODEL = "gemini-2.5-flash"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]

# ------------------------------------------
# Auth Bootstrap
# ------------------------------------------
def get_drive_service():
    """Authenticate and return a Google Drive service."""
    creds = None
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))

    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")

    if not credentials_rel or not token_rel:
        raise EnvironmentError(
            "[DRIVE] Expected GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE env vars "
            "(relative to project root)."
        )

    credentials_path = os.path.join(project_root, credentials_rel)
    token_path = os.path.join(project_root, token_rel)

    print(f"[DRIVE] Looking for credentials at: {credentials_path}")
    print(f"[DRIVE] Looking for token at: {token_path}")

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path)
            print("[DRIVE] Existing token.json loaded.")
        except (UnicodeDecodeError, ValueError):
            print("[DRIVE] token.json invalid. Re-authorizing…")
            try:
                os.remove(token_path)
            except OSError:
                pass
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[DRIVE] Refreshing expired credentials…")
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f"[DRIVE] Missing credentials.json at {credentials_path}")
            print("[DRIVE] Launching browser for new OAuth flow…")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

    if creds is None:
        raise RuntimeError("[DRIVE] No credentials available after auth flow/refresh.")

    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        raise RuntimeError(f"[DRIVE] Failed to build Drive service: {e}") from e

    print("[DRIVE] Drive service initialized successfully.")
    return service


# ------------------------------------------
# Tools
# ------------------------------------------
def list_drive_files(max_results: int = 20) -> List[str]:
    """List recent non-trashed files from Google Drive."""
    drive = get_drive_service()
    try:
        results = (
            drive.files()
            .list(
                pageSize=max_results,
                fields="files(id, name, mimeType, modifiedTime, webViewLink)",
                q="trashed=false",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        files = results.get("files", [])
        if not files:
            return ["No files found in Drive."]
        return [
            f"{f.get('name','(untitled)')} — ID: {f['id']} — Type: {f['mimeType']} — "
            f"Modified: {f.get('modifiedTime','?')} — Link: {f.get('webViewLink','-')}"
            for f in files
        ]
    except HttpError as e:
        raise ValueError(f"Failed to list Drive files: {e}")


def get_drive_file_content(file_id: str) -> str:
    """Download file content as text, if possible."""
    drive = get_drive_service()
    try:
        meta = drive.files().get(fileId=file_id, fields="id, name, mimeType, webViewLink").execute()
        mime = meta["mimeType"]

        export_map = {
            "application/vnd.google-apps.document": "text/plain",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "text/plain",
        }

        if mime in export_map:
            request = drive.files().export_media(fileId=file_id, mimeType=export_map[mime])
        else:
            request = drive.files().get_media(fileId=file_id)

        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()

        content = fh.getvalue()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = f"[Binary or unsupported text encoding — {len(content)} bytes]"

        return f"File: {meta['name']} (ID: {file_id}, Type: {mime})\n\n{text}"
    except HttpError as e:
        raise ValueError(f"Failed to read Drive file: {e}")


def create_drive_file(file_name: str, content: Optional[str] = None, folder_id: str = "root") -> str:
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
            .create(body=metadata, media_body=media, fields="id, name, webViewLink")
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
    """
    Retrieve structured last-modified timestamp of a Drive file.
    """
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
        modified_local = modified_dt.astimezone(local_tz)

        return {
            "file_id": file_id,
            "file_name": meta.get("name", "(untitled)"),
            "mime_type": meta.get("mimeType"),
            "datetime": modified_local.isoformat(),
            "date": modified_local.strftime("%Y-%m-%d"),
            "time": modified_local.strftime("%H:%M:%S"),
            "weekday": modified_local.strftime("%A"),
            "timezone": str(local_tz),
            "summary": modified_local.strftime(
                "Last modified on %A, %b %d %Y at %I:%M %p %Z"
            ),
            "link": meta.get("webViewLink"),
        }
    except HttpError as e:
        raise ValueError(f"Failed to retrieve modified time: {e}")


# ------------------------------------------
# Agent Definition
# ------------------------------------------
drive_agent_instruction_text = """
You are a Google Drive assistant. You can:
- List files and folders
- Read file contents
- Upload or create new files
- Check sharing permissions
- Retrieve modification times for Drive files

Rules:
- Use file IDs from list_drive_files().
- Never expose credentials.
- Keep text responses concise.
- If you can't find the exact file, find the most similar one and confirm with the orchestrator.
""".strip()


def build_agent():
    """Return the Google Drive Agent."""
    return Agent(
        model=MODEL,
        name="google_drive_agent",
        description=(
            "Google Drive assistant for listing, reading, uploading, and managing Drive files. "
            + drive_agent_instruction_text
        ),
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[
            list_drive_files,
            get_drive_file_content,
            create_drive_file,
            upload_drive_file_from_url,
            get_drive_file_permissions,
            get_drive_file_modified_time,  # <-- Added new tool
        ],
    )