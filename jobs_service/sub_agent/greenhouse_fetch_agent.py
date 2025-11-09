"""
ATS Jobs Agent (Greenhouse Only + ADK-compatible)

Queries official public Greenhouse job board APIs.

Capabilities:
- Find jobs by title (e.g., "Data Scientist") using public Greenhouse boards.
- Search by title and experience across:
    * companies explicitly passed to the tool,
    * or companies configured in session.state,
    * or DEFAULT_COMPANIES fallback.
- List jobs with a given title in the last N days (1–10).
- List jobs posted/updated "today".
- List jobs posted/updated in the last N months (1–3).
- Filter by required experience (numeric or 'junior/mid/senior').
- Provide an ADK-compatible make_time_context helper.

ALL CORE SEARCH FUNCTIONS RETURN A STRUCTURED LIST:
    List[Dict[str, Any]] with keys:
        - title
        - company
        - location
        - url
        - date_posted
        - id          (when available)
        - description (normalized text, when available)

Use `format_jobs_for_display` ONLY when you need a human-readable string.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import requests
from google.adk.agents import Agent
from google.genai import types
from tzlocal import get_localzone
from zoneinfo import ZoneInfo
import json

from utils.time_utils import get_time_context

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# -------------------------------
# Helpers
# -------------------------------

def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _cutoff_from_session(session: Optional[dict]) -> Optional[datetime]:
    if not isinstance(session, dict):
        return None

    state = session.get("state") or session
    tc = state.get("time_context") if isinstance(state.get("time_context"), dict) else state.get("time_context")
    if not isinstance(tc, dict):
        return None

    iso_ = tc.get("cutoff_iso_local")
    if not iso_:
        return None

    try:
        return datetime.fromisoformat(iso_)
    except Exception:
        return None


def _is_recent(ts: Optional[datetime], cutoff: Optional[datetime]) -> bool:
    if cutoff is None or ts is None:
        return True
    return ts.astimezone(timezone.utc) >= cutoff.astimezone(timezone.utc)


def _normalize_text(html_or_text: Optional[str]) -> str:
    if not html_or_text:
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html_or_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_experience(text: str) -> Optional[int]:
    if not text:
        return None

    m = re.search(r"(\d+)\s*(?:\+?\s*)?(?:years?|yrs?)", text, re.I)
    if m:
        return int(m.group(1))

    levels = {
        "junior": 1,
        "entry": 1,
        "mid": 4,
        "intermediate": 4,
        "senior": 6,
        "lead": 8,
    }
    lowered = text.lower()
    for word, yrs in levels.items():
        if word in lowered:
            return yrs
    return None


def _title_matches(job_title: str, target_title: str) -> bool:
    return target_title.lower() in job_title.lower()


def find_experience_in_description(description: str) -> Optional[int]:
    if not description:
        return None

    text = description.lower()
    match = re.search(
        r'(?:(?:at\s+least|minimum|over|around)\s*)?'
        r'(\d+)\s*(?:[-to–]\s*(\d+))?\s*(?:\+?\s*)?(?:years?|yrs?)',
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    return max(start, end)

# -------------------------------
# Time Context (ADK compatible)
# -------------------------------

def make_time_context(preferred_tz: Optional[str] = None) -> dict:
    """
    Return an ADK-compatible time context, using shared utils.
    """
    ctx = get_time_context(preferred_tz)

    try:
        dt = datetime.fromisoformat(ctx["datetime"])
    except Exception:
        dt = datetime.now(get_localzone())

    cutoff = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    ctx["cutoff_iso_local"] = cutoff.isoformat()
    try:
        ctx["summary"] = dt.strftime("%A, %b %d %Y, %I:%M %p %Z")
    except Exception:
        ctx["summary"] = f"{ctx.get('weekday','')}, {ctx.get('date','')} {ctx.get('time','')} {ctx.get('timezone','')}"
    return ctx

# -------------------------------
# Greenhouse API
# -------------------------------

GH_LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
GH_DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}?content=true"

DEFAULT_COMPANIES = [
    "openai",
    "stripe",
    "databricks",
    "notion",
    "anthropic",
    "asana",
]


def _get_companies(session: Optional[dict], companies: Optional[List[str]]) -> List[str]:
    if companies:
        return [c for c in companies if c]

    if isinstance(session, dict):
        state = session.get("state") or session
        from_state = (
            state.get("companies")
            or state.get("greenhouse_companies")
            or state.get("job_companies")
        )
        if isinstance(from_state, list):
            resolved = [str(c) for c in from_state if c]
            if resolved:
                return resolved
        if isinstance(from_state, str):
            resolved = [c.strip() for c in from_state.split(",") if c.strip()]
            if resolved:
                return resolved

    return DEFAULT_COMPANIES


def greenhouse_list_jobs(company: str, session: Optional[dict] = None) -> List[Dict[str, Any]]:
    if not company:
        raise ValueError("Please provide a Greenhouse board token (e.g., 'openai').")

    url = GH_LIST_URL.format(company=company.lower())
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    jobs = data.get("jobs", [])
    cutoff = _cutoff_from_session(session)

    results: List[Dict[str, Any]] = []
    for j in jobs:
        updated_at = _parse_iso(j.get("updated_at") or j.get("created_at") or "")
        if not _is_recent(updated_at, cutoff):
            continue
        results.append(
            {
                "company": company,
                "title": j.get("title", ""),
                "location": (j.get("location") or {}).get("name", ""),
                "date_posted": updated_at.isoformat() if updated_at else "",
                "id": str(j.get("id")),
                "url": j.get("absolute_url", ""),
                "description": _normalize_text(j.get("content") or ""),
            }
        )
    return results


def greenhouse_get_job(company: str, job_id: int) -> Dict[str, Any]:
    if not company:
        raise ValueError("Please provide a company name.")
    if not job_id:
        raise ValueError("Please provide a valid job_id.")

    url = GH_DETAIL_URL.format(company=company.lower(), job_id=job_id)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    j = r.json() or {}

    updated_at = j.get("updated_at") or j.get("created_at")
    return {
        "company": company,
        "title": j.get("title", ""),
        "location": (j.get("location") or {}).get("name", ""),
        "date_posted": updated_at,
        "id": str(j.get("id")),
        "url": j.get("absolute_url", ""),
        "description": _normalize_text(j.get("content") or j.get("description") or ""),
    }

# -------------------------------
# Core search functions (structured outputs)
# -------------------------------

def search_jobs(
    query: str,
    companies: Optional[List[str]] = None,
    session: Optional[dict] = None,
    max_results: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Search across companies by inferred title + experience.
    Returns a list of job dicts (no pretty string).
    """
    companies = _get_companies(session, companies)
    years_exp = _parse_experience(query)

    title_match = re.findall(
        r"(?:job|role|position)\s*(?:for|as)?\s*([\w\s\-]+)",
        query,
        re.IGNORECASE,
    )
    target_title = title_match[0].strip() if title_match else query.strip()

    combined: List[Dict[str, Any]] = []

    for comp in companies:
        try:
            gh_jobs = greenhouse_list_jobs(comp, session=session)
        except Exception:
            continue

        for job in gh_jobs:
            if not _title_matches(job["title"], target_title):
                continue

            job_exp = find_experience_in_description(job["description"])
            if years_exp and job_exp and job_exp > years_exp + 2:
                continue

            combined.append(job)
            if max_results and len(combined) >= max_results:
                break
        if max_results and len(combined) >= max_results:
            break

    return combined


