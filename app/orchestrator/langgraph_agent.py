import os
from typing import List
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import SystemMessage, AnyMessage
from datetime import datetime
from dateutil import tz

from .mcp_tools import (
    calendar__list_calendars_tool,
    calendar__get_events_tool,
    calendar__create_event_tool,
    calendar__modify_event_tool,
    calendar__delete_event_tool,
    calendar__get_today_events_tool,
)

load_dotenv()


USER_TZ = os.getenv("USER_TZ", "America/New_York")
_now = datetime.now(tz.gettz(USER_TZ))
_today_local = _now.strftime("%Y-%m-%d")
_now_str = _now.strftime("%Y-%m-%d %H:%M:%S")

OPENAI_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0)

SYSTEM = (
    "You are a calendar-savvy assistant. Prefer the MCP calendar tools to fetch or modify schedules. "
    f"Current local date/time is: {_now_str} ({USER_TZ}). "
    f"When the user says 'today', interpret it as {_today_local} in {USER_TZ} and query that full local-day window. "
    "For internal reasoning, treat times as absolute ISO-8601 timestamps, "
    "but when presenting to the user, format times in the user's local timezone in a readable style, "
    "e.g., 'from 6:00 pm to 6:30 pm: Cancel PureGym'. "
    "Do not include ISO timestamps in the final message unless the user asks for them."
    "When asked for free time or busy time on a diven day ( either date or today/yesterday/tomorrow) be exhaustive and list all the blocks"
)




tools = [
    calendar__list_calendars_tool,
    calendar__get_events_tool,
    calendar__create_event_tool,
    calendar__modify_event_tool,
    calendar__delete_event_tool,
    calendar__get_today_events_tool,
]

agent = create_react_agent(model=llm, tools=tools, prompt=SYSTEM)

async def run_messages(messages: List[AnyMessage]) -> str:
    convo = [SystemMessage(content=SYSTEM)] + messages
    result = await agent.ainvoke({"messages": convo})
    msgs = result["messages"]
    return msgs[-1].content if msgs and hasattr(msgs[-1], "content") else "Done."
