import datetime
import os.path
import re
from dateutil import parser as dateutil_parser
import dateparser
import pytz
from tzlocal import get_localzone
from typing import Optional  # keep minimal

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types

MODEL = "gemini-2.5-flash"
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    """Returns an authenticated Google Calendar service instance."""
    creds = None

    # Resolve project root (parent of this file's directory: calendarAgent/)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))

    # Allow .env overrides; otherwise default to creds/ at project root
    credentials_rel = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    token_rel       = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")

    credentials_path = os.path.join(project_root, credentials_rel)
    token_path       = os.path.join(project_root, token_rel)

    print(f"Looking for credentials at: {credentials_path}")
    print(f"Looking for token at: {token_path}")

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            print("Existing token.json found and loaded.")
        except (UnicodeDecodeError, ValueError):
            print("Warning: token.json is invalid or corrupted. Re-authorizing...")
            try:
                os.remove(token_path)
            except OSError:
                pass
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired credentials...")
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
                print("Refreshed token.json saved.")
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f"Missing credentials.json at {credentials_path}")
            print("Launching browser for new Google OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
                print("New token.json created successfully.")

    print("Google Calendar service initialized successfully.")
    return build("calendar", "v3", credentials=creds)


def get_user_timezone() -> str:
    """
    Detect the user's local time zone. Falls back to 'Asia/Kolkata' if detection fails.
    """
    try:
        tz = str(get_localzone())
        print(f"Detected local timezone: {tz}")
        return tz
    except Exception as e:
        print(f"Warning: Could not detect local time zone ({str(e)}). Falling back to 'Asia/Kolkata'.")
        return "Asia/Kolkata"


def search_events(
    query: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
    calendar_id: str = "primary"
) -> list[str]:
    service = get_calendar_service()
    params: dict = {
        "calendarId": calendar_id,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime"
    }
    if query:
        params["q"] = query
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max

    try:
        events_result = service.events().list(**params).execute()
        events = events_result.get("items", [])

        if not events:
            return ["No events found."]

        user_tz = pytz.timezone(get_user_timezone())
        formatted_events: list[str] = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            if 'dateTime' in event['start']:
                utc_time = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                local_time = utc_time.astimezone(user_tz)
                formatted_time = local_time.strftime("%Y-%m-%d %I:%M %p %Z")
            else:
                formatted_time = start
            formatted_events.append(f"{formatted_time} - {event.get('summary','(no title)')} - ID: {event['id']}")

        return formatted_events
    except HttpError as error:
        raise ValueError(f"Failed to search events: {str(error)}")


# ---- Original helper (kept internal). Returns non-JSON types -> don't expose as a tool.
def _parse_nl_datetime(
    datetime_string: str,
    duration: Optional[str] = None,
    time_preference: Optional[str] = None
) -> tuple[str, str, Optional[tuple[datetime.time, datetime.time]]]:
    user_timezone = get_user_timezone()
    settings = {
        'TIMEZONE': user_timezone,
        'TO_TIMEZONE': 'UTC',
        'RETURN_AS_TIMEZONE_AWARE': True,
        'PREFER_DATES_FROM': 'future',
        'DATE_ORDER': 'DMY',
        'STRICT_PARSING': False
    }

    time_window = None
    if time_preference:
        if time_preference.lower() in ["morning", "afternoon", "evening"]:
            time_ranges = {
                "morning": (datetime.time(9, 0), datetime.time(12, 0)),
                "afternoon": (datetime.time(12, 0), datetime.time(17, 0)),
                "evening": (datetime.time(17, 0), datetime.time(21, 0))
            }
            time_window = time_ranges.get(time_preference.lower())
        else:
            try:
                match = re.match(
                    r'(\d+\s*(?:AM|PM|am|pm))\s*to\s*(\d+\s*(?:AM|PM|am|pm))',
                    time_preference,
                    re.IGNORECASE
                )
                if match:
                    start_str, end_str = match.groups()
                    start_time = dateutil_parser.parse(start_str).time()
                    end_time = dateutil_parser.parse(end_str).time()
                    time_window = (start_time, end_time)
            except ValueError:
                print(f"Could not parse time preference: {time_preference}")

    parsed_datetime = dateparser.parse(
        datetime_string,
        languages=['en'],
        settings=settings
    )

    if not parsed_datetime:
        match = re.match(
            r'next\s+([a-zA-Z]+)(?:\s+at\s+(.+?))?(?:\s+(morning|afternoon|evening))?$',
            datetime_string,
            re.IGNORECASE
        )
        if match:
            day_name, time_part, period = match.groups()
            day_map = {
                'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                'friday': 4, 'saturday': 5, 'sunday': 6
            }
            if day_name.lower() not in day_map:
                raise ValueError(f"Invalid day name: {day_name}")

            target_weekday = day_map[day_name.lower()]
            current_date = datetime.datetime.now(pytz.timezone(user_timezone))
            current_weekday = current_date.weekday()
            days_ahead = (target_weekday - current_weekday + 7) % 7 or 7
            target_date = current_date + datetime.timedelta(days=days_ahead)

            default_hour = 9
            if period:
                period_map = {'morning': 9, 'afternoon': 13, 'evening': 18}
                default_hour = period_map.get(period.lower(), 9)
                time_part = time_part or f"{default_hour}:00"

            if time_part:
                try:
                    time_parsed = dateutil_parser.parse(time_part, fuzzy=True)
                    parsed_datetime = target_date.replace(
                        hour=time_parsed.hour,
                        minute=time_parsed.minute,
                        second=0,
                        microsecond=0
                    )
                except ValueError:
                    raise ValueError(f"Could not parse time part: {time_part}")
            else:
                parsed_datetime = target_date.replace(
                    hour=default_hour,
                    minute=0,
                    second=0,
                    microsecond=0
                )

    if not parsed_datetime:
        try:
            parsed_datetime = dateutil_parser.parse(datetime_string, fuzzy=True)
            parsed_datetime = pytz.timezone(user_timezone).localize(parsed_datetime)
        except ValueError:
            raise ValueError(f"Could not parse date/time: {datetime_string}")

    parsed_datetime = parsed_datetime.astimezone(pytz.UTC)
    start_datetime = parsed_datetime.isoformat().replace('+00:00', 'Z')

    if duration:
        duration_minutes = parse_duration(duration)
        end_datetime = (parsed_datetime + datetime.timedelta(minutes=duration_minutes)).isoformat().replace('+00:00', 'Z')
    else:
        end_datetime = (parsed_datetime + datetime.timedelta(hours=1)).isoformat().replace('+00:00', 'Z')

    return start_datetime, end_datetime, time_window


# ---- JSON-friendly wrapper we DO expose as a tool.
def nl_datetime_to_iso(
    datetime_string: str,
    duration: Optional[str] = None,
    time_preference: Optional[str] = None
) -> dict:
    """
    Return a JSON-safe dict: {start_datetime, end_datetime, time_window_start?, time_window_end?}
    All datetimes are ISO-8601 UTC (Z).
    """
    start, end, tw = _parse_nl_datetime(datetime_string, duration, time_preference)
    out: dict = {"start_datetime": start, "end_datetime": end}
    if tw:
        out["time_window_start"] = tw[0].strftime("%H:%M")
        out["time_window_end"] = tw[1].strftime("%H:%M")
    return out


def parse_duration(duration: str) -> int:
    duration_match = re.match(r'(?:for\s+)?(\d+)\s*(hour|hours|minute|minutes)', duration, re.IGNORECASE)
    if duration_match:
        value, unit = duration_match.groups()
        value = int(value)
        return value * 60 if unit.lower().startswith('hour') else value
    raise ValueError(f"Could not parse duration: {duration}")


def create_event(
    summary: str,
    start_datetime: str,
    end_datetime: str,
    location: str = "",
    description: str = "",
    recurrence: Optional[str] = None,
    attendees: Optional[list[dict]] = None
) -> str:
    user_timezone = get_user_timezone()
    service = get_calendar_service()
    event: dict = {
        "summary": summary,
        "start": {"dateTime": start_datetime, "timeZone": user_timezone},
        "end": {"dateTime": end_datetime, "timeZone": user_timezone},
    }

    if location and location.strip() != "":
        event["location"] = location
    if description and description.strip() != "":
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
    match = re.match(r'every\s+(\w+)\s*(for\s+(\d+)\s*(week|month|year)s?)?', recurrence_string, re.IGNORECASE)
    if match:
        freq_map = {
            'daily': 'DAILY', 'weekly': 'WEEKLY', 'monthly': 'MONTHLY', 'yearly': 'YEARLY',
            'monday': 'WEEKLY;BYDAY=MO', 'tuesday': 'WEEKLY;BYDAY=TU', 'wednesday': 'WEEKLY;BYDAY=WE',
            'thursday': 'WEEKLY;BYDAY=TH', 'friday': 'WEEKLY;BYDAY=FR', 'saturday': 'WEEKLY;BYDAY=SA', 'sunday': 'WEEKLY;BYDAY=SU'
        }
        day_or_freq = match.group(1).lower()
        rrule = f"RRULE:FREQ={freq_map.get(day_or_freq, 'WEEKLY')}"
        if match.group(2):
            count = match.group(3)
            unit = match.group(4).upper()
            if unit.startswith('WEEK'):
                rrule += f";COUNT={count}"
            elif unit.startswith('MONTH'):
                rrule += f";COUNT={int(count) * 4}"
            elif unit.startswith('YEAR'):
                rrule += f";COUNT={int(count) * 52}"
        return rrule
    raise ValueError(f"Could not parse recurrence: {recurrence_string}")


def get_event(event_id: str, calendar_id: str = "primary") -> dict:
    """Return the raw Google Calendar event as a plain dict (JSON-serializable)."""
    service = get_calendar_service()
    try:
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        return event  # plain dict (built-in), not typing.Dict
    except HttpError as error:
        raise ValueError(f"Failed to get event: {str(error)}")


def update_event(
    event_id: str,
    summary: Optional[str] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    location: Optional[str] = None,
    description: Optional[str] = None,
    recurrence: Optional[str] = None,
    attendees: Optional[list[dict]] = None,
    calendar_id: str = "primary",
    send_updates: str = "none"  # "all", "externalOnly", or "none"
) -> str:
    service = get_calendar_service()
    update_body: dict = {}

    if summary is not None:
        update_body["summary"] = summary
    if start_datetime is not None:
        update_body["start"] = {"dateTime": start_datetime, "timeZone": get_user_timezone()}
    if end_datetime is not None:
        update_body["end"] = {"dateTime": end_datetime, "timeZone": get_user_timezone()}
    if location is not None:
        update_body["location"] = location
    if description is not None:
        update_body["description"] = description
    if recurrence is not None:
        update_body["recurrence"] = [recurrence]
    if attendees is not None:
        update_body["attendees"] = attendees

    if not update_body:
        raise ValueError("No fields provided to update.")

    try:
        updated = service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=update_body,
            sendUpdates=send_updates
        ).execute()
        return f"Event updated: {updated.get('htmlLink')}"
    except HttpError as error:
        raise ValueError(f"Failed to update event: {str(error)}")


