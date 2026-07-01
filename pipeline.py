"""
Fintech conference sponsor scraping pipeline.

Flow:
  1. Read events.json
  2. Filter to events within 90 days ahead (and up to PAST_GRACE_DAYS in the past)
  3. Scrape sponsor page (Firecrawl → Jina → BS fallback chain)
  4. Deduplicate via SQLite
  5. Write raw company list to Google Sheets (one tab per event + Summary tab)

ICP filtering and contact enrichment are handled downstream in Clay.

Flags:
  --dry-run   Use stub companies for testing, no real scrape
  --all       Ignore date window; process every event in events.json
"""

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

import db
import scraper
import sheets

load_dotenv()

EVENTS_FILE = Path("events.json")
WINDOW_DAYS = 90
PAST_GRACE_DAYS = 30


def load_events() -> list[dict]:
    with open(EVENTS_FILE) as f:
        return json.load(f)


def within_window(event_date_str: str) -> bool:
    event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
    today = date.today()
    return today - timedelta(days=PAST_GRACE_DAYS) <= event_date <= today + timedelta(days=WINDOW_DAYS)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Use stub companies, no real scrape")
    parser.add_argument("--all", action="store_true",
                        help="Ignore date window; process every event in events.json")
    args = parser.parse_args()
    dry_run = args.dry_run
    process_all = args.all

    if dry_run:
        print("DRY RUN MODE — stub companies used")
    if process_all:
        print("--all flag set: ignoring date window, processing all events")

    db.init_db()
    events = load_events()

    upcoming = events if process_all else [e for e in events if within_window(e["date"])]
    if not upcoming:
        print(f"No events within the next {WINDOW_DAYS} days. Update events.json or use --all.")
        return

    print(f"Processing {len(upcoming)} event(s)...")
    summary_rows = []

    for event in upcoming:
        name = event["name"]
        url = event["sponsor_url"]
        event_date = event["date"]
        print(f"\n{'='*60}")
        print(f"Event: {name}  ({event_date})")
        print(f"Sponsor URL: {url}")

        # Scrape
        if dry_run:
            raw_companies = [
                {"name": "Stub Company A", "domain": "stub-a.com"},
                {"name": "Stub Company B", "domain": "stub-b.com"},
            ]
        else:
            pdf_source = event.get("sponsor_pdf_path") or event.get("sponsor_pdf_url")
            try:
                raw_companies = scraper.scrape_sponsors(url, pdf_source=pdf_source)
            except Exception as e:
                print(f"  Scrape failed: {e}")
                raw_companies = []

        print(f"  Found {len(raw_companies)} raw companies")

        # Dedup into SQLite
        new_count = 0
        for company in raw_companies:
            if db.upsert_company(
                name=company["name"],
                domain=company["domain"],
                event_name=name,
                sponsor_url=url,
            ):
                new_count += 1

        print(f"  {new_count} new (not previously seen)")

        # Write all companies for this event to sheet
        all_companies = db.get_companies(name)
        try:
            sheets.write_event_tab(name, all_companies, event_date)
        except Exception as e:
            print(f"  Sheets write failed: {e}")

        summary_rows.append({
            "event_name": name,
            "event_url": url,
            "event_date": event_date,
            "total_sponsors": db.count_companies(name),
        })

    # Write summary tab
    try:
        sheets.write_summary_tab(summary_rows)
    except Exception as e:
        print(f"Summary tab write failed: {e}")

    print("\nPipeline complete.")
    for row in summary_rows:
        print(f"  {row['event_name']}: {row['total_sponsors']} sponsors scraped")


if __name__ == "__main__":
    run()
