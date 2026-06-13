# Fintech Event Sponsor Pipeline

A weekly pipeline that scrapes fintech conference sponsor pages, filters companies against Gradient Labs' ICP, and surfaces buying-committee contacts in a Google Sheet — ready for outreach before the event.

**Live Google Sheet:** [View here](https://docs.google.com/spreadsheets/d/1YPml-EFTLLH0BeWczKLRtO6qVUDSgmlgO4aeza1owI4)

---

## The Signal

Gradient Labs currently has been seeing success targeting companies at FinTech conferences. Companies that sponsor these events are growth-stage fintechs actively investing in business development, the same profile as Gradient Labs' best customers. This pipeline automates contact sourcing of event sponsors: identifying those companies and getting outreach in front of them before the event, so that any in-person meeting is a warm follow-up rather than a cold introduction.

The 90-day window before a conference is when these companies are most receptive. The pipeline runs weekly to catch new sponsors as they're added.

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

> **Note:** contacts are capped at 20 per event for this demo. In production, the pipeline pulls the full ICP list with no cap.

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
| Money20/20 Europe 2026 | 424 (PDF) | 197 | 20 |
| Fintech Devcon | 57 | 22 | 20 |
| FinovateFall | 149 | 61 | 20 |
| **Total** | **630+** | **280+** | **60** |

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

### Schedule (weekly, macOS)

```bash
crontab -e
```

```
0 7 * * 1 cd "/path/to/fintech-sponsor-pipeline" && source venv/bin/activate && python pipeline.py >> pipeline_cron.log 2>&1
```

Runs every Monday at 7am. New sponsors added to event pages mid-cycle are picked up automatically.

---

## What I'd Build Next

1. **AI-written outreach copy** — once a contact is identified, use Claude to generate a personalized first touch based on the company, their event sponsorship, and Gradient Labs' value prop
2. **Automated sequence launch** — pipe new contacts directly into an outbound sequence (La Growth Machine, Lemlist, Amplemarket etc.) triggered the moment they're added to the sheet

