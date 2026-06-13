import base64
import json
import os
import requests
import anthropic
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def extract_sponsors_from_pdf(source: str) -> list[dict]:
    """
    Extract sponsor companies from a PDF file or URL.
    source: local file path or https:// URL.
    Returns list of {name, domain, category} dicts.
    """
    pdf_bytes = _load_pdf(source)
    if not pdf_bytes:
        return []

    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    return _extract_via_claude(pdf_b64)


def _extract_partial_objects(text: str) -> list:
    """Extracts complete JSON objects from a possibly-truncated array string."""
    import re
    pattern = re.compile(r'\{[^{}]+\}')
    results = []
    for match in pattern.finditer(text):
        try:
            obj = json.loads(match.group())
            results.append(obj)
        except json.JSONDecodeError:
            pass
    return results


def _load_pdf(source: str) -> Optional[bytes]:
    if source.startswith("http://") or source.startswith("https://"):
        try:
            resp = requests.get(source, timeout=60)
            if resp.status_code == 200:
                return resp.content
            print(f"  [PDF] HTTP {resp.status_code} downloading {source}")
            return None
        except Exception as e:
            print(f"  [PDF] Download error: {e}")
            return None
    else:
        # Local file path
        try:
            with open(source, "rb") as f:
                return f.read()
        except Exception as e:
            print(f"  [PDF] File read error: {e}")
            return None


def _extract_via_claude(pdf_b64: str) -> list[dict]:
    """
    Send the PDF to Claude using the document content block API.
    Extracts company names, categories, and best-guess domains.
    """
    client = _get_client()

    prompt = """This PDF contains a sponsor listing page from a fintech conference.
Extract every company listed as a sponsor or exhibitor.

For each company return:
- "name": the company name exactly as shown
- "category": their listed industry category (e.g. "Payments & Payments Technology", "Banking", "Blockchain & Cryptocurrencies", "Data, Analytics & AI", "Regtech / Suptech", etc.)
- "domain": your best-guess website domain (e.g. "stripe.com", "plaid.com"). If unsure, derive it from the company name.

Skip: navigation items, section headers (like "5 STAR SPONSOR"), government pavilions, trade associations, and non-company entries.

Return ONLY a JSON array. Example format:
[
  {"name": "Stripe", "category": "Payments & Payments Technology", "domain": "stripe.com"},
  {"name": "Banking Circle", "category": "Banking", "domain": "bankingcircle.com"}
]"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )

        content = message.content[0].text.strip()
        start = content.find("[")
        end = content.rfind("]") + 1

        raw = None
        if start != -1 and end > 0:
            try:
                raw = json.loads(content[start:end])
            except json.JSONDecodeError:
                pass

        if raw is None:
            # Response may have been truncated — extract complete JSON objects
            raw = _extract_partial_objects(content)
            if raw:
                print(f"  [PDF] Partial JSON recovered ({len(raw)} objects)")
            else:
                print("  [PDF] Claude returned no parseable JSON")
                return []
        results = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            domain = (item.get("domain") or "").strip().lower()
            category = (item.get("category") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            results.append({"name": name, "domain": domain, "category": category})

        return results

    except Exception as e:
        print(f"  [PDF] Claude extraction error: {type(e).__name__}: {e}")
        return []
