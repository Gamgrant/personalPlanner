import os

import uvicorn
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app

# Directory that contains your agent folders (orchestrator/, calendar_service/, etc.)
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

# Store sessions in /tmp so Cloud Run can write to it
SESSION_SERVICE_URI = "sqlite:////tmp/sessions.db"

# CORS settings – keep it simple for now
ALLOWED_ORIGINS = ["*"]  # tighten later if you want

# Whether to serve the ADK dev UI (Swagger-like web UI)
SERVE_WEB_INTERFACE = True  # set False if you only want pure API

# Build the FastAPI app that exposes all ADK agents under AGENT_DIR
app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    session_service_uri=SESSION_SERVICE_URI,
    allow_origins=ALLOWED_ORIGINS,
    web=SERVE_WEB_INTERFACE,
)

if __name__ == "__main__":
    # Cloud Run injects $PORT; default to 8080 for local dev
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
