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

####################    timezone related block #############################
def _pick_tz(preferred_tz: Optional[str]) -> str:
    """
    Choose a canonical IANA tz string in this order:
      1) explicit arg
      2) env USER_TZ
      3) system tz (tzlocal)
      4) fallback to America/New_York
    """
    if preferred_tz:
        return preferred_tz
    env_tz = os.environ.get("USER_TZ")
    if env_tz:
        return env_tz
    try:
        return str(get_localzone())
    except Exception:
        return "America/New_York"


def _aware_to_epoch_ms_utc(dt_local: datetime) -> int:
    """Convert an aware local datetime to epoch ms in UTC."""
    if dt_local.tzinfo is None:
        raise ValueError("dt_local must be timezone-aware")
    dt_utc = dt_local.astimezone(timezone.utc)
    return int(dt_utc.timestamp() * 1000)


def _iso_local(dt_local: datetime) -> str:
    """Return ISO-8601 string with local offset, e.g. 2025-11-02T15:00:00-05:00."""
    if dt_local.tzinfo is None:
        raise ValueError("dt_local must be timezone-aware")
    return dt_local.isoformat()


def make_time_context(
    tz: Optional[str] = None,
    since_local: Optional[str] = None,
    date_hint: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build a normalized time context for the current user turn.

    Returns:
      {
        tz: IANA string,
        now_iso_local: str,
        today_start_iso_local: str,
        today_end_iso_local: str,
        today_start_epoch_ms_utc: int,
        today_end_epoch_ms_utc: int,
        cutoff_iso_local?: str,
        cutoff_epoch_ms_utc?: int
      }
    """
    tz_str = _pick_tz(tz)
    zone = ZoneInfo(tz_str)

    now_local = datetime.now(zone)
    # day bounds (DST-safe with ZoneInfo)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end_local = today_start_local + timedelta(days=1)

    cutoff_local = None
    if since_local:
        # If user says "since 3 pm today", we pass since_local="3 pm", date_hint="today"
        query_text = f"{since_local} {date_hint}" if date_hint else since_local
        parsed = dateparser.parse(
            query_text,
            languages=["en"],
            settings={
                "TIMEZONE": tz_str,
                "TO_TIMEZONE": tz_str,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "past",
                "DATE_ORDER": "MDY",
            },
        )
        if parsed:
            cutoff_local = parsed.astimezone(zone)

    out: dict[str, Any] = {
        "tz": tz_str,
        "now_iso_local": _iso_local(now_local),
        "today_start_iso_local": _iso_local(today_start_local),
        "today_end_iso_local": _iso_local(today_end_local),
        "today_start_epoch_ms_utc": _aware_to_epoch_ms_utc(today_start_local),
        "today_end_epoch_ms_utc": _aware_to_epoch_ms_utc(today_end_local),
    }
    if cutoff_local is not None:
        out["cutoff_iso_local"] = _iso_local(cutoff_local)
        out["cutoff_epoch_ms_utc"] = _aware_to_epoch_ms_utc(cutoff_local)
    return out


ORCH_INSTRUCTIONS = """
You are the top-level coordinator.

Time context (MUST DO at the start of every user turn):
- Call make_time_context(tz?, since_local?, date_hint?) and save the dict to session.state.time_context.
- Derive since_local and/or date_hint from the user’s words when relevant (e.g., “since 3 pm today” → since_local="3 pm", date_hint="today").
- Keep time_context in session.state and pass it along with transfers so sub-agents use the same tz and cutoffs.

Routing:
- Calendar requests → google_calendar_agent
- Docs/notes/meeting-doc requests → google_docs_agent
- Email/Gmail requests → google_gmail_agent
- Sheets data/operations → google_sheets_agent
- Drive file/folder search/browse/download/export/sharing → google_drive_agent
- General web/public info requests → google_search_agent

Gmail intent examples (route to google_gmail_agent):
- “search my inbox for …”, “find unread from …”, “show thread about …”
- “send an email to …”, “reply to … with …”, “forward …”, “add CC/BCC …”
- “mark as read/unread”, “archive this”, “trash/delete”, “list labels”, “download attachments”

Calendar intent examples (route to google_calendar_agent):
- “create/update/delete a meeting”, “what’s on my calendar”, “suggest times next Tue”
- “recurring every Friday…”, “invite alice@example.com”

Docs intent examples (route to google_docs_agent):
- “create a meeting notes doc”, “summarize this into a doc”, “insert bullets/sections”

Sheets intent examples (route to google_sheets_agent):
- “list my spreadsheets”, “open the sheet called ‘Q4 Pipeline’”
- “read Sheet1!A1:D20 from <ID>”, “write these rows to ‘Tasks’!A2:C”
- “clear ‘Data’!A1:Z”, “add a new tab ‘Summary’”, “create a new spreadsheet named ‘Weekly Plan’”
- “update B2 with today’s date”, “append rows to ‘Log’”


Search intent examples (route to google_search_agent)
- “what’s the weather in London?”
- “who won the game last night?”
- “search the web for electric car reviews”
- “how tall is the Eiffel Tower?”

“latest news on the stock market”
State handoff (MUST):
- Always pass session.state (includes time_context) with transfer_to_agent.
- Sub-agents must read session.state.time_context for date/time parsing and display.

Behavior:
- Prefer explicit transfer_to_agent when the target is obvious.
- Keep your own replies brief; let specialists do the heavy lifting.
- If user intent is ambiguous, ask one concise clarifying question before routing.
- Do not reveal internal tool signatures or implementation details.
"""



############ Edit here ################
# Import factories, not Agent instances
from calendar_service.agent_calendar import build_agent as build_calendar_agent
from google_docs_service.agent_google_docs import build_agent as build_docs_agent
from gmail_service.agent_gmail import build_agent as build_gmail_agent
from google_sheets_service.agent_google_sheets import build_agent as build_sheets_agent


# Instantiate the sub-agents here (not in their modules)
_calendar_agent = build_calendar_agent()
_docs_agent = build_docs_agent()
_gmail_agent  = build_gmail_agent()
_sheets_agent = build_sheets_agent()


orchestrator_agent = Agent(
    model="gemini-2.5-flash",
    name="orchestrator",
    description=ORCH_INSTRUCTIONS,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    sub_agents=[_calendar_agent, _docs_agent, _gmail_agent, _sheets_agent, _drive_agent],
    tools=[make_time_context], 
)


