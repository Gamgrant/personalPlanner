from google.cloud import bigquery
from google.adk.agents import Agent
from google.genai import types

def store_jobs_in_bigquery(jobs: list[dict]) -> str:
    client = bigquery.Client()
    table_id = "project_id.dataset.greenhouse_jobs"
    errors = client.insert_rows_json(table_id, jobs)
    return "Data uploaded successfully" if not errors else f"Errors: {errors}"

bigquery_storage_agent = Agent(
    model="gemini-2.5-flash",
    name="bigquery_storage_agent",
    description="Stores cleaned job postings into BigQuery.",
    tools=[store_jobs_in_bigquery],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)