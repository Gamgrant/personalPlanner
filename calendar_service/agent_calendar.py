import datetime
import os
import re
from dateutil import parser as dateutil_parser
import dateparser
import pytz
from tzlocal import get_localzone
from typing import Optional

from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types

# Import centralized helper for Google API authentication and service construction.
from utils.google_service_helpers import get_google_service

# Import centralized time utilities for consistent time handling across modules.
from utils.time_utils import ensure_rfc3339, get_time_context

# Load the model name from environment variables if available. Defaults to
# 'gemini-2.5-flash' for backward compatibility. Using os.environ ensures
# that a single value can be set in .env and reused throughout the project.
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# OAuth scopes required for Calendar operations. These are passed to
# utils.google_service_helpers.get_google_service when constructing the
# Calendar service. If additional scopes are ever needed (e.g., to
# manage other resources), add them here.
SCOPES = ["https://www.googleapis.com/auth/calendar"]


# =====================================================
#  Google Calendar Authentication
# =====================================================

def get_calendar_service() -> object:
    """
    Return an authenticated Google Calendar service.

    This function delegates authentication and service construction to the
    centralized helper defined in utils.google_service_helpers. By centralizing
    this logic, we avoid duplicating credential loading and OAuth flows in
    multiple modules. The helper internally calls utils.routing.ensure_google_oauth_env
    to make sure environment variables point to the shared .cred folder, so
    credentials_rel and token_rel environment variables may be relative or
    absolute. See utils/google_service_helpers.py for details.
    """
    return get_google_service("calendar", "v3", SCOPES, "CALENDAR")


# =====================================================
#  Utility
# =====================================================

# Note: The local implementation of _ensure_rfc3339 has been removed. Use
# utils.time_utils.ensure_rfc3339 instead. See search_events below for usage.


# =====================================================
#  Search Events
# =====================================================

def search_events(
    query: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int | None = None,
    calendar_id: str = "primary"
) -> list[str]:
    service = get_calendar_service()
    tz = get_localzone()
    # Use the centralized ensure_rfc3339 helper for consistent formatting.
    time_min = ensure_rfc3339(time_min, tz)
    time_max = ensure_rfc3339(time_max, tz)

    params = {
        "calendarId": calendar_id,
        "singleEvents": True,
        "orderBy": "startTime",
        "timeMin": time_min,
        "timeMax": time_max,
    }
    # Only include maxResults if provided and positive.  When omitted, the
    # Calendar API defaults to returning up to 250 events.
    if max_results and max_results > 0:
        params["maxResults"] = max_results
    if query:
        params["q"] = query

    try:
        events_result = service.events().list(**params).execute()
        events = events_result.get("items", [])

        if not events:
            return [f"No events found between {time_min} and {time_max}."]

        user_tz = get_user_timezone()
        formatted_events = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            if 'dateTime' in event['start']:
                utc_time = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                local_time = utc_time.astimezone(pytz.timezone(user_tz))
                formatted_time = local_time.strftime("%Y-%m-%d %I:%M %p %Z")
            else:
                formatted_time = start
            formatted_events.append(
                f"{formatted_time} - {event.get('summary','(no title)')} - ID: {event['id']}"
            )
        return formatted_events
    except HttpError as error:
        raise ValueError(f"Failed to search events: {str(error)}")


# =====================================================
#  Natural Language Datetime Parsing
# =====================================================

