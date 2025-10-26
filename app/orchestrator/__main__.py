# app/orchestrator/__main__.py
import os
from dotenv import load_dotenv
import uvicorn

load_dotenv()
HOST = os.getenv("API_HOST", "127.0.0.1")
PORT = int(os.getenv("API_PORT", "8080"))
uvicorn.run("app.orchestrator.app:app", host=HOST, port=PORT, reload=True)