def find_jobs_by_title_in_last_days(
    title: str,
    days: int,
    companies: Optional[List[str]] = None,
    session: Optional[dict] = None,
    max_results: Optional[int] = None,
) -> List[Dict[str, Any]]:
    companies = _get_companies(session, companies)

    days = max(1, min(days, 10))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    target_title = title.strip()

    combined: List[Dict[str, Any]] = []

    for comp in companies:
        try:
            gh_jobs = greenhouse_list_jobs(comp, session=session)
        except Exception:
            continue

        for job in gh_jobs:
            ts = _parse_iso(job.get("date_posted", ""))
            if not ts or ts < cutoff:
                continue
            if not _title_matches(job["title"], target_title):
                continue

            combined.append(job)
            if max_results and len(combined) >= max_results:
                break
        if max_results and len(combined) >= max_results:
            break

    return combined


def list_recent_jobs(
    companies: Optional[List[str]] = None,
    session: Optional[dict] = None,
    max_results: Optional[int] = None,
) -> List[Dict[str, Any]]:
    companies = _get_companies(session, companies)

    cutoff = _cutoff_from_session(session)
    if cutoff is None:
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    combined: List[Dict[str, Any]] = []

    for comp in companies:
        try:
            gh_jobs = greenhouse_list_jobs(comp, session=session)
        except Exception:
            continue

        for job in gh_jobs:
            ts = _parse_iso(job.get("date_posted", ""))
            if ts and _is_recent(ts, cutoff):
                combined.append(job)
                if max_results and len(combined) >= max_results:
                    break
        if max_results and len(combined) >= max_results:
            break

    return combined


def list_jobs_in_last_months(
    months: int = 1,
    companies: Optional[List[str]] = None,
    session: Optional[dict] = None,
    max_results: Optional[int] = None,
) -> List[Dict[str, Any]]:
    companies = _get_companies(session, companies)

    months = max(1, min(months, 3))
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months)
    combined: List[Dict[str, Any]] = []

    for comp in companies:
        try:
            gh_jobs = greenhouse_list_jobs(comp, session=session)
        except Exception:
            continue

        for job in gh_jobs:
            ts = _parse_iso(job.get("date_posted", ""))
            if ts and ts >= cutoff:
                combined.append(job)
                if max_results and len(combined) >= max_results:
                    break
        if max_results and len(combined) >= max_results:
            break

    return combined

# -------------------------------
# Optional: pretty-printer for UI
# -------------------------------

