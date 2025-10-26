# app/orchestrator/mcp_tools.py
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from .mcp_bridge import MCPBroker
import os
from datetime import datetime, time
from dateutil import tz

# ----- Schemas -----

class ListCalendarsInput(BaseModel):
    user_google_email: Optional[str] = Field(None, description="Your Google account email")

class GetEventsInput(BaseModel):
    user_google_email: Optional[str] = None
    calendar_id: str = "primary"
    event_id: Optional[str] = None
    time_min: Optional[str] = None
    time_max: Optional[str] = None
    max_results: int = 25
    query: Optional[str] = None
    detailed: bool = False
    include_attachments: bool = False

class CreateEventInput(BaseModel):
    user_google_email: Optional[str] = None
    summary: str
    start_time: str
    end_time: str
    calendar_id: str = "primary"
    description: Optional[str] = None
    location: Optional[str] = None
    attendees: Optional[List[str]] = None
    timezone: Optional[str] = None
    attachments: Optional[List[str]] = None
    add_google_meet: bool = False
    reminders: Optional[Any] = None
    use_default_reminders: bool = True
    transparency: Optional[str] = None

class ModifyEventInput(BaseModel):
    user_google_email: Optional[str] = None
    event_id: str
    calendar_id: str = "primary"
    summary: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    attendees: Optional[List[str]] = None
    timezone: Optional[str] = None
    add_google_meet: Optional[bool] = None
    reminders: Optional[Any] = None
    use_default_reminders: Optional[bool] = None
    transparency: Optional[str] = None

class DeleteEventInput(BaseModel):
    user_google_email: Optional[str] = None
    event_id: str
    calendar_id: str = "primary"

class GetTodayInput(BaseModel):
    user_google_email: Optional[str] = None
    calendar_id: str = Field("primary", description="Calendar ID")
    detailed: bool = Field(False, description="Return detailed output")
    
# ----- Tools (LangChain) -----

@tool("calendar__list_calendars", args_schema=ListCalendarsInput)
async def calendar__list_calendars_tool(user_google_email: Optional[str] = None) -> Dict[str, Any]:
    """List calendars accessible to the authenticated user (MCP call)."""
    async with MCPBroker() as mcp:
        return await mcp.call("calendar__list_calendars", {"user_google_email": user_google_email})

@tool("calendar__get_events", args_schema=GetEventsInput)
async def calendar__get_events_tool(**kwargs) -> Dict[str, Any]:
    """Get events from a calendar (MCP call)."""
    async with MCPBroker() as mcp:
        return await mcp.call("calendar__get_events", kwargs)

@tool("calendar__create_event", args_schema=CreateEventInput)
async def calendar__create_event_tool(**kwargs) -> Dict[str, Any]:
    """Create an event (MCP call)."""
    async with MCPBroker() as mcp:
        return await mcp.call("calendar__create_event", kwargs)

@tool("calendar__modify_event", args_schema=ModifyEventInput)
async def calendar__modify_event_tool(**kwargs) -> Dict[str, Any]:
    """Modify an event (MCP call)."""
    async with MCPBroker() as mcp:
        return await mcp.call("calendar__modify_event", kwargs)

@tool("calendar__delete_event", args_schema=DeleteEventInput)
async def calendar__delete_event_tool(**kwargs) -> Dict[str, Any]:
    """Delete an event (MCP call)."""
    async with MCPBroker() as mcp:
        return await mcp.call("calendar__delete_event", kwargs)

@tool("calendar__get_today_events", args_schema=GetTodayInput)
async def calendar__get_today_events_tool(user_google_email: Optional[str] = None,
                                          calendar_id: str = "primary",
                                          detailed: bool = False) -> Dict[str, Any]:
    """Fetch events for the user's full local day (00:00:00..23:59:59 local), using USER_TZ (default America/New_York)."""

    tz_str = os.getenv("USER_TZ", "America/New_York")
    zone = tz.gettz(tz_str)
    now = datetime.now(zone)

    start_local = datetime.combine(now.date(), time(0, 0, 0, tzinfo=zone))
    end_local   = datetime.combine(now.date(), time(23, 59, 59, tzinfo=zone))

    # Convert to UTC ISO-8601 (Z)
    start_iso = start_local.astimezone(tz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso   = end_local.astimezone(tz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with MCPBroker() as mcp:
        return await mcp.call("calendar__get_events", {
            "user_google_email": user_google_email,
            "calendar_id": calendar_id,
            "time_min": start_iso,
            "time_max": end_iso,
            "detailed": detailed
        })
