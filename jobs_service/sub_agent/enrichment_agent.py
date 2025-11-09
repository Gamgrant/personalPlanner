from __future__ import annotations

import os
import re
import html as htmllib
import requests
from typing import List, Optional, Dict, Any

from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import get_google_service

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# -------------------------------
# Google API clients
# -------------------------------

def get_sheets_service() -> object:
    return get_google_service("sheets", "v4", SCOPES, "BACKFILL_SHEETS")


def get_drive_service() -> object:
    return get_google_service("drive", "v3", SCOPES, "BACKFILL_DRIVE")


# -------------------------------
# Spreadsheet discovery helpers
# -------------------------------

def _find_job_search_spreadsheet_id(name: str = "Job_search_Database") -> str:
    """
    Locate the job search spreadsheet by exact name across all accessible drives.
    """
    drive = get_drive_service()
    try:
        resp = drive.files().list(
            q=(
                "mimeType='application/vnd.google-apps.spreadsheet' "
                f"and name='{name}' and trashed=false"
            ),
            pageSize=10,
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Drive API error: {e}")

    files: List[Dict[str, Any]] = resp.get("files", []) or []
    if not files:
        raise RuntimeError(
            f"[BACKFILL] Spreadsheet '{name}' not found. "
            f"Create it or update the name in _find_job_search_spreadsheet_id."
        )
    return files[0]["id"]


def _get_first_sheet_name(spreadsheet_id: str) -> str:
    """
    Return the title of the first sheet/tab in the spreadsheet.
    """
    sheets = get_sheets_service()
    resp = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(title))",
    ).execute()
    sheets_list = resp.get("sheets", []) or []
    if not sheets_list:
        raise RuntimeError("[BACKFILL] Target spreadsheet has no sheets.")
    return sheets_list[0]["properties"]["title"]


# -------------------------------
# Extraction helpers (from FULL plain text in column F)
# -------------------------------

_DEGREE_LEVELS = [
    ("phd", r"\b(ph\.?d\.?|doctorate|doctoral)\b"),
    ("master's", r"\b(master'?s|ms|m\.s\.|msc|m\.sc\.|m\.eng|meng|mba)\b"),
    ("bachelor's", r"\b(bachelor'?s|bs|b\.s\.|ba|b\.a\.|bsc|b\.sc\.|b\.eng|beng)\b"),
    ("associate", r"\b(associate'?s|aas|a\.a\.s\.|as|a\.s\.)\b"),
    ("high school", r"\b(high\s*school|ged)\b"),
]

# Common hard + soft skills; expand freely
_SKILL_ALIASES: Dict[str, str] = {
    # core
    "Python": r"\bpython\b",
    "R": r"(?<!\w)r(?!\w)",
    "SQL": r"\bsql\b",
    "Excel": r"\bexcel\b",
    "PowerPoint": r"\bpower\s*point|powerpoint\b",
    "Tableau": r"\btableau\b",
    "Looker": r"\blooker\b",
    "Power BI": r"\bpower\s*bi\b",
    "Git": r"\bgit\b",
    "Linux": r"\blinux\b",
    # data/ML/eng
    "Pandas": r"\bpandas\b",
    "NumPy": r"\bnumpy\b",
    "Scikit-learn": r"\bscikit[-\s]?learn|sklearn\b",
    "TensorFlow": r"\btensorflow\b",
    "PyTorch": r"\bpytorch\b",
    "Spark": r"\bspark\b",
    "Hadoop": r"\bhadoop\b",
    "Airflow": r"\bairflow\b",
    # cloud/devops
    "AWS": r"\baws\b",
    "GCP": r"\bgcp|google\s+cloud\b",
    "Azure": r"\bazure\b",
    "Docker": r"\bdocker\b",
    "Kubernetes": r"\bkubernetes|k8s\b",
    "Snowflake": r"\bsnowflake\b",
    # web/app/other
    "Java": r"\bjava(?!script)\b",
    "JavaScript": r"\bjavascript|node\.?js|nodejs\b",
    "TypeScript": r"\btypescript\b",
    "React": r"\breact\b",
    "Go": r"\bgolang|\bgo\b",
    "C++": r"\bc\+\+\b",
    "C#": r"\bc#\b",
    # soft/other
    "Communication": r"\bcommunication|communicator\b",
    "Leadership": r"\blead(?:er|ership)\b",
    "Project Management": r"\bproject\s+management\b",
}