def _coerce_to_jobs_list(jobs: Union[str, Dict[str, Any], List[Any]]) -> List[Dict[str, Any]]:
    """
    Normalize many possible inputs to List[Dict].
    - If JSON string, parse it.
    - If dict envelope, unwrap common keys like 'jobs' or 'results'.
    - If list contains strings/JSON strings, try to parse; otherwise wrap with {'title': <str>}.
    """
    # JSON string or preformatted text
    if isinstance(jobs, str):
        try:
            jobs = json.loads(jobs)
        except Exception:
            # Treat as a single preformatted line
            return [{"title": jobs}]

    # Single dict (possibly an envelope)
    if isinstance(jobs, dict):
        if isinstance(jobs.get("jobs"), list):
            jobs = jobs["jobs"]
        elif isinstance(jobs.get("results"), list):
            jobs = jobs["results"]
        else:
            jobs = [jobs]

    # Ensure list
    if not isinstance(jobs, list):
        return [{"title": str(jobs)}]

    normalized: List[Dict[str, Any]] = []
    for item in jobs:
        if isinstance(item, dict):
            normalized.append(item)
            continue
        if isinstance(item, str):
            # Try JSON per item
            try:
                obj = json.loads(item)
                if isinstance(obj, dict):
                    normalized.append(obj)
                    continue
            except Exception:
                pass
            normalized.append({"title": item})
            continue
        # Fallback
        normalized.append({"title": str(item)})

    return normalized


from typing import Any, Dict, List, Optional
import json

def format_jobs_for_display(jobs: List[Dict[str, Any]], header: Optional[str] = None) -> str:
    """
    Convert a structured jobs list to a human-readable string.

    IMPORTANT: The type hint stays strict (List[Dict[str, Any]]) so ADK
    does not emit an `anyOf` in the tool schema. We still coerce at runtime
    for safety if something passes a string/dict by mistake.
    """
    # ---- Runtime coercion (keeps schema simple) ----
    if not isinstance(jobs, list):
        # try JSON string
        if isinstance(jobs, str):
            try:
                parsed = json.loads(jobs)
            except Exception:
                parsed = [{"title": jobs}]
        else:
            parsed = jobs

        if isinstance(parsed, dict):
            if isinstance(parsed.get("jobs"), list):
                jobs = parsed["jobs"]
            elif isinstance(parsed.get("results"), list):
                jobs = parsed["results"]
            else:
                jobs = [parsed]
        elif isinstance(parsed, list):
            jobs = parsed
        else:
            jobs = [{"title": str(parsed)}]

    # Ensure list of dicts
    norm: List[Dict[str, Any]] = []
    for item in jobs:
        if isinstance(item, dict):
            norm.append(item)
        else:
            # best-effort wrap
            norm.append({"title": str(item)})
    jobs = norm

    if not jobs:
        return "No jobs found."

    lines: List[str] = []
    if header:
        lines.append(header.strip() + "\n")

    for idx, j in enumerate(jobs, 1):
        title = j.get("title") or j.get("name") or j.get("job_title") or j.get("position") or ""

        company = ""
        c = j.get("company")
        if isinstance(c, dict):
            company = c.get("name") or c.get("company_name") or ""
        elif isinstance(c, str):
            company = c
        company = company or j.get("company_name") or j.get("organization") or ""

        loc = j.get("location") or j.get("location_name") or ""
        if isinstance(loc, dict):
            loc = loc.get("name") or loc.get("city") or loc.get("region") or ""
        elif isinstance(loc, list):
            loc = ", ".join([x.get("name", "") if isinstance(x, dict) else str(x) for x in loc])

        date_posted = j.get("date_posted", "") or j.get("updated_at", "") or j.get("created_at", "")
        url = j.get("url", "") or j.get("absolute_url", "")

        main = " — ".join([x for x in [title, company, loc] if x])
        lines.append(f"{idx}. {main}".rstrip())
        if date_posted:
            lines.append(f"   Date: {date_posted}")
        if url:
            lines.append(f"   Link: {url}")

    return "\n".join(lines) if lines else "No jobs found."

# -------------------------------
# Agent
# -------------------------------

ats_agent_instruction_text = """
You are a helpful ATS jobs assistant that uses only official public Greenhouse APIs.

Rules:
- Use search_jobs / find_jobs_by_title_in_last_days / list_recent_jobs /
  list_jobs_in_last_months to return structured job lists.
- These functions MUST be treated as the source of truth and their outputs
  passed directly to downstream tools (e.g., Sheets, BigQuery, enrichment).
- When a human-readable answer is needed, call format_jobs_for_display(jobs=...).
- Do NOT write parsing code inside tool calls.
"""

greenhouse_fetch_agent = Agent(
    model=MODEL,
    name="ats_jobs_agent",
    description=(
        "Fetches structured job listings from public Greenhouse APIs for use in a multi-step pipeline. "
        + ats_agent_instruction_text
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    tools=[
        greenhouse_list_jobs,
        greenhouse_get_job,
        search_jobs,
        find_jobs_by_title_in_last_days,
        list_recent_jobs,
        list_jobs_in_last_months,
        format_jobs_for_display,
        find_experience_in_description,
        make_time_context,
    ],
    output_key="jobs_result",  # downstream agents read this
)

__all__ = ["greenhouse_fetch_agent"]