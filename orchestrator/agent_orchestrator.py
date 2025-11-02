# agent_orchestrator.py (top of file)
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    # dotenv is optional; ignore if missing
    pass


from google.genai import types
from google.adk.agents import Agent

# Import factories, not Agent instances
from calendar_service.agent_calendar import build_agent as build_calendar_agent
from google_docs_service.agent_google_docs import build_agent as build_docs_agent

ORCH_INSTRUCTIONS = """
You are the top-level coordinator.

Routing:
- Calendar requests → `google_calendar_agent`
- Docs/notes/meeting-doc requests → `google_docs_agent`

Behavior:
- Prefer explicit `transfer_to_agent` when the target is obvious.
- Keep your own replies brief; let specialists do the heavy lifting.
- Preserve and pass along useful context via session.state when handing off.
- Do not reveal internal tool signatures or implementation details.
"""

# Instantiate the sub-agents here (not in their modules)
_calendar_agent = build_calendar_agent()
_docs_agent = build_docs_agent()

orchestrator_agent = Agent(
    model="gemini-2.5-flash",
    name="orchestrator",
    description=ORCH_INSTRUCTIONS,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
    sub_agents=[_calendar_agent, _docs_agent],
)
