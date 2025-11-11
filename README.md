# Welcome to GoodJobs!

## Dependencies

Before running the package you need to create an environment
uv --venv .venv

sync the dependencies
uv sync

NOTE: google-adk-must be installed

## Environment Variables and API

in .env, you need to have all these API Keys as an environment variable
GOOGLE_GENAI_USE_VERTEXAI=True
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=
GOOGLE_OAUTH_CLIENT_FILE=
GOOGLE_OAUTH_TOKEN_FILE=
APOLLO_API_KEY=
GOOGLE_API_KEY=
MODEL = "gemini-2.5-flash"
DRIVE_RESUMES_FOLDER_ID  =".."
DRIVE_PROJECTS_FOLDER_ID =".."
SHEET_ID_LINKS   =".."
SHEET_RANGE = "Links!A:A"  # sheet named 'Links', column A
JOB_SEARCH_SPREADSHEET_ID=".."
JOB_SEARCH_FOLDER_ID=".."
APOLLO_WEBHOOK_URL = ".."
ELEVENLABS_API_KEY=".."
ELEVENLABS_AGENT_ID=".."
ELEVENLABS_PHONE_NUMBER_ID= ".."

## Authorization Schemes

In google cloud run, you must authenticate all the APIs that is listed in verify.py to your project ID

## Running the Program via the front-end 
streamlit:
1) to run : 
streamlit run app.py
