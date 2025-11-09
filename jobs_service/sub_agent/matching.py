from __future__ import annotations

import os
import re
from typing import List, Dict, Any, Optional, Tuple

from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types
import io
from pypdf import PdfReader
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
    return get_google_service("sheets", "v4", SCOPES, "CV_MATCH_SHEETS")

def get_drive_service() -> object:
    return get_google_service("drive", "v3", SCOPES, "CV_MATCH_DRIVE")

# -------------------------------
# Spreadsheet helpers
# -------------------------------

def _find_job_search_spreadsheet_id(name: str = "Job_search_Database") -> str:
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
        raise RuntimeError(f"[CV-MATCH] Drive API error: {e}")

    files: List[Dict[str, Any]] = resp.get("files", []) or []
    if not files:
        raise RuntimeError(f"[CV-MATCH] Spreadsheet '{name}' not found.")
    return files[0]["id"]

def _get_first_sheet_name(spreadsheet_id: str) -> str:
    sheets = get_sheets_service()
    resp = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(title))",
    ).execute()
    lst = resp.get("sheets", []) or []
    if not lst:
        raise RuntimeError("[CV-MATCH] Target spreadsheet has no sheets.")
    return lst[0]["properties"]["title"]

# -------------------------------
# Drive: locate and load CV text
# -------------------------------


