"""
Hunter.io contact discovery for event speakers.

Uses Hunter's email-finder endpoint: given a speaker's name and company domain,
returns their verified email address. This is 1 credit per lookup vs. a full
domain search — much more efficient when you already know who you're targeting.

Flow per speaker:
  1. Split name into first / last
  2. Call email-finder with first_name, last_name, domain
  3. Return email if confidence >= MIN_CONFIDENCE, else None
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

HUNTER_BASE = "https://api.hunter.io/v2"
MIN_CONFIDENCE = 70   # Hunter confidence score threshold (0–100)


def find_speaker_email(name: str, domain: str, dry_run: bool = False) -> str:
    """
    Finds email for a known speaker using Hunter's email-finder endpoint.
    Returns the email string if found with sufficient confidence, else empty string.
    """
    if not domain:
        return ""

    if dry_run:
        slug = domain.split(".")[0]
        parts = name.lower().split()
        return f"{parts[0]}.{parts[-1]}@{domain}" if len(parts) > 1 else f"{parts[0]}@{domain}"

    api_key = os.environ.get("HUNTER_API_KEY", "")
    if not api_key:
        print(f"  HUNTER_API_KEY not set — skipping {name}")
        return ""

    parts = name.strip().split()
    first = parts[0] if parts else ""
    last  = parts[-1] if len(parts) > 1 else ""

    if not first or not last:
        return ""

    try:
        resp = requests.get(
            f"{HUNTER_BASE}/email-finder",
            params={
                "first_name": first,
                "last_name": last,
                "domain": domain,
                "api_key": api_key,
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"    Hunter request error for {name} @ {domain}: {e}")
        return ""

    if resp.status_code == 401:
        print("  Hunter API key invalid or quota exceeded")
        return ""
    if resp.status_code == 429:
        print(f"  Hunter rate limit hit — skipping {name}")
        return ""
    if resp.status_code == 404:
        # No email found — not an error, just no match
        return ""
    if resp.status_code != 200:
        print(f"  Hunter error {resp.status_code} for {name}: {resp.text[:120]}")
        return ""

    data  = resp.json().get("data", {})
    email = data.get("email", "")
    score = data.get("score", 0)

    if email and score >= MIN_CONFIDENCE:
        return email

    return ""
