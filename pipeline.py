"""
Fintech conference sponsor scraping pipeline.

Flow:
  1. Read events.json
  2. Filter to events within 90 days from today (no lower bound — recent past events included)
  3. Scrape sponsor page (PDF if available, then Firecrawl → Jina → BS+Claude fallback chain)
  4. Filter companies to fintech ICP using Claude Haiku
  5. Pull contacts from Apollo
  6. Write results to Google Sheets (one tab per event + Summary tab)
  7. Deduplicate via SQLite throughout

Flags:
  --dry-run   Skip real Apollo calls; use stub contacts for testing
  --all       Ignore date window; process every event in events.json
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

import db
import scraper
import icp_filter
import hunter_client as apollo_client  # swap: hunter_client is a drop-in for apollo_client
import sheets

load_dotenv()

EVENTS_FILE = Path("events.json")
WINDOW_DAYS = 90
MAX_CONTACTS_PER_EVENT = 20


def load_events() -> list[dict]:
    with open(EVENTS_FILE) as f:
        return json.load(f)


def within_window(event_date_str: str) -> bool:
    event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
    today = date.today()
    return event_date <= today + timedelta(days=WINDOW_DAYS)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip real Apollo calls and use stub contacts instead")
    parser.add_argument("--all", action="store_true",
                        help="Ignore date window; process every event in events.json")
    args = parser.parse_args()
    dry_run = args.dry_run
    process_all = args.all

    if dry_run:
        print("DRY RUN MODE — Apollo calls skipped, stub contacts used")
    if process_all:
        print("--all flag set: ignoring date window, processing all events")

    db.init_db()
    events = load_events()

    upcoming = events if process_all else [e for e in events if within_window(e["date"])]
    if not upcoming:
        print(f"No events within the next {WINDOW_DAYS} days. Update events.json or use --all.")
        return

    print(f"Processing {len(upcoming)} event(s) within the next {WINDOW_DAYS} days...")
    summary_rows = []

    for event in upcoming:
        name = event["name"]
        url = event["sponsor_url"]
        event_date = event["date"]
        print(f"\n{'='*60}")
        print(f"Event: {name}  ({event_date})")
        print(f"Sponsor URL: {url}")

        # Step 1: Scrape (use PDF if available, then fall back to web scraping)
        pdf_source = event.get("sponsor_pdf_path") or event.get("sponsor_pdf_url")
        if pdf_source:
            print(f"  Scraping via PDF + web fallback...")
        else:
            print("  Scraping sponsor page...")
        try:
            raw_companies = scraper.scrape_sponsors(url, pdf_source=pdf_source)
        except Exception as e:
            print(f"  Scrape failed: {e}")
            raw_companies = []

        print(f"  Found {len(raw_companies)} raw companies")

        # Step 2: Deduplicate into SQLite
        new_companies = []
        for company in raw_companies:
            is_new = db.upsert_company(
                name=company["name"],
                domain=company["domain"],
                event_name=name,
                sponsor_url=url,
            )
            if is_new:
                new_companies.append(company)

        print(f"  {len(new_companies)} new (not previously seen)")

        # Step 3: ICP filter
        print("  Running ICP filter with Claude Haiku...")
        if raw_companies:
            icp_companies = icp_filter.filter_fintech_icp(raw_companies)
        else:
            icp_companies = []

        print(f"  {len(icp_companies)} passed ICP filter")

        for company in icp_companies:
            db.mark_icp_passed(company["name"], name)

        # Step 4: Pull contacts from Apollo
        # Sort by ICP tier (Tier 1 first) so highest-fit companies fill the cap first
        icp_companies_sorted = sorted(icp_companies, key=lambda c: c.get("tier", 2))
        total_contacts = 0
        for company in icp_companies_sorted:
            if total_contacts >= MAX_CONTACTS_PER_EVENT:
                print(f"  Reached {MAX_CONTACTS_PER_EVENT} contact cap — skipping remaining companies")
                break
            print(f"  Pulling contacts for {company['name']} (Tier {company.get('tier', 2)})...")
            try:
                contacts = apollo_client.search_contacts(company["name"], company["domain"], dry_run=dry_run)
            except Exception as e:
                print(f"    Apollo error: {e}")
                contacts = []

            for contact in contacts:
                contact["event_name"] = name
                db.upsert_contact(contact)

            total_contacts += len(contacts)

        # Step 5: Write to Sheets
        all_contacts = db.get_contacts_for_event(name)
        all_icp = db.get_icp_companies(name)
        try:
            sheets.write_event_tab(name, all_contacts, all_icp)
        except Exception as e:
            print(f"  Sheets write failed: {e}")

        summary_rows.append({
            "event_name": name,
            "event_url": url,
            "event_date": event_date,
            "total_sponsors": len(raw_companies),
            "passed_icp": len(icp_companies),
            "contacts_found": len(all_contacts),
        })

    # Write summary tab
    try:
        sheets.write_summary_tab(summary_rows)
    except Exception as e:
        print(f"Summary tab write failed: {e}")

    print("\nPipeline complete.")
    for row in summary_rows:
        print(f"  {row['event_name']}: {row['total_sponsors']} sponsors → "
              f"{row['passed_icp']} ICP → {row['contacts_found']} contacts")


if __name__ == "__main__":
    run()
