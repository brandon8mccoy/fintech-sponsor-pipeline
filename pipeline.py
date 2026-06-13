"""
Fintech conference sponsor scraping pipeline.

Flow:
  1. Read events.json
  2. Filter to events within 90 days ahead (and up to PAST_GRACE_DAYS in the past)
  3. Scrape sponsor page (PDF if available, then Firecrawl → Jina → BS+Claude fallback chain)
  4. Filter companies to fintech ICP using Claude Haiku
  5. Pull contacts from Hunter.io
  6. Write results to Google Sheets (one tab per event + Summary tab)
  7. Deduplicate via SQLite throughout

Flags:
  --dry-run   Skip real contact-provider calls; use stub contacts for testing
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
import hunter_client as contact_client  # Hunter.io is the current contact provider
import sheets

load_dotenv()

EVENTS_FILE = Path("events.json")
WINDOW_DAYS = 90
# Keep a just-finished event in scope briefly (warm follow-up window), but drop
# events older than this so stale entries in events.json aren't re-scraped forever.
PAST_GRACE_DAYS = 30
MAX_CONTACTS_PER_EVENT = 20


def load_events() -> list[dict]:
    with open(EVENTS_FILE) as f:
        return json.load(f)


def within_window(event_date_str: str) -> bool:
    """In scope if the event is upcoming within WINDOW_DAYS, or finished no more
    than PAST_GRACE_DAYS ago. The lower bound stops stale past events from being
    re-scraped (and re-spending API calls) on every daily run."""
    event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
    today = date.today()
    return today - timedelta(days=PAST_GRACE_DAYS) <= event_date <= today + timedelta(days=WINDOW_DAYS)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip real contact-provider calls and use stub contacts instead")
    parser.add_argument("--all", action="store_true",
                        help="Ignore date window; process every event in events.json")
    args = parser.parse_args()
    dry_run = args.dry_run
    process_all = args.all

    if dry_run:
        print("DRY RUN MODE — contact lookups skipped, stub contacts used")
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

        # Step 3: ICP filter — only classify companies new to this run.
        # Previously-seen companies keep their stored passed_icp state, so we
        # don't re-spend Haiku calls on them every day.
        print("  Running ICP filter with Claude Haiku...")
        if new_companies:
            icp_companies = icp_filter.filter_fintech_icp(new_companies)
        else:
            icp_companies = []

        print(f"  {len(icp_companies)} new companies passed ICP filter")

        for company in icp_companies:
            db.mark_icp_passed(company["name"], name)

        # Step 4: Pull contacts from Hunter.io for the new ICP companies only.
        # Sort by ICP tier (Tier 1 first) so highest-fit companies fill the cap first.
        # The cap counts contacts already stored for this event, so re-runs keep
        # the per-event total at MAX_CONTACTS_PER_EVENT rather than adding 20 more.
        icp_companies_sorted = sorted(icp_companies, key=lambda c: c.get("tier", 2))
        total_contacts = len(db.get_contacts_for_event(name))
        for company in icp_companies_sorted:
            if total_contacts >= MAX_CONTACTS_PER_EVENT:
                print(f"  Reached {MAX_CONTACTS_PER_EVENT} contact cap — skipping remaining companies")
                break
            print(f"  Pulling contacts for {company['name']} (Tier {company.get('tier', 2)})...")
            try:
                contacts = contact_client.search_contacts(company["name"], company["domain"], dry_run=dry_run)
            except Exception as e:
                print(f"    Contact lookup error: {e}")
                contacts = []

            for contact in contacts:
                contact["event_name"] = name
                # upsert_contact returns False when the email already exists, so the
                # same person is never written twice and only fresh inserts count
                # toward the cap.
                if db.upsert_contact(contact):
                    total_contacts += 1

        # Step 5: Write to Sheets
        all_contacts = db.get_contacts_for_event(name)
        all_icp = db.get_icp_companies(name)
        try:
            sheets.write_event_tab(name, all_contacts, all_icp)
        except Exception as e:
            print(f"  Sheets write failed: {e}")

        # Report cumulative DB totals so the summary reflects everything seen for
        # the event across runs, not just the companies new in this run.
        summary_rows.append({
            "event_name": name,
            "event_url": url,
            "event_date": event_date,
            "total_sponsors": db.count_companies(name),
            "passed_icp": len(all_icp),
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
