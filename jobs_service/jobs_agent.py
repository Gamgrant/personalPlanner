from google.adk.agents import SequentialAgent, Agent

# -----------------------------------------------------------------------------
# Load shared credential environment variables
#
# This module participates in a larger orchestrator but may also be executed
# independently. To ensure that all sub-agents see the correct locations for
# OAuth credentials, we set up the environment variables at import time. The
# ensure_google_oauth_env function searches upwards from this file until it
# finds the project root (containing .cred/.creds or pyproject.toml) and then
# populates GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE with absolute
# paths into that directory. This call is a no-op if utils.routing isn't
# available or if the environment variables are already defined.
try:
    from utils.routing import ensure_google_oauth_env
    ensure_google_oauth_env(__file__)
except Exception:
    pass
from google.genai import types

import os
from jobs_service.sub_agent.database_agent import database_agent
from jobs_service.sub_agent.greenhouse_fetch_agent import greenhouse_fetch_agent
from jobs_service.sub_agent.enrichment_agent import description_agent

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# -------------------------------
# Detailed workflow description
# -------------------------------
JOB_SEARCH_DESCRIPTION = (
    "An end-to-end, LLM-orchestrated job discovery pipeline that turns natural-language requests "
    "into curated, structured job listings. The workflow is composed of these stages:\n\n"
    "1. Job Fetching & Enrichment (ats_jobs_agent / greenhouse_fetch_agent)\n"
    "   - Uses only official public Greenhouse job-board APIs.\n"
    "   - Interprets free-form queries (e.g., role title, timeframe, experience level).\n"
    "   - Returns structured records for each job, including title, company, location, URL, "
    "date posted, and a normalized/enriched description.\n\n"
    "2. Sheet Sync & Lightweight Views (job_search_sheets_agent)\n"
    "   - Syncs selected job fields (title, URL, company, location, description, date posted) "
    "   into the existing 'Job_search_Database' Google Sheet to support quick review and manual curation.\n\n"
    "3. Data enrichment (filter_ui_agent)\n"
    "   - access the website in the website url column of the job_search_database"
    "   - enrich the column description in job_search_database to have job description\n.\n\n"
    "Across all steps, LLM reasoning is used to interpret ambiguous instructions, map them to the right tools, "
    "and maintain a consistent, data-backed job discovery experience."
)

# -------------------------------
# Sequential pipeline definition
# -------------------------------
job_search_pipeline = SequentialAgent(
    name="job_search_pipeline",
    description=JOB_SEARCH_DESCRIPTION,
    sub_agents=[
        greenhouse_fetch_agent,
        database_agent,
        description_agent,
    ],
)

# -------------------------------
# Root-level orchestrator agent
# -------------------------------
root_agent = Agent(
    model=MODEL,
    name="manager_agent",
    description=(
        "Root orchestrator agent for managing job discovery pipelines. "
        "It coordinates the job search pipeline, which uses LLMs to interpret user intent, "
        "fetch job postings from official APIs, store them in sheets, and enrich data with descriptions "
        "to produce curated job listings for users."
    ),
    sub_agents=[job_search_pipeline],
    generate_content_config=types.GenerateContentConfig(temperature=0.3),
)