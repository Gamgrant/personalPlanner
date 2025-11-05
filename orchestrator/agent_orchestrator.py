# agent_orchestrator.py (top of file)
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    # dotenv is optional; ignore if missing
    pass

from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from zoneinfo import ZoneInfo
from tzlocal import get_localzone
import dateparser
import os

from google.genai import types
from google.adk.agents import Agent
from google.adk.tools import AgentTool


ORCH_INSTRUCTIONS = """
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
Notes for search:
- Use only the Google Programmable Search API (API key). Do not initiate OAuth or scraping.
- If `session.state.time_context.cutoff_iso_local` exists, prefer recency using `dateRestrict`.
- Keep results concise with titles, URLs, and short snippets.

### ATS jobs intent examples (route to ats_jobs_agent)
- “list open roles at {Company}”
- “get the JD for {Company} {Role}”
- “show software jobs from {Company} in {Location}”
Notes for ATS:
- Use only public endpoints: Greenhouse Boards API and Lever Postings API.
- If a company slug is unknown, ask to search for the correct slug via google_search_agent (e.g., “site:boards.greenhouse.io {Company}”).
- Respect `time_context.cutoff_iso_local` to filter older postings.

### State handoff — MUST
- Always pass `session.state` (includes `time_context`) with `transfer_to_agent`.
- Sub-agents must read `session.state.time_context` for parsing and display.

### Behavior
- Prefer explicit `transfer_to_agent` when the target is obvious.
- Keep your own replies brief; let specialists do the work.
- If user intent is ambiguous, ask one concise clarifying question before routing.
- Never reveal API keys, internal env var names, or implementation details.
- Never use scraping or non-official endpoints.
"""



############ Edit here ################
# Import factories, not Agent instances
from calendar_service.agent_calendar import build_agent as build_calendar_agent
from google_docs_service.agent_google_docs import build_agent as build_docs_agent
from gmail_service.agent_gmail import build_agent as build_gmail_agent
from google_sheets_service.agent_google_sheets import build_agent as build_sheets_agent
from google_drive_service.agent_google_drive import build_agent as build_drive_agent
from google_search_service.agent_google_search import build_agent as build_search_agent
from ats_jobs_service.agent_ats_jobs_full import build_agent as build_ats_agent


# Instantiate the sub-agents here (not in their modules)
_calendar_agent = build_calendar_agent()
_docs_agent = build_docs_agent()
_gmail_agent  = build_gmail_agent()
_sheets_agent = build_sheets_agent()
_drive_agent = build_drive_agent()
_search_tool = AgentTool(agent=build_search_agent())
_build_ats_agent = build_ats_agent()

orchestrator_agent = Agent(
    model="gemini-2.5-flash",
    name="orchestrator",
    description=ORCH_INSTRUCTIONS,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    sub_agents=[_calendar_agent, _docs_agent, _gmail_agent, _sheets_agent, _drive_agent, _build_ats_agent],
    tools=[_search_tool],  # lets the LLM explicitly hand off; no search tool here
)



