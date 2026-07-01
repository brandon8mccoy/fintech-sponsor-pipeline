# Fintech Event Speaker Pipeline

A daily pipeline that scrapes fintech conference speaker pages, filters speaker companies against Gradient Labs' ICP, and surfaces verified emails in a Google Sheet — ready for warm outreach before the event.

**Live Google Sheet:** [View here](https://docs.google.com/spreadsheets/d/1YPml-EFTLLH0BeWczKLRtO6qVUDSgmlgO4aeza1owI4)

---

## The Signal

Gradient Labs' current highest performing signal is FinTech events. Speakers are a stronger signal than sponsors: they're pre-qualified decision-makers actively engaging with the industry, their job titles are listed publicly, and they're attending the event — which means an in-person meeting is possible. This pipeline automates speaker sourcing: identifying ICP-matched speakers and their emails before the event so every in-person touchpoint is backed by warm outreach.

The 90-day window before a conference is when these contacts are most receptive. The pipeline is built to run daily — new speakers are added to event pages as the conference approaches, and each run picks up anyone new.

---

## How It Works

```
events.json
    │
    ▼
[Date Filter] ── 90-day rolling window
    │
    ▼
[Speaker Scraper] ── 3-tier fallback chain per event
    │   Stage 1: Firecrawl  (clean markdown, JS-rendered pages)
    │   Stage 2: Jina AI    (JS-heavy fallback)
    │   Stage 3: BeautifulSoup + Claude Haiku (raw HTML fallback)
    │   → Extracts: name, title, company, domain, LinkedIn URL
    │
    ▼
[SQLite Dedup] ── Skip speakers already processed
    │
    ▼
[ICP Filter] ── Claude Haiku scores each speaker's company:
    │   Tier 1: Consumer-facing fintechs (neobanks, payments, lending, insurtech)
    │   Tier 2: SMB-facing fintechs (B2B payments, expense mgmt, embedded finance)
    │   Excluded: B2B data/API vendors, core banking platforms, enterprise RegTech
    │
    ▼
[Hunter Email Finder] ── 1 credit per speaker (name + domain → verified email)
    │   Capped at 20 lookups/run (daily throttle), 200 lifetime per event
    │
    ▼
[Google Sheets] ── One tab per event + Summary tab
    Speaker Name | Title | Company | Domain | Email | LinkedIn | Date | ✓ Actioned
```

**Why speakers over sponsors:** Sponsors are companies; speakers are people. The pipeline already knows the speaker's name and title, so Hunter's email-finder (not domain search) can return a verified email for the exact person — 1 credit instead of potentially many. Speakers are also often VP / C-suite level and pre-sorted by relevance to the conference theme.

---

## Events Covered

| Event | Date | Region |
|---|---|---|
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

## Setup

### Prerequisites

- Python 3.9+
- API keys: Anthropic, Firecrawl, Hunter.io
- Google Service Account with Sheets API access

### Install

```bash
git clone <repo-url>
cd fintech-speaker-pipeline
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
python3 pipeline.py            # events in the 90-day window
python3 pipeline.py --dry-run  # stub speakers, no API calls
python3 pipeline.py --all      # ignore date filter
python3 reset.py && python3 pipeline.py  # fresh run
```

### Schedule (daily, macOS)

```bash
crontab -e
```

```
0 7 * * * cd "/Users/your-name/fintech-speaker-pipeline" && python3 pipeline.py >> pipeline_cron.log 2>&1
```

Runs every morning at 7am. New speakers added to event pages are picked up automatically.

---

## What I'd Build Next

1. **LinkedIn enrichment** — speaker pages often include LinkedIn URLs; use those to pull current title, tenure, and connections before the event meeting
2. **Automated outreach trigger** — pipe ICP-matched speakers directly into a sequence (La Growth Machine, Amplemarket) the moment their email is found
3. **Attendee targeting** — extend beyond speakers to scrape attendee lists where publicly available; combine with speaker data for full event coverage
