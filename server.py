# server.py

import os
from fastapi import FastAPI
from pydantic import BaseModel
from google.adk.runners import InMemoryRunner
from google.genai import types
from orchestrator.agent import orchestrator_agent  # your agent

# ------------- config -------------

APP_NAME = os.getenv("APP_NAME", "personal-planner-agent")
USER_ID = "cloud-run-user"
INITIAL_STATE = {}  # if you use ADK state, put defaults here

# ------------- init runner -------------

runner: InMemoryRunner | None = None

def get_runner() -> InMemoryRunner:
    global runner
    if runner is None:
        runner = InMemoryRunner(agent=orchestrator_agent, app_name=APP_NAME)
    return runner

# ------------- FastAPI -------------

app = FastAPI()

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """
    Single-turn call into your orchestrator.
    (You can extend to multi-turn with session IDs if you’d like.)
    """
    r = get_runner()

    # Make a new session per request (simple) or keep one global.
    session = r.session_service.create_session_sync(
        app_name=APP_NAME,
        user_id=USER_ID,
        state=INITIAL_STATE,
    )

    content = types.Content(
        role="user",
        parts=[types.Part.from_text(req.message)],
    )

    last_text = ""

    for event in r.run(
        user_id=USER_ID,
        session_id=session.id,
        new_message=content,
    ):
        if getattr(event, "content", None):
            for part in getattr(event.content, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    last_text = text

    return ChatResponse(reply=last_text or "No response text from agent.")