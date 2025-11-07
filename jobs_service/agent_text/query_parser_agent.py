# query_parser_agent.py

import re
from typing import Dict, Optional
from google.adk.agents import LlmAgent
from google.genai import types

# Load the model name from environment variables if available. Defaults to
# 'gemini-2.5-flash' when unspecified. Centralizing this variable allows
# configuration via .env without editing code in multiple places.
import os
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# --------------------------------------------
# Tool function: parse natural-language job query
# --------------------------------------------
def parse_job_query(query: xstr) -> Dict[str, Optional[str]]:
    """
    Parse structured job search parameters (position, location, experience, degree) 
    from a natural-language user query.

    Args:
        query (str): User input describing a job search intent, e.g.:
                     "Find a data scientist role in Chicago with 3 years of experience. I have a master's in chemical engineering."

    Returns:
        Dict[str, Optional[str]]: Structured query fields, e.g.:
            {
                "position": "data scientist",
                "location": "Chicago",
                "years_experience": "3",
                "degree": "master's in chemical engineering"
            }
    """
    # Extract position / title
    title_match = re.findall(
        r"(?:job|role|position)\s*(?:for|as)?\s*([\w\s\-]+)", query, re.I
    )
    position = title_match[0].strip() if title_match else None

    # Extract location
    location_match = re.search(
        r"(?:in|at|based in)\s+([A-Z][a-zA-Z\s]+)", query
    )
    location = location_match.group(1).strip() if location_match else None

    # Extract years of experience
    exp_match = re.search(r"(\d+)\s*(?:years?|yrs?)", query, re.I)
    years_experience = exp_match.group(1) if exp_match else None

    # Extract degree information
    degree_match = re.search(
        r"(?:degree|bachelor'?s|master'?s|ph\.?d\.?|mba|m\.?s\.?|b\.?s\.?)"
        r"[\s\w\-]*", query, re.I
    )
    degree = degree_match.group(0).strip() if degree_match else None

    return {
        "position": position,
        "location": location,
        "years_experience": years_experience,
        "degree": degree,
    }


# --------------------------------------------
# Agent definition
# --------------------------------------------
query_parser_agent = LlmAgent(
    model=MODEL,
    name="query_parser_agent",
    description=(
        "Analyzes a user's natural-language job search query and extracts structured information "
        "for downstream agents. The parser identifies:\n"
        "- Desired job position or title (e.g., 'data scientist', 'process engineer').\n"
        "- Target location or region if mentioned (e.g., 'in Chicago', 'based in Boston').\n"
        "- Years of experience explicitly stated by the user (e.g., '3 years', '5 yrs').\n"
        "- Any academic degree information (e.g., 'bachelor's in chemical engineering', "
        "'master's in computer science', 'Ph.D.').\n\n"
        "If degree information is missing, the agent should politely ask the user "
        "whether they hold a degree and in what field, so it can complete the structured query.\n\n"
        "Output the final parsed query as a JSON-like dictionary under the key "
        "'position_specifications'."
    ),
    tools=[parse_job_query],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
    output_key="position_specifications",
)
__all__ = [query_parser_agent]  # no public exports
