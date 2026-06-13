# Fintech Event Sponsor Pipeline

A daily pipeline that identifies fintech companies sponsoring industry conferences, filters them against Gradient Labs' ICP, and surfaces buying-committee contacts in a Google Sheet — ready for outreach before the event.

---

## The Signal: Why Event Sponsorship Predicts Purchase Intent

Event sponsorship is already one of Gradient Labs' best-performing channels. The team sponsors and attends these conferences because the buyers are there — companies actively investing in their customer-facing operations, looking for tools that help them scale. This pipeline is **More** of what's already working: automating the manual work of identifying and reaching those same buyers before the event, at scale.

A fintech company that pays to sponsor an industry conference has, by definition:

- **Active budget allocated** to business development — sponsorship is a strategic spend, not a tactical one
- **A decision-maker attending** who can be reached before, during, or after the event
- **Demonstrated growth ambition** — these companies are investing in being seen, which means they're investing in growth
- **The exact profile of a Gradient Labs customer** — consumer-facing fintechs scaling their operations, generating support volume, and looking for tools to handle it

The pipeline targets the 90-day window before each event. That window is an outreach opportunity: warm contacts before the conference floor, so that when Gradient Labs meets them in person, it's a follow-up — not a cold introduction. Companies sponsoring these events are already looking for products like Gradient Labs. This surfaces them systematically and gets outreach in front of them first.

**Why this signal over others:** Tested three alternatives — FCA new authorizations (too early in lifecycle, no support volume yet), complaints handler job postings (~41 live roles at any given time in UK fintech, too low volume), and funding + FCA filter (commoditized, replicable by any Sales Nav user). Event sponsorship is harder to automate — JS-heavy pages, PDF-only lists, no clean API — which is exactly what makes it defensible.

---

## Architecture

```
events.json
    │
    ▼
[Date Filter] ── 90-day rolling window, no lower bound
    │
    ▼
[Scraper] ── 4-tier fallback chain per event
    │   Stage 0: PDF (if event provides a sponsor PDF)
    │   Stage 1: Firecrawl (handles most static pages)
    │   Stage 2: Jina AI Reader (JS-heavy pages)
    │   Stage 3: BeautifulSoup + Claude Haiku (last resort)
    │
    ▼
[SQLite Dedup] ── Skip companies already seen for this event
    │
    ▼
[ICP Filter] ── Claude Haiku classifies each company with tier scoring:
    │   Tier 1: Consumer-facing fintechs (neobanks, payments, lending, insurtech)
    │   Tier 2: SMB-facing fintechs (B2B payments, expense mgmt, embedded finance)
    │   ✗ Exclude: B2B data/API vendors, core banking platforms, enterprise RegTech
    │   ✓ Size guidance: 30–2,000 employees
    │
    ▼
[Hunter.io Contacts] ── 2 contacts per company, Tier 1 companies first, capped at 20/event
    │   Priority 1: Head of CX / VP Customer Operations / Support Lead
    │   Priority 2: Head of Operations / COO
    │   Priority 3: CEO / Founder (fallback)
    │
    ▼
[Google Sheets Output]
    Summary tab: all events × companies × contacts
    Per-event tabs: Company | Website | Size | Contact | Title | Email | LinkedIn | Date Scraped | Outreach Done?
```

---

## Output

