def filter_jobs(location: str, experience: int) -> list[dict]:
    client = bigquery.Client()
    query = f"""
    SELECT * FROM `project_id.dataset.greenhouse_jobs`
    WHERE LOWER(location) LIKE LOWER('%{location}%')
    """
    df = client.query(query).to_dataframe()
    return df.to_dict("records")

filter_ui_agent = Agent(
    model="gemini-2.5-flash",
    name="filter_ui_agent",
    description="Filters jobs based on user-selected location and experience.",
    tools=[filter_jobs],
    generate_content_config=types.GenerateContentConfig(temperature=0.3),
)