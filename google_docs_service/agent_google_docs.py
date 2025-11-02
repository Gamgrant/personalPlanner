# google_docs_service/agent_google_docs.py

def build_agent():
    # Import inside the factory so merely importing this module
    # doesn't create a top-level Agent that ADK will auto-discover.
    from google.genai import types
    from google.adk.agents import Agent

    return Agent(
        model="gemini-2.5-flash",
        name="google_docs_agent",
        description="Drafts/edits meeting notes and agendas; returns clean text.",
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
        tools=[],
    )
