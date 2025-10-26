from typing import Optional, Any, Dict, List
import os
from dotenv import load_dotenv; load_dotenv()
from mcp.server.fastmcp import FastMCP

from .gcal import (
    list_calendars as gcal_list_calendars,
    get_events as gcal_get_events,
    create_event as gcal_create_event,
    modify_event as gcal_modify_event,
    delete_event as gcal_delete_event,
)

USER_EMAIL = os.getenv("GCAL_USER_EMAIL", os.getenv("USER_EMAIL","unknown@example.com"))

mcp = FastMCP("calendar-mcp")

@mcp.tool(name="calendar__list_calendars", description="List calendars accessible to the authenticated user")
def calendar__list_calendars(user_google_email: Optional[str] = None) -> Dict[str, Any]:
    email = user_google_email or USER_EMAIL
    text = gcal_list_calendars(email)
    return {"ok": True, "text": text}

@mcp.tool(name="calendar__get_events", description="Get events from a calendar. Can target a specific event_id or a time window.")
def calendar__get_events(user_google_email: Optional[str] = None,
                         calendar_id: str = "primary",
                         event_id: Optional[str] = None,
                         time_min: Optional[str] = None,
                         time_max: Optional[str] = None,
                         max_results: int = 25,
                         query: Optional[str] = None,
                         detailed: bool = False,
                         include_attachments: bool = False) -> Dict[str, Any]:
    email = user_google_email or USER_EMAIL
    text = gcal_get_events(email, calendar_id, event_id, time_min, time_max, max_results, query, detailed, include_attachments)
    return {"ok": True, "text": text}

@mcp.tool(name="calendar__create_event", description="Create a new calendar event")
def calendar__create_event(user_google_email: Optional[str],
                           summary: str,
                           start_time: str,
                           end_time: str,
                           calendar_id: str = "primary",
                           description: Optional[str] = None,
                           location: Optional[str] = None,
                           attendees: Optional[List[str]] = None,
                           timezone: Optional[str] = None,
                           attachments: Optional[List[str]] = None,
                           add_google_meet: bool = False,
                           reminders: Optional[Any] = None,
                           use_default_reminders: bool = True,
                           transparency: Optional[str] = None) -> Dict[str, Any]:
    email = user_google_email or USER_EMAIL
    text = gcal_create_event(email, summary, start_time, end_time, calendar_id, description, location, attendees, timezone, attachments, add_google_meet, reminders, use_default_reminders, transparency)
    return {"ok": True, "text": text}

@mcp.tool(name="calendar__modify_event", description="Modify an existing event by ID")
def calendar__modify_event(user_google_email: Optional[str],
                           event_id: str,
                           calendar_id: str = "primary",
                           summary: Optional[str] = None,
                           start_time: Optional[str] = None,
                           end_time: Optional[str] = None,
                           description: Optional[str] = None,
                           location: Optional[str] = None,
                           attendees: Optional[List[str]] = None,
                           timezone: Optional[str] = None,
                           add_google_meet: Optional[bool] = None,
                           reminders: Optional[Any] = None,
                           use_default_reminders: Optional[bool] = None,
                           transparency: Optional[str] = None) -> Dict[str, Any]:
    email = user_google_email or USER_EMAIL
    text = gcal_modify_event(email, event_id, calendar_id, summary, start_time, end_time, description, location, attendees, timezone, add_google_meet, reminders, use_default_reminders, transparency)
    return {"ok": True, "text": text}

@mcp.tool(name="calendar__delete_event", description="Delete an event by ID")
def calendar__delete_event(user_google_email: Optional[str],
                           event_id: str,
                           calendar_id: str = "primary") -> Dict[str, Any]:
    email = user_google_email or USER_EMAIL
    text = gcal_delete_event(email, event_id, calendar_id)
    return {"ok": True, "text": text}

if __name__ == "__main__":
    mcp.run(transport="stdio")