def _find_cv_file_id(file_name: str = "steven_yeo_cv") -> tuple[str, str]:
    """
    Return (file_id, mimeType). Prefers Google Docs, then text, then PDF.
    """
    drive = get_drive_service()
    try:
        q_common = (
            "("
            "mimeType='application/vnd.google-apps.document' or "
            "mimeType='text/plain' or "
            "mimeType='application/pdf'"
            ") and trashed=false"
        )

        # Exact name first
        resp = drive.files().list(
            q=f"name='{file_name}' and {q_common}",
            pageSize=10,
            fields="files(id,name,mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = resp.get("files", []) or []

        # If not found, try contains/starts with
        if not files:
            resp = drive.files().list(
                q=f"(name contains '{file_name}' or name starts with '{file_name}') and {q_common}",
                pageSize=10,
                fields="files(id,name,mimeType)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files = resp.get("files", []) or []
    except HttpError as e:
        raise RuntimeError(f"[CV-MATCH] Drive search error: {e}")

    # Preference order
    docs = [f for f in files if f.get("mimeType") == "application/vnd.google-apps.document"]
    txts = [f for f in files if f.get("mimeType") == "text/plain"]
    pdfs = [f for f in files if f.get("mimeType") == "application/pdf"]
    pick = (docs or txts or pdfs)
    if not pick:
        raise RuntimeError(
            "[CV-MATCH] Could not find a Google Doc, text, or PDF named like 'steven_yeo_cv'."
        )
    return pick[0]["id"], pick[0]["mimeType"]

def _load_cv_text_from_drive(file_name: str = "steven_yeo_cv") -> str:
    """
    Export Google Doc to text, or download & extract text from TXT/PDF.
    """
    drive = get_drive_service()
    file_id, mime = _find_cv_file_id(file_name)
    try:
        if mime == "application/vnd.google-apps.document":
            data = drive.files().export(fileId=file_id, mimeType="text/plain").execute()
            return data.decode("utf-8", errors="ignore") if isinstance(data, (bytes, bytearray)) else str(data)

        if mime == "text/plain":
            data = drive.files().get_media(fileId=file_id).execute()
            return data.decode("utf-8", errors="ignore") if isinstance(data, (bytes, bytearray)) else str(data)

        if mime == "application/pdf":
            pdf_bytes = drive.files().get_media(fileId=file_id).execute()
            try:
                from pypdf import PdfReader  # pip install pypdf
            except Exception as e:
                raise RuntimeError("Install the PDF parser: `pip install pypdf`") from e

            import io
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages_text = []
            for p in reader.pages:
                pages_text.append(p.extract_text() or "")
            text = "\n".join(pages_text).strip()

            if not text:
                # Likely a scanned PDF (images only). Hook up OCR if needed.
                raise RuntimeError("[CV-MATCH] The PDF appears to be scanned or has no extractable text.")
            return text

        raise RuntimeError(f"[CV-MATCH] Unsupported CV mimeType: {mime}")
    except HttpError as e:
        raise RuntimeError(f"[CV-MATCH] Failed to load CV content: {e}")

# -------------------------------
# NLP helpers (degree / skills / YOE)
# -------------------------------

_DEGREE_LEVELS = [
    ("phd",        r"\b(ph\.?d\.?|doctorate|doctoral)\b"),
    ("master's",   r"\b(master'?s|ms|m\.s\.|msc|m\.sc\.|m\.eng|meng|mba)\b"),
    ("bachelor's", r"\b(bachelor'?s|bs|b\.s\.|ba|b\.a\.|bsc|b\.sc\.|b\.eng|beng)\b"),
    ("associate",  r"\b(associate'?s|aas|a\.a\.s\.|as|a\.s\.)\b"),
    ("high school",r"\b(high\s*school|ged)\b"),
]
_DEG_ORDER = {"high school":0, "associate":1, "bachelor's":2, "master's":3, "phd":4}

def _extract_degree_label(text: str) -> Optional[str]:
    found = []
    for label, pat in _DEGREE_LEVELS:
        if re.search(pat, text, re.IGNORECASE):
            found.append(label)
    if not found:
        return None
    highest = sorted(found, key=lambda x: _DEG_ORDER[x], reverse=True)[0]
    return "PhD" if highest == "phd" else highest.title()

# Skills dictionary (expand anytime)
_SKILL_ALIASES: Dict[str, str] = {
    # core & data
    "Python": r"\bpython\b",
    "R": r"(?<!\w)r(?!\w)",
    "SQL": r"\bsql\b",
    "Pandas": r"\bpandas\b",
    "NumPy": r"\bnumpy\b",
    "Scikit-learn": r"\bscikit[-\s]?learn|sklearn\b",
    "TensorFlow": r"\btensorflow\b",
    "PyTorch": r"\bpytorch\b",
    "Spark": r"\bspark\b",
    "Hadoop": r"\bhadoop\b",
    "Airflow": r"\bairflow\b",
    "Snowflake": r"\bsnowflake\b",
    # BI / productivity
    "Tableau": r"\btableau\b",
    "Looker": r"\blooker\b",
    "Power BI": r"\bpower\s*bi\b",
    "Excel": r"\bexcel\b",
    "PowerPoint": r"\bpower\s*point|powerpoint\b",
    # devops/cloud
    "Git": r"\bgit\b",
    "Linux": r"\blinux\b",
    "Docker": r"\bdocker\b",
    "Kubernetes": r"\bkubernetes|k8s\b",
    "AWS": r"\baws\b",
    "GCP": r"\bgcp|google\s+cloud\b",
    "Azure": r"\bazure\b",
    # web/app
    "Java": r"\bjava(?!script)\b",
    "JavaScript": r"\bjavascript|node\.?js|nodejs\b",
    "TypeScript": r"\btypescript\b",
    "React": r"\breact\b",
    "Go": r"\bgolang|\bgo\b",
    "C++": r"\bc\+\+\b",
    "C#": r"\bc#\b",
    # soft/management
    "Project Management": r"\bproject\s+management\b",
    "Leadership": r"\blead(?:er|ership)\b",
    "Communication": r"\bcommunication|communicator\b",
}

def _extract_skills(text: str, max_count: int = 50) -> List[str]:
    hits = [name for name, pat in _SKILL_ALIASES.items() if re.search(pat, text, re.IGNORECASE)]
    # Priority ordering
    priority = [
        "Python","SQL","Pandas","NumPy","Scikit-learn","TensorFlow","PyTorch",
        "AWS","GCP","Azure","Docker","Kubernetes","Spark","Hadoop","Snowflake",
        "Tableau","Looker","Power BI","Excel","Git","Linux","Airflow",
        "Java","JavaScript","TypeScript","React","Go","C++","C#",
        "Project Management","Leadership","Communication","PowerPoint","R",
    ]
    ordered = [s for s in priority if s in hits]
    tail = [s for s in hits if s not in ordered]
    return (ordered + tail)[:max_count]

# YOE parsing
_NUM_WORDS = {
    "zero":"0","one":"1","two":"2","three":"3","four":"4","five":"5",
    "six":"6","seven":"7","eight":"8","nine":"9","ten":"10","eleven":"11","twelve":"12",
}
def _normalize_spelled_numbers(t: str) -> str:
    return re.sub(
        r"\b(" + "|".join(map(re.escape, _NUM_WORDS.keys())) + r")\b",
        lambda m: _NUM_WORDS[m.group(1).lower()],
        t, flags=re.IGNORECASE
    )

_RANGE = r"(?:-|–|—|\bto\b|\bthrough\b|\bthru\b|\band\b)"
_YOE_PATTERNS = [
    rf"\bbetween\s+(?P<min>\d+(?:\.\d+)?)\s+{_RANGE}\s+(?P<max>\d+(?:\.\d+)?)\s*(?:\+)?\s*(?:years?|yrs?)\b",
    rf"\b(?P<min>\d+(?:\.\d+)?)\s*(?:\+|{_RANGE}\s*(?P<max>\d+(?:\.\d+)?))\s*(?:\+)?\s*(?:years?|yrs?)\b",
    r"\b(?:minimum|at\s+least|>=?)\s*(?P<min>\d+(?:\.\d+)?)\s*(?:\+)?\s*(?:years?|yrs?)\b",
    r"\b(?:up\s*to|<=)\s*(?P<max>\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b",
    r"\b(entry[-\s]?level|new\s*grad)\b",
    r"\bintern(ship)?\b",
]

def _parse_yoe_phrase(text: str) -> Tuple[Optional[float], Optional[float]]:
    if not text:
        return (None, None)
    t = _normalize_spelled_numbers(text)
    for pat in _YOE_PATTERNS:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            gd = m.groupdict()
            minv = float(gd["min"]) if gd.get("min") else None
            maxv = float(gd["max"]) if gd.get("max") else None
            if minv is None and maxv is None:
                if re.search(r"entry[-\s]?level|new\s*grad", t, re.I):
                    return (0.0, 1.0)
                if re.search(r"\bintern(ship)?\b", t, re.I):
                    return (0.0, 0.0)
            return (minv, maxv)
    return (None, None)

def _cv_yoe(text: str) -> Optional[float]:
    mn, mx = _parse_yoe_phrase(text)
    if mn is not None:
        return mn
    return None  # We avoid date span heuristics for CV here to stay conservative

# -------------------------------
# Scoring helpers
# -------------------------------

_DEG_ORDER = {"high school":0, "associate":1, "bachelor's":2, "master's":3, "phd":4}
def _degree_level(label: Optional[str]) -> Optional[int]:
    if not label: return None
    l = label.strip().lower()
    if l in {"phd", "ph.d", "ph.d."}:
        l = "phd"
    return _DEG_ORDER.get(l)

def _extract_degree_from_desc(desc: str) -> Optional[str]:
    return _extract_degree_label(desc or "")

def _extract_yoe_str_from_desc(desc: str) -> str:
    y_min, y_max = _parse_yoe_phrase(desc or "")
    if y_min is not None and y_max is not None:
        return f"{y_min}-{y_max} years"
    if y_min is not None:
        return f"{y_min}+ years"
    if y_max is not None:
        return f"up to {y_max} years"
    return ""

def _extract_skills_from_desc(desc: str) -> List[str]:
    return _extract_skills(desc or "")

def _parse_row_yoe_bounds(yoe_str: str) -> Tuple[Optional[float], Optional[float]]:
    if not yoe_str:
        return (None, None)
    return _parse_yoe_phrase(yoe_str)

def _skills_overlap_score(job_skills: List[str], cv_skills: List[str]) -> float:
    if not cv_skills or not job_skills:
        return 0.0
    js = {s.lower() for s in job_skills}
    cs = {s.lower() for s in cv_skills}
    inter = js & cs
    return (len(inter) / max(1, len(js)))

def _degree_score(job_degree: Optional[str], cv_degree: Optional[str]) -> float:
    if not job_degree:
        return 0.5  # unspecified: neutral
    if not cv_degree:
        return 0.0
    jl = _degree_level(job_degree)
    cl = _degree_level(cv_degree)
    if jl is None:
        return 0.5
    if cl is None:
        return 0.0
    return 1.0 if cl >= jl else 0.0

def _yoe_score(job_yoe: str, cv_yoe_years: Optional[float]) -> float:
    if cv_yoe_years is None:
        return 0.0
    jmin, jmax = _parse_row_yoe_bounds(job_yoe)
    if jmin is None and jmax is None:
        return 0.5
    ok = True
    if jmin is not None and cv_yoe_years + 1e-9 < jmin - 1e-9:
        ok = False
    # Over-qualification is not penalized
    return 1.0 if ok else 0.0

def _title_score(job_title: str, desired_keywords: Optional[str]) -> float:
    if not desired_keywords:
        return 0.0
    keys = [k.strip().lower() for k in re.split(r"[,\s/]+", desired_keywords) if k.strip()]
    if not keys:
        return 0.0
    jt = job_title.lower()
    hits = sum(1 for k in keys if k in jt)
    return (hits / max(1, len(keys)))

def _dynamic_weight(parts: List[Tuple[float, float]]) -> float:
    if not parts:
        return 0.0
    wsum = sum(w for _, w in parts)
    if wsum <= 1e-9:
        return 0.0
    return sum(s * w for s, w in parts) / wsum

# -------------------------------
# Core scoring (writes J/K)
# -------------------------------

def score_jobs_against_cv(
    cv_text: str,
    desired_title_keywords: str = "",
    min_score: float = 0.60,
    spreadsheet_name: str = "Job_search_Database",
    output_start_col: str = "J",  # J: score, K: Yes/No
    max_rows: Optional[int] = None,
) -> str:
    """
    Compute a match score for each job row (A..I) against a user's CV text and
    write:
      J: Match Score (0–100)
      K: Match (Yes/No by min_score)

    Scoring uses (when available):
      - Skills overlap (I or derived from F)           ~50%
      - Degree (G or derived from F)                   ~20%
      - YOE (H or derived from F)                      ~20%
      - Title match (A vs desired_title_keywords)      ~10%
    """
    if not cv_text or not cv_text.strip():
        raise ValueError("cv_text must be a non-empty string.")

    sheets = get_sheets_service()
    sid = _find_job_search_spreadsheet_id(spreadsheet_name)
    sheet = _get_first_sheet_name(sid)

    data_range = f"{sheet}!A2:I"
    try:
        rows = sheets.spreadsheets().values().get(
            spreadsheetId=sid,
            range=data_range,
        ).execute().get("values", []) or []
    except HttpError as e:
        raise RuntimeError(f"[CV-MATCH] Failed to read sheet values: {e}")

    if max_rows and max_rows > 0:
        rows = rows[:max_rows]

    cv_degree = _extract_degree_label(cv_text)
    cv_skills = _extract_skills(cv_text)
    cv_yoe    = _cv_yoe(cv_text)

    updates: List[Dict[str, Any]] = []
    good = 0

    def _next_col(col: str) -> str:
        def to_num(c: str) -> int:
            n = 0
            for ch in c:
                n = n * 26 + (ord(ch) - 64)
            return n
        def to_col(n: int) -> str:
            s = ""
            while n > 0:
                n, r = divmod(n - 1, 26)
                s = chr(65 + r) + s
            return s
        return to_col(to_num(col) + 1)

    for i, row in enumerate(rows):
        if len(row) < 9:
            row = row + [""] * (9 - len(row))

        title = row[0] or ""
        desc  = row[5] or ""
        r_deg = row[6] or ""
        r_yoe = row[7] or ""
        r_skl = row[8] or ""

        j_degree = r_deg if r_deg.strip() else _extract_degree_from_desc(desc)
        j_yoe    = r_yoe if r_yoe.strip() else _extract_yoe_str_from_desc(desc)
        j_skills = [s.strip() for s in (r_skl.split(",") if r_skl else _extract_skills_from_desc(desc)) if s.strip()]

        parts: List[Tuple[float, float]] = []
        if j_skills and cv_skills:
            parts.append((_skills_overlap_score(j_skills, cv_skills), 0.50))
        if j_degree:
            parts.append((_degree_score(j_degree, cv_degree), 0.20))
        if j_yoe:
            parts.append((_yoe_score(j_yoe, cv_yoe), 0.20))
        if desired_title_keywords.strip():
            parts.append((_title_score(title, desired_title_keywords), 0.10))

        score = _dynamic_weight(parts)
        label = "Yes" if score >= min_score else "No"
        if label == "Yes":
            good += 1

        rnum = i + 2
        start = output_start_col.upper().strip()  # J
        next_col = _next_col(start)               # K
        updates.append({"range": f"{sheet}!{start}{rnum}", "values": [[f"{round(score*100, 2)}"]]} )
        updates.append({"range": f"{sheet}!{next_col}{rnum}", "values": [[label]]} )

    if updates:
        try:
            get_sheets_service().spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "USER_ENTERED", "data": updates},
            ).execute()
        except HttpError as e:
            raise RuntimeError(f"[CV-MATCH] Failed to write scores: {e}")

    return f"[CV-MATCH] Scored {len(rows)} jobs. Good matches ≥ {int(min_score*100)}%: {good}. Written to J/K."