_YOE_PATTERNS = [
    # 2-3 years / 2 to 3 years / 2+ years, optional "of full-time experience"
    r"\b(?P<min>\d+(?:\.\d+)?)\s*(?:\+|(?:-|–|—|to)\s*(?P<max>\d+(?:\.\d+)?))?\s*"
    r"(?:years?|yrs?)'?(?:\s+of)?\s*(?:full[-\s]*time\s*)?(?:experience|exp)?\b",

    # at least / minimum of 3 years
    r"\b(?:minimum|at\s+least)(?:\s+of)?\s*(?P<min>\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b",

    # up to 5 years preferred
    r"\b(?P<max>\d+(?:\.\d+)?)\s*(?:years?|yrs?)\s*(?:experience)?\s*preferred\b",

    # entry/new-grad / internship fallbacks
    r"\b(entry[-\s]?level|new\s*grad)\b",
    r"\bintern(ship)?\b",
]

def _extract_years_experience(text: str) -> str:
    # Try explicit ranges / plus / “to” first
    for p in _YOE_PATTERNS[:3]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            gd = m.groupdict()
            minv = gd.get("min")
            maxv = gd.get("max")
            if minv and maxv:
                return f"{minv}-{maxv} years"
            if minv:
                return f"{minv}+ years"
            if maxv:
                return f"up to {maxv} years"

    # Fallback labels
    if re.search(_YOE_PATTERNS[3], text, re.IGNORECASE):
        return "0-1 years (entry level)"
    if re.search(_YOE_PATTERNS[4], text, re.IGNORECASE):
        return "0 years (internship)"
    return ""

def _extract_degree(text: str) -> str:
    found = []
    for label, pat in _DEGREE_LEVELS:
        if re.search(pat, text, re.IGNORECASE):
            found.append(label)
    if not found:
        return ""
    order = {lvl: i for i, (lvl, _) in enumerate(_DEGREE_LEVELS)}
    highest = sorted(found, key=lambda x: order[x])[0]
    return "PhD" if highest == "phd" else highest.title()

def _extract_skills(text: str, max_count: int = 30) -> str:
    hits = [name for name, pat in _SKILL_ALIASES.items() if re.search(pat, text, re.IGNORECASE)]
    priority = [
        "Python","SQL","Pandas","NumPy","Scikit-learn","TensorFlow","PyTorch",
        "AWS","GCP","Azure","Docker","Kubernetes","Spark","Hadoop","Snowflake",
        "Tableau","Looker","Power BI","Excel","Git","Linux","Airflow",
        "Java","JavaScript","TypeScript","React","Go","C++","C#",
        "Project Management","Leadership","Communication","PowerPoint","R",
    ]
    ordered = [s for s in priority if s in hits][:max_count]
    return ", ".join(ordered)

def _extract_all_fields(text: str) -> Dict[str, str]:
    return {
        "degree": _extract_degree(text),
        "yoe": _extract_years_experience(text),
        "skills": _extract_skills(text),
    }

# -------------------------------
# HTML / description helpers (NO TRUNCATION)
# -------------------------------

def _html_to_text_full(html: str) -> str:
    """
    Convert HTML to full plain text with structure preserved (no truncation).
    """
    if not html:
        return ""

    # Remove scripts/styles
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)

    # Replace common block tags with newlines to preserve structure
    block_tags = [
        "p","div","br","li","ul","ol","section","article",
        "h1","h2","h3","h4","h5","h6","table","tr","td","th"
    ]
    for tag in block_tags:
        html = re.sub(fr"</?{tag}[^>]*>", "\n", html, flags=re.IGNORECASE)

    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", html)

    # Unescape entities
    text = htmllib.unescape(text)

    # Normalize whitespace
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)  # collapse 3+ newlines to 2
    return text.strip()


