"""
ATS Jobs Agent (Greenhouse Only + ADK-compatible)

This agent queries official public Greenhouse job board APIs.

Capabilities:
- Find jobs by title (e.g., "Data Scientist") using public Greenhouse boards.
- Search by title and experience across:
    * companies explicitly passed to the tool,
    * or companies configured in session.state,
    * or a small curated DEFAULT_COMPANIES fallback.
- List jobs with a given title in the last N days (1–10).
- List jobs posted/updated "today".
- List jobs posted/updated in the last N months (1–3).
- Filter by required experience (numeric or 'junior/mid/senior').
- Provide an ADK-compatible make_time_context helper.

Notes:
- The user can say: "Data scientist job posted today" or
  "Find data scientist jobs in last 5 days" with no company specified.
- The agent will:
    1) Prefer companies passed in the tool call;
    2) Else use companies from session.state (if provided);
    3) Else search across DEFAULT_COMPANIES.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import requests
from google.adk.agents import Agent
from google.genai import types
from tzlocal import get_localzone
from zoneinfo import ZoneInfo

# Load the model name from environment variables if available. Defaults to
# 'gemini-2.5-flash' when unspecified. Centralizing this variable allows
# configuration via .env without editing code in multiple places.
import os
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# Import centralized time utilities to provide consistent time context
# across all modules. We wrap make_time_context below using get_time_context.
from utils.time_utils import get_time_context

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
    text = re.sub(r"<[^>]+>", " ", html_or_text)
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
    Return an ADK-compatible time context for job queries.

    This wrapper uses utils.time_utils.get_time_context to obtain the
    base date/time fields, then adds a cutoff timestamp (start of the
    current day) and a human-readable summary. By delegating to
    get_time_context, we ensure consistent timezone handling across
    modules.

    Args:
        preferred_tz: Optional IANA timezone string. If provided, the
            context is based on that timezone. Otherwise, the local
            timezone is used.

    Returns:
        A dictionary containing ISO datetime, date, time, weekday,
        timezone, UTC offset, cutoff time (start of day), and summary.
    """
    ctx = get_time_context(preferred_tz)
    # Compute start of the day in the same timezone as the ISO datetime
    try:
        dt = datetime.fromisoformat(ctx["datetime"])
        cutoff = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_iso_local = cutoff.isoformat()
    except Exception:
        # Fallback: use current datetime
        dt = datetime.now(get_localzone())
        cutoff_iso_local = dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    # Human-readable summary
    try:
        summary = dt.strftime("%A, %b %d %Y, %I:%M %p %Z")
    except Exception:
        summary = f"{ctx.get('weekday', '')}, {ctx.get('date', '')} {ctx.get('time', '')} {ctx.get('timezone', '')}"
    # Extend context
    ctx["cutoff_iso_local"] = cutoff_iso_local
    ctx["summary"] = summary
    return ctx

# -------------------------------
# Greenhouse API
# -------------------------------

GH_LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
GH_DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}?content=true"

# Fallback set used ONLY when caller/session does not provide companies
DEFAULT_COMPANIES = [
    "openai",
    "stripe",
    "databricks",
    "notion",
    "anthropic",
    "asana",
]

def _get_companies(session: Optional[dict], companies: Optional[List[str]]) -> List[str]:
    """
    Resolve companies to query:
      1) Explicit `companies` argument (if provided)
      2) session.state.companies / greenhouse_companies / job_companies
      3) DEFAULT_COMPANIES fallback
    """
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

    # Final fallback: internal default set (so natural-language queries just work)
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
# Search by query (title + experience)
# -------------------------------

def search_jobs(
    query: str,
    companies: Optional[List[str]] = None,
    session: Optional[dict] = None,
    max_results: int | None = None,
) -> str:
    companies = _get_companies(session, companies)
    years_exp = _parse_experience(query)

    title_match = re.findall(
        r"(?:job|role|position)\s*(?:for|as)?\s*([\w\s\-]+)",
        query,
        re.IGNORECASE,
    )
    target_title = title_match[0].strip() if title_match else query.strip()

    combined_jobs: List[Dict[str, Any]] = []

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

            combined_jobs.append(job)
            # If a max_results cap is provided and we've reached it, stop
            if max_results and max_results > 0 and len(combined_jobs) >= max_results:
                break
        if max_results and max_results > 0 and len(combined_jobs) >= max_results:
            break

    if not combined_jobs:
        return (
            f"No suitable '{target_title}' roles found (~{years_exp or 'any'} yrs exp) "
            f"across: {', '.join(companies)}."
        )

    lines = [
        f"Found {len(combined_jobs)} matching '{target_title}' jobs "
        f"(~{years_exp or 'any'} yrs exp) across {', '.join(companies)}:\n"
    ]
    # Determine how many jobs to display based on max_results (if provided)
    display_count = len(combined_jobs) if not (max_results and max_results > 0) else min(len(combined_jobs), max_results)
    for idx, j in enumerate(combined_jobs[:display_count], 1):
        exp_info = find_experience_in_description(j["description"])
        exp_text = f" | Req: {exp_info} yrs" if exp_info else ""
        lines.append(
            f"{idx}. {j['title']} — {j['company']} — {j['location']}{exp_text}\n"
            f"   Date: {j['date_posted']}\n"
            f"   ID: {j['id']}\n"
            f"   Link: {j['url']}\n"
        )
    return "\n".join(lines)

# -------------------------------
# Title + last N days
# -------------------------------

