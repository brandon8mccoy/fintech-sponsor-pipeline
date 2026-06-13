import os
import re
import json
from typing import Optional
from urllib.parse import urljoin

import requests
import anthropic
from bs4 import BeautifulSoup
from firecrawl import FirecrawlApp
from dotenv import load_dotenv

import pdf_parser

load_dotenv()

FALLBACK_THRESHOLD = 10  # retry with next method if fewer than this many companies found

_firecrawl = None
_anthropic = None


def _get_firecrawl() -> FirecrawlApp:
    global _firecrawl
    if _firecrawl is None:
        _firecrawl = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])
    return _firecrawl


def _get_anthropic() -> anthropic.Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


def scrape_sponsors(url: str, pdf_source: str = None) -> list[dict]:
    """
    Scrapes a sponsor page using a four-stage fallback chain:
      0. PDF (explicit pdf_source, or one auto-discovered on the page)
      1. Firecrawl (markdown parser)
      2. Jina AI Reader (free, handles some JS pages differently)
      3. Direct requests + BeautifulSoup + Claude Haiku extraction
    Moves to the next stage if the current one returns < FALLBACK_THRESHOLD companies.
    """
    source_domain = _extract_domain(url)
    companies = []

    # Stage 0: PDF (highest priority when available).
    # An explicit path/URL from events.json is trusted outright; otherwise try to
    # auto-discover a linked PDF sponsor list on the page.
    manual_pdf = pdf_source is not None
    if not manual_pdf:
        pdf_source = _discover_pdf_link(url)
        if pdf_source:
            print(f"  [PDF] Auto-discovered sponsor PDF: {pdf_source[:80]}")

    if pdf_source:
        print(f"  [PDF] Reading from {pdf_source[:80]}...")
        pdf_companies = pdf_parser.extract_sponsors_from_pdf(pdf_source)
        # An explicitly-provided PDF is authoritative — return whatever it yields.
        if manual_pdf and pdf_companies:
            print(f"  [PDF] {len(pdf_companies)} companies extracted")
            return pdf_companies
        # An auto-discovered PDF is only trusted if it clears the threshold;
        # otherwise keep its results and let the web stages add to them.
        if len(pdf_companies) >= FALLBACK_THRESHOLD:
            print(f"  [PDF] {len(pdf_companies)} companies extracted")
            return pdf_companies
        companies = pdf_companies
        if companies:
            print(f"  [PDF] {len(companies)} companies — below threshold, merging with web scrape...")
        else:
            print("  [PDF] No companies extracted, falling through to web scrape...")

    # Stage 1: Firecrawl
    companies = _merge(companies, _try_firecrawl(url, source_domain))
    if len(companies) >= FALLBACK_THRESHOLD:
        print(f"  [Firecrawl] {len(companies)} companies found")
        return companies
    print(f"  [Firecrawl] {len(companies)} companies — below threshold, trying Jina AI...")

    # Stage 2: Jina AI Reader
    jina_companies = _try_jina(url, source_domain)
    # Merge: keep Firecrawl results and add any new ones from Jina
    companies = _merge(companies, jina_companies)
    if len(companies) >= FALLBACK_THRESHOLD:
        print(f"  [Jina AI] {len(jina_companies)} companies found — merged total: {len(companies)}")
        return companies
    print(f"  [Jina AI] {len(jina_companies)} companies — below threshold, trying BeautifulSoup + Claude...")

    # Stage 3: BeautifulSoup + Claude Haiku
    bs_companies = _try_bs_claude(url, source_domain)
    companies = _merge(companies, bs_companies)
    print(f"  [BS+Claude] {len(bs_companies)} companies found — merged total: {len(companies)}")
    return companies


# ---------------------------------------------------------------------------
# Stage 1: Firecrawl
# ---------------------------------------------------------------------------

def _try_firecrawl(url: str, source_domain: str) -> list[dict]:
    try:
        app = _get_firecrawl()
        result = app.scrape_url(url, formats=["markdown"])
        markdown = result.markdown if hasattr(result, "markdown") else ""
        return _parse_companies_from_markdown(markdown, source_domain)
    except Exception as e:
        print(f"  [Firecrawl] Error: {e}")
        return []


# ---------------------------------------------------------------------------
# Stage 2: Jina AI Reader
# ---------------------------------------------------------------------------

def _try_jina(url: str, source_domain: str) -> list[dict]:
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = requests.get(
            jina_url,
            headers={"Accept": "text/markdown", "X-Return-Format": "markdown"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [Jina AI] HTTP {resp.status_code}")
            return []
        return _parse_companies_from_markdown(resp.text, source_domain)
    except Exception as e:
        print(f"  [Jina AI] Error: {e}")
        return []


# ---------------------------------------------------------------------------
# Stage 3: BeautifulSoup + Claude Haiku
# ---------------------------------------------------------------------------

def _try_bs_claude(url: str, source_domain: str) -> list[dict]:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; sponsor-scraper/1.0)"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [BS+Claude] HTTP {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove boilerplate tags
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # Prefer the main content area; fall back to full body text
        main = soup.find("main") or soup.find("article") or soup.find(id="content") or soup.body
        text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)

        # Trim to ~6000 chars to stay within Haiku's efficient range
        text = text[:6000]

        return _extract_via_claude(text, source_domain)
    except Exception as e:
        print(f"  [BS+Claude] Error: {e}")
        return []


