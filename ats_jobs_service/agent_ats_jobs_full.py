"""
ATS Jobs Agent (Greenhouse Only + ADK-compatible)

This agent queries official public Greenhouse job board APIs.
It supports:
- Listing company-specific jobs.
- Finding jobs by title and years of experience across multiple companies.
- Listing all new jobs posted today (no company input required).
- Filtering by required experience (numeric or "junior/mid/senior").
- A make_time_context tool compatible with ADK schema validation.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests
from google.adk.agents import Agent
from google.genai import types
from tzlocal import get_localzone
from zoneinfo import ZoneInfo

MODEL = "gemini-2.5-flash"

# -------------------------------
# Helpers
# -------------------------------

def _parse_iso(ts: str) -> Optional[datetime]:
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
    """Extract numeric experience from user query or text."""
    if not text:
        return None
    # Try for numeric years
    match = re.search(r"(\d+)\s*(?:\+?\s*)?(?:years?|yrs?)\s*(?:of\s*)?(?:experience)?", text, re.I)
    if match:
        return int(match.group(1))

    # Map qualitative levels
    levels = {
        "junior": 1,
        "entry": 1,
        "mid": 4,
        "intermediate": 4,
        "senior": 6,
        "lead": 8,
    }
    for word, yrs in levels.items():
        if word in text.lower():
            return yrs
    return None


def _title_matches(job_title: str, target_title: str) -> bool:
    return target_title.lower() in job_title.lower()


def find_experience_in_description(description: str) -> Optional[int]:
    """
    Extracts a representative years-of-experience requirement (integer)
    from a job description.
    Examples:
        '2-5 years of experience' -> 5
        '3+ years experience' -> 3
        'minimum 4 years' -> 4
    Returns None if no match found.
    """
    if not description:
        return None
    text = description.lower()

    match = re.search(
        r'(?:(?:at\s+least|minimum|over|around)\s*)?(\d+)\s*(?:[-to–]\s*(\d+))?\s*(?:\+?\s*)?(?:years?|yrs?)\s*(?:of\s+experience)?',
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
    try:
        tz = ZoneInfo(preferred_tz) if preferred_tz else get_localzone()
    except Exception:
        tz = ZoneInfo("America/New_York")

    now = datetime.now(tz)
    return {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "timezone": str(tz),
        "utc_offset": now.strftime("%z"),
        "cutoff_iso_local": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        "summary": now.strftime("%A, %b %d %Y, %I:%M %p %Z"),
    }

# -------------------------------
# Greenhouse
# -------------------------------

GH_LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
GH_DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}?content=true"

DEFAULT_COMPANIES = ["openai", "stripe", "databricks", "notion", "anthropic", "asana"]

def greenhouse_list_jobs(company: str, session: Optional[dict] = None) -> List[Dict[str, Any]]:
    url = GH_LIST_URL.format(company=company)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    jobs = data.get("jobs", [])
    cutoff = _cutoff_from_session(session)
    results = []
    for j in jobs:
        updated_at = _parse_iso(j.get("updated_at") or j.get("created_at") or "")
        if not _is_recent(updated_at, cutoff):
            continue
        results.append({
            "company": company,
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "date_posted": updated_at.isoformat() if updated_at else "",
            "id": str(j.get("id")),
            "url": j.get("absolute_url", ""),
            "description": _normalize_text(j.get("content") or ""),
        })
    return results


def greenhouse_get_job(company: str, job_id: int) -> Dict[str, Any]:
    url = GH_DETAIL_URL.format(company=company, job_id=job_id)
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
        "description": _normalize_text(j.get("content") or j.get("description")),
    }

# -------------------------------
# Multi-company Search / Experience Filter
# -------------------------------

def search_jobs(query: str, session: Optional[dict] = None, max_results: int = 50) -> str:
    """
    Searches Greenhouse jobs across default companies by title and optional experience.
    Enforces experience filter: keeps only jobs requiring ≤ (years_exp + 2).
    """
    years_exp = _parse_experience(query)
    title_match = re.findall(r"(?:job|role|position)\s*(?:for|as)?\s*([\w\s\-]+)", query, re.I)
    target_title = title_match[0] if title_match else query.strip()

    combined_jobs: List[Dict[str, Any]] = []
    for comp in DEFAULT_COMPANIES:
        try:
            gh_jobs = greenhouse_list_jobs(comp, session=session)
            for job in gh_jobs:
                if not _title_matches(job["title"], target_title):
                    continue

                job_exp = find_experience_in_description(job["description"])
                if years_exp and job_exp and job_exp > years_exp + 2:
                    continue  # skip overqualified postings

                combined_jobs.append(job)
                if len(combined_jobs) >= max_results:
                    break
        except Exception:
            continue

    if not combined_jobs:
        return f"No suitable '{target_title}' jobs found for ~{years_exp or 'any'} years of experience."

    lines = [f"Found {len(combined_jobs)} matching '{target_title}' jobs (~{years_exp or 'any'} yrs exp):\n"]
    for idx, j in enumerate(combined_jobs[:max_results], 1):
        exp_info = find_experience_in_description(j["description"])
        exp_text = f" | Req: {exp_info} yrs" if exp_info else ""
        lines.append(
            f"{idx}. {j['title']} — {j['company']} — {j['location']}{exp_text}\n"
            f"   Date: {j['date_posted']}\n   ID: {j['id']}\n   Link: {j['url']}\n"
            f"   Description: {j['description']}\n"
        )
    return "\n".join(lines)


def list_recent_jobs(session: Optional[dict] = None, max_results: int = 50) -> str:
    """
    Lists all jobs posted or updated today across default Greenhouse companies.
    Uses session.state.time_context.cutoff_iso_local to define 'today'.
    """
    cutoff = _cutoff_from_session(session)
    if cutoff is None:
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    combined_jobs: List[Dict[str, Any]] = []
    for comp in DEFAULT_COMPANIES:
        try:
            gh_jobs = greenhouse_list_jobs(comp, session=session)
            for job in gh_jobs:
                ts = _parse_iso(job.get("date_posted", "")) or datetime.now(timezone.utc)
                if _is_recent(ts, cutoff):
                    combined_jobs.append(job)
                if len(combined_jobs) >= max_results:
                    break
        except Exception:
            continue

    if not combined_jobs:
        return "No new jobs posted today across monitored Greenhouse companies."

    lines = [f"Found {len(combined_jobs)} new jobs today ({cutoff.date()}):\n"]
    for idx, j in enumerate(combined_jobs[:max_results], 1):
        exp_info = find_experience_in_description(j["description"])
        exp_text = f" | Req: {exp_info} yrs" if exp_info else ""
        lines.append(
            f"{idx}. {j['title']} — {j['company']} — {j['location']}{exp_text}\n"
            f"   Date: {j['date_posted']}\n   ID: {j['id']}\n   Link: {j['url']}\n"
            f"   Description: {j['description']}\n"
        )
    return "\n".join(lines)

# -------------------------------
# Agent Factory
# -------------------------------

ats_agent_instruction_text = """
You are a helpful ATS jobs assistant that uses official public Greenhouse APIs (no scraping).
Capabilities:
- List company-specific jobs.
- Search for roles by title and years of experience.
- List all new jobs posted today (no input required).
- Always include: title, company, location, date posted, description, ID, and link.
- Enforce that returned jobs roughly match the user's requested experience (±2 years).
- Respect orchestrator session.state.time_context.cutoff_iso_local.
"""

def build_agent():
    return Agent(
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
            list_recent_jobs,
            find_experience_in_description,
            make_time_context,
        ],
    )