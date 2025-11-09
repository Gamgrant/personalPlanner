# agent_orchestrator.py (top of file)
from pathlib import Path
import sys

# -----------------------------------------------------------------------------
# Project initialization
#
# - Add the project root (personalPlanner) to sys.path so that sibling modules
#   can be imported without relative paths.
# - Set up OAuth environment variables using the shared `.cred` folder via
#   utils.routing.ensure_google_oauth_env. This ensures that any code reading
#   GOOGLE_OAUTH_CLIENT_FILE or GOOGLE_OAUTH_TOKEN_FILE will see absolute paths
#   pointing into personalPlanner/.cred. When the second argument passed to
#   os.path.join is absolute, the first argument is ignored, so no other code
#   changes are required.
# - Attempt to load a .env file from either the shared `.cred` or `.creds`
#   directory, falling back to the project root. The .env file may define
#   additional secrets or configuration. If none of these files exist, the
#   dotenv import is ignored gracefully.

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

try:
    # Try to set up shared OAuth environment variables. We do this before
    # importing any submodules so that all code sees the correct values.
    from utils.routing import ensure_google_oauth_env
    ensure_google_oauth_env(__file__)
except Exception:
    # utils.routing may not be available; ignore quietly.
    pass

try:
    # Load environment variables from .env if available. We support three possible
    # locations: `.cred/.env`, `.creds/.env`, and `.env` in the project root.
    from dotenv import load_dotenv  # type: ignore
    for _env_path in [ROOT / ".cred" / ".env", ROOT / ".creds" / ".env", ROOT / ".env"]:
        if _env_path.exists():
            load_dotenv(_env_path)
            break
except Exception:
    # If dotenv is not installed or no .env file is found, continue silently.
    pass

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import datetime

from google.genai import types
from google.adk.agents import Agent
from google.adk.tools import AgentTool

# Import the centralized time helper. This provides a consistent way to
# retrieve current date/time and timezone information across all modules.
from utils.time_utils import get_time_context

import os

### for time context ###
# Fetch the current time context once when this module is imported. This
# ensures that the orchestrator's instructions reflect the local time at
# startup and makes it easy to update the message by changing a single
# utility function.
_time_ctx = get_time_context()

current_time_info = (
    f"Current local time context:\n"
    f"- Date: {_time_ctx['date']}\n"
    f"- Time: {_time_ctx['time']}\n"
    f"- Weekday: {_time_ctx['weekday']}\n"
    f"- Timezone: {_time_ctx['timezone']} (UTC{_time_ctx['utc_offset']})\n"
)

