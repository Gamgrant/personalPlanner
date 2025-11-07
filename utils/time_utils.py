"""
Utility functions for working with time, dates, and timezones.

This module centralizes common operations related to the current time and
date, timezone handling, and RFC3339 formatting. By using these helpers
across all agents, we ensure consistent handling of time throughout the
project.

Functions:
    get_time_context(preferred_tz: Optional[str] = None) -> dict
        Return a dictionary with the current date, time, and timezone
        information. This is useful for building prompts or logging
        context-sensitive information.

    ensure_rfc3339(dt_str: Optional[str], tz: Optional[tzinfo] = None) -> str
        Convert a date or datetime string into an RFC3339-compliant
        ISO timestamp with timezone information. If the input is None
        or invalid, returns the current time in the specified timezone.

    get_current_datetime(tz: Optional[str] = None) -> datetime
        Return a timezone-aware datetime object for the current moment.

Note:
    These functions rely on the tzlocal library to detect the local
    timezone when no preferred timezone is specified. They fall back to
    America/New_York if detection fails.

"""

from __future__ import annotations

import datetime
from typing import Optional
from zoneinfo import ZoneInfo
from tzlocal import get_localzone

def get_time_context(preferred_tz: Optional[str] = None) -> dict:
    """Return current date, time, and timezone information.

    Args:
        preferred_tz: Optional IANA timezone string (e.g., 'America/New_York').
            If provided, the returned context will be based on this
            timezone. Otherwise, the local timezone is used.

    Returns:
        A dictionary containing:
            - 'datetime': ISO 8601 timestamp with timezone offset
            - 'date': YYYY-MM-DD formatted date
            - 'time': HH:MM:SS formatted time
            - 'weekday': Name of the weekday
            - 'timezone': Timezone name
            - 'utc_offset': Offset from UTC in ±HHMM format
    """
    try:
        tz = ZoneInfo(preferred_tz) if preferred_tz else get_localzone()
    except Exception:
        tz = ZoneInfo("America/New_York")

    now = datetime.datetime.now(tz)
    return {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "timezone": str(tz),
        "utc_offset": now.strftime("%z"),
    }


def ensure_rfc3339(dt_str: Optional[str], tz: Optional[ZoneInfo] = None) -> str:
    """Return an RFC3339 timestamp given a date/time string.

    This function mirrors the behavior of the calendar agent's internal
    _ensure_rfc3339 implementation. It attempts to parse the input string
    as either an ISO timestamp or a date. If parsing fails or the input is
    None, the current time is used.

    Args:
        dt_str: The date or datetime string to convert. May be None.
        tz: Optional timezone instance. If not provided, the local
            timezone is used.

    Returns:
        A string representing the datetime in ISO 8601 format with
        timezone information (RFC3339).
    """
    tz = tz or get_localzone()
    if not dt_str:
        return datetime.datetime.now(tz).isoformat()

    # Try direct ISO parsing
    try:
        dt = datetime.datetime.fromisoformat(str(dt_str))
        if dt.tzinfo is None:
            dt = tz.localize(dt) if hasattr(tz, "localize") else dt.replace(tzinfo=tz)
        return dt.isoformat()
    except Exception:
        pass

    # Try date-only parsing (YYYY-MM-DD)
    try:
        dt = datetime.datetime.strptime(str(dt_str), "%Y-%m-%d").astimezone(tz)
        return dt.isoformat()
    except Exception:
        return datetime.datetime.now(tz).isoformat()


def get_current_datetime(tz: Optional[str] = None) -> datetime.datetime:
    """Return the current datetime with timezone awareness.

    Args:
        tz: Optional IANA timezone string. If provided, the result
            will be in that timezone. Otherwise, uses the local timezone.

    Returns:
        A timezone-aware datetime object representing now.
    """
    try:
        zone = ZoneInfo(tz) if tz else get_localzone()
    except Exception:
        zone = ZoneInfo("America/New_York")
    return datetime.datetime.now(zone)
