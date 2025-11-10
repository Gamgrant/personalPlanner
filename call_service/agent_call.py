"""
ElevenLabs Calling Agent for Job Search Outreach

This agent:

- Reads targets from your Job_Search_Database Google Sheet.
- Uses ElevenLabs Conversational AI (your configured agent + number)
  to call recruiters / contacts.
- Expects the ElevenLabs agent to emit:

    MEETING_CONFIRM: {
      "name": "<Name or Company>",
      "email": "<email>",
      "time": "<ISO 8601 datetime>",
      "duration_minutes": 30,
      "notes": "<optional>"
    }

  ONLY when a meeting is truly confirmed.

- On MEETING_CONFIRM:
    - Creates a Google Calendar event.
- Never returns full transcripts; only structured results.

You can interact with this via your UI, e.g.:
  "Call the first 5 rows."
  "Call everyone with an Outreach Phone Number on Sheet1."
  "Run another batch of 3 calls."

Agent will decide when to use:
  - run_calls_from_job_sheet(...)    for batch calling
  - phone_call(...)                  for a single target

Requirements (env vars):
  - ELEVENLABS_API_KEY
  - ELEVENLABS_AGENT_ID
  - ELEVENLABS_PHONE_NUMBER_ID
  - JOB_SEARCH_SPREADSHEET_ID
  - CALLER_PROMPT aligned with MEETING_CONFIRM contract
"""

import os
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

import asyncio
from dotenv import load_dotenv
from elevenlabs import ElevenLabs

from google.adk.tools import FunctionTool
from google.adk.agents import Agent
from google.genai import types
from utils.google_service_helpers import get_google_service

from .prompts import CALLER_PROMPT

# ---------------------------------------------------------------------
# Environment / config
# ---------------------------------------------------------------------

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID")
ELEVENLABS_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID")
JOB_SEARCH_SPREADSHEET_ID = os.getenv("JOB_SEARCH_SPREADSHEET_ID")

MODEL = os.getenv("MODEL", "gemini-2.5-flash")

logger = logging.getLogger(__name__)


# =====================================================================
# Google API helpers
# =====================================================================

def _get_sheets_service():
    return get_google_service(
        "sheets",
        "v4",
        ["https://www.googleapis.com/auth/spreadsheets"],
        "SHEETS",
    )


def _get_calendar_service():
    return get_google_service(
        "calendar",
        "v3",
        ["https://www.googleapis.com/auth/calendar"],
        "CALENDAR",
    )


# =====================================================================
# Sheet helpers (Job_Search_Database)
# =====================================================================

def _load_jobs_from_sheet(
    spreadsheet_id: str,
    sheet_name: str = "Sheet1",
) -> List[Dict[str, str]]:
    """
    Load rows from Job_Search_Database as a list of dicts:
        { header1: value1, header2: value2, ... }
    """
    sheets = _get_sheets_service()

    # Headers
    header_res = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:Z1",
    ).execute()
    header_values = (header_res.get("values") or [[]])[0]
    headers = [h.strip() for h in header_values]

    if not headers:
        raise ValueError("No headers found in Job_Search_Database.")

    # Data
    data_res = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A2:Z1000",
    ).execute()
    rows = data_res.get("values", []) or []

    records: List[Dict[str, str]] = []
    for row in rows:
        if not any((cell or "").strip() for cell in row):
            continue
        rec: Dict[str, str] = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            rec[h] = (row[i].strip() if i < len(row) else "")
        records.append(rec)

    return records


def _extract_phone_from_record(rec: Dict[str, Any]) -> Optional[str]:
    """
    Find a phone number in a sheet row.
    """
    return (
        rec.get("Outreach Phone Number")
        or rec.get("Outreach phone number")
        or rec.get("Outreach Phone")
        or rec.get("phone")
        or rec.get("Phone")
        or rec.get("phone_number")
        or rec.get("Phone Number")
        or None
    )


# =====================================================================
# ElevenLabs helpers
# =====================================================================

def _init_elevenlabs_client():
    """Initialize ElevenLabs client + Conversational AI subclient."""
    try:
        if not ELEVENLABS_API_KEY:
            raise ValueError("ELEVENLABS_API_KEY not set.")
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        convai = client.conversational_ai
        return client, convai
    except Exception as e:
        logger.error(f"Failed to initialize ElevenLabs client: {e}")
        return None, None


