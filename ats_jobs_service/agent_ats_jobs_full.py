"""
ATS Jobs Agent (Legit sources only)

This agent queries **official public endpoints** for applicant-tracking systems (ATS) that expose
careers data without scraping or ToS gray areas. It mirrors the structure of your calendar agent:
- Env-free (no OAuth or secrets needed for public boards)
- JSON‑friendly, sync tools
- `build_agent()` returns an Agent with tools registered

Supported providers (public endpoints):
1) Greenhouse Boards API (public):
   - List:   https://boards-api.greenhouse.io/v1/boards/{company}/jobs
   - Detail: https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{id}?content=true

2) Lever Postings API (public):
   - List:   https://api.lever.co/v0/postings/{company}?mode=json
   - Detail: https://api.lever.co/v0/postings/{company}/{post_id}?mode=json

These endpoints are explicitly documented as public job-board APIs and are widely used.
We **do not** log or store anything sensitive.

Optional behavior:
- If session.state.time_context.cutoff_iso_local is present, we filter out postings older than that cutoff when possible.

Notes:
- Company slug (board token) varies; e.g., "openai", "databricks", "stripe". If unknown, use the
  Google Search agent to discover it (e.g., "site:boards.greenhouse.io {Company} careers").
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

from google.adk.agents import Agent
from google.genai import types

MODEL = "gemini-2.5-flash"

# -------------------------------
# Helpers
# -------------------------------

def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        # Greenhouse sometimes returns '2025-01-02T12:34:56Z'
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
    # Compare in UTC to be safe
    cu = cutoff.astimezone(timezone.utc)
    tu = ts.astimezone(timezone.utc)
    return tu >= cu


def _normalize_text(html_or_text: Optional[str]) -> str:
    if not html_or_text:
        return ""
    # Very light cleanup: strip tags crudely
    text = re.sub(r"<[^>]+>", " ", html_or_text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# -------------------------------
# Greenhouse
# -------------------------------

GH_LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
GH_DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}?content=true"


def greenhouse_list_jobs(company: str, session: Optional[dict] = None) -> str:
    """Return a concise, formatted list of current Greenhouse jobs for a company."""
    url = GH_LIST_URL.format(company=company)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    jobs = data.get("jobs", [])
    cutoff = _cutoff_from_session(session)

    lines: List[str] = [f"Greenhouse jobs for '{company}': {len(jobs)} found\n"]
    idx = 1
    for j in jobs:
        # Greenhouse: keys: id, title, updated_at, location{name}, absolute_url, departments, offices
        updated_at = _parse_iso(j.get("updated_at") or j.get("created_at") or "")
        if not _is_recent(updated_at, cutoff):
            continue
        title = j.get("title", "(untitled)")
        loc = (j.get("location") or {}).get("name", "")
        job_id = j.get("id")
        abs_url = j.get("absolute_url", "")
        lines.append(f"{idx}. {title} — {loc}\n   ID: {job_id}  URL: {abs_url}")
        idx += 1
    if idx == 1:
        lines.append("No postings at/after cutoff.")
    return "\n".join(lines)


def greenhouse_get_job(company: str, job_id: int) -> str:
    """Fetch a single job with full description from Greenhouse."""
    url = GH_DETAIL_URL.format(company=company, job_id=job_id)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    j = r.json() or {}
    title = j.get("title", "(untitled)")
    loc = (j.get("location") or {}).get("name", "")
    abs_url = j.get("absolute_url", "")
    desc = _normalize_text(j.get("content") or j.get("description"))
    updated_at = j.get("updated_at") or j.get("created_at")

    return (
        f"Greenhouse Job Detail\nTitle: {title}\nLocation: {loc}\nUpdated: {updated_at}\nURL: {abs_url}\n\nDescription:\n{desc[:4000]}"
    )


# -------------------------------
# Lever
# -------------------------------

LEVER_LIST_URL = "https://api.lever.co/v0/postings/{company}?mode=json"
LEVER_DETAIL_URL = "https://api.lever.co/v0/postings/{company}/{post_id}?mode=json"


def lever_list_jobs(company: str, session: Optional[dict] = None) -> str:
    """Return a concise, formatted list of current Lever jobs for a company."""
    url = LEVER_LIST_URL.format(company=company)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    posts = r.json() or []
    cutoff = _cutoff_from_session(session)

    lines: List[str] = [f"Lever jobs for '{company}': {len(posts)} found\n"]
    idx = 1
    for p in posts:
        # Lever: keys include id, text (title), createdAt (ms), updatedAt (ms), categories{location, team}, hostedUrl
        updated_ms = p.get("updatedAt") or p.get("createdAt")
        ts = datetime.fromtimestamp(updated_ms / 1000.0, tz=timezone.utc) if updated_ms else None
        if not _is_recent(ts, cutoff):
            continue
        title = p.get("text", "(untitled)")
        loc = (p.get("categories") or {}).get("location", "")
        post_id = p.get("id")
        abs_url = p.get("hostedUrl", "")
        lines.append(f"{idx}. {title} — {loc}\n   ID: {post_id}  URL: {abs_url}")
        idx += 1
    if idx == 1:
        lines.append("No postings at/after cutoff.")
    return "\n".join(lines)


def lever_get_job(company: str, post_id: str) -> str:
    """Fetch a single Lever posting with full description."""
    url = LEVER_DETAIL_URL.format(company=company, post_id=post_id)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    p = r.json() or {}
    title = p.get("text", "(untitled)")
    loc = (p.get("categories") or {}).get("location", "")
    abs_url = p.get("hostedUrl", "")
    desc = _normalize_text(p.get("descriptionPlain") or p.get("description") or "")
    updated_ms = p.get("updatedAt") or p.get("createdAt")
    updated_iso = (
        datetime.fromtimestamp(updated_ms / 1000.0, tz=timezone.utc).isoformat() if updated_ms else ""
    )

    return (
        f"Lever Job Detail\nTitle: {title}\nLocation: {loc}\nUpdated: {updated_iso}\nURL: {abs_url}\n\nDescription:\n{desc[:4000]}"
    )


# -------------------------------
# Convenience / Multi-provider
# -------------------------------

def list_company_jobs(company: str, providers: Optional[List[str]] = None, session: Optional[dict] = None) -> str:
    """Try multiple providers for a company slug and combine results.

    providers: subset of {"greenhouse", "lever"}. If None, tries both.
    """
    prov = [p.lower() for p in (providers or ["greenhouse", "lever"])]
    chunks: List[str] = []
    if "greenhouse" in prov:
        try:
            chunks.append(greenhouse_list_jobs(company, session=session))
        except Exception as e:
            chunks.append(f"Greenhouse error: {e}")
    if "lever" in prov:
        try:
            chunks.append(lever_list_jobs(company, session=session))
        except Exception as e:
            chunks.append(f"Lever error: {e}")
    return "\n\n".join(chunks)


def get_job_details(provider: str, company: str, job_id: str) -> str:
    """Dispatch to provider-specific detail getter.

    provider: "greenhouse" expects integer job_id; "lever" expects posting id string.
    """
    p = provider.lower()
    if p == "greenhouse":
        try:
            jid = int(job_id)
        except ValueError:
            raise ValueError("Greenhouse job_id must be an integer.")
        return greenhouse_get_job(company, jid)
    if p == "lever":
        return lever_get_job(company, job_id)
    raise ValueError("Unsupported provider. Use 'greenhouse' or 'lever'.")


# -------------------------------
# Agent factory
# -------------------------------

ats_agent_instruction_text = """
You are a helpful ATS jobs assistant that ONLY uses official public job-board APIs (no scraping).

Capabilities:
- List a company's open roles via Greenhouse or Lever when given the company slug.
- Fetch details for a specific job by ID.
- Respect orchestrator session.state.time_context.cutoff_iso_local by filtering older postings.

Behavior:
- Keep responses concise and readable: title, location, URL, and a short description preview in details.
- If the company slug is unknown or returns 404, suggest using the web search agent to discover the correct slug.
- Never imply LinkedIn scraping or private APIs.
"""

def build_agent():
    return Agent(
        model=MODEL,
        name="ats_jobs_agent",
        description=(
            "An assistant that queries public Greenhouse/Lever job-board APIs for legit job listings. "
            + ats_agent_instruction_text
        ),
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[
            greenhouse_list_jobs,
            greenhouse_get_job,
            lever_list_jobs,
            lever_get_job,
            list_company_jobs,
            get_job_details,
        ],
    )
