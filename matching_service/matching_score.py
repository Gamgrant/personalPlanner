from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

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
    return get_google_service("sheets", "v4", SCOPES, "PREF_MATCH_SHEETS")

def get_drive_service() -> object:
    return get_google_service("drive", "v3", SCOPES, "PREF_MATCH_DRIVE")


# -------------------------------
# Spreadsheet helpers
# -------------------------------

def _find_job_search_spreadsheet_id(name: str = "Job_search_Database") -> str:
    """
    Locate the job search spreadsheet by exact name across all accessible drives.
    """
    drive = get_drive_service()
    try:
        resp = drive.files().list(
            q=f"mimeType='application/vnd.google-apps.spreadsheet' and name='{name}' and trashed=false",
            pageSize=10,
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[PREF-MATCH] Drive API error: {e}")

    files: List[Dict[str, Any]] = resp.get("files", []) or []
    if not files:
        raise RuntimeError(f"[PREF-MATCH] Spreadsheet '{name}' not found.")
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
    lst = resp.get("sheets", []) or []
    if not lst:
        raise RuntimeError("[PREF-MATCH] Target spreadsheet has no sheets.")
    return lst[0]["properties"]["title"]


# -------------------------------
# Parsing helpers
# -------------------------------

def _parse_date(value: str) -> Optional[datetime]:
    """
    Parse Date Posted (E) into datetime. Supports a few common formats.
    Returns None if parsing fails.
    """
    if not value:
        return None
    v = value.strip()
    # Try ISO-like first
    fmts = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
    ]
    for f in fmts:
        try:
            return datetime.strptime(v, f)
        except ValueError:
            continue
    # Very dumb fallback: if looks like '2025-11-09 12:34:56'
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _parse_job_yoe(yoe_str: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Parse a YOE requirement like:
      '2-4 years', '3+ years', 'up to 5 years'
    into (min, max). If can't parse, returns (None, None).
    """
    if not yoe_str:
        return (None, None)

    text = yoe_str.lower().strip()

    # between 2 and 4 years / 2-4 years / 2 to 4 years
    m = re.search(
        r"(?P<min>\d+(?:\.\d+)?)\s*(?:-|–|—|to|and)\s*(?P<max>\d+(?:\.\d+)?)\s*(?:\+)?\s*(?:years?|yrs?)",
        text,
    )
    if m:
        return (float(m.group("min")), float(m.group("max")))

    # 3+ years / at least 3 years
    m = re.search(
        r"(?:at\s+least|minimum|>=)?\s*(?P<min>\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)",
        text,
    )
    if m:
        return (float(m.group("min")), None)

    # up to 5 years
    m = re.search(
        r"(?:up\s*to|<=)\s*(?P<max>\d+(?:\.\d+)?)\s*(?:years?|yrs?)",
        text,
    )
    if m:
        return (None, float(m.group("max")))

    # entry-level / new grad / intern
    if re.search(r"entry[-\s]?level|new\s*grad", text):
        return (0.0, 1.0)
    if re.search(r"\bintern(ship)?\b", text):
        return (0.0, 0.0)

    return (None, None)


def _title_matches(job_title: str, job_keywords: str) -> float:
    """
    Simple score: fraction of keywords that appear in job title.
    """
    if not job_keywords.strip():
        return 0.0
    title = job_title.lower()
    tokens = [t.strip().lower() for t in re.split(r"[,\s/]+", job_keywords) if t.strip()]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in title)
    return hits / max(1, len(tokens))


def _location_matches(job_location: str, location_pref: str) -> float:
    if not location_pref.strip():
        return 0.0
    jl = (job_location or "").lower()
    lp = location_pref.lower()
    return 1.0 if lp in jl else 0.0


def _company_matches(job_company: str, company_pref: str) -> float:
    if not company_pref.strip():
        return 0.0
    jc = (job_company or "").lower()
    cp = company_pref.lower()
    return 1.0 if cp in jc else 0.0


def _yoe_matches(job_yoe_str: str, user_yoe: Optional[float]) -> float:
    if user_yoe is None:
        return 0.0
    jmin, jmax = _parse_job_yoe(job_yoe_str)
    # If no requirement, treat as neutral partial score.
    if jmin is None and jmax is None:
        return 0.5
    # Check minimum bound
    if jmin is not None and user_yoe + 1e-9 < jmin:
        return 0.0
    # Max bound is soft (we don't punish being higher)
    return 1.0


def _date_matches(date_posted_str: str, max_days_posted: Optional[int]) -> float:
    if max_days_posted is None or max_days_posted <= 0:
        return 0.0
    dt = _parse_date(date_posted_str)
    if not dt:
        return 0.0
    today = datetime.utcnow().date()
    delta = (today - dt.date()).days
    if delta < 0:
        # future-dated or parse weird: treat as not matching
        return 0.0
    return 1.0 if delta <= max_days_posted else 0.0


def _weighted_score(
    company_score: float,
    title_score: float,
    location_score: float,
    yoe_score: float,
    date_score: float,
) -> float:
    """
    Combine scores with simple weights.
    Adjust as needed.
    """
    parts: List[Tuple[float, float]] = []
    if company_score > 0:
        parts.append((company_score, 0.25))
    if title_score > 0:
        parts.append((title_score, 0.30))
    if location_score > 0:
        parts.append((location_score, 0.20))
    if yoe_score > 0:
        parts.append((yoe_score, 0.15))
    if date_score > 0:
        parts.append((date_score, 0.10))

    if not parts:
        return 0.0

    wsum = sum(w for _, w in parts)
    return sum(s * w for s, w in parts) / max(1e-9, wsum)


def _next_col(col: str) -> str:
    """
    Given 'J', return 'K', etc.
    """
    col = col.upper().strip()
    # convert letters to number
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    n += 1
    # back to letters
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# -------------------------------
# Core tool: preference-based matching
# -------------------------------

def find_matching_jobs_by_preferences(
    company: str = "",
    job_keywords: str = "",
    location_preference: str = "",
    user_yoe: Optional[float] = None,
    max_days_posted: Optional[int] = None,
    spreadsheet_name: str = "Job_search_Database",
    output_start_col: str = "J",
    min_score: float = 0.60,
    max_rows: Optional[int] = None,
) -> str:
    """
    Filter and score jobs in 'Job_search_Database' based on user-stated preferences
    instead of a CV.

    Inputs (to be collected from the user by the orchestrator):
      - company: desired company name (optional)
      - job_keywords: target roles or keywords (e.g. "data scientist, ml, infra")
      - location_preference: city/region/remote preference (optional)
      - user_yoe: candidate's years of experience (float, optional)
      - max_days_posted: only consider jobs posted within this many days (int, optional)

    Behavior:
      - Reads rows from first sheet:
          A: Job Title
          B: Website
          C: Company
          D: Location
          E: Date Posted
          F: Description
          G: Degree
          H: YOE (requirement)
          I: Skills
      - For each row, computes:
          company_score   (match on C vs company)
          title_score     (match on A vs job_keywords)
          location_score  (match on D vs location_preference)
          yoe_score       (user_yoe vs H)
          date_score      (E vs max_days_posted)
      - Combines into a match score (0–1), then:
          J: match score * 100 (rounded to 2 decimals)
          K: "Yes" if score >= min_score else "No"
      - Does NOT modify A–I.

    Returns:
      A brief summary string:
        "[PREF-MATCH] Scored X jobs. Matches ≥ YY%: Z. Written to J/K."
    """
    sheets = get_sheets_service()
    sid = _find_job_search_spreadsheet_id(spreadsheet_name)
    sheet = _get_first_sheet_name(sid)

    data_range = f"{sheet}!A2:I"
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=sid,
            range=data_range,
        ).execute()
        rows: List[List[str]] = result.get("values", []) or []
    except HttpError as e:
        raise RuntimeError(f"[PREF-MATCH] Failed to read sheet values: {e}")

    if not rows:
        return "[PREF-MATCH] No rows found."

    if max_rows and max_rows > 0:
        rows = rows[:max_rows]

    updates: List[Dict[str, Any]] = []
    match_count = 0

    start_col = output_start_col.upper().strip()
    label_col = _next_col(start_col)

    for i, row in enumerate(rows):
        # Pad to I
        if len(row) < 9:
            row = row + [""] * (9 - len(row))

        job_title = row[0] or ""
        job_company = row[2] or ""
        job_location = row[3] or ""
        job_date_posted = row[4] or ""
        job_yoe_req = row[7] or ""  # H (requirement string)

        # Compute individual scores
        c_score = _company_matches(job_company, company)
        t_score = _title_matches(job_title, job_keywords)
        l_score = _location_matches(job_location, location_preference)
        y_score = _yoe_matches(job_yoe_req, user_yoe)
        d_score = _date_matches(job_date_posted, max_days_posted)

        score = _weighted_score(c_score, t_score, l_score, y_score, d_score)
        score_pct = round(score * 100.0, 2)
        label = "Yes" if score >= min_score else "No"
        if label == "Yes":
            match_count += 1

        rownum = i + 2
        updates.append({
            "range": f"{sheet}!{start_col}{rownum}",
            "values": [[f"{score_pct}"]],
        })
        updates.append({
            "range": f"{sheet}!{label_col}{rownum}",
            "values": [[label]],
        })

    if updates:
        try:
            sheets.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "USER_ENTERED", "data": updates},
            ).execute()
        except HttpError as e:
            raise RuntimeError(f"[PREF-MATCH] Failed to write scores: {e}")

    return (
        f"[PREF-MATCH] Scored {len(rows)} jobs. "
        f"Matches ≥ {int(min_score * 100)}%: {match_count}. "
        f"Written to {start_col}/{label_col}."
    )


# -------------------------------
# Agent definition
# -------------------------------

pref_match_agent_instruction = """
Using the 
Data layout (first sheet of Job_search_Database):
  A: Job Title
  B: Website
  C: Company
  D: Location
  E: Date Posted
  F: Description
  G: Degree (requirement)
  H: YOE (requirement)
  I: Skills (requirement)

Behavior:
- For each row, compute:
    • company_score   based on company vs C
    • title_score     based on job_keywords vs A
    • location_score  based on location_preference vs D
    • yoe_score       based on user_yoe vs H
    • date_score      based on max_days_posted vs E
- Combine into a weighted match score.
- Write:
    • J: Match Score (0–100)
    • K: "Yes" if score ≥ min_score, else "No"
- Do NOT modify columns A–I.
"""

match_agent = Agent(
    model=MODEL,
    name="preference_match_agent",
    description=pref_match_agent_instruction,
    tools=[find_matching_jobs_by_preferences],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)

__all__ = ["match_agent"]