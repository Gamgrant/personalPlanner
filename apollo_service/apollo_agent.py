import os
import requests
from google.adk.agents import Agent
from google.genai import types

MODEL = "gemini-2.5-flash"
BASE_URL = "https://api.apollo.io/v1"

API_KEY = os.getenv("APOLLO_API_KEY")
if not API_KEY:
    raise EnvironmentError("Missing APOLLO_API_KEY environment variable.")


def search_people(
    name: str = "",
    company: str = "",
    title: str = "",
    location: str = "",
    num_results: int = 5
) -> str:
    """
    Search for people on Apollo.io using the official API.
    Fields are optional and combined as filters.
    """
    url = f"{BASE_URL}/mixed_people/search"
    headers = {"Content-Type": "application/json"}
    payload = {
        "api_key": API_KEY,
        "page": 1,
        "per_page": num_results,
        "person_name": name,
        "organization_name": company,
        "person_titles": [title] if title else [],
        "person_locations": [location] if location else [],
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    people = resp.json().get("people", [])

    if not people:
        return f"No contacts found for {name or title or company}."

    lines = [f"Apollo.io results ({len(people)}):\n"]
    for idx, p in enumerate(people, 1):
        org = p.get("organization", {})
        lines.append(
            f"{idx}. {p.get('name')} — {p.get('title')} at {org.get('name')}\n"
            f"   Email: {p.get('email') or '(hidden)'}\n"
            f"   Location: {p.get('city')}, {p.get('state')}\n"
            f"   LinkedIn: {p.get('linkedin_url')}\n"
        )
    return "\n".join(lines)


def build_agent():
    return Agent(
        model=MODEL,
        name="apollo_agent",
        description="Agent that queries Apollo.io's official API for professional contact and company data.",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[search_people],
    )