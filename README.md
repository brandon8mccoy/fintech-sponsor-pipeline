# Fintech Event Sponsor Pipeline

A daily pipeline that scrapes fintech conference sponsor pages and outputs a clean company list to Google Sheets — ready for ICP filtering and contact enrichment in Clay.

**Live Google Sheet:** [View here](https://docs.google.com/spreadsheets/d/1YPml-EFTLLH0BeWczKLRtO6qVUDSgmlgO4aeza1owI4)

---

## The Signal

Gradient Labs' current highest performing signal is FinTech events. Building a system around scraping sponsors prior to the events gives the greatest lift to what already performs well. Sponsors at FinTech conferences are actively engaging with tools like Gradient Labs provides — the same profile as Gradient Labs' best customers. This pipeline automates company sourcing: identifying sponsors before the event so that any in-person meeting comes with contacts ready for warm outreach rather than a cold introduction.

The 90-day window before a conference is when these companies are most receptive. The pipeline is built to run daily — new sponsors are picked up as they're added to event pages, which typically happens in waves as the conference approaches. (For this demonstration, the cron job is not scheduled — but setup takes one command; see below.)

---

## How It Works

```
events.json
    │
    ▼
[Date Filter] ── 90-day rolling window
    │
    ▼
[Scraper] ── 3-tier fallback chain per event
    │   Stage 1: Firecrawl
    │   Stage 2: Jina AI Reader (JS-heavy pages)
    │   Stage 3: BeautifulSoup + Claude Haiku
    │
    ▼
[SQLite Dedup] ── Skip companies already scraped
    │
    ▼
[Google Sheets] ── Raw company list (one tab per event + Summary tab)
    │
    ▼
[Clay] ── ICP filter → contact finding → enrichment → outreach
```

Python handles scraping and deduplication. Clay handles everything downstream: ICP classification, waterfall email finding, and contact enrichment. This keeps the Python layer simple and lets Clay's native integrations (LinkedIn, Apollo, Hunter, Clearbit) do the heavy lifting on contact data.

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

## Results

| Event | Sponsors Scraped |
|---|---|
| Fintech Devcon | 57 |
| FinovateFall | 163 |
| **Total** | **220** |

Both events were scraped entirely from their web pages — no manual work required.

---

## Setup

### Prerequisites

- Python 3.9+
- API keys: Firecrawl (scraping), Jina AI (fallback)
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
FIRECRAWL_API_KEY=fc-...
GOOGLE_SHEET_ID=...
GOOGLE_CREDENTIALS_PATH=service_account.json
```

### Run

```bash
python3 pipeline.py          # events in the 90-day window
python3 pipeline.py --dry-run  # stub companies, no API calls
python3 pipeline.py --all      # ignore date filter
python3 reset.py && python3 pipeline.py  # fresh run
```

### Schedule (daily, macOS)

```bash
crontab -e
```

```
0 7 * * * cd "/Users/your-name/fintech-sponsor-pipeline" && python3 pipeline.py >> pipeline_cron.log 2>&1
```

Runs every morning at 7am. New sponsors added to event pages are picked up automatically.

---

## What I'd Build Next

1. **Render JS-heavy sponsor pages** — Money20/20 USA and IFGS load sponsors client-side, so the static-HTML scraper finds nothing. A headless render (Firecrawl's JS mode or Playwright) would unlock them.
2. **Automated Clay → sequence handoff** — trigger an outbound sequence (La Growth Machine, Lemlist, Amplemarket) the moment a contact clears ICP in Clay, with no manual steps
3. **Attendee targeting** — extend beyond sponsors to scrape attendee lists where available; speakers are often publicly listed and represent warm intros at the event
