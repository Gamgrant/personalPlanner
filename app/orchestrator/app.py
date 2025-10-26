import os
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage
from .langgraph_agent import run_messages
from .mcp_bridge import MCPBroker

load_dotenv()

app = FastAPI(title="Personal CIM", openapi_url="/openapi.json", docs_url="/docs", redoc_url=None)
templates = Jinja2Templates(directory="app/orchestrator/templates")

SESSIONS: dict[str, list] = {}


@app.on_event("startup")
async def _warm_mcp():
    try:
        # Kick once so the server process spins up
        async with MCPBroker() as mcp:
            await mcp.call("calendar__list_calendars", {"user_google_email": None})
    except Exception:
        # If warmup fails, it's fine; first real call will spawn it again.
        pass

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/send", response_class=HTMLResponse)
async def send(text: str = Form(...), chat_id: str = Form(...)):
    try:
        user_msg = (text or "").strip()
        if not user_msg:
            return HTMLResponse("", status_code=204)
        history = SESSIONS.setdefault(chat_id, [])
        history.append(HumanMessage(content=user_msg))
        reply_text = await run_messages(history)
        history.append(AIMessage(content=reply_text))
        bot_html  = f'<div class="row bot"><div class="bubble bot">{_esc(reply_text)}</div></div>'
        return HTMLResponse(bot_html)
    except Exception as e:
        # Always return 200 with error bubble so the client resets loading state.
        err_html = (
            "<div class='row bot'>"
            "<div class='bubble bot'><b>Error</b><br/>"
            f"<pre style='white-space:pre-wrap'>{_esc(str(e))}</pre>"
            "</div></div>"
        )
        return HTMLResponse(err_html)

class ChatRequest(BaseModel):
    text: str
    chat_id: str | None = None

@app.post("/message")
async def message(req: ChatRequest):
    try:
        chat_id = req.chat_id or "default"
        history = SESSIONS.setdefault(chat_id, [])
        history.append(HumanMessage(content=req.text))
        reply_text = await run_messages(history)
        history.append(AIMessage(content=reply_text))
        return {"message": reply_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _esc(s: str) -> str:
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
              .replace('"',"&quot;").replace("'","&#39;").replace("\n","<br>"))
