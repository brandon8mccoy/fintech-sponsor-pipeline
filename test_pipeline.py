"""
Offline unit tests for the fintech sponsor pipeline.

These exercise only pure logic + a temp SQLite DB. No network, no API keys,
no Google Sheets, no live scraping. Run with:  pytest -q
"""
import importlib
from datetime import date, timedelta
from unittest import mock

import pytest

import scraper
import hunter_client
import pdf_parser
import pipeline
from pipeline import PAST_GRACE_DAYS
import db


# ---------------------------------------------------------------------------
# scraper: domain + name helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.stripe.com/about", "stripe.com"),
    ("http://plaid.com", "plaid.com"),
    ("https://sub.example.co.uk/path?q=1", "sub.example.co.uk"),
    ("not-a-url", ""),
])
def test_extract_domain(url, expected):
    assert scraper._extract_domain(url) == expected


@pytest.mark.parametrize("name,expected", [
    ("Stripe Inc.", "stripe.com"),
    ("ACME Financial", "acme.com"),
    ("Foo Technologies", "foo.com"),
    ("", ""),
])
def test_guess_domain(name, expected):
    assert scraper._guess_domain(name) == expected


@pytest.mark.parametrize("text,ok", [
    ("Stripe", True),
    ("Plaid", True),
    ("a", False),            # too short
    ("x" * 81, False),       # too long
    ("Platinum", False),     # noise word
    ("learn more", False),   # noise word
    ("lowercase", False),    # must start uppercase/digit
    ("7Shifts", True),       # starts with digit -> allowed
])
def test_looks_like_company(text, ok):
    assert scraper._looks_like_company(text) is ok


def test_parse_companies_from_markdown_filters_conference_domain():
    md = (
        "[Stripe](https://stripe.com) and "
        "[Plaid](https://plaid.com) sponsor "
        "[Register](https://conf.com/register) at "
        "[ConfSelfLink](https://conf.com/home)"
    )
    out = scraper._parse_companies_from_markdown(md, "conf.com")
    names = {c["name"] for c in out}
    # Real companies kept
    assert "Stripe" in names and "Plaid" in names
    # "Register" is a noise word -> filtered
    assert "Register" not in names
    # A link whose name passes but points to conference domain gets a guessed domain
    self_link = next((c for c in out if c["name"] == "ConfSelfLink"), None)
    if self_link:
        assert self_link["domain"] != "conf.com"


def test_parse_companies_dedupes_by_name():
    md = "[Stripe](https://stripe.com) [Stripe](https://stripe.com/x)"
    out = scraper._parse_companies_from_markdown(md, "conf.com")
    assert len([c for c in out if c["name"] == "Stripe"]) == 1


def test_merge_adds_only_new_names():
    base = [{"name": "Stripe", "domain": "stripe.com"}]
    additions = [
        {"name": "stripe", "domain": "other.com"},  # dup (case-insensitive)
        {"name": "Plaid", "domain": "plaid.com"},
    ]
    out = scraper._merge(base, additions)
    assert [c["name"] for c in out] == ["Stripe", "Plaid"]


# ---------------------------------------------------------------------------
# scraper: PDF link auto-discovery (requests mocked)
# ---------------------------------------------------------------------------

def _fake_resp(status=200, text=""):
    m = mock.Mock()
    m.status_code = status
    m.text = text
    return m


def test_discover_pdf_link_scores_keywords():
    html = """
    <a href="/files/random.pdf">download</a>
    <a href="/files/exhibitor-sponsor-list.pdf">Exhibitor Sponsor List</a>
    <a href="/page.html">not a pdf</a>
    """
    with mock.patch.object(scraper.requests, "get", return_value=_fake_resp(text=html)):
        link = scraper._discover_pdf_link("https://conf.com/sponsors")
    # The keyword-rich link should win and be absolute
    assert link == "https://conf.com/files/exhibitor-sponsor-list.pdf"


def test_discover_pdf_link_matches_pdf_with_query_string():
    html = '<a href="https://cdn.com/list.pdf?v=2">Sponsor brochure</a>'
    with mock.patch.object(scraper.requests, "get", return_value=_fake_resp(text=html)):
        link = scraper._discover_pdf_link("https://conf.com/sponsors")
    assert link == "https://cdn.com/list.pdf?v=2"


def test_discover_pdf_link_none_when_no_pdf():
    html = '<a href="/about.html">about</a>'
    with mock.patch.object(scraper.requests, "get", return_value=_fake_resp(text=html)):
        assert scraper._discover_pdf_link("https://conf.com/sponsors") is None


def test_discover_pdf_link_handles_request_error():
    with mock.patch.object(scraper.requests, "get",
                           side_effect=scraper.requests.RequestException("boom")):
        assert scraper._discover_pdf_link("https://conf.com/sponsors") is None


