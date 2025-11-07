import os
import requests

def apollo_match_person(first_name, last_name, organization=None):
    api_key = os.getenv("APOLLO_API_KEY", "LVvpQZoG23wbkE_xLcVDgQ")

    base = "https://api.apollo.io/api/v1/people/match"
    params = {
        "first_name": first_name,
        "last_name": last_name,
        "reveal_personal_emails": "true",
        "reveal_phone_number": "false",
    }
    if organization:
        params["organization_name"] = organization

    # build the URL manually (since Apollo expects query params)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{base}?{query}"

    headers = {
        "accept": "application/json",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "api-key":"jTGzr5QShAu1J8j5yB_Z_w" ,
    }

    resp = requests.post(url, headers=headers)
    resp.raise_for_status()
    return resp.json()

apollo_match_person("tim","zheng")