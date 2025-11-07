from google.adk.agents import SequentialAgent

job_search_agent = SequentialAgent(
    name="job_search_pipeline",
    description="Fetch and filter job postings based on user query.",
    sub_agents=[
        query_parser_agent,
        greenhouse_fetch_agent,
        bigquery_storage_agent,
        filter_ui_agent
    ],
)