# Optional domain -> Greenhouse board mapping
GH_COMPANY_FROM_DOMAIN: Dict[str, str] = {
    "stripe.com": "stripe",
    "databricks.com": "databricks",
    "asana.com": "asana",
    "anthropic.com": "anthropic",
    # extend as needed
}

def _infer_greenhouse_company_from_url(url: str) -> Optional[str]:
    """
    Best-effort: infer Greenhouse board name for gh_jid URLs.
    """
    url_lower = url.lower()
    for domain, board in GH_COMPANY_FROM_DOMAIN.items():
        if domain in url_lower:
            return board
    m = re.search(r"job-boards\.greenhouse\.io/([^/]+)/jobs/", url_lower)
    if m:
        return m.group(1)
    return None


def _fetch_description_from_url(url: str) -> str:
    """
    Get FULL job description text from a Website URL (no length limits).

    - If URL has gh_jid, try Greenhouse API (content=true) and return full text.
    - Otherwise fetch HTML and convert to full plain text.
    """
    if not url:
        return ""

    # Case 1: Greenhouse API path (...?gh_jid=123456)
    gh_match = re.search(r"[?&]gh_jid=(\d+)", url)
    if gh_match:
        job_id = gh_match.group(1)
        company = _infer_greenhouse_company_from_url(url)
        if company:
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}?content=true"
            try:
                r = requests.get(api_url, timeout=20)
                r.raise_for_status()
                data = r.json() or {}
                html = data.get("content") or data.get("description") or ""
                if html:
                    return _html_to_text_full(html)
            except Exception:
                # Fall through to generic fetch
                pass

    # Case 2: generic GET, then HTML->text
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return _html_to_text_full(r.text)
    except Exception:
        return ""


# -------------------------------
# Core tool: backfill FULL descriptions
# -------------------------------

def backfill_job_descriptions(
    max_rows: Optional[int] = None,
) -> str:
    """
    Backfill the FULL Description column in Job_search_Database.

    Expected layout (first sheet):
        A: Jobs
        B: Website (URL, source of truth)
        C: Company
        D: Location
        E: Date Posted
        F: Description  <-- filled by this tool with FULL text (no truncation)
        G: Years of Experience

    For each row where:
        - Website (B) has a non-empty URL, and
        - Description (F) is empty,
    fetch the FULL description text from Website and write it into F.

    Only column F is modified. Other columns are left unchanged.
    """
    sheets = get_sheets_service()
    spreadsheet_id = _find_job_search_spreadsheet_id("Job_search_Database")
    sheet_name = _get_first_sheet_name(spreadsheet_id)

    # Include through column G so we don't accidentally shrink rows
    data_range = f"{sheet_name}!A2:G"

    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=data_range,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Failed to read sheet values: {e}")

    rows: List[List[str]] = result.get("values", []) or []
    if not rows:
        return "[BACKFILL] No rows found."

    # Decide how many rows to inspect
    limit = len(rows) if not max_rows or max_rows <= 0 else min(max_rows, len(rows))

    updates: List[Dict[str, Any]] = []
    updated_count = 0

    for idx in range(limit):
        row = rows[idx]

        # Ensure at least 7 columns (A-G)
        if len(row) < 7:
            row = row + [""] * (7 - len(row))

        website = (row[1] or "").strip()      # B: Website
        description = (row[5] or "").strip()  # F: Description

        if website and not description:
            full_desc = _fetch_description_from_url(website)
            if full_desc:
                row_number = idx + 2  # data starts at row 2
                updates.append({
                    "range": f"{sheet_name}!F{row_number}",
                    "values": [[full_desc]],
                })
                updated_count += 1

    if not updates:
        return "[BACKFILL] No descriptions updated (all filled or no fetchable content)."

    try:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": updates,
            },
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Failed to write updated descriptions: {e}")

    return f"[BACKFILL] Updated descriptions for {updated_count} job(s) in '{sheet_name}'."


