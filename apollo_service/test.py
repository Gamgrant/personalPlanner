#!/usr/bin/env python3
import os
import sys
import requests
from typing import Dict, Any, List, Optional

BASE_URL = "https://api.apollo.io/api/v1"

# ---------------------------------------------------
# CONFIG
# ---------------------------------------------------
APOLLO_API_KEY ="ha52Fyh29mmmdhZ7gRbSmA"
# If you want to hardcode for testing (not recommended), uncomment:
# APOLLO_API_KEY = "YOUR_X_API_KEY_HERE"

if not APOLLO_API_KEY:
    print("Error: Please set APOLLO_API_KEY in the APOLLO_API_KEY env var.")
    sys.exit(1)


def _headers() -> Dict[str, str]:
    """Shared headers using x-api-key auth."""
    return {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "x-api-key": APOLLO_API_KEY,
    }


# ---------------------------------------------------
# STEP 1: People Search (find recruiter candidates)
# ---------------------------------------------------

def search_recruiters_at_company(
    domain: str,
    per_page: int = 5,
) -> List[Dict[str, Any]]:
    """
    Use /mixed_people/search to find recruiter-type people at a given domain.
    This DOES NOT unlock new emails; it's just candidate discovery.
    """
    url = f"{BASE_URL}/mixed_people/search"

    payload = {
        "q_organization_domains_list": [domain],
        "person_titles": [
            "recruiter",
            "technical recruiter",
            "university recruiter",
            "campus recruiter",
            "talent acquisition",
            "talent acquisition specialist",
            "recruiting manager",
            "talent acquisition partner",
        ],
        "include_similar_titles": True,
        "person_seniorities": ["entry", "senior", "manager", "director", "head"],
        "contact_email_status": ["verified"],
        "page": 1,
        "per_page": per_page,
    }

    print(f"[Search] POST {url}")
    resp = requests.post(url, headers=_headers(), json=payload)
    print(f"[Search] Status: {resp.status_code}")

    if not resp.ok:
        print("[Search] Error response:")
        print(resp.text)
        sys.exit(1)

    data = resp.json()
    people = (
        data.get("people")
        or data.get("contacts")
        or data.get("persons")
        or []
    )

    print(f"[Search] Found {len(people)} candidate(s) at {domain}.\n")

    for i, p in enumerate(people, start=1):
        first = (p.get("first_name") or "").strip()
        last = (p.get("last_name") or "").strip()
        name = (first + " " + last).strip() or "(no name)"
        title = (p.get("title") or "").strip()
        preview_email = p.get("email") or ""
        print(f"{i}. {name} — {title}")
        if preview_email:
            print(f"   Preview (may be locked): {preview_email}")
        print("-" * 40)

    return people


# ---------------------------------------------------
# STEP 2: /people/match for a specific person
# ---------------------------------------------------

def match_person_for_email(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    domain: Optional[str] = None,
    person_id: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Optional[str]:
    """
    Call /people/match to retrieve a real email for one person.
    This is the call that consumes a credit IF Apollo finds & returns enriched data.

    You should pass as many identifiers as you have:
    - person_id from search (best)
    - OR (first_name, last_name, domain)
    - OR linkedin_url, etc.

    Returns:
        work_email (str) if found, else None.
    """
    url = f"{BASE_URL}/people/match"

    payload = {
        "id": person_id,
        "first_name": first_name,
        "last_name": last_name,
        "domain": domain,
        "linkedin_url": linkedin_url,
        "reveal_personal_emails": False,  # stay with work email only
        # DO NOT send reveal_phone_number → avoids webhook mechanics
    }

    # strip out None
    payload = {k: v for k, v in payload.items() if v is not None}

    print(f"[Match] POST {url}")
    print(f"[Match] Payload: {payload}")

    if not payload:
        print("[Match] No identifiers supplied; cannot match.")
        return None

    resp = requests.post(url, headers=_headers(), json=payload)
    print(f"[Match] Status: {resp.status_code}")

    if not resp.ok:
        print("[Match] Error response:")
        print(resp.text)
        return None

    data = resp.json()
    person = data.get("person") or {}

    # Apollo may return direct `email` or `email_addresses`
    direct_email = person.get("email")
    if direct_email:
        return direct_email

    emails = person.get("email_addresses") or []
    work_email = None
    for e in emails:
        if not isinstance(e, dict):
            continue
        addr = e.get("email")
        etype = e.get("type")
        if addr:
            if etype == "work":
                work_email = addr
                break
            if not work_email:
                work_email = addr

    return work_email


# ---------------------------------------------------
# DEMO: use /people/match for Stripe recruiter(s)
# ---------------------------------------------------

def demo_karishma_borah_stripe():
    """
    Example: directly match Karishma Borah at stripe.com via /people/match.
    This mirrors the successful JSON you pasted.
    """
    email = match_person_for_email(
        first_name="Karishma",
        last_name="Borah",
        domain="stripe.com",
    )
    print(f"[Demo Karishma] Resolved email: {email or 'None'}")


def demo_top_recruiter_at_stripe():
    """
    Example full flow:
      1. Search recruiters at stripe.com
      2. Take top candidate
      3. Call /people/match using their id + domain
    """
    people = search_recruiters_at_company("stripe.com", per_page=5)
    if not people:
        print("[Demo] No recruiters found for stripe.com")
        return

    top = people[0]
    first = (top.get("first_name") or "").strip()
    last = (top.get("last_name") or "").strip()
    pid = top.get("id") or top.get("person_id")
    org = top.get("organization") or {}
    domain = org.get("primary_domain") or org.get("domain") or "stripe.com"
    linkedin = top.get("linkedin_url")

    print(f"[Demo] Enriching top candidate: {first} {last} ({pid}) @ {domain}")

    email = match_person_for_email(
        first_name=first or None,
        last_name=last or None,
        domain=domain,
        person_id=pid,
        linkedin_url=linkedin or None,
    )

    print(f"[Demo] Top recruiter resolved email: {email or 'None'}")


# ---------------------------------------------------
# MAIN
# ---------------------------------------------------

def main():
    # Example 1: specific person you care about
    demo_karishma_borah_stripe()

    print("\n" + "=" * 60 + "\n")

    # Example 2: dynamic: search then match
    demo_top_recruiter_at_stripe()


if __name__ == "__main__":
    main()