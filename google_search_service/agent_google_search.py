from google.adk.tools import google_search
from google.adk.agents import LlmAgent
from google.genai import types

MODEL = "gemini-2.5-flash"
def build_agent():
    agent = LlmAgent(
        model=MODEL,
        name="google_search_agent",
        description="An assistant that uses Google Search for real-time information.",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        tools=[google_search],  # start with empty
    )
    return agent