def _extract_meeting_from_turns(turns) -> Optional[Dict[str, Any]]:
    """
    Scan conversation turns for:
        MEETING_CONFIRM: {...}
    """
    if not turns:
        return None

    for t in turns:
        msg = getattr(t, "message", None) or getattr(t, "text", None) or ""
        if hasattr(msg, "text"):
            msg = msg.text
        if not isinstance(msg, str):
            continue

        line = msg.strip()
        if line.startswith("MEETING_CONFIRM:"):
            raw = line[len("MEETING_CONFIRM:"):].strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Failed to parse MEETING_CONFIRM JSON payload.")
                continue

            if "time" in data and ("email" in data or "name" in data):
                return data

    return None


def _create_calendar_event_from_meeting(meeting: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a Google Calendar event from a MEETING_CONFIRM payload.
    """
    service = _get_calendar_service()

    start_str = meeting["time"]
    duration = int(meeting.get("duration_minutes") or 30)

    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    except Exception:
        logger.warning(f"Could not parse meeting time '{start_str}', defaulting to now().")
        start_dt = datetime.utcnow()
    end_dt = start_dt + timedelta(minutes=duration)

    summary = f"Intro call with Steven Yeo and {meeting.get('name', 'Recruiter')}"
    description = meeting.get(
        "notes",
        "Introductory conversation about potential roles and mutual fit.",
    )

    attendees: List[Dict[str, str]] = []
    if meeting.get("email"):
        attendees.append({"email": meeting["email"]})

    event_body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
        "attendees": attendees,
    }

    created = service.events().insert(
        calendarId="primary",
        body=event_body,
        sendUpdates="all",
    ).execute()

    return {
        "event_id": created.get("id"),
        "html_link": created.get("htmlLink"),
        "summary": created.get("summary"),
        "start": created.get("start"),
        "end": created.get("end"),
    }


# =====================================================================
# Core ElevenLabs call
# =====================================================================

async def _make_call(
    to_number: str,
    system_prompt: str,
    poll_interval: float = 1.0,
) -> Dict[str, Any]:
    """
    Place a single outbound call via ElevenLabs Conversational AI.

    Returns:
      {
        "status": "...",
        "conversation_id": str | None,
        "meeting_created": bool,
        "meeting": dict | None,
        "calendar_event": dict | None,
        "error": str | None,
      }
    """
    result: Dict[str, Any] = {
        "status": "initializing",
        "conversation_id": None,
        "meeting_created": False,
        "meeting": None,
        "calendar_event": None,
        "error": None,
    }

    # Env sanity
    for var, val in [
        ("ELEVENLABS_API_KEY", ELEVENLABS_API_KEY),
        ("ELEVENLABS_AGENT_ID", ELEVENLABS_AGENT_ID),
        ("ELEVENLABS_PHONE_NUMBER_ID", ELEVENLABS_PHONE_NUMBER_ID),
    ]:
        if not val:
            err = f"{var} environment variable is not set"
            logger.error(err)
            result.update(status="error_config", error=err)
            return result

    client, convai = _init_elevenlabs_client()
    if not client or not convai:
        err = "Failed to initialize ElevenLabs client"
        logger.error(err)
        result.update(status="error_client", error=err)
        return result

    logger.info(f"Initiating ElevenLabs outbound call → {to_number}")

    # Only override the prompt; do NOT override first_message (forbidden by config)
    conv_init_data = {
        "conversation_config_override": {
            "agent": {
                "prompt": {"prompt": system_prompt}
            }
        }
    }

    try:
        response = convai.twilio.outbound_call(
            agent_id=ELEVENLABS_AGENT_ID,
            agent_phone_number_id=ELEVENLABS_PHONE_NUMBER_ID,
            to_number=to_number,
            conversation_initiation_client_data=conv_init_data,
        )
    except Exception as exc:
        err = f"Error initiating ElevenLabs call: {exc}"
        logger.error(err)
        result.update(status="error_start", error=str(exc))
        return result

    conv_id = getattr(response, "conversation_id", None) or getattr(response, "callSid", None)
    if not conv_id:
        err = "Conversation ID missing in ElevenLabs outbound_call response"
        logger.error(err)
        result.update(status="error_no_conversation_id", error=err)
        return result

    result["conversation_id"] = conv_id
    result["status"] = "initiated"
    logger.info(f"Call started (conversation_id={conv_id})")

    # Poll until done/failed
    terminal_status = {"done", "failed"}
    details = None

    while True:
        time.sleep(poll_interval)
        try:
            details = convai.conversations.get(conv_id)
            status = getattr(details, "status", "unknown")
            result["status"] = status
            logger.info(f"[{conv_id}] Polling status: {status}")
            if status in terminal_status:
                break
        except Exception as exc:
            err = f"Error polling ElevenLabs conversation: {exc}"
            logger.error(err)
            result.update(status="error_polling", error=str(exc))
            return result

    if not details:
        return result

    # Optional: surface any end reason / error from ElevenLabs
    if isinstance(details, dict):
        for key in ("error", "error_message", "end_reason", "hangup_reason"):
            if details.get(key) and not result.get("error"):
                logger.warning(f"[{conv_id}] {key}: {details[key]}")
                result["error"] = f"{key}: {details[key]}"

    turns = (
        getattr(details, "transcript", None)
        or getattr(details, "turns", None)
        or []
    )

    meeting = _extract_meeting_from_turns(turns)
    if meeting:
        try:
            cal_event = _create_calendar_event_from_meeting(meeting)
            result["meeting_created"] = True
            result["meeting"] = meeting
            result["calendar_event"] = cal_event
            logger.info(
                f"[{conv_id}] Meeting created in Google Calendar: {cal_event.get('html_link')}"
            )
        except Exception as exc:
            err = f"Failed to create Google Calendar event: {exc}"
            logger.error(err)
            result["error"] = err

    return result


# =====================================================================
# Public tools
# =====================================================================

async def phone_call(business_data: Dict[str, Any], proposal: str) -> Dict[str, Any]:
    """
    Trigger a call for a single target.
    """
    logger.info("⚒️ [TOOL] 📞 Starting ElevenLabs phone_call tool...")

    business_phone = (
        business_data.get("phone")
        or business_data.get("phone_number")
        or business_data.get("Outreach Phone Number")
        or business_data.get("outreach_phone_number")
    )

    if not business_phone:
        err = "No phone number found in business_data."
        logger.error(err)
        return {"status": "error", "error": err}

    to_number = str(business_phone).strip()

    system_prompt = CALLER_PROMPT.format(
        business_data=json.dumps(business_data, indent=2),
        proposal=proposal,
    )

    return await _make_call(
        to_number=to_number,
        system_prompt=system_prompt,
        poll_interval=2.0,
    )


async def run_calls_from_job_sheet(
    sheet_name: str = "Sheet1",
    limit: int = 5,
    proposal: str = "I’d like to schedule a short introductory call to explore potential fit.",
) -> List[Dict[str, Any]]:
    """
    Batch:
      - Read JOB_SEARCH_SPREADSHEET_ID.
      - For each row with a phone, call via phone_call.
      - Stop after `limit`.
      - Return list of {business_data, call_result}.
    """
    if not JOB_SEARCH_SPREADSHEET_ID:
        raise ValueError("JOB_SEARCH_SPREADSHEET_ID env var not set.")

    records = _load_jobs_from_sheet(JOB_SEARCH_SPREADSHEET_ID, sheet_name=sheet_name)

    results: List[Dict[str, Any]] = []
    count = 0

    for rec in records:
        if count >= limit:
            break

        phone = _extract_phone_from_record(rec)
        if not phone:
            continue

        business_data: Dict[str, Any] = {
            "phone": phone,
            "company": rec.get("Company") or rec.get("Company Name") or "",
            "website": rec.get("Website") or "",
            "outreach_name": rec.get("Outreach Name") or "",
            "outreach_email": rec.get("Outreach email") or rec.get("Outreach Email") or "",
            "row": rec,
        }

        logger.info(
            f"📞 Batch call #{count + 1} → "
            f"{business_data.get('outreach_name') or ''} @ "
            f"{business_data.get('company') or phone}"
        )

        call_result = await phone_call(business_data, proposal=proposal)

        results.append(
            {
                "business_data": business_data,
                "call_result": call_result,
            }
        )

        count += 1

    return results


phone_call_tool = FunctionTool(func=phone_call)
run_calls_from_job_sheet_tool = FunctionTool(func=run_calls_from_job_sheet)


# =====================================================================
# The Agent: use this in your UI
# =====================================================================

elevenlabs_calling_agent = Agent(
    model=MODEL,
    name="elevenlabs_job_search_calling_agent",
    description=(
        "Agent that reads the Job_Search_Database sheet, calls recruiters using "
        "ElevenLabs Conversational AI on Steven's behalf, and books meetings. "
        "Uses MEETING_CONFIRM markers from the voice agent to create Google "
        "Calendar events. Never exposes full transcripts, only structured results."
    ),
    tools=[phone_call_tool, run_calls_from_job_sheet_tool],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.2,
    ),
    output_key="job_search_call_results",
)

__all__ = [
    "phone_call",
    "run_calls_from_job_sheet",
    "phone_call_tool",
    "run_calls_from_job_sheet_tool",
    "elevenlabs_calling_agent",
]