def delete_event(event_id: str, calendar_id: str = "primary", send_updates: str = "none") -> str:
    service = get_calendar_service()
    try:
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates=send_updates
        ).execute()
        return "Event deleted successfully."
    except HttpError as error:
        raise ValueError(f"Failed to delete event: {str(error)}")


def list_events(max_results: int = 10) -> list[str]:
    now = datetime.datetime.now(tz=pytz.UTC).isoformat()
    return search_events(time_min=now, max_results=max_results)


def suggest_meeting_times(
    date_string: str,
    duration: Optional[str] = "1 hour",
    time_preference: Optional[str] = None,
    calendar_id: str = "primary",
    max_suggestions: int = 3
) -> list[str]:
    """
    Suggest available meeting times based on calendar free/busy status.
    Returns local-time strings like "2025-09-23 10:00 AM EDT - 11:00 AM EDT".
    """
    service = get_calendar_service()
    user_timezone = get_user_timezone()
    user_tz = pytz.timezone(user_timezone)

    start_end = nl_datetime_to_iso(date_string, duration, time_preference)
    start_datetime = start_end["start_datetime"]

    parsed_date = datetime.datetime.fromisoformat(start_datetime.replace('Z', '+00:00')).astimezone(user_tz)
    day_start = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + datetime.timedelta(days=1)

    duration_minutes = parse_duration(duration or "60 minutes")

    body = {
        "timeMin": day_start.astimezone(pytz.UTC).isoformat(),
        "timeMax": day_end.astimezone(pytz.UTC).isoformat(),
        "items": [{"id": calendar_id}]
    }
    try:
        freebusy = service.freebusy().query(body=body).execute()
        busy_periods = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    except HttpError as error:
        raise ValueError(f"Failed to query free/busy status: {str(error)}")

    busy_slots: list[tuple[datetime.datetime, datetime.datetime]] = []
    for period in busy_periods:
        start = datetime.datetime.fromisoformat(period["start"].replace('Z', '+00:00')).astimezone(user_tz)
        end = datetime.datetime.fromisoformat(period["end"].replace('Z', '+00:00')).astimezone(user_tz)
        busy_slots.append((start, end))

    free_slots: list[datetime.datetime] = []
    current_time = day_start
    tw_start = start_end.get("time_window_start")
    tw_end   = start_end.get("time_window_end")
    while current_time + datetime.timedelta(minutes=duration_minutes) <= day_end:
        slot_end = current_time + datetime.timedelta(minutes=duration_minutes)
        is_free = True
        for busy_start, busy_end in busy_slots:
            if not (slot_end <= busy_start or current_time >= busy_end):
                is_free = False
                break
        # respect time window if provided
        if is_free:
            if tw_start and tw_end:
                t0 = datetime.datetime.strptime(tw_start, "%H:%M").time()
                t1 = datetime.datetime.strptime(tw_end, "%H:%M").time()
                if not (t0 <= current_time.time() <= t1):
                    current_time += datetime.timedelta(minutes=30)
                    continue
            free_slots.append(current_time)
        current_time += datetime.timedelta(minutes=30)

    if not free_slots:
        return [f"No available slots found for a {duration} meeting on {day_start.strftime('%Y-%m-%d')}. "
                f"Would you like suggestions for another day or a shorter duration?"]

    formatted_slots: list[str] = []
    for slot in free_slots[:max_suggestions]:
        slot_end = slot + datetime.timedelta(minutes=duration_minutes)
        formatted_slots.append(f"{slot.strftime('%Y-%m-%d %I:%M %p %Z')} - {slot_end.strftime('%I:%M %p %Z')}")
    return formatted_slots