ORCH_INSTRUCTIONS = " the current time and timezone is " + current_time_info +  """
You are the top-level coordinator.

### Time context — MUST DO

### Routing (only legit sources; no scraping)
- Calendar requests → google_calendar_agent
- Docs/notes/meeting-doc requests → google_docs_agent
- Email/Gmail requests → google_gmail_agent
- Sheets data/operations → google_sheets_agent
- Drive file/folder search/browse/download/export/sharing → google_drive_agent
- General web/public info → google_search_agent (Google Programmable Search; API key; **no OAuth, no scraping**)
- Official job listings (ATS) → ats_jobs_agent (Greenhouse/Lever public APIs; **no scraping**)
- Professional contact search / outreach / people finder → apollo_agent (Apollo.io official API; **no scraping**)

### Gmail intent examples (route to google_gmail_agent)
- “search my inbox for …”, “find unread from …”, “show thread about …”
- “send an email to …”, “reply to … with …”, “forward …”, “add CC/BCC …”
- “mark as read/unread”, “archive this”, “trash/delete”, “list labels”, “download attachments”

### Calendar intent examples (route to google_calendar_agent)
- “create/update/delete a meeting”, “what’s on my calendar”, “suggest times next Tue”
- “recurring every Friday…”, “invite alice@example.com”

### Docs intent examples (route to google_docs_agent)
- “create a meeting notes doc”, “summarize this into a doc”, “insert bullets/sections”

### Sheets intent examples (route to google_sheets_agent)
- “list my spreadsheets”, “open the sheet called ‘Q4 Pipeline’”
- “read Sheet1!A1:D20 from <ID>”, “write these rows to ‘Tasks’!A2:C”
- “clear ‘Data’!A1:Z”, “add a new tab ‘Summary’”, “create a new spreadsheet named ‘Weekly Plan’”
- “update B2 with today’s date”, “append rows to ‘Log’”

### Web search intent examples (route to google_search_agent)
- “what’s the weather in London?”
- “who won the game last night?”
- “search the web for electric car reviews”
- “how tall is the Eiffel Tower?”
- “latest news on the stock market”
- "find me a recruiter for this {Company}"

### Notes for search:
- Use only the Google Programmable Search API (API key). Do not initiate OAuth or scraping.
- If `session.state.time_context.cutoff_iso_local` exists, prefer recency using `dateRestrict`.
- Keep results concise with titles, URLs, and short snippets.
- if user ask for a recruiter, find the linkedin URL given the company name

### Job Management & Search System (`manager_agent`)
Use this branch for any requests involving jobs, roles, positions, openings, or postings.
Examples:
- “Find data scientist jobs posted in the last week”
- “Add the new listings into my Job_search_Database sheet”
- “Backfill missing job descriptions in my database”
- “Show me recent roles at OpenAI or Anthropic”

The job system includes:
1. **ats_jobs_agent (Greenhouse Fetch)** — Queries official Greenhouse APIs to fetch real job postings with title, company, location, date, and URL.  
2. **job_search_sheets_agent (BigQuery/Sheets Storage)** — Appends structured job data to the 'Job_search_Database' Google Sheet.  
3. **job_description_backfill_agent (UI/Enrichment)** — Visits URLs or uses APIs to fill in missing descriptions or metadata.  
4. **query_parser_agent** — Parses natural-language job search queries into structured filters (title, location, experience, degree).

All these subagents are managed internally by `manager_agent`, which orchestrates them through a pipeline (`job_search_pipeline`).  
You do **not** need to call them individually — just route job-related queries to `manager_agent`.


### State handoff — MUST
- Always pass `session.state` (includes `time_context`) with `transfer_to_agent`.
- Sub-agents must read `session.state.time_context` for parsing and display.

### Behavior
- Prefer explicit `transfer_to_agent` when the target is obvious.
- Keep your own replies brief; let specialists do the work.
- If user intent is ambiguous, ask one concise clarifying question before routing.
- Never reveal API keys, internal env var names, or implementation details.
- Never use scraping or non-official endpoints.
#
"""



############ Edit here ################
# Import factories, not Agent instances
from calendar_service.agent_calendar import calendar_agent
from google_docs_service.agent_google_docs import google_docs_agent
from gmail_service.agent_gmail import gmail_agent
from google_sheets_service.agent_google_sheets import google_sheets_agent
from google_drive_service.agent_google_drive import google_drive_agent
from google_search_service.agent_google_search import google_search_agent
from jobs_service.jobs_agent import root_agent as jobs_root_agent  

# from TESTING_apollo_service.apollo_agent import apollo_agent  # if present

# Hook up search agent as AgentTool 
_search_tool = AgentTool(agent=google_search_agent)

# Use the MODEL environment variable for the orchestrator as well. If MODEL is
# not set, default to 'gemini-2.5-flash'. This allows the orchestrator and
# all sub-agents to share the same model configuration from .env.
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

orchestrator_agent = Agent(
    model=MODEL,
    name="orchestrator",
    description=ORCH_INSTRUCTIONS,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    sub_agents=[calendar_agent, google_docs_agent, gmail_agent, google_sheets_agent, google_drive_agent, jobs_root_agent],
    tools=[_search_tool],  # lets the LLM explicitly hand off; no search tool here
)

root_agent = orchestrator_agent

__all__ = ["root_agent"]

# do we need this or no?
# from google.adk.apps.app import App

# app = App(root_agent=root_agent, name="app")
