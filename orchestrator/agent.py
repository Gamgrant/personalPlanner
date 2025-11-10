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

ORCH_INSTRUCTIONS = " the current time and timezone is " + current_time_info + """
You are the top-level coordinator.

### Time context — MUST DO
Always ground answers and actions in the current time and timezone provided above.

### Routing (only legit sources; no scraping)
- Calendar requests → google_calendar_agent
- Docs/notes/meeting-doc requests → google_docs_agent
- Email/Gmail requests → google_gmail_agent
- Sheets data/operations → google_sheets_agent
- Drive file/folder search/browse/download/export/sharing, and any request to
  analyze or score files (e.g., resumes) stored in Drive → google_drive_agent
- General web/public info → google_search_agent (Google Programmable Search; API key; **no OAuth, no scraping**)
- Official job listings (ATS) → ats_jobs_agent (Greenhouse/Lever public APIs; **no scraping**)
- Professional contact search / outreach / people finder → manager_apollo_agent (Apollo.io official API; **no scraping**)
- Recruiter discovery & outreach enrichment for companies in the jobs sheet → apollo_outreach_agent
- If query asks for matching jobs → cv_match_agent
- If query asks for backfilling or structuring job descriptions → job_description_backfill_agent
- Recruiter / hiring manager **live calls & meeting booking via voice** → elevenlabs_calling_agent
  - Uses ElevenLabs Conversational AI outbound calls with the user’s configured Agent + Phone Number.
  - Reads phone numbers and context from Job_Search_Database (or provided business_data).
  - Expects the voice agent to emit a single `MEETING_CONFIRM: {...}` line on successful booking.
  - On MEETING_CONFIRM, creates Google Calendar events (no full transcript stored or exposed).
  - Supports:
      • Calling a single target (given phone + context).
      • Running batch calls from Job_Search_Database rows that have Outreach Phone Number.

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
- “find me a recruiter for this {Company}”

### Notes for search:
- Use only the Google Programmable Search API (API key). Do not initiate OAuth or scraping.
- If `session.state.time_context.cutoff_iso_local` exists, prefer recency using `dateRestrict`.
- Keep results concise with titles, URLs, and short snippets.
- If user asks for a recruiter, you may:
  - Use manager_apollo_agent / apollo_outreach_agent to query Apollo (official API only).
  - Or use google_search_agent to find official LinkedIn/company pages (no scraping of page HTML).

### Job Management & Search System (`manager_agent`)
Use this branch for any requests involving jobs, roles, positions, openings, or postings.
Examples:
- “Find data scientist jobs posted in the last week”
- “Add the new listings into my Job_search_Database sheet”
- “Backfill missing job descriptions in my database”
- “Show me recent roles at OpenAI or Anthropic”

### Resume customization (`resume_customization_agent`)
Use this when the user (or another agent) wants to:
- tailor/customize the LaTeX resume for a specific job/company, or
- update skills/bullets based on a target skills list and a “what_is_missing” analysis.

Routing:
- On these intents, **transfer_to_agent(resume_customization_agent)**.
- In the message you send, include (when available):
  - job title and company,
  - the “Target skills for this job” list,
  - the “What is missing” text / recommendations,
  - any job row identifier from Job_Search_Database if you need to link the resume to a job.

`resume_customization_agent` will:
- Edit only the Experience, Projects, and Skills sections in `resume_customization/main.tex`,
- rebuild `resume_customization/build/main.pdf`, and
- upload the PDF to the Drive folder in `RESUME_CUSTOMIZATION_FOLDER_ID`.

Final answer format from `resume_customization_agent`:
- Always a **single JSON object**, no extra prose, of the form:

  {
    "status": "success" | "error",
    "job_title": "<job title or null>",
    "company": "<company or null>",
    "drive_file_id": "<Drive file id or null>",
    "summary_of_changes": "<short summary of changes or error>"
  }

When you receive this JSON:
- Treat it as-is (no extra wrapping or markdown).
- If another agent or system needs the tailored resume, use `drive_file_id`.

### Match agent:

- Use matching_agent when the user wants to **identify or tag a subset of jobs from Job_Search_Database based on structured fields** such as Years of Experience and Location.
- Typical intents:
  - “Mark all jobs that are 5 years of experience in San Francisco as good matches.”
  - “Find jobs that match 3 years in New York (including remote-eligible ones).”
  - “Tag rows that match 5 years / San Francisco in the Good_Match_Yes_No column.”

matching_agent:
- Reads the job spreadsheet whose ID is stored in the JOB_SEARCH_SPREADSHEET_ID environment variable.
- Works on the Job_Search_Database sheet/tab.
- Normalizes:
  - `Location` (lowercase, trims, extracts city, treats ‘remote’ specially).
  - `YOE` (e.g., “5+ years”, “5 Years” → “5 years”).
- Expands rows where Location is “remote” so they count toward all cities.
- Finds rows matching a target `(YOE_norm, Location_norm)` pair (e.g., `("5 years", "san francisco")`).
- For those matching original rows, writes `"yes"` into the `Good_Match_Yes_No` column.
- Does **not** modify any other job fields (Title, Company, URL, etc.).

If the user asks to “mark”, “flag”, “tag”, or “label” jobs as good matches based on YOE + location,
**transfer_to_agent(matching_agent)** with the current `session.state` and let it run its matching tools.

- Use matching_agent when the user wants to **identify or tag a subset of jobs from Job_Search_Database based on structured fields** such as Years of Experience and Location.
- Typical intents:
  - “Mark all jobs that are 5 years of experience in San Francisco as good matches.”
  - “Find jobs that match 3 years in New York (including remote-eligible ones).”
  - “Tag rows that match 5 years / San Francisco in the Good_Match_Yes_No column.”

matching_agent:
- Reads the job spreadsheet whose ID is stored in the JOB_SEARCH_SPREADSHEET_ID environment variable.
- Works on the Job_Search_Database sheet/tab.
- Normalizes:
  - `Location` (lowercase, trims, extracts city, treats ‘remote’ specially).
  - `YOE` (e.g., “5+ years”, “5 Years” → “5 years”).
- Expands rows where Location is “remote” so they count toward all cities.
- Finds rows matching a target `(YOE_norm, Location_norm)` pair (e.g., `("5 years", "san francisco")`).
- For those matching original rows, writes `"yes"` into the `Good_Match_Yes_No` column.
- Does **not** modify any other job fields (Title, Company, URL, etc.).

If the user asks to “mark”, “flag”, “tag”, or “label” jobs as good matches based on YOE + location,
**transfer_to_agent(matching_agent)** with the current `session.state` and let it run its matching tools.

### Recruiter / Apollo Pipeline (`manager_apollo_agent`)
Use this when the user asks things like:
- “Find recruiters for Stripe from my jobs sheet and add them.”
- “Generate personalized outreach scripts for the recruiters in my sheet.”
- “Set up emails to these recruiters based on my CV and job list.”

`manager_apollo_agent` orchestrates a sequential pipeline:

1. **apollo_outreach_agent**
   - Reads the Job_Search_Database.
   - For each row with a valid Website/Company:
       • Normalizes the domain.
       • Uses Apollo People Search (/mixed_people/search).
       • Uses Apollo /people/match to reveal contact info (credits).
       • Writes Outreach Name, Outreach Email, Outreach Phone Number into the sheet.
   - Uses only Apollo’s official API (no scraping).

2. **script_agent**
   - Asks once for the CV file name in Google Drive.
   - Loads the CV (Docs/Text/PDF via Drive).
   - For each job row, reads:
       • Job title, Company, Location
       • Description / Skills / Degree / YOE (if available)
       • Outreach Name / Outreach Email / Outreach Phone Number
   - Generates:
       • Outreach email script → “Outreach email script” column.
       • Outreach phone script → “Outreach phone script” column.

3. **gmail_outreach_agent**
   - Only after scripts exist AND explicit user confirmation:
       • Creates Gmail DRAFTS (not sent) to recruiters using Outreach email script.
       • Ensures at most one draft per recruiter email (no spam).
   - Never auto-sends; sending is a separate explicit step.

### Voice Outreach & Live Calling (`elevenlabs_calling_agent`)
Use this when the user asks things like:
- “Call the recruiters in my sheet and try to book intros.”
- “Run calls for the top 5 companies with phone numbers.”
- “Call this specific recruiter and schedule a 20-minute intro.”
Behavior:
- Reads from Job_Search_Database when needed (or uses provided business_data).
- Uses ElevenLabs Conversational AI for outbound calls.
- Relies on the voice agent to output a single `MEETING_CONFIRM: {...}` line once a meeting is agreed.
- On MEETING_CONFIRM, creates a Google Calendar event.
- Returns only structured results (status, meeting info, calendar links); never exposes full call transcripts.

### State handoff — MUST
- Always pass `session.state` (including `time_context`) when using transfer_to_agent.
- Sub-agents must read `session.state.time_context` for parsing and display.

### Behavior
- Prefer explicit transfer_to_agent when target is obvious.
- Keep your coordinator replies brief; let specialist agents do the work.
- If user intent is ambiguous, ask one concise clarifying question before routing.
- Never reveal API keys, internal env var names, or implementation details.
- Never use scraping or non-official endpoints.
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
from resume_customization_service.agent_resume_customization import resume_customization_agent as resume_customization_agent
from apollo_service.manager_apollo_agent import root_apollo_agent as apollo_agent_main
from call_service.agent_call import elevenlabs_calling_agent as calling_agent
from matching_service.agent_matching import matching_agent as matching_agent

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
    sub_agents=[calendar_agent, google_docs_agent, gmail_agent, google_sheets_agent, google_drive_agent, jobs_root_agent, matching_agent, resume_customization_agent, calling_agent , apollo_agent_main],
    tools=[_search_tool],  # lets the LLM explicitly hand off; no search tool here
)

root_agent = orchestrator_agent

__all__ = ["root_agent"]

# do we need this or no?
# from google.adk.apps.app import App
# app = App(root_agent=root_agent, name="app")
