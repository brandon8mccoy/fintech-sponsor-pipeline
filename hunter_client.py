"""
Hunter.io contact discovery — drop-in replacement for apollo_client.py.

Flow per company:
  1. Domain search → get all known emails + titles at that domain
  2. Score each person by title priority across 5 buying-committee roles
  3. Return top N contacts based on company size segment:
       SMB (<200 employees)       → 8 contacts
       Mid Market (200–1000)      → 15 contacts
       Enterprise (1000+)         → 10 contacts
     Falls back to SMB cap if employee count is unavailable.
"""

import os
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()

HUNTER_BASE = "https://api.hunter.io/v2"

# Contact caps by company size segment
CONTACTS_SMB = 8         # < 200 employees
CONTACTS_MID_MARKET = 15  # 200–1000 employees
CONTACTS_ENTERPRISE = 20  # 1000+ employees


def _contacts_cap(employee_count: Optional[int]) -> int:
    """Returns contact cap based on employee count. Defaults to SMB if unknown."""
    if employee_count is None:
        return CONTACTS_SMB
    if employee_count >= 1000:
        return CONTACTS_ENTERPRISE
    if employee_count >= 200:
        return CONTACTS_MID_MARKET
    return CONTACTS_SMB

# Title keywords by priority — checked against lowercase title
PRIORITY_KEYWORDS = [
    # P1 — CX / Support / Customer Operations (primary buyer/champion)
    [
        "customer experience", "customer support", "customer operations",
        "customer success", "cx", "head of support", "vp support",
        "director of support", "service operations", "client experience",
        "head of service", "vp customer", "director of customer",
        "contact centre", "contact center", "complaints", "member experience",
    ],
    # P2 — Operations / COO
    [
        "chief operating", "coo", "head of operations", "vp operations",
        "vp of operations", "director of operations", "business operations",
        "head of ops", "vp ops",
    ],
    # P3 — CEO / Founder (decision maker at growth-stage fintechs)
    [
        "chief executive", "ceo", "founder", "co-founder", "managing director",
        "general manager",
    ],
    # P4 — Product
    [
        "chief product", "cpo", "vp product", "head of product",
        "director of product", "vp of product",
    ],
    # P5 — Marketing
    [
        "chief marketing", "cmo", "vp marketing", "head of marketing",
        "director of marketing", "head of brand", "vp brand",
        "head of demand", "growth marketing",
    ],
]


def _score_title(title: str) -> int:
    """Returns priority score: 1 (best, CX/Support) → 5 (Marketing) → 99 (no match)."""
    t = title.lower()
    for priority, keywords in enumerate(PRIORITY_KEYWORDS, start=1):
        if any(kw in t for kw in keywords):
            return priority
    return 99


def search_contacts(company_name: str, domain: str, dry_run: bool = False) -> list[dict]:
    """
    Finds contacts at the given domain, capped by company size segment.
    Returns list of contact dicts compatible with the rest of the pipeline.
    """
    if dry_run:
        return [
            _stub_contact(company_name, domain, "Head of Customer Experience", "P1"),
            _stub_contact(company_name, domain, "Head of Operations", "P2"),
        ]

    api_key = os.environ.get("HUNTER_API_KEY", "")
    if not api_key:
        print(f"  HUNTER_API_KEY not set — skipping {domain}")
        return []

    try:
        resp = requests.get(
            f"{HUNTER_BASE}/domain-search",
            params={"domain": domain, "api_key": api_key, "limit": 20},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"  Hunter request error for {domain}: {e}")
        return []

    if resp.status_code == 401:
        print(f"  Hunter API key invalid or quota exceeded")
        return []
    if resp.status_code == 429:
        print(f"  Hunter rate limit hit — skipping {domain}")
        return []
    if resp.status_code != 200:
        print(f"  Hunter error {resp.status_code} for {domain}: {resp.text[:150]}")
        return []

    data = resp.json().get("data", {})
    emails = data.get("emails", [])
    if not emails:
        return []

    # Determine cap from company size returned by Hunter
    employee_count = data.get("organization", {}).get("estimated_num_employees")
    cap = _contacts_cap(employee_count)
    segment = (
        "Enterprise" if employee_count and employee_count >= 1000
        else "Mid Market" if employee_count and employee_count >= 200
        else "SMB"
    )
    print(f"    {segment} ({employee_count or '?'} employees) → cap {cap} contacts")

    # Filter to people who have a name and an email
    candidates = [
        e for e in emails
        if e.get("first_name") and e.get("value")
    ]

    # Score and sort by priority
    candidates.sort(key=lambda e: _score_title(e.get("position") or ""))

    selected = candidates[:cap]
    return [_format_contact(company_name, e) for e in selected]


def _format_contact(company_name: str, email_data: dict) -> dict:
    return {
        "company_name": company_name,
        "first_name": email_data.get("first_name", ""),
        "last_name": email_data.get("last_name", ""),
        "title": email_data.get("position", ""),
        "email": email_data.get("value", ""),
        "linkedin_url": email_data.get("linkedin", ""),
        "apollo_id": "",  # not applicable for Hunter
    }


def _stub_contact(company_name: str, domain: str, title: str, priority: str) -> dict:
    slug = domain.split(".")[0]
    return {
        "company_name": company_name,
        "first_name": "Test",
        "last_name": priority,
        "title": title,
        "email": f"{priority.lower()}@{domain}",
        "linkedin_url": f"https://linkedin.com/in/{slug}-{priority.lower()}",
        "apollo_id": f"dry-run-{domain}-{priority}",
    }
