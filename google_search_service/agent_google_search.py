from google.adk.tools import google_search
from google.adk.agents import Agent
from google.genai import types

MODEL = "gemini-2.5-flash"
def build_agent():
    agent = Agent(
        model=MODEL,
        name="google_search_agent",
        description="An assistant that uses Google Search for real-time information.",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[],  # start with empty
    )

    # 🔒 Overwrite any preloaded tools and only attach google_search
    agent.tools = [google_search]

    return agent