def _parse_nl_datetime(
    datetime_string,
    duration: Optional[str] = None,
    time_preference: Optional[str] = None
) -> tuple[str, str, Optional[tuple[datetime.time, datetime.time]]]:
    # Handle if ADK context dict accidentally passed in
    if isinstance(datetime_string, dict):
        datetime_string = datetime_string.get("datetime") or datetime_string.get("date")
    elif not isinstance(datetime_string, str) or not datetime_string.strip():
        raise ValueError("Invalid datetime_string input; must be non-empty string or context dict.")

    user_timezone = get_user_timezone()
    settings = {
        "TIMEZONE": user_timezone,
        "TO_TIMEZONE": "UTC",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "future",
        "DATE_ORDER": "DMY",
        "STRICT_PARSING": False,
    }

    time_window = None
    if time_preference:
        if time_preference.lower() in ["morning", "afternoon", "evening"]:
            ranges = {
                "morning": (datetime.time(9, 0), datetime.time(12, 0)),
                "afternoon": (datetime.time(12, 0), datetime.time(17, 0)),
                "evening": (datetime.time(17, 0), datetime.time(21, 0)),
            }
            time_window = ranges[time_preference.lower()]
        else:
            try:
                match = re.match(
                    r"(\d+\s*(?:AM|PM|am|pm))\s*to\s*(\d+\s*(?:AM|PM|am|pm))",
                    time_preference, re.IGNORECASE
                )
                if match:
                    start_str, end_str = match.groups()
                    start_time = dateutil_parser.parse(start_str).time()
                    end_time = dateutil_parser.parse(end_str).time()
                    time_window = (start_time, end_time)
            except ValueError:
                print(f"Could not parse time preference: {time_preference}")

    parsed_datetime = dateparser.parse(datetime_string, languages=["en"], settings=settings)

    if not parsed_datetime:
        try:
            parsed_datetime = dateutil_parser.parse(datetime_string, fuzzy=True)
            parsed_datetime = pytz.timezone(user_timezone).localize(parsed_datetime)
        except Exception:
            raise ValueError(f"Could not parse date/time: {datetime_string}")

    parsed_datetime = parsed_datetime.astimezone(pytz.UTC)
    start_datetime = parsed_datetime.isoformat().replace("+00:00", "Z")

    if duration:
        duration_minutes = parse_duration(duration)
        end_datetime = (parsed_datetime + datetime.timedelta(minutes=duration_minutes)).isoformat().replace("+00:00", "Z")
    else:
        end_datetime = (parsed_datetime + datetime.timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    return start_datetime, end_datetime, time_window


def nl_datetime_to_iso(datetime_string: str, duration: Optional[str] = None, time_preference: Optional[str] = None) -> dict:
    start, end, tw = _parse_nl_datetime(datetime_string, duration, time_preference)
    out = {"start_datetime": start, "end_datetime": end}
    if tw:
        out["time_window_start"] = tw[0].strftime("%H:%M")
        out["time_window_end"] = tw[1].strftime("%H:%M")
    return out


# =====================================================
#  Other Calendar Functions
# =====================================================

def parse_duration(duration: str) -> int:
    m = re.match(r"(?:for\s+)?(\d+)\s*(hour|hours|minute|minutes)", duration, re.IGNORECASE)
    if m:
        value, unit = m.groups()
        value = int(value)
        return value * 60 if unit.lower().startswith("hour") else value
    raise ValueError(f"Could not parse duration: {duration}")


def create_event(summary: str, start_datetime: str, end_datetime: str,
                 location: str = "", description: str = "",
                 recurrence: Optional[str] = None, attendees: Optional[list[dict]] = None) -> str:
    user_timezone = get_user_timezone()
    service = get_calendar_service()
    event = {
        "summary": summary,
        "start": {"dateTime": start_datetime, "timeZone": user_timezone},
        "end": {"dateTime": end_datetime, "timeZone": user_timezone},
    }
    if location:
        event["location"] = location
    if description:
        event["description"] = description
    if recurrence:
        event["recurrence"] = [recurrence]
    if attendees:
        event["attendees"] = attendees
    try:
        created = service.events().insert(calendarId="primary", body=event).execute()
        return f"Event created: {created.get('htmlLink')}"
    except HttpError as error:
        raise ValueError(f"Failed to create event: {str(error)}")


def parse_recurrence(recurrence_string: str) -> str:
    match = re.match(r"every\s+(\w+)\s*(for\s+(\d+)\s*(week|month|year)s?)?", recurrence_string, re.IGNORECASE)
    if match:
        freq_map = {
            "daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY",
            "monday": "WEEKLY;BYDAY=MO", "tuesday": "WEEKLY;BYDAY=TU", "wednesday": "WEEKLY;BYDAY=WE",
            "thursday": "WEEKLY;BYDAY=TH", "friday": "WEEKLY;BYDAY=FR", "saturday": "WEEKLY;BYDAY=SA", "sunday": "WEEKLY;BYDAY=SU"
        }
        day_or_freq = match.group(1).lower()
        rrule = f"RRULE:FREQ={freq_map.get(day_or_freq, 'WEEKLY')}"
        if match.group(2):
            count = match.group(3)
            unit = match.group(4).upper()
            if unit.startswith("WEEK"):
                rrule += f";COUNT={count}"
            elif unit.startswith("MONTH"):
                rrule += f";COUNT={int(count) * 4}"
            elif unit.startswith("YEAR"):
                rrule += f";COUNT={int(count) * 52}"
        return rrule
    raise ValueError(f"Could not parse recurrence: {recurrence_string}")


def get_event(event_id: str, calendar_id: str = "primary") -> dict:
    service = get_calendar_service()
    try:
        return service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as error:
        raise ValueError(f"Failed to get event: {str(error)}")


def update_event(event_id: str, summary: Optional[str] = None,
                 start_datetime: Optional[str] = None, end_datetime: Optional[str] = None,
                 location: Optional[str] = None, description: Optional[str] = None,
                 recurrence: Optional[str] = None, attendees: Optional[list[dict]] = None,
                 calendar_id: str = "primary", send_updates: str = "none") -> str:
    service = get_calendar_service()
    update_body = {}
    if summary is not None:
        update_body["summary"] = summary
    if start_datetime:
        update_body["start"] = {"dateTime": start_datetime, "timeZone": get_user_timezone()}
    if end_datetime:
        update_body["end"] = {"dateTime": end_datetime, "timeZone": get_user_timezone()}
    if location:
        update_body["location"] = location
    if description:
        update_body["description"] = description
    if recurrence:
        update_body["recurrence"] = [recurrence]
    if attendees:
        update_body["attendees"] = attendees
    if not update_body:
        raise ValueError("No fields provided to update.")
    try:
        updated = service.events().patch(calendarId=calendar_id, eventId=event_id,
                                         body=update_body, sendUpdates=send_updates).execute()
        return f"Event updated: {updated.get('htmlLink')}"
    except HttpError as error:
        raise ValueError(f"Failed to update event: {str(error)}")


def delete_event(event_id: str, calendar_id: str = "primary", send_updates: str = "none") -> str:
    service = get_calendar_service()
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates=send_updates).execute()
        return "Event deleted successfully."
    except HttpError as error:
        raise ValueError(f"Failed to delete event: {str(error)}")


