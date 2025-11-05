from google.adk.agents import Agent
from google.adk.tools import google_search  # built-in tool object (not callable)

def build_agent():
    return Agent(
        name="search_agent",
        model="gemini-2.5-flash",
        description="Answers user questions by doing web searches.",
        tools=[google_search],  # note: no parentheses
    )
