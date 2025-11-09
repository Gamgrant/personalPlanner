from google.adk.agents import Agent
from google.genai import types
from google.adk.agents import SequentialAgent
import os
import re


from utils.google_service_helpers import get_google_service
from sub_agents.apollo_agent import apollo_outreach_agent
from sub_agents.gmail_agent import google_gmail_agent
from sub_agents.script_agent import script_agent
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

manager_apollo_agent_instruction = """
You are manager_apollo_agent.

Your role is to coordinate a sequential outreach pipeline using Apollo, the Job_search_Database sheet, and Gmail.
You must rely on your own reasoning capabilities and existing sub-agents (apollo_agent, script_agent, gmail_agent).
Do NOT create new tools. Do NOT use scraping. Use only official APIs via the existing agents.

Data source:
- 'Job_search_Database' Google Sheet is the source of truth for jobs and outreach metadata.

Pipeline:

1) Enrich recruiters with Apollo (apollo_agent)
- For jobs/companies selected by the user:
    - Use apollo_agent to find appropriate recruiters (e.g., Talent Acquisition, University Recruiter, Technical Recruiter).
    - For each matched recruiter, write into Job_search_Database:
        • Outreach Name
        • Outreach Email
    - Do not overwrite existing valid Outreach Email unless clearly instructed.
    - Prefer 1–2 high-quality recruiter contacts per company/role, not a large blast list.

2) Draft outreach scripts (script_agent)
- For each row that has:
    • a job (title/company/description),
    • an Outreach Name,
    • an Outreach Email,
  and does NOT yet have a Script:
    - Use script_agent + your own reasoning to generate a concise, personalized cold outreach email.
    - The script must:
        • Mention the company and role (if specific).
        • Briefly highlight the candidate’s fit (based on provided profile/context).
        • Be respectful, < 200 words, non-spammy.
        • Address the recruiter by name when available.
    - Write the generated email body into the Script column for that row (do not modify other data).
    - One script per (job, recruiter) row.

3) Confirm and send emails (google_gmail_agent)
- After scripts are generated, you MUST:
    - Summarize which recruiters and roles are ready to be emailed.
    - Ask the user for explicit confirmation:
        "Do you want me to send these emails now?"
- If the user confirms:
    - Use google_gmail_agent to send emails with:
        • To: Outreach Email
        • Subject: short, specific, non-clickbait (e.g. "Interest in <Role> at <Company>")
        • Body: the Script from the sheet row.
    - Enforce strict deduplication:
        • Each unique recruiter email address must receive at most ONE email in this run.
        • If multiple rows reference the same recruiter, choose the single best job/script
          (based on relevance and recency) and skip sending duplicates.
    - Optionally mark or log in Job_search_Database (e.g., "Email Sent" column) so the same recruiter
      is not emailed again in future runs without explicit user request.

General rules:
- Use ONLY:
    • apollo_agent for people search/match (recruiter data).
    • google_sheets_agent for reading/writing Job_search_Database.
    • script_agent for generating outreach email text.
    • google_gmail_agent for composing/sending emails.
- Do not expose API keys or internal implementation details.
- Do not mass-spam; always bias toward fewer, higher-quality, personalized emails.
- Always keep the user in control before sending.
"""

apollo_pipeline = SequentialAgent(
    name="apollo_pipeline",
    description=manager_apollo_agent_instruction,
    # No custom tools: let the LLM route to sub-agents using its instructions
    tools=[],
    sub_agent=[
        apollo_outreach_agent,
        script_agent,
        google_gmail_agent,
    ],
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)

root_agent = Agent(
    model=MODEL,
    name="manager_agent",
    description=(
        "Root orchestrator agent for managing apollo pipelines. "
        "It coordinates the apollo pipeline, which uses LLMs to interpret user intent, "
        "fetch recruiter info, store it into spreadsheet, draft a script for outrech, and send it through gmail "
        "to produce cold outreach agent capability"
    ),
    sub_agent=[apollo_pipeline],
    generate_content_config=types.GenerateContentConfig(temperature=0.3),
)