# time_service/time_agent.py
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

from google.adk.agents import Agent
from orchestrator.agent_orchestrator import make_time_context

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

def build_agent():
    return Agent(
        name="time_agent",
        model="gemini-2.5-flash",
        tools=[make_time_context],
        description="Builds a normalized time context."
    )