def find_jobs_by_title_in_last_days(
    title: str,
    days: int,
    companies: Optional[List[str]] = None,
    session: Optional[dict] = None,
    max_results: int | None = None,
) -> str:
    """
    Core for prompts like:
      "Find Data scientist jobs within the last 5 days"
      "Data scientist job posted today"
    """
    companies = _get_companies(session, companies)

    if days < 1:
        days = 1
    if days > 10:
        days = 10

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    target_title = title.strip()
    combined_jobs: List[Dict[str, Any]] = []

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

            combined_jobs.append(job)
            if max_results and max_results > 0 and len(combined_jobs) >= max_results:
                break
        if max_results and max_results > 0 and len(combined_jobs) >= max_results:
            break

    if not combined_jobs:
        return (
            f"No '{target_title}' jobs found in the last {days} day(s) "
            f"across: {', '.join(companies)}."
        )

    lines = [
        f"Found {len(combined_jobs)} '{target_title}' jobs in the last {days} day(s) "
        f"across {', '.join(companies)}:\n"
    ]
    display_count = len(combined_jobs) if not (max_results and max_results > 0) else min(len(combined_jobs), max_results)
    for idx, j in enumerate(combined_jobs[:display_count], 1):
        lines.append(
            f"{idx}. {j['title']} — {j['company']} — {j['location']}\n"
            f"   Date: {j['date_posted']}\n"
            f"   Link: {j['url']}\n"
        )
    return "\n".join(lines)

# -------------------------------
# Today + last N months
# -------------------------------

def list_recent_jobs(
    companies: Optional[List[str]] = None,
    session: Optional[dict] = None,
    max_results: int | None = None,
) -> str:
    companies = _get_companies(session, companies)

    cutoff = _cutoff_from_session(session)
    if cutoff is None:
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    combined_jobs: List[Dict[str, Any]] = []

    for comp in companies:
        try:
            gh_jobs = greenhouse_list_jobs(comp, session=session)
        except Exception:
            continue

        for job in gh_jobs:
            ts = _parse_iso(job.get("date_posted", ""))
            if ts and _is_recent(ts, cutoff):
                combined_jobs.append(job)
                if max_results and max_results > 0 and len(combined_jobs) >= max_results:
                    break
        if max_results and max_results > 0 and len(combined_jobs) >= max_results:
            break

    if not combined_jobs:
        return f"No new jobs posted today ({cutoff.date()}) across: {', '.join(companies)}."

    lines = [f"Found {len(combined_jobs)} new jobs today ({cutoff.date()}) across {', '.join(companies)}:\n"]
    display_count = len(combined_jobs) if not (max_results and max_results > 0) else min(len(combined_jobs), max_results)
    for idx, j in enumerate(combined_jobs[:display_count], 1):
        exp_info = find_experience_in_description(j["description"])
        exp_text = f" | Req: {exp_info} yrs" if exp_info else ""
        lines.append(
            f"{idx}. {j['title']} — {j['company']} — {j['location']}{exp_text}\n"
            f"   Date: {j['date_posted']}\n"
            f"   Link: {j['url']}\n"
        )
    return "\n".join(lines)


def list_jobs_in_last_months(
    months: int = 1,
    companies: Optional[List[str]] = None,
    session: Optional[dict] = None,
    max_results: int | None = None,
) -> str:
    companies = _get_companies(session, companies)

    if months < 1:
        months = 1
    if months > 3:
        months = 3

    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months)
    combined_jobs: List[Dict[str, Any]] = []

    for comp in companies:
        try:
            gh_jobs = greenhouse_list_jobs(comp, session=session)
        except Exception:
            continue

        for job in gh_jobs:
            ts = _parse_iso(job.get("date_posted", ""))
            if ts and ts >= cutoff:
                combined_jobs.append(job)
                if max_results and max_results > 0 and len(combined_jobs) >= max_results:
                    break
        if max_results and max_results > 0 and len(combined_jobs) >= max_results:
            break

    if not combined_jobs:
        return f"No jobs found in the last {months} month(s) across: {', '.join(companies)}."

    lines = [f"Found {len(combined_jobs)} jobs in the last {months} month(s) across {', '.join(companies)}:\n"]
    display_count = len(combined_jobs) if not (max_results and max_results > 0) else min(len(combined_jobs), max_results)
    for idx, j in enumerate(combined_jobs[:display_count], 1):
        lines.append(
            f"{idx}. {j['title']} — {j['company']} — {j['location']}\n"
            f"   Date: {j['date_posted']}\n"
            f"   Link: {j['url']}\n"
        )
    return "\n".join(lines)

# -------------------------------
# Agent Factory
# -------------------------------

ats_agent_instruction_text = """
You are a helpful ATS jobs assistant that uses only official public Greenhouse APIs.

Behavior:
- For queries like "Data scientist job posted today" or
  "Find data scientist jobs within the last 5 days":
    * Infer the role title from the text.
    * Use find_jobs_by_title_in_last_days with days inferred from "today"/"last N days".
    * If no companies are passed, rely on internal/default or session-configured companies;
      do NOT ask the user for company names.
- For queries mentioning experience, filter out roles requiring more than (requested + 2) years.
- Always return real jobs only, including: title, company, location, date posted, ID, link.
"""

greenhouse_fetch_agent = Agent(
        model=MODEL,
        name="ats_jobs_agent",
        description=(
            "An assistant that queries public Greenhouse job-board APIs for legitimate job listings. "
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
            find_experience_in_description,
            make_time_context,
        ],
    )
__all__ = ["greenhouse_fetch_agent"]