# -------------------------------
# Convenience tool: fetch CV from Drive by name and score
# -------------------------------

def score_jobs_against_drive_cv(
    cv_file_name: str = "steven_yeo_cv",
    desired_title_keywords: str = "",
    min_score: float = 0.60,
    spreadsheet_name: str = "Job_search_Database",
    output_start_col: str = "J",
    max_rows: Optional[int] = None,
) -> str:
    """
    Finds CV in Google Drive (prefer Google Doc or text) by name, exports text,
    then scores and writes to J/K.
    """
    cv_text = _load_cv_text_from_drive(cv_file_name)
    return score_jobs_against_cv(
        cv_text=cv_text,
        desired_title_keywords=desired_title_keywords,
        min_score=min_score,
        spreadsheet_name=spreadsheet_name,
        output_start_col=output_start_col,
        max_rows=max_rows,
    )

# -------------------------------
# Agent definition
# -------------------------------

cv_match_agent_instruction = """
You compare the candidate's CV (loaded from Google Drive) with each row in 'Job_search_Database' and write:
- J: Match Score (0–100)
- K: Match (Yes/No by threshold)

Inputs:
- cv_file_name (default: 'steven_yeo_cv'): name of a Google Doc or text file in Drive.
- desired_title_keywords (optional): comma/space-separated keywords for title match (e.g., "data scientist ml engineer").

Scoring (dynamic weights; criteria used only if present):
- Skills overlap between CV and job (from I; else derived from Description F). ~50%
- Degree: CV degree meets/exceeds job degree (G; else derived from F). ~20%
- YOE: CV years meet job min (H; else derived from F). ~20%
- Title: A vs desired_title_keywords. ~10%

Only write J and K. Do not alter A–I.
"""

match_agent = Agent(
    model=MODEL,
    name="cv_match_agent",
    description=cv_match_agent_instruction,
    tools=[score_jobs_against_cv, score_jobs_against_drive_cv],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)

__all__ = ["cv_match_agent", "score_jobs_against_cv", "score_jobs_against_drive_cv"]