# sync deps 
```bash
uv sync
```
# run project 
```bash
uv run -- adk web 
# or if using .venv just 
source .venv/bin/activate 
adk web
```
# how to add packages
```bash
uv add <packageName>
uv lock
uv sync
```
# run .creds/verify.py for the first time to generate token
```bash
uv run .creds/verify.py
```
---
# current tree:
```
personalPlanner
├── .creds
│   ├── credentials.json
│   ├── token.json              
│   └── verify.py
├── calendarAgent
│   └── agent.py
├── .env
├── pyproject.toml
├── README.md
└── uv.lock
```
run adk web from personalPlanner directory
--- 
turn on Vertex and auth (ADC)
```bash
gcloud services enable aiplatform.googleapis.com
gcloud auth application-default login
```
your .env should look like this:
```
GOOGLE_GENAI_USE_VERTEXAI=True
GOOGLE_CLOUD_PROJECT=!!!!!!!!!!!!!!your project id from gcp!!!!!!!!!!!!!!!!!
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_OAUTH_CLIENT_FILE=.creds/credentials.json
GOOGLE_OAUTH_TOKEN_FILE=.creds/token.json
```

