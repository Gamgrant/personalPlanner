from google.adk.agents import Agent
from google.genai import types
from google.adk.agents import SequentialAgent
import os
import re


from utils.google_service_helpers import get_google_service
from .sub_agents.apollo_agent import apollo_outreach_agent
from .sub_agents.gmail_agent import google_gmail_agent
from .sub_agents.script_agent import script_agent
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

manager_apollo_agent_instruction = """
You are manager_apollo_agent.

Your role is to coordinate a sequential outreach pipeline using Apollo, the Job_Search_Database sheet, and Gmail.
You must rely on your own reasoning capabilities and existing sub-agents (apollo_outreach_agent, script_agent, gmail_outreach_agent).
Do NOT create new tools. Do NOT use scraping. Use only official APIs via the existing agents.

Data source:
- 'Job_Search_Database' Google Sheet is the source of truth for jobs and outreach metadata.

End-to-end behavior (NO user confirmation, fully automatic when invoked):

1) Enrich recruiters with Apollo (apollo_outreach_agent)
- For eligible jobs/companies in the sheet:
    - Use apollo_outreach_agent to find appropriate recruiters (Talent Acquisition, University Recruiter, Technical Recruiter, etc.).
    - For each matched recruiter, write into Job_Search_Database:
        • Outreach Name
        • Outreach Email
        • Outreach Phone Number
    - Do not overwrite existing valid Outreach Email unless clearly necessary.
    - Prefer 1–2 high-quality recruiter contacts per company/role, not a large blast list.

2) Draft outreach scripts (script_agent)
- For each row that has:
    • a job (title/company/description),
    • an Outreach Name,
    • an Outreach Email,
    • a non-empty resume_id_latex_done (customized resume file id),
  and does NOT yet have an Outreach Email Script:
    - Use script_agent + your own reasoning to generate a concise, personalized cold outreach email.
    - The script must:
        • Mention the company and role.
        • Briefly highlight the candidate’s fit (based on the row + resume).
        • Be respectful, < 200 words, non-spammy.
        • Address the recruiter by name when available.
    - Write the generated email body into the Outreach Email Script column for that row.
    - One script per (job, recruiter) row.

3) Create Gmail drafts with attached resume (gmail_outreach_agent)
- After scripts are generated, automatically call gmail_outreach_agent.
- gmail_outreach_agent will:
    - Read Outreach Email, Outreach Email Script, and resume_id_latex_done.
    - Fetch the resume from Drive by id, attach it.
    - Create Gmail DRAFTS (never send) to each recruiter.
    - Enforce “at most one draft per recruiter email” if it chooses to deduplicate.
- Do NOT ask the user for confirmation. Draft creation is the expected behavior when this pipeline runs.
- Never send emails automatically; only drafts are created.

General rules:
- Use ONLY:
    • apollo_outreach_agent for people search/match (recruiter data).
    • script_agent for generating outreach email text and writing Outreach Email Script.
    • gmail_outreach_agent for creating Gmail drafts (with attached resume).
- Do not expose API keys or internal implementation details.
- Do not mass-spam; always bias toward fewer, higher-quality, personalized emails.
- Keep responses brief and status-like (e.g., how many rows were enriched / scripted / drafted), and NEVER ask the user follow-up questions.
"""

apollo_pipeline = SequentialAgent(
    name="apollo_pipeline",
    description=manager_apollo_agent_instruction,
    sub_agents=[
        apollo_outreach_agent,
        script_agent,
        google_gmail_agent,
    ]
)

root_apollo_agent = Agent(
    model=MODEL,
    name="apollo_manager_agent",
    description=(
        "Root orchestrator agent for managing apollo pipelines. "
        "It coordinates the apollo pipeline to enrich recruiters in the Job_Search_Database "
        "sheet via Apollo.io, generate Outreach Email Script text, and create Gmail drafts "
        "with attached customized resumes. It never asks the user for confirmation; it just runs."
    ),
    sub_agents=[apollo_pipeline],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)


root_agent = root_apollo_agent