def list_events(max_results: int | None = None) -> list[str]:
    """
    List upcoming events starting from now.

    Args:
        max_results: Optional maximum number of events to return. If None or
            non-positive, the Calendar API defaults to returning up to 250 events.

    Returns:
        A list of formatted event descriptions.
    """
    now = datetime.datetime.now(tz=pytz.UTC).isoformat()
    return search_events(time_min=now, max_results=max_results)


def suggest_meeting_times(date_string: str, duration: Optional[str] = "1 hour",
                          time_preference: Optional[str] = None,
                          calendar_id: str = "primary", max_suggestions: int = 3) -> list[str]:
    service = get_calendar_service()
    user_tz = get_localzone()
    start_end = nl_datetime_to_iso(date_string, duration, time_preference)
    start_datetime = start_end["start_datetime"]
    parsed_date = datetime.datetime.fromisoformat(start_datetime.replace("Z", "+00:00")).astimezone(user_tz)
    day_start = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + datetime.timedelta(days=1)
    duration_minutes = parse_duration(duration or "60 minutes")
    body = {"timeMin": day_start.astimezone(pytz.UTC).isoformat(),
            "timeMax": day_end.astimezone(pytz.UTC).isoformat(),
            "items": [{"id": calendar_id}]}
    try:
        freebusy = service.freebusy().query(body=body).execute()
        busy_periods = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    except HttpError as error:
        raise ValueError(f"Failed to query free/busy status: {str(error)}")
    busy_slots = [(datetime.datetime.fromisoformat(p["start"].replace("Z", "+00:00")).astimezone(user_tz),
                   datetime.datetime.fromisoformat(p["end"].replace("Z", "+00:00")).astimezone(user_tz))
                  for p in busy_periods]
    free_slots, current_time = [], day_start
    tw_start, tw_end = start_end.get("time_window_start"), start_end.get("time_window_end")
    while current_time + datetime.timedelta(minutes=duration_minutes) <= day_end:
        slot_end = current_time + datetime.timedelta(minutes=duration_minutes)
        if all(slot_end <= bs or current_time >= be for bs, be in busy_slots):
            if tw_start and tw_end:
                t0 = datetime.datetime.strptime(tw_start, "%H:%M").time()
                t1 = datetime.datetime.strptime(tw_end, "%H:%M").time()
                if not (t0 <= current_time.time() <= t1):
                    current_time += datetime.timedelta(minutes=30)
                    continue
            free_slots.append(current_time)
        current_time += datetime.timedelta(minutes=30)
    if not free_slots:
        return [f"No available slots for a {duration} meeting on {day_start:%Y-%m-%d}."]
    return [f"{s:%Y-%m-%d %I:%M %p %Z} - {(s + datetime.timedelta(minutes=duration_minutes)):%I:%M %p %Z}"
            for s in free_slots[:max_suggestions]]


# =====================================================
#  Time Context + Agent Definition
# =====================================================

from zoneinfo import ZoneInfo

# --------------------------------------------------------------------
# Time context helper
# --------------------------------------------------------------------

def make_time_context(preferred_tz: Optional[str] = None) -> dict:
    """
    Alias for utils.time_utils.get_time_context.

    This wrapper preserves the original API while delegating to the
    centralized helper. Callers can continue using make_time_context
    without modification, and the returned dictionary remains
    backwards-compatible.
    """
    return get_time_context(preferred_tz)


calendar_agent_instruction_text = """
You are a helpful and precise calendar assistant that operates in the user's local time zone.
...
(unchanged instruction text)
"""

def get_user_timezone(session=None):
    if session and hasattr(session.state, "time_context"):
        return session.state.time_context.get("tz") or str(get_localzone())
    return str(get_localzone())


calendar_agent: Agent = Agent(
    model=MODEL,
    name="google_calendar_agent",
    description=(
        "An AI assistant that manages your Google Calendar using natural language, including creating, "
        "updating, deleting, searching, and suggesting meeting times in your local time zone. "
        "Use make_time_context if user asks about day/date/time."
        + calendar_agent_instruction_text
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    tools=[
        nl_datetime_to_iso,
        parse_recurrence,
        create_event,
        get_event,
        update_event,
        delete_event,
        search_events,
        list_events,
        suggest_meeting_times,
        make_time_context,
    ],
)

# Optional: make the public API explicit
__all__ = ["calendar_agent"]