# -------------------------------
# Core tool: extract Degree/YOE/Skill into G–I
# -------------------------------

def extract_structured_fields(
    max_rows: Optional[int] = None,
    overwrite: bool = False,
) -> str:
    """
    From each row's FULL Description in F:
      - Write G: Degree
      - Write H: YOE
      - Write I: Skill(s) (comma-separated)

    Only fills empty cells unless overwrite=True.
    """
    sheets = get_sheets_service()
    spreadsheet_id = _find_job_search_spreadsheet_id("Job_search_Database")
    sheet_name = _get_first_sheet_name(spreadsheet_id)

    # Read A..I so we can inspect Description (F) and targets (G..I)
    data_range = f"{sheet_name}!A2:I"
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=data_range,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[FIELDS] Failed to read sheet values: {e}")

    rows: List[List[str]] = result.get("values", []) or []
    if not rows:
        return "[FIELDS] No rows found."

    limit = len(rows) if not max_rows or max_rows <= 0 else min(max_rows, len(rows))

    updates: List[Dict[str, Any]] = []
    counts = {"degree": 0, "yoe": 0, "skills": 0}

    for i in range(limit):
        row = rows[i]
        # Pad to at least column I (index 8)
        if len(row) < 9:
            row = row + [""] * (9 - len(row))

        desc   = (row[5] or "").strip()  # F
        cur_g  = (row[6] or "").strip()  # G Degree
        cur_h  = (row[7] or "").strip()  # H YOE
        cur_i  = (row[8] or "").strip()  # I Skill

        want_g = overwrite or not cur_g
        want_h = overwrite or not cur_h
        want_i = overwrite or not cur_i

        if not desc or not (want_g or want_h or want_i):
            continue

        extracted = _extract_all_fields(desc)
        rownum = i + 2

        if want_g and extracted["degree"]:
            updates.append({"range": f"{sheet_name}!G{rownum}", "values": [[extracted["degree"]]]})
            counts["degree"] += 1
        if want_h and extracted["yoe"]:
            updates.append({"range": f"{sheet_name}!H{rownum}", "values": [[extracted["yoe"]]]})
            counts["yoe"] += 1
        if want_i and extracted["skills"]:
            updates.append({"range": f"{sheet_name}!I{rownum}", "values": [[extracted["skills"]]]})
            counts["skills"] += 1

    if not updates:
        return "[FIELDS] Nothing to update (no descriptions or target cells already filled)."

    try:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[FIELDS] Failed to write structured fields: {e}")

    return (
        f"[FIELDS] Updated — Degree={counts['degree']}, "
        f"YOE={counts['yoe']}, Skill(s)={counts['skills']}."
    )

# -------------------------------
# Agent definition
# -------------------------------

backfill_agent_instruction = """
You enrich the existing 'Job_search_Database' Google Sheet.

Layout (first sheet):
  A: Jobs
  B: Website
  C: Company
  D: Location
  E: Date Posted
  F: Description   (FULL plain text, no truncation)
  G: Degree
  H: YOE
  I: Skill

Behavior:
- If Description (F) is empty and Website (B) has a URL:
    • Fetch the page (use Greenhouse API when gh_jid appears; else fetch HTML).
    • Convert to FULL plain text and write to F.
- From the FULL Description (F), extract and write:
    • G: Degree
    • H: YOE
    • I: Skill(s) (comma-separated)
- Only fill empty cells unless the tool is called with overwrite=True.
- Never modify other columns or create new spreadsheets.
""".strip()

description_agent = Agent(
    model=MODEL,
    name="job_description_backfill_agent",
    description=backfill_agent_instruction,
    tools=[backfill_job_descriptions, extract_structured_fields],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)

__all__ = ["description_agent", "backfill_job_descriptions", "extract_structured_fields"]