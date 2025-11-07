from google.adk.agents import Agent
from google.genai import types
from ats_jobs_agent import greenhouse_list_jobs, search_jobs, make_time_context


greenhouse_fetch_agent = Agent(
    model="gemini-2.5-flash",
    name="greenhouse_fetch_agent",
    description="Fetches jobs from public Greenhouse APIs using parsed query parameters.",
    tools=[greenhouse_list_jobs, search_jobs, make_time_context],
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)