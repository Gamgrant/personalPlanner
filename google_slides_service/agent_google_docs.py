import os
import json
from typing import List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google.adk.agents import Agent
from google.genai import types

MODEL = "gemini-2.5-flash"

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ------------------------------------------
# Auth Bootstrap
# ------------------------------------------
def get_docs_service():
    """Return an authenticated Google Docs service (never None)."""
    creds = None
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))

    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")

    if not credentials_rel or not token_rel:
        raise EnvironmentError(
            "[DOCS] Expected GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE env vars "
            "(relative to project root)."
        )

    credentials_path = os.path.join(project_root, credentials_rel)
    token_path = os.path.join(project_root, token_rel)

    print(f"[DOCS] Looking for credentials at: {credentials_path}")
    print(f"[DOCS] Looking for token at: {token_path}")

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path)
            print("[DOCS] Existing token.json loaded.")
        except (UnicodeDecodeError, ValueError):
            print("[DOCS] token.json invalid. Re-authorizing…")
            try:
                os.remove(token_path)
            except OSError:
                pass
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[DOCS] Refreshing expired credentials…")
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[DOCS] Refreshed token.json saved.")
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f"[DOCS] Missing credentials.json at {credentials_path}")
            print("[DOCS] Launching browser for new Google OAuth flow…")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[DOCS] New token.json created successfully.")

    if creds is None:
        raise RuntimeError("[DOCS] No credentials available after auth flow/refresh.")

    try:
        service = build("docs", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        raise RuntimeError(f"[DOCS] Failed to build Docs service: {e}") from e

    print("[DOCS] Docs service initialized successfully.")
    return service


def get_drive_service():
    """Return an authenticated Drive service for listing Docs."""
    creds = None
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))

    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")

    if not credentials_rel or not token_rel:
        raise EnvironmentError(
            "[DOCS/DRIVE] Expected GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE env vars "
            "(relative to project root)."
        )

    credentials_path = os.path.join(project_root, credentials_rel)
    token_path = os.path.join(project_root, token_rel)

    print(f"[DOCS/DRIVE] Looking for credentials at: {credentials_path}")
    print(f"[DOCS/DRIVE] Looking for token at: {token_path}")

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path)
            print("[DOCS/DRIVE] Existing token.json loaded.")
        except (UnicodeDecodeError, ValueError):
            print("[DOCS/DRIVE] token.json invalid. Re-authorizing…")
            try:
                os.remove(token_path)
            except OSError:
                pass
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[DOCS/DRIVE] Refreshing expired credentials…")
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[DOCS/DRIVE] Refreshed token.json saved.")
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f"[DOCS/DRIVE] Missing credentials.json at {credentials_path}")
            print("[DOCS/DRIVE] Launching browser for new Google OAuth flow…")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
                print("[DOCS/DRIVE] New token.json created successfully.")

    if creds is None:
        raise RuntimeError("[DOCS/DRIVE] No credentials available after auth flow/refresh.")

    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        raise RuntimeError(f"[DOCS/DRIVE] Failed to build Drive service: {e}") from e

    print("[DOCS/DRIVE] Drive service initialized successfully.")
    return service


# ------------------------------------------
# Tools
# ------------------------------------------
def list_docs(max_results: int = 25) -> List[str]:
    """List Google Docs files accessible to the user."""
    drive = get_drive_service()
    try:
        resp = drive.files().list(
            q="mimeType='application/vnd.google-apps.document' and trashed=false",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()
        files = resp.get("files", []) or []
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


# ------------------------------------------
# Agent Definition
# ------------------------------------------
docs_agent_instruction_text = """
You are a specialized Google Docs assistant. You can:
- List or search Google Docs
- Retrieve document text
- Create new documents
- Append or write text

Rules:
- Use document IDs from the list function.
- When writing, confirm changes with the document ID or title.
- Never expose raw credentials or tokens.
""".strip()


def build_agent():
    return Agent(
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
        ],
    )