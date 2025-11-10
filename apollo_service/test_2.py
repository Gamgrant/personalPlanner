import os
import json
import requests

BASE_URL = "https://api.apollo.io/api/v1"

# Prefer env var; fallback only for local testing (not recommended for prod)
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "ha52Fyh29mmmdhZ7gRbSmA")


def get_headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "cache-control": "no-cache",
        "x-api-key": APOLLO_API_KEY,
    }


def extract_email_and_phone(data: dict) -> tuple[str | None, str | None]:
    """
    Best-effort extraction of:
      - work email
      - sanitized phone

    Checks:
      - person.email
      - person.email_addresses / contact_emails
      - contact.email
      - person.phone_numbers[*].sanitized_number
      - person.sanitized_phone / phone_number / phone
      - contact.sanitized_phone
      - contact.phone_numbers[*].sanitized_number
      - organization.primary_phone.sanitized_number
      - organization.sanitized_phone
    """
    person = data.get("person") or {}
    contact = data.get("contact") or person.get("contact") or {}
    org = person.get("organization") or data.get("organization") or {}

    # -------- Email --------
    email = None

    # 1) Direct
    if isinstance(person.get("email"), str):
        email = person["email"].strip() or None

    # 2) From email_addresses / contact_emails
    if not email:
        emails = person.get("email_addresses") or person.get("contact_emails") or []
        if isinstance(emails, list):
            for e in emails:
                if not isinstance(e, dict):
                    continue
                addr = (e.get("email") or "").strip()
                etype = (e.get("type") or "").lower()
                if addr:
                    if etype == "work":
                        email = addr
                        break
                    if not email:
                        email = addr

    # 3) contact.email
    if not email and isinstance(contact.get("email"), str):
        email = contact["email"].strip() or None

    # -------- Phone (sanitized) --------
    phone = None

    # 1) person.phone_numbers[*].sanitized_number
    phones = person.get("phone_numbers") or []
    if isinstance(phones, list):
        for p in phones:
            if not isinstance(p, dict):
                continue
            sn = (p.get("sanitized_number") or p.get("sanitized_phone") or "").strip()
            rn = (p.get("raw_number") or "").strip()
            ptype = (p.get("type") or "").lower()

            if sn:
                # Prefer work-ish types, but accept any if nothing else
                if ptype in ("work", "work_direct", "direct", "mobile", "other"):
                    phone = sn
                    break
                if not phone:
                    phone = sn
            elif rn and not phone:
                phone = rn

    # 2) person.sanitized_phone / phone_number / phone
    if not phone:
        for key in ("sanitized_phone", "phone_number", "phone"):
            val = person.get(key)
            if isinstance(val, str) and val.strip():
                phone = val.strip()
                break

    # 3) contact.sanitized_phone
    if not phone:
        val = contact.get("sanitized_phone")
        if isinstance(val, str) and val.strip():
            phone = val.strip()

    # 4) contact.phone_numbers[*].sanitized_number
    if not phone:
        cphones = contact.get("phone_numbers") or []
        if isinstance(cphones, list):
            for p in cphones:
                if not isinstance(p, dict):
                    continue
                sn = (p.get("sanitized_number") or p.get("sanitized_phone") or "").strip()
                rn = (p.get("raw_number") or "").strip()
                if sn:
                    phone = sn
                    break
                if rn and not phone:
                    phone = rn

    # 5) organization.primary_phone.sanitized_number
    if not phone:
        primary_phone = (org.get("primary_phone") or {})
        val = primary_phone.get("sanitized_number") or primary_phone.get("number")
        if isinstance(val, str) and val.strip():
            phone = val.strip()

    # 6) organization.sanitized_phone
    if not phone:
        val = org.get("sanitized_phone")
        if isinstance(val, str) and val.strip():
            phone = val.strip()

    return email, phone


def match_with_phone_example() -> None:
    webhook_url = (
        "https://kaylyn-pseudomythical-jeffrey.ngrok-free.dev"
        "/apollo-webhook?token=super-secret-token"
    )

    payload = {
        "first_name": "Marisa",
        "last_name": "Rhazzal",
        "domain": "asana.com",
        "reveal_personal_emails": False,
        "reveal_phone_number": True,
        "webhook_url": webhook_url,
    }

    print(">>> Calling Apollo /people/match")
    print(f"Request URL: {BASE_URL}/people/match")
    print("Request Payload:")
    print(json.dumps(payload, indent=2))

    resp = requests.post(f"{BASE_URL}/people/match", headers=get_headers(), json=payload)

    print("\n>>> Response")
    print(f"HTTP {resp.status_code}")

    try:
        data = resp.json()
        print("Full JSON:")
        print(json.dumps(data, indent=2))

        email, phone = extract_email_and_phone(data)

        print("\n--- Extracted Values ---")
        print(f"Email: {email or '(none found)'}")
        print(f"Sanitized phone: {phone or '(none found)'}")

    except ValueError:
        # Not JSON
        print("Raw response text:")
        print(resp.text)


if __name__ == "__main__":
    match_with_phone_example()