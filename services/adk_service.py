# services/adk_service.py

from typing import Tuple

from google.genai import types
from google.adk.runners import InMemoryRunner

from orchestrator.agent import orchestrator_agent
from config.settings import APP_NAME_FOR_ADK, USER_ID, INITIAL_STATE

_runner: InMemoryRunner | None = None
_session_id: str | None = None


def initialize_adk() -> Tuple[InMemoryRunner, str]:
    """
    Initialize a global InMemoryRunner + one session and return them.

    Called once from the Streamlit app; reused across messages.
    """
    global _runner, _session_id

    # Create the runner once
    if _runner is None:
        _runner = InMemoryRunner(
            agent=orchestrator_agent,
            app_name=APP_NAME_FOR_ADK,
        )

    # Create (or reuse) a session
    if _session_id is None:
        # Use the sync helper so we don't mess with asyncio in Streamlit
        session = _runner.session_service.create_session_sync(
            app_name=APP_NAME_FOR_ADK,
            user_id=USER_ID,
            state=INITIAL_STATE,
        )
        _session_id = session.id

    return _runner, _session_id


def run_adk_sync(runner: InMemoryRunner, session_id: str, prompt: str) -> str:
    """
    Send a message to the ADK runner and return the last text response
    as a plain string for Streamlit to render.
    """
    # Wrap user message as ADK Content
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(prompt)],
    )

    last_text = ""

    # Stream events from the runner
    for event in runner.run(
        user_id=USER_ID,
        session_id=session_id,
        new_message=content,
    ):
        # Grab any assistant text from events
        if getattr(event, "content", None) and getattr(event.content, "parts", None):
            for part in event.content.parts:
                text = getattr(part, "text", None)
                if text:
                    last_text = text

    # Fallback in case nothing came back
    return last_text or "I didn't receive any text response from the agent."