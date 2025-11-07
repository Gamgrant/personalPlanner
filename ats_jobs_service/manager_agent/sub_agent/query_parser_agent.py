from google.adk.agents import Agent
from google.genai import types
import re

def parse_job_query(query: str) -> dict:
    """Parse job title and experience level from user query."""
    title_match = re.findall(r"(?:job|role|position)\s*(?:for|as)?\s*([\w\s\-]+)", query, re.I)
    title = title_match[0].strip() if title_match else query.strip()
    exp_match = re.search(r"(\d+)\s*(?:years?|yrs?)", query, re.I)
    years_exp = int(exp_match.group(1)) if exp_match else None
    return {"title": title, "years_exp": years_exp}

query_parser_agent = Agent(
    model="gemini-2.5-flash",
    name="query_parser_agent",
    description="Parses user input into structured job query data.",
    tools=[parse_job_query],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)