def _extract_via_claude(page_text: str, source_domain: str) -> list[dict]:
    prompt = f"""You are reading the text content of a conference sponsor page.
Extract all company names that appear to be sponsors or exhibitors.
For each company, make your best guess at their website domain (e.g. stripe.com, plaid.com).
Do NOT include the conference domain ({source_domain}) as any company's domain.

Return a JSON array of objects with keys "name" and "domain". Example:
[{{"name": "Stripe", "domain": "stripe.com"}}, {{"name": "Plaid", "domain": "plaid.com"}}]

Page text:
{page_text}

Return only the JSON array, no other text."""

    try:
        client = _get_anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        content = msg.content[0].text.strip()
        start, end = content.find("["), content.rfind("]") + 1
        if start == -1 or end == 0:
            return []
        raw = json.loads(content[start:end])
        return [
            {"name": item["name"].strip(), "domain": item.get("domain", "").strip().lower()}
            for item in raw
            if isinstance(item, dict)
            and item.get("name")
            and _looks_like_company(item["name"])
            and item.get("domain", "").strip().lower() != source_domain
        ]
    except Exception as e:
        print(f"  [BS+Claude] Claude extraction error: {e}")
        return []


# ---------------------------------------------------------------------------
# Stage 0 helper: PDF link auto-discovery
# ---------------------------------------------------------------------------

# Keywords that suggest an anchor points to a sponsor/exhibitor PDF list.
_PDF_LINK_KEYWORDS = (
    "sponsor", "exhibitor", "partner", "brochure",
    "prospectus", "list", "directory", "guide",
)


def _discover_pdf_link(url: str) -> Optional[str]:
    """
    Best-effort scan of a sponsor page's HTML for a link to a PDF sponsor list.
    Returns the absolute URL of the most likely PDF, or None.

    Only sees PDFs linked in the static HTML — JS-injected links won't appear,
    which is fine: this is a cheap pre-check before the web fallback chain.
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; sponsor-scraper/1.0)"},
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"  [PDF] Discovery request error: {e}")
        return None
    if resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    candidates = []  # (score, absolute_url)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Match .pdf even when followed by a query string or fragment.
        if not re.search(r"\.pdf(\?|#|$)", href, re.IGNORECASE):
            continue
        absolute = urljoin(url, href)
        haystack = f"{href} {a.get_text() or ''}".lower()
        score = sum(1 for kw in _PDF_LINK_KEYWORDS if kw in haystack)
        candidates.append((score, absolute))

    if not candidates:
        return None
    # Highest keyword score wins; ties keep document order (first link found).
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_companies_from_markdown(markdown: str, source_domain: str) -> list[dict]:
    """Extracts {name, domain} from markdown links, filtering out conference-domain links."""
    companies = []
    seen = set()

    link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')
    for match in link_pattern.finditer(markdown):
        name = match.group(1).strip()
        href = match.group(2).strip()
        domain = _extract_domain(href)

        if domain == source_domain:
            domain = _guess_domain(name)

        if not name or not _looks_like_company(name):
            continue

        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        companies.append({"name": name, "domain": domain})

    return companies


def _merge(base: list[dict], additions: list[dict]) -> list[dict]:
    """Adds companies from additions that aren't already in base (by name)."""
    existing = {c["name"].lower() for c in base}
    merged = list(base)
    for company in additions:
        if company["name"].lower() not in existing:
            existing.add(company["name"].lower())
            merged.append(company)
    return merged


def _extract_domain(url: str) -> str:
    match = re.match(r'https?://(?:www\.)?([^/\?#]+)', url)
    return match.group(1).lower() if match else ""


def _guess_domain(company_name: str) -> str:
    """Derives a best-effort domain from a company name."""
    name = company_name.lower()
    for suffix in [" inc.", " inc", " llc", " ltd.", " ltd", " corp.", " corp",
                   " co.", " co", " group", " technologies", " technology",
                   " solutions", " services", " global", " financial"]:
        name = name.replace(suffix, "")
    name = re.sub(r'[^a-z0-9]', '', name)
    return f"{name}.com" if name else ""


def _looks_like_company(text: str) -> bool:
    text = text.strip()
    if len(text) < 2 or len(text) > 80:
        return False
    noise = {"home", "about", "contact", "sponsors", "exhibitors", "register", "login",
             "sign in", "menu", "close", "back", "next", "previous", "read more",
             "learn more", "click here", "here", "website", "view all", "see all",
             "platinum", "gold", "silver", "bronze", "diamond", "sponsor", "exhibitor"}
    if text.lower() in noise:
        return False
    if not (text[0].isupper() or text[0].isdigit()):
        return False
    return True
