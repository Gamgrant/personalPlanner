"""
Resume Customization Agent

This module defines an ADK-compatible agent that:
- Cleans build artifacts for the LaTeX resume
- Reads and writes resume_customization/main.tex
- Rebuilds the resume PDF via `uv run build-resume`
- Uploads the generated PDF to a Google Drive folder specified by
  RESUME_CUSTOMIZATION_FOLDER_ID environment variable

The orchestrator should send:
- Target skills for the job
- A natural-language description of what is missing / needs to change

The model will:
- Use the tools here to manipulate files ONLY under resume_customization/
- Edit only Experience, Projects, and Skills sections
- Then rebuild the PDF and upload it to Drive.
"""

import os
import shutil
import subprocess
from pathlib import Path

from google.adk.agents import Agent
from google.genai import types

from utils.routing import find_project_root
from utils.google_service_helpers import get_google_service

from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# ---------------------------------------------------------------------------
# Path setup (use routing.find_project_root)
# ---------------------------------------------------------------------------

# This file lives somewhere under the project; routing will climb up until it
# finds .creds / .cred / pyproject.toml / .git and treat that as the root.
PROJECT_ROOT: Path = find_project_root(__file__)

# Your resume lives under <PROJECT_ROOT>/resume_customization
RESUME_DIR: Path = PROJECT_ROOT / "resume_customization"
MAIN_TEX_PATH: Path = RESUME_DIR / "main.tex"

# ---------------------------------------------------------------------------
# Google Drive setup
# ---------------------------------------------------------------------------

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]


def get_drive_service() -> object:
    """
    Return an authenticated Google Drive service.

    This uses utils.google_service_helpers.get_google_service, which in turn
    uses the routing/credentials logic shared across the project.
    """
    return get_google_service("drive", "v3", DRIVE_SCOPES, "DRIVE")


# ---------------------------------------------------------------------------
# Tools (AFC-friendly: no unions/Optionals, simple return types)
# ---------------------------------------------------------------------------

def cleanup_resume_build() -> str:
    """
    Remove build artifacts for the LaTeX resume.

    Deletes:
      - resume_customization/__pycache__
      - resume_customization/build

    Safe to call even if the directories do not exist.
    """
    if not RESUME_DIR.exists():
        raise ValueError(f"Expected resume directory at {RESUME_DIR}, but it does not exist.")

    removed = []
    for sub in ["__pycache__", "build"]:
        target = RESUME_DIR / sub
        try:
            shutil.rmtree(target, ignore_errors=True)
            removed.append(str(target))
        except Exception as e:
            # We keep going, but surface the error to the model.
            removed.append(f"{target} (error during removal: {e})")

    return "Cleaned resume build artifacts: " + ", ".join(removed)


def read_resume_tex() -> str:
    """
    Read and return the full contents of resume_customization/main.tex as a string.

    The model is responsible for carefully editing this content while preserving:
    - Preamble, macros, and formatting
    - The Education section
    """
    if not MAIN_TEX_PATH.exists():
        raise ValueError(f"main.tex not found at {MAIN_TEX_PATH}")

    return MAIN_TEX_PATH.read_text(encoding="utf-8")


def write_resume_tex(updated_content: str) -> str:
    """
    Overwrite resume_customization/main.tex with the provided content.

    The caller (model) MUST:
    - Preserve the LaTeX structure and macros.
    - Only change Experience, Projects, and Skills sections.
    - Ensure LaTeX special characters are properly escaped (e.g., % -> \\%, & -> \\&).

    This tool writes the ENTIRE file, not a diff.
    """
    if not RESUME_DIR.exists():
        raise ValueError(f"Expected resume directory at {RESUME_DIR}, but it does not exist.")

    MAIN_TEX_PATH.write_text(updated_content, encoding="utf-8")
    return f"Wrote updated resume to {MAIN_TEX_PATH}"