calendar_agent_instruction_text = """
You are a helpful and precise calendar assistant that operates in the user's local time zone (e.g., IST for Asia/Kolkata).

Event Creation Instructions:
When the user wants to create an event:
- Collect essential details: title, start time, end time/duration.
- Use `nl_datetime_to_iso` to parse dates/times/durations into ISO 8601 UTC.
- Location and description are optional; only include if provided.
- For recurring events, parse recurrence (e.g., "every Tuesday for 5 weeks") using `parse_recurrence` and pass as RRULE string.
- For attendees, parse emails (e.g., "invite bob@example.com and alice@example.com") as list of dicts [{email: "bob@example.com"}, {email: "alice@example.com"}].
- Call `create_event` with parsed values, including recurrence and attendees if provided.
- Respond with confirmation, title/time in local TZ, and link.

Event Updating/Editing Instructions:
When the user wants to update or edit an event:
- Identify the event: Use `search_events` or `get_event` if ID is known.
- Ask for clarification if multiple matches or ambiguous.
- Use `nl_datetime_to_iso` if updating times/durations.
- For updating recurrence or attendees, parse and pass as in creation.
- Call `update_event` with the event ID and only changed fields (pass None for unchanged), including recurrence or attendees.
- Set `send_updates` to "all" if attendees might be affected, else "none".
- Respond with confirmation and updated details in local TZ.

Event Deletion Instructions:
When the user wants to delete an event:
- Identify the event: Use `search_events` to find the event ID.
- Confirm with the user if needed (e.g., show details via `get_event`).
- Call `delete_event` with the event ID.
- Set `send_updates` to "all" if notifying others, else "none".
- Respond with confirmation.

Event Search and Querying Instructions:
When the user asks to search or query events:
- Use `search_events` with query (keywords), time_min/max (parsed via `nl_datetime_to_iso` if needed).
- Display results in local TZ, including event ID for reference.
- If no results, say so politely.
- For upcoming events, use `list_events`.

Meeting Time Suggestions Instructions:
When the user asks to suggest meeting times (e.g., "Suggest a time for a meeting next Tuesday"):
- Use `suggest_meeting_times` with the target date, duration, and optional time preference (e.g., "morning", "9 AM to 2 PM").
- Parse inputs using `nl_datetime_to_iso` to get the date and duration.
- Return 2-3 free time slots in local TZ.
- If no slots are available, suggest alternative days or durations.
- Offer to create an event with the chosen slot.

Time context:
- If session.state.time_context exists, treat its tz as authoritative for parsing and display.
- Present times in time_context.tz; convert to UTC only for API calls.
- If time_context is missing, ask orchestrator to create it (or fall back to system tz as a last resort).

General Instructions:
- If event ID unknown for update/delete, search first.
- Handle ambiguities by asking questions.
- Keep responses short, user-friendly; no raw JSON.
- Prioritize clarity and correctness.
- The search may not be exact name, please do like a semantic search on the closest event you can find
"""

def build_agent():
    from google.genai import types
    from google.adk.agents import Agent

    return Agent(
        model=MODEL,
        name="google_calendar_agent",
        description=(
            "An AI assistant that manages your Google Calendar using natural language, including creating "
            "(with recurrence and attendees), updating, deleting, searching, and suggesting meeting times "
            "in your local time zone."
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
        ],
    )
