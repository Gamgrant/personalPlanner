import os
from google.adk.agents import Agent
from google.adk.tools import google_search  # built-in tool object (not callable)

# Load the model name from environment variables if available. Defaults to
# 'gemini-2.5-flash' when unspecified. Centralizing this variable allows
# configuration via .env without editing code in multiple places.
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

google_search_agent:Agent = Agent(
        name="search_agent",
        model=MODEL,
        description="Answers user questions by doing web searches.",
        tools=[google_search],  # note: no parentheses
    )

__all__ = ["google_search_agent"]