def build_resume_pdf() -> str:
    """
    Run `uv run build-resume` from the project root to build the PDF.

    Assumptions:
    - `uv` is installed in the environment.
    - A `build-resume` script/entry-point is configured.
    - The script writes the final PDF to: resume_customization/build/main.pdf
    """
    if not PROJECT_ROOT.exists():
        raise ValueError(f"Project root {PROJECT_ROOT} does not exist.")

    try:
        # Run from project root so `build-resume` behaves the same as locally.
        proc = subprocess.run(
            ["uv", "run", "build-resume"],
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise ValueError(f"Failed to invoke 'uv'. Is it installed in this container? Error: {e}")

    if proc.returncode != 0:
        msg = (
            "build-resume failed with non-zero exit code.\n"
            f"STDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
        )
        raise ValueError(msg)

    pdf_path = RESUME_DIR / "build" / "main.pdf"
    return f"Successfully built resume PDF at {pdf_path}"


def upload_built_resume_to_drive() -> dict:
    """
    Upload the built PDF (resume_customization/build/main.pdf) to a Google Drive folder.

    The folder ID is taken from the RESUME_CUSTOMIZATION_FOLDER_ID environment variable,
    which is expected to be set via .creds/.env or Cloud Run env config.

    Returns:
      A JSON-serializable dict:
        {
          "file_id": "<Drive file id>",
          "webViewLink": "<Drive view link (if available)>"
        }

    Requirements:
    - build_resume_pdf() should have been called beforehand so that
      resume_customization/build/main.pdf exists.
    """
    pdf_path = RESUME_DIR / "build" / "main.pdf"
    if not pdf_path.exists():
        raise ValueError("Built resume PDF not found at "
                         f"{pdf_path}. Call build_resume_pdf() first.")

    folder_id = os.environ.get("RESUME_CUSTOMIZATION_FOLDER_ID", "").strip()
    if not folder_id:
        raise ValueError(
            "RESUME_CUSTOMIZATION_FOLDER_ID environment variable is not set. "
            "Set it in your .env / Cloud Run configuration to the target Drive folder ID."
        )

    drive = get_drive_service()
    try:
        file_metadata = {
            "name": pdf_path.name,
            "parents": [folder_id],
        }
        with open(pdf_path, "rb") as fh:
            media = MediaIoBaseUpload(fh, mimetype="application/pdf", resumable=True)
            created = (
                drive.files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        return {
            "file_id": created["id"],
            "webViewLink": created.get("webViewLink", ""),
        }
    except HttpError as e:
        raise ValueError(f"Failed to upload resume PDF to Drive: {e}")


# ---------------------------------------------------------------------------
# Agent Instruction Text
# ---------------------------------------------------------------------------

resume_agent_instruction_text = r"""
You are a **resume customization assistant** for a single LaTeX resume located at:
- `resume_customization/main.tex`
The orchestrator will give you:
- A description of **target skills** for a job (e.g., "Machine Learning, Deep Learning, PyTorch, JAX, C++, Communication")
- A paragraph describing **what is missing** or should be improved in the resume.

Your job:
1. Clean build artifacts.
2. Carefully edit `main.tex` in the **Experience**, **Projects**, and/or **Skills** sections.
3. Rebuild the PDF with `uv run build-resume`.
4. Upload the generated PDF to Google Drive using `upload_built_resume_to_drive()`.
5. Return a concise summary of what you changed, including the uploaded Drive file ID.

You have the following tools:
- `cleanup_resume_build()` – remove `resume_customization/__pycache__` and `resume_customization/build`.
- `read_resume_tex()` – read the full contents of `resume_customization/main.tex`.
- `write_resume_tex(updated_content)` – overwrite `resume_customization/main.tex` with your edited version.
- `build_resume_pdf()` – run `uv run build-resume` from the project root and rebuild `resume_customization/build/main.pdf`.
- `upload_built_resume_to_drive()` – upload `resume_customization/build/main.pdf` to the Drive folder whose ID is in
  the RESUME_CUSTOMIZATION_FOLDER_ID environment variable, returning the Drive file id.

[... full instructions from earlier about what you may modify in Experience/Projects/Skills, skill handling,
bullet handling, tool usage pattern, etc. ...]
""".strip()


# ---------------------------------------------------------------------------
# Agent Definition
# ---------------------------------------------------------------------------

resume_customization_agent: Agent = Agent(
    model=MODEL,
    name="resume_customization_agent",
    description=(
        "Agent that customizes a single LaTeX resume (resume_customization/main.tex) "
        "based on job-specific target skills and recommendations, rebuilds the PDF, "
        "and uploads it to a configured Google Drive folder."
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    tools=[
        cleanup_resume_build,
        read_resume_tex,
        write_resume_tex,
        build_resume_pdf,
        upload_built_resume_to_drive,
    ],
)

__all__ = ["resume_customization_agent"]
