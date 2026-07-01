"""
Speaker page scraper.

Fallback chain per event:
  Stage 1 — Firecrawl  (handles JS-rendered pages, returns clean markdown)
  Stage 2 — Jina AI    (JS-heavy pages Firecrawl misses)
  Stage 3 — BeautifulSoup + Claude Haiku (raw HTML fallback)

Each stage returns a list of speaker dicts:
  {
    "name":         str,   # full name
    "title":        str,   # job title / role
    "company":      str,   # employer name
    "domain":       str,   # guessed domain (e.g. "stripe.com")
    "linkedin_url": str,   # LinkedIn profile URL if found, else ""
  }

Domain is extracted by Claude Haiku from the company name — it does a good job
guessing public fintech companies. When uncertain it returns an empty string,
and the pipeline skips Hunter lookup for that speaker (no domain = no email).
"""

import os
import json
import re
import requests
from typing import Optional
from bs4 import BeautifulSoup
import anthropic
from dotenv import load_dotenv

load_dotenv()

_anthropic_client: Optional[anthropic.Anthropic] = None

SPEAKER_EXTRACTION_PROMPT = """You are extracting speaker information from a fintech conference page.

Given the following page content, extract ALL speakers and return a JSON array.

For each speaker include:
- "name": full name (string)
- "title": job title or role (string, empty string if not found)
- "company": employer / organisation name (string, empty string if not found)
- "domain": the company's website domain (e.g. "stripe.com", "monzo.com"). Guess from
  the company name using your knowledge of fintech companies. Use empty string if unknown.
- "linkedin_url": LinkedIn profile URL if present on the page, else empty string

Rules:
- Only include real named people with a company affiliation
- Skip keynote-only labels, moderators with no company listed, or venue/logistics staff
- If the same person appears multiple times, include them once
- Return ONLY the JSON array, no explanation

Page content:
{content}
"""


def _get_anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic_client


def _parse_with_haiku(content: str) -> list[dict]:
    """Use Claude Haiku to extract structured speaker data from raw page content."""
    if not content or len(content.strip()) < 100:
        return []

    # Truncate to avoid token limits (~120k chars is well within Haiku context)
    content = content[:120_000]

    client = _get_anthropic()
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": SPEAKER_EXTRACTION_PROMPT.format(content=content),
            }],
        )
    except Exception as e:
        print(f"    Haiku extraction error: {e}")
        return []

    text = message.content[0].text.strip()
    start = text.find("[")
    end   = text.rfind("]") + 1
    if start == -1 or end == 0:
        return []

    try:
        speakers = json.loads(text[start:end])
        return [s for s in speakers if s.get("name") and s.get("company")]
    except json.JSONDecodeError:
        return []


def _try_firecrawl(url: str) -> Optional[list[dict]]:
    """Stage 1: Firecrawl → clean markdown → Haiku extraction."""
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        return None

    try:
        from firecrawl import FirecrawlApp
        app = FirecrawlApp(api_key=api_key)
        result = app.scrape_url(url, params={"formats": ["markdown"]})
        content = result.get("markdown", "") if isinstance(result, dict) else ""
        if not content:
            return None
        print("    Stage 1 (Firecrawl) succeeded")
        return _parse_with_haiku(content)
    except Exception as e:
        print(f"    Stage 1 (Firecrawl) failed: {e}")
        return None


def _try_jina(url: str) -> Optional[list[dict]]:
    """Stage 2: Jina AI Reader → markdown → Haiku extraction."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {"Accept": "text/plain"}
        jina_key = os.environ.get("JINA_API_KEY", "")
        if jina_key:
            headers["Authorization"] = f"Bearer {jina_key}"
        resp = requests.get(jina_url, headers=headers, timeout=30)
        if resp.status_code != 200 or len(resp.text.strip()) < 200:
            return None
        print("    Stage 2 (Jina) succeeded")
        return _parse_with_haiku(resp.text)
    except Exception as e:
        print(f"    Stage 2 (Jina) failed: {e}")
        return None


def _try_bs_haiku(url: str) -> Optional[list[dict]]:
    """Stage 3: BeautifulSoup raw HTML → Haiku extraction."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; bot/1.0)"},
            timeout=20,
        )
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "head"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        if len(text.strip()) < 200:
            return None

        print("    Stage 3 (BS+Haiku) succeeded")
        return _parse_with_haiku(text)
    except Exception as e:
        print(f"    Stage 3 (BS+Haiku) failed: {e}")
        return None


def scrape_speakers(url: str) -> list[dict]:
    """
    Scrapes a speaker page and returns a list of speaker dicts.
    Tries Firecrawl → Jina → BS+Haiku in order, returns first success.
    """
    print(f"  Scraping speakers from: {url}")

    for stage_fn in [_try_firecrawl, _try_jina, _try_bs_haiku]:
        result = stage_fn(url)
        if result is not None:
            return result

    print("  All stages failed — returning empty list")
    return []
