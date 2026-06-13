# Fintech Event Sponsor Pipeline

A daily pipeline that scrapes fintech conference sponsor pages, filters companies against Gradient Labs' ICP, and surfaces buying-committee contacts in a Google Sheet — ready for outreach before the event.

**Live Google Sheet:** [View here](https://docs.google.com/spreadsheets/d/1YPml-EFTLLH0BeWczKLRtO6qVUDSgmlgO4aeza1owI4)

---

## The Signal

Gradient Labs currently has been seeing success targeting companies at FinTech conferences. Companies that sponsor these events are growth-stage fintechs actively investing in business development, the same profile as Gradient Labs' best customers. This pipeline automates contact sourcing of event sponsors: identifying those companies and getting outreach in front of them before the event, so that any in-person meeting is a warm follow-up rather than a cold introduction.

The 90-day window before a conference is when these companies are most receptive. The pipeline runs daily so new sponsors are picked up as soon as they're added to event pages, which typically happens in waves as the conference approaches.

---

## How It Works

```
events.json
    │
    ▼
[Date Filter] ── 90-day rolling window
    │
    ▼
[Scraper] ── 4-tier fallback chain per event
    │   Stage 0: PDF (most reliable when available)
    │   Stage 1: Firecrawl
    │   Stage 2: Jina AI Reader (JS-heavy pages)
    │   Stage 3: BeautifulSoup + Claude Haiku
    │
    ▼
[SQLite Dedup] ── Skip companies already processed
    │
    ▼
[ICP Filter] ── Claude Haiku scores each company:
    │   Tier 1: Consumer-facing fintechs (neobanks, payments, lending, insurtech)
    │   Tier 2: SMB-facing fintechs (B2B payments, expense mgmt, embedded finance)
    │   Excluded: B2B data/API vendors, core banking platforms, enterprise RegTech
    │
    ▼
[Hunter.io] ── 2 contacts per company, Tier 1 first
    │   Priority 1: CX / Customer Operations / Support
    │   Priority 2: Operations / COO
    │   Priority 3: CEO / Founder
    │
    ▼
[Google Sheets] ── One tab per event + Summary tab
```

**On PDFs:** Some events publish their full sponsor list as a PDF, which yields significantly more complete data than web scraping alone (Money20/20 Europe: 424 companies from PDF vs. ~4 from the web page). When a PDF path is provided in `events.json`, the pipeline uses it first. When none is provided, the scraper auto-discovers a linked PDF on the sponsor page (scoring links by sponsor/exhibitor/brochure keywords) before falling back to the 4-tier web chain — so no manual input is required.

> **Note:** Local PDFs referenced by `sponsor_pdf_path` in `events.json` (e.g. `money2020_europe_2026_sponsors.pdf`) are gitignored (`*.pdf`) and are **not** committed to the repo. Place the file in the repo root before running; if it's missing, the pipeline logs a `[PDF] File read error` and falls through to web scraping rather than failing.

**On contact volume:** Contacts are capped at **200 per event** (lifetime) and **20 per event per run** (daily throttle). Each run pulls contacts for ICP companies that don't have them yet — newly discovered *and* a backfill of previously-seen ones — Tier 1 first, until the daily cap is hit. Over successive days an event fills toward 200 without spending the whole Hunter budget in one run. A company is only ever queried once (even if it yields no emails), so Hunter usage stays bounded. The caps are tunable via `MAX_CONTACTS_PER_EVENT` / `MAX_CONTACTS_PER_RUN` in `pipeline.py`; Hunter's free tier is small, so raising them or running daily on a free key will exhaust it.

---

## Events Covered

| Event | Date | Region |
|---|---|---|
| Money20/20 Europe 2026 | June 4, 2026 | EU |
| Fintech Devcon | Aug 3, 2026 | US |
| FinovateFall | Sep 9, 2026 | US |
| Sibos 2026 | Sep 29, 2026 | US |
| DC Fintech Week | Oct 5, 2026 | US |
| Money20/20 USA | Oct 18, 2026 | US |
| FinovateEurope | Feb 10, 2027 | EU |
| Innovate Finance Global Summit | Apr 20, 2027 | EU |
| Nordic Fintech Summit | May 13, 2027 | EU |
| Money20/20 Europe 2027 | Jun 2, 2027 | EU |

New events can be added to `events.json` — the pipeline picks them up on the next run once they enter the 90-day window.

---

## Results (first run)

| Event | Sponsors Scraped | Passed ICP | Contacts |
|---|---|---|---|
| Money20/20 Europe 2026 ¹ | 424 | 197 | 20 |
| Fintech Devcon | 57 | 22 | 20 |
| FinovateFall | 149 | 61 | 20 |
| **Total** | **630+** | **280+** | **60** |

¹ Gradient Labs attended Money20/20 Europe 2026. The event's published PDF sponsor list was used for this run, which is why it yielded significantly more companies. Fintech Devcon and FinovateFall were scraped entirely from their web pages — no PDF required.

---

## Setup

### Prerequisites

- Python 3.11+
- API keys: Anthropic, Firecrawl, Hunter.io
- Google Service Account with Sheets API access

### Install

```bash
git clone <repo-url>
cd fintech-sponsor-pipeline
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Fill in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
FIRECRAWL_API_KEY=fc-...
HUNTER_API_KEY=...
GOOGLE_SHEET_ID=...
GOOGLE_CREDENTIALS_PATH=service_account.json
```

### Run

```bash
python pipeline.py          # events in the 90-day window
python pipeline.py --dry-run  # stub contacts, no API calls
python pipeline.py --all      # ignore date filter
python reset.py && python pipeline.py  # fresh run
```

### Schedule (daily, macOS)

```bash
crontab -e
```

```
0 7 * * * cd "/path/to/fintech-sponsor-pipeline" && source venv/bin/activate && python pipeline.py >> pipeline_cron.log 2>&1
```

Runs every morning at 7am. New sponsors added to event pages are picked up automatically on the next run.

---

## What I'd Build Next

1. **Render JS-heavy sponsor pages** — Money20/20 USA and IFGS load sponsors client-side, so the static-HTML tiers (and PDF link discovery) find nothing. A headless render (Firecrawl's JS mode or Playwright) would unlock them.
2. **AI-written outreach copy** — once a contact is identified, use Claude to generate a personalized first touch based on the company, their event sponsorship, and Gradient Labs' value prop
3. **Automated sequence launch** — pipe new contacts directly into an outbound sequence (La Growth Machine, Lemlist, Amplemarket etc.) triggered the moment they're added to the sheet
4. **CRM sync** — push ICP accounts to HubSpot/Salesforce with event metadata so reps have full context before any conversation

