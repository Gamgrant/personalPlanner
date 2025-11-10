"""
Resume Customization Agent

This module defines an ADK-compatible agent that:
- Cleans build artifacts for the LaTeX resume
- Reads and writes resume_customization/main.tex
- Rebuilds the resume PDF via a LaTeX engine (e.g., lualatex)
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
# Project roots: handle read-only Cloud Run filesystem
# ---------------------------------------------------------------------------

# This is the real repo root (e.g., /app in Cloud Run)
READONLY_ROOT: Path = find_project_root(__file__).resolve()


def _ensure_writable_project_root(readonly_root: Path) -> Path:
    """
    Return a project root that we are allowed to write into.

    Strategy:
    - Try to actually create & delete a small test file under `readonly_root`.
      If that works, we treat it as writable (local dev).
    - If it fails (e.g., Cloud Run source mount is read-only), we create a
      working copy of the repo under /tmp and use that instead.
    """
    test_file = readonly_root / ".resume_rw_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        try:
            test_file.unlink()
        except Exception:
            # Not fatal if cleanup fails
            pass
        return readonly_root
    except Exception:
        # Any failure here → treat the root as read-only
        pass

    # Fall back to a writable copy under /tmp (or override via env)
    work_root_env = os.environ.get("RESUME_CUSTOMIZATION_WORKDIR")
    work_root = Path(work_root_env) if work_root_env else Path("/tmp/personalplanner_work")
    work_root = work_root.resolve()
    work_root.mkdir(parents=True, exist_ok=True)

    # Copy project tree once; dirs_exist_ok=True allows repeated imports
    if not any(work_root.iterdir()):
        shutil.copytree(readonly_root, work_root, dirs_exist_ok=True)

    return work_root


# This is the root we will actually read/write from
PROJECT_ROOT: Path = _ensure_writable_project_root(READONLY_ROOT)

# Your resume lives under <PROJECT_ROOT>/resume_customization
RESUME_DIR: Path = PROJECT_ROOT / "resume_customization"
MAIN_TEX_PATH: Path = RESUME_DIR / "main.tex"
BUILD_DIR: Path = RESUME_DIR / "build"

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

PDF_BASENAME = "resume_Grant_Ovsepyan"


def build_resume_pdf() -> str:
    """
    Build the resume PDF directly with a LaTeX engine (no `uv`).

    Assumptions:
    - A LaTeX engine such as `lualatex` is installed in the container and
      available on PATH.
    - The final PDF should be written to: resume_customization/build/main.pdf
    """
    if not PROJECT_ROOT.exists():
        raise ValueError(f"Project root {PROJECT_ROOT} does not exist.")

    if not RESUME_DIR.exists():
        raise ValueError(f"Resume directory {RESUME_DIR} does not exist.")

    if not MAIN_TEX_PATH.exists():
        raise ValueError(f"main.tex not found at {MAIN_TEX_PATH}")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # Choose LaTeX engine; default to lualatex to match your LaTeX preamble
    latex_cmd = os.environ.get("RESUME_LATEX_CMD", "lualatex")

    try:
        # Run LaTeX in the resume directory, outputting to BUILD_DIR.
        # -interaction=nonstopmode + -halt-on-error avoid interactive prompts and
        # make sure we get a non-zero exit code on failure.
        proc = subprocess.run(
            [
                latex_cmd,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-output-directory",
                str(BUILD_DIR),
                "-jobname",
                PDF_BASENAME,
                "main.tex",
            ],
            cwd=str(RESUME_DIR),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise ValueError(
            f"Failed to invoke LaTeX engine '{latex_cmd}'. "
            "Is it installed in this container and on PATH? "
            f"Error: {e}"
        )

    if proc.returncode != 0:
        msg = (
            f"LaTeX build failed with exit code {proc.returncode}.\n"
            f"STDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
        )
        raise ValueError(msg)

    pdf_path = BUILD_DIR / f"{PDF_BASENAME}.pdf"
    if not pdf_path.exists():
        raise ValueError(f"LaTeX reported success but {pdf_path} was not found.")

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
    pdf_path = BUILD_DIR / f"{PDF_BASENAME}.pdf"
    if not pdf_path.exists():
        raise ValueError(
            "Built resume PDF not found at "
            f"{pdf_path}. Call build_resume_pdf() first."
        )

    folder_id = os.environ.get("RESUME_CUSTOMIZATION_FOLDER_ID", "").strip()
    if not folder_id:
        raise ValueError(
            "RESUME_CUSTOMIZATION_FOLDER_ID environment variable is not set. "
            "Set it in your .env / Cloud Run configuration to the target Drive folder ID."
        )

    drive = get_drive_service()
    try:
        file_metadata = {
            "name": f"{PDF_BASENAME}.pdf",
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
3. Rebuild the PDF using the `build_resume_pdf()` tool (which runs a LaTeX engine and writes `resume_Grant_Ovsepyan.pdf`).
4. Upload the generated PDF to Google Drive using `upload_built_resume_to_drive()`.
5. Return a concise summary of what you changed, including the uploaded Drive file ID.

You have the following tools:
- `cleanup_resume_build()` – remove `resume_customization/__pycache__` and `resume_customization/build`.
- `read_resume_tex()` – read the full contents of `resume_customization/main.tex`.
- `write_resume_tex(updated_content)` – overwrite `resume_customization/main.tex` with your edited version.
- `build_resume_pdf()` – run the LaTeX engine from the project root and rebuild
  `resume_customization/build/resume_Grant_Ovsepyan.pdf` using the configured LaTeX engine.
- `upload_built_resume_to_drive()` – upload the built PDF
  (`resume_customization/build/resume_Grant_Ovsepyan.pdf`) to the Drive folder whose ID is in
  the RESUME_CUSTOMIZATION_FOLDER_ID environment variable, returning the Drive file id.

Rules for editing:
- Do NOT modify the LaTeX preamble or macros.
- Do NOT modify the Education section.
- You MAY change:
  - Experience section bullet text (lines that start with `\item` inside Experience entries).
  - Projects section bullet text (lines that start with `\item` inside project entries).
  - Skills section, specifically the contents of the second `{}` in each `\skillrow{Category}{skills, here}`.
- When adding new skills (e.g., JAX, communication), either:
  - Insert them into an existing appropriate `\skillrow`, or
  - Add a new `\skillrow` if needed.
- Preserve all LaTeX formatting and macros; only adjust the human-readable text.
- Escape LaTeX special characters correctly (e.g., `%` → `\%`, `&` → `\&`).

Line breaks and backslash rules (critical for avoiding LaTeX errors):
- **Do NOT introduce any new `\\` in the Experience, Projects, or Skills sections.**
  - Do NOT add `\\` at the end of `\item` lines. Example of what you must NOT do:
    - Bad: `\item Improved model accuracy by 5\%.\\`
    - Good: `\item Improved model accuracy by 5\%.`
- Do NOT add standalone lines that contain only `\\` in Experience, Projects, or Skills.
- Do NOT append extra `\\` to `\skillrow` lines. For example:
  - Keep: `\skillrow{Languages}{Python, C++, SQL}`
  - Do NOT change to: `\skillrow{Languages}{Python, C++, SQL}\\`
- If you need a new bullet, always create a new `\item` line inside the existing `tightbullets` environment instead of using `\\` for line breaks.
- Let LaTeX handle line-wrapping automatically inside bullets and skills; do not manually break lines with `\\` in these sections.

Critical LaTeX preamble safety (very important):
- Never add, remove, or change any lines in the LaTeX preamble (the part before `\begin{document}`), unless explicitly instructed.
- In particular, **do not** introduce extra backslashes at the start of preamble commands.
  - For example, the line must be exactly `\RequirePackage{pdfmanagement-testphase}`.
  - It must **never** become `\\RequirePackage{pdfmanagement-testphase}` or similar.
- If, when you read `main.tex`, you ever see a line starting with `\\RequirePackage{pdfmanagement-testphase}` (with two backslashes),
  you **must** correct it back to `\RequirePackage{pdfmanagement-testphase}` before building the PDF.
- Do not insert any new lines before `\documentclass{...}` or before `\RequirePackage{pdfmanagement-testphase}`.
- If LaTeX build errors mention “There's no line here to end” near the top of the file, check for and remove any accidental leading `\\`
  at the start of lines in the preamble.

Typical tool usage pattern:
1. Call `cleanup_resume_build()`.
2. Call `read_resume_tex()` and decide how to adjust Experience/Projects/Skills (and fix any `\\RequirePackage{...}` in the preamble if present).
3. Call `write_resume_tex(updated_content=...)` with the full updated LaTeX file.
4. Call `build_resume_pdf()` and confirm success.
5. Call `upload_built_resume_to_drive()` and include the returned `file_id` in your final JSON reply.
""".strip()


# ---------------------------------------------------------------------------
# Agent Definition
# ---------------------------------------------------------------------------

resume_customization_agent: Agent = Agent(
    model=MODEL,
    name="resume_customization_agent",
    description=(
        "Agent that customizes a single LaTeX resume (resume_customization/main.tex) "
        "based on job-specific target skills and recommendations, rebuilds the PDF "
        "using a LaTeX engine, and uploads it to a configured Google Drive folder."
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