**Live Google Sheet:** [View here](https://docs.google.com/spreadsheets/d/1YPml-EFTLLH0BeWczKLRtO6qVUDSgmlgO4aeza1owI4)

The sheet is updated on every pipeline run. Each row is one contact at one ICP-matched company. The `Outreach Done?` column gives reps a lightweight tracking field — no CRM required for the first touch.

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

## Setup

### Prerequisites

- Python 3.11+
- API keys for: Anthropic, Firecrawl, Hunter.io (free tier supported)
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

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```
ANTHROPIC_API_KEY=sk-ant-...
FIRECRAWL_API_KEY=fc-...
HUNTER_API_KEY=...
GOOGLE_SHEET_ID=...
GOOGLE_CREDENTIALS_PATH=service_account.json
```

Place your Google service account JSON at `service_account.json` (see [Google Sheets API docs](https://docs.gspread.org/en/latest/oauth2.html)).

### Run

```bash
# Normal run (events within 90-day window)
python pipeline.py

# Test without real API calls (stub contacts)
python pipeline.py --dry-run

# Process all events regardless of date
python pipeline.py --all

# Wipe DB and clear sheet before a fresh run
python reset.py && python pipeline.py
```

### Schedule (weekly, macOS)

```bash
crontab -e
```

Add:
```
0 7 * * 1 cd "/path/to/fintech-sponsor-pipeline" && source venv/bin/activate && python pipeline.py >> pipeline_cron.log 2>&1
```

This runs every Monday at 7am, checks for new sponsors across all events in the 90-day window, and updates the Google Sheet automatically. New sponsors added to event pages mid-cycle are picked up on the next run.

---

## Results (as of first run)

| Event | Sponsors Scraped | Passed ICP Filter | Contacts |
|---|---|---|---|
| Money20/20 Europe 2026 | 424 (PDF) | 197 | 20 |
| Fintech Devcon | 57 | 22 | 20 |
| FinovateFall | 149 | 61 | 20 |
| **Total** | **630+** | **280+** | **60** |

Each contact has a verified email address and LinkedIn URL sourced via Hunter.io, prioritizing CX/Support and Operations roles — the buying committee most likely to evaluate a CX platform like Gradient Labs.

---

## Design Decisions

**Why event sponsorship over other signals?**
Tested three alternatives before landing here: (1) FCA new authorizations — too early in company lifecycle, no support volume yet; (2) complaints handler job postings — only ~41 UK fintech-specific roles live at any given time, too low volume; (3) funding + FCA filter — commoditized, any Sales Nav user can replicate it. Event sponsorship is harder to automate (JS-heavy pages, PDF-only lists, no clean API) which is exactly why it's defensible.

**Why the 4-tier scraper fallback?**
Event sponsor pages are among the hardest pages to scrape — they're typically JS-rendered marketing pages, sometimes PDF-only, and frequently behind CDNs. Firecrawl handles ~70% of cases. Jina AI handles most of the rest. The BS+Claude tier catches what's left. The PDF tier is the most reliable when available — Money20/20 Europe yields 424 companies from PDF vs. 4 from direct scraping.

**Why Claude Haiku for ICP filtering instead of a rules-based filter?**
Company descriptions are ambiguous. "Nuvei" could be a payments company (ICP) or an enterprise ERP vendor (not ICP) depending on context. Haiku reads the actual company description and makes a judgment call — this catches edge cases that keyword matching misses. Cost per run is under $0.10.

**Why Hunter.io for contacts instead of Apollo?**
Apollo's People Search API requires a paid plan tier above the $149/mo basic plan — all contact lookups return `API_INACCESSIBLE` at that level. Hunter.io's free tier supports domain-level email searches with no such restriction. It returns verified emails with names and job titles, which is sufficient for identifying the buying committee. The `hunter_client.py` module is a drop-in replacement for `apollo_client.py` — same function signature, same output dict shape.

**Why cap at 20 contacts per event?**
Hunter.io's free plan allows ~100 searches/month. With 3 active events and 2 contacts per company, a 20-contact cap uses ~10 credits per event. This keeps the pipeline sustainable on a free tier while still producing a meaningful output (60 actionable contacts across events). The cap is tier-sorted — Tier 1 (consumer-facing fintechs, Gradient Labs' best-fit accounts) fill the cap first.

**Why SQLite deduplication?**
The pipeline runs weekly. Sponsor lists are stable between runs, but new sponsors are added to event pages as the conference approaches. Without dedup, every run would re-process the same 400 companies and re-hit Hunter for the same contacts. SQLite tracks which companies have been seen per event and which contacts have been fetched, so each run only does incremental work on genuinely new additions.

---

## What I'd Build Next

1. **Enrich with live signals on top of the sponsor list** — cross-reference ICP companies from the sponsor list against FCA new authorization dates, recent funding rounds, and Trustpilot rating velocity to tier accounts by urgency
2. **Automated outreach sequencing** — trigger a personalized Apollo sequence the moment a new ICP company is added, with event-specific messaging ("saw you're sponsoring Money20/20...")
3. **Sponsor announcement monitoring** — watch event websites and press for new sponsor announcements rather than relying on a static sponsor page, to catch companies earlier in the cycle
4. **CRM sync** — push ICP accounts directly to HubSpot/Salesforce with event metadata as a custom field