# ---------------------------------------------------------------------------
# hunter_client: title scoring + dry-run stubs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,score", [
    ("Head of Customer Experience", 1),
    ("VP Support", 1),
    ("Chief Operating Officer", 2),
    ("Director of Operations", 2),
    ("CEO & Founder", 3),
    ("Software Engineer", 99),
    ("", 99),
])
def test_score_title(title, score):
    assert hunter_client._score_title(title) == score


def test_search_contacts_dry_run_returns_two_stubs_no_network():
    # Patch requests.get to explode — dry_run must never call it.
    with mock.patch.object(hunter_client.requests, "get",
                           side_effect=AssertionError("network called in dry-run!")):
        contacts = hunter_client.search_contacts("Stripe", "stripe.com", dry_run=True)
    assert len(contacts) == 2
    assert contacts[0]["email"] == "p1@stripe.com"
    assert contacts[1]["email"] == "p2@stripe.com"
    assert contacts[0]["company_name"] == "Stripe"


def test_search_contacts_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    assert hunter_client.search_contacts("Stripe", "stripe.com", dry_run=False) == []


# ---------------------------------------------------------------------------
# pdf_parser: partial JSON recovery
# ---------------------------------------------------------------------------

def test_extract_partial_objects_recovers_from_truncation():
    truncated = '[{"name": "Stripe", "domain": "stripe.com"}, {"name": "Plaid", "doma'
    out = pdf_parser._extract_partial_objects(truncated)
    assert out == [{"name": "Stripe", "domain": "stripe.com"}]


def test_extract_partial_objects_empty_on_garbage():
    assert pdf_parser._extract_partial_objects("no json here") == []


# ---------------------------------------------------------------------------
# pipeline: date windowing
# ---------------------------------------------------------------------------

def test_within_window_future_event_inside_90_days():
    soon = (date.today() + timedelta(days=30)).isoformat()
    assert pipeline.within_window(soon) is True


def test_within_window_future_event_outside_90_days():
    far = (date.today() + timedelta(days=120)).isoformat()
    assert pipeline.within_window(far) is False


def test_within_window_recent_past_inside_grace():
    # Just-finished events stay in scope for the warm-follow-up grace period.
    recent = (date.today() - timedelta(days=PAST_GRACE_DAYS - 1)).isoformat()
    assert pipeline.within_window(recent) is True


def test_within_window_stale_past_excluded():
    # Events older than the grace period are dropped (no longer re-scraped forever).
    stale = (date.today() - timedelta(days=PAST_GRACE_DAYS + 5)).isoformat()
    assert pipeline.within_window(stale) is False


# ---------------------------------------------------------------------------
# db: dedup + cap logic against a temp SQLite file
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db


def test_upsert_company_dedupes(temp_db):
    assert temp_db.upsert_company("Stripe", "stripe.com", "EventA", "u") is True
    # Same (name, event) -> not new
    assert temp_db.upsert_company("Stripe", "stripe.com", "EventA", "u") is False
    # Same name, different event -> new (per-event dedup)
    assert temp_db.upsert_company("Stripe", "stripe.com", "EventB", "u") is True


def test_mark_icp_and_get_icp_companies(temp_db):
    temp_db.upsert_company("Stripe", "stripe.com", "EventA", "u")
    temp_db.upsert_company("BigBank", "bigbank.com", "EventA", "u")
    temp_db.mark_icp_passed("Stripe", "EventA")
    icp = temp_db.get_icp_companies("EventA")
    assert [c["name"] for c in icp] == ["Stripe"]


def test_upsert_contact_unique_per_event(temp_db):
    c = {
        "company_name": "Stripe", "event_name": "EventA",
        "first_name": "A", "last_name": "B", "title": "CX",
        "email": "a@stripe.com", "linkedin_url": "", "apollo_id": "",
    }
    assert temp_db.upsert_contact(dict(c)) is True
    # Same email, same event -> ignored (False), keeps the per-event cap correct
    assert temp_db.upsert_contact(dict(c)) is False
    assert len(temp_db.get_contacts_for_event("EventA")) == 1
    # Same email, DIFFERENT event -> inserted, so the person can appear on both tabs
    c_other = dict(c, event_name="EventB")
    assert temp_db.upsert_contact(c_other) is True
    assert len(temp_db.get_contacts_for_event("EventB")) == 1


def test_count_companies(temp_db):
    temp_db.upsert_company("Stripe", "stripe.com", "EventA", "u")
    temp_db.upsert_company("Plaid", "plaid.com", "EventA", "u")
    assert temp_db.count_companies("EventA") == 2
    assert temp_db.count_companies("EventB") == 0
