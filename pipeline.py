"""
Fintech conference speaker pipeline.

Flow:
  1. Read events.json
  2. Filter to events within 90 days ahead (and up to PAST_GRACE_DAYS in the past)
  3. Scrape speaker page (Firecrawl → Jina → BS+Haiku fallback chain)
  4. Deduplicate via SQLite
  5. ICP filter on speaker companies (Claude Haiku)
  6. Hunter email-finder for ICP-passed speakers (1 credit per lookup)
  7. Write results to Google Sheets (one tab per event + Summary tab)

Flags:
  --dry-run   Skip real scrape/contact calls; use stub speakers for testing
  --all       Ignore date window; process every event in events.json
"""

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

import db
import speaker_scraper
import icp_filter
import hunter_client
import sheets

load_dotenv()

EVENTS_FILE    = Path("events.json")
WINDOW_DAYS    = 90
PAST_GRACE_DAYS = 30
MAX_EMAILS_PER_EVENT = 200   # lifetime ceiling across all runs
MAX_EMAILS_PER_RUN   = 20    # new email lookups per event per run (daily throttle)


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
                        help="Use stub speakers, no real API calls")
    parser.add_argument("--all", action="store_true",
                        help="Ignore date window; process every event in events.json")
    args = parser.parse_args()
    dry_run    = args.dry_run
    process_all = args.all

    if dry_run:
        print("DRY RUN MODE — stub speakers used")
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
        name        = event["name"]
        url         = event["speaker_url"]
        event_date  = event["date"]
        print(f"\n{'='*60}")
        print(f"Event: {name}  ({event_date})")
        print(f"Speaker URL: {url}")

        # ── Step 1: Scrape speakers ──────────────────────────────────────────
        if dry_run:
            raw_speakers = [
                {"name": "Jane Smith",   "title": "VP Customer Experience", "company": "Monzo",   "domain": "monzo.com",   "linkedin_url": ""},
                {"name": "Alex Johnson", "title": "Head of Operations",      "company": "Wise",    "domain": "wise.com",    "linkedin_url": ""},
                {"name": "Chris Lee",    "title": "CEO",                     "company": "Zego",    "domain": "zego.com",    "linkedin_url": ""},
            ]
        else:
            try:
                raw_speakers = speaker_scraper.scrape_speakers(url)
            except Exception as e:
                print(f"  Scrape failed: {e}")
                raw_speakers = []

        print(f"  Found {len(raw_speakers)} raw speakers")

        # ── Step 2: Dedup into SQLite ────────────────────────────────────────
        new_speakers = []
        for sp in raw_speakers:
            is_new = db.upsert_speaker(
                name=sp["name"],
                title=sp.get("title", ""),
                company=sp.get("company", ""),
                domain=sp.get("domain", ""),
                linkedin_url=sp.get("linkedin_url", ""),
                event_name=name,
                speaker_url=url,
            )
            if is_new:
                new_speakers.append(sp)

        print(f"  {len(new_speakers)} new speakers (not previously seen)")

        # ── Step 3: ICP filter on new speakers' companies ───────────────────
        if new_speakers and not dry_run:
            print("  Running ICP filter with Claude Haiku...")
            companies_to_filter = [
                {"name": sp["company"], "domain": sp.get("domain", "")}
                for sp in new_speakers
                if sp.get("company")
            ]
            try:
                icp_companies = icp_filter.filter_fintech_icp(companies_to_filter)
            except Exception as e:
                print(f"  ICP filter failed: {e}")
                icp_companies = []

            icp_company_names = {c["name"].lower() for c in icp_companies}
            icp_tiers = {c["name"].lower(): c.get("tier", 2) for c in icp_companies}

            for sp in new_speakers:
                co_lower = sp.get("company", "").lower()
                if co_lower in icp_company_names:
                    db.mark_icp_passed(sp["name"], name, icp_tiers.get(co_lower, 2))

            print(f"  {len(icp_companies)} new speaker companies passed ICP filter")
        elif dry_run:
            # In dry run, mark all stub speakers as ICP passed
            for sp in new_speakers:
                db.mark_icp_passed(sp["name"], name, 1)

        # ── Step 4: Hunter email-finder for ICP speakers ─────────────────────
        to_lookup = db.get_speakers_needing_email(name)
        emails_found_this_run = 0

        for sp in to_lookup:
            if emails_found_this_run >= MAX_EMAILS_PER_RUN:
                print(f"  Reached {MAX_EMAILS_PER_RUN}-lookup daily cap — stopping for this run")
                break
            if db.count_emails_found(name) >= MAX_EMAILS_PER_EVENT:
                print(f"  Reached {MAX_EMAILS_PER_EVENT}-email lifetime cap for event — stopping")
                break

            print(f"  Looking up email for {sp['name']} ({sp.get('company', '')} / {sp.get('domain', '')})...")
            try:
                email = hunter_client.find_speaker_email(sp["name"], sp.get("domain", ""), dry_run=dry_run)
            except Exception as e:
                print(f"    Hunter error: {e}")
                email = ""

            if email:
                print(f"    Found: {email}")
                emails_found_this_run += 1
            else:
                print(f"    No email found")

            db.mark_email_attempted(sp["name"], name, email)

        # ── Step 5: Write to Sheets ──────────────────────────────────────────
        icp_speakers = db.get_speakers(name)
        try:
            sheets.write_event_tab(name, icp_speakers, event_date)
        except Exception as e:
            print(f"  Sheets write failed: {e}")

        summary_rows.append({
            "event_name":    name,
            "event_url":     url,
            "event_date":    event_date,
            "total_speakers": db.count_speakers(name),
            "icp_speakers":  db.count_icp_speakers(name),
            "emails_found":  db.count_emails_found(name),
        })

    # ── Summary tab ──────────────────────────────────────────────────────────
    try:
        sheets.write_summary_tab(summary_rows)
    except Exception as e:
        print(f"Summary tab write failed: {e}")

    print("\nPipeline complete.")
    for row in summary_rows:
        print(f"  {row['event_name']}: {row['total_speakers']} speakers → "
              f"{row['icp_speakers']} ICP → {row['emails_found']} emails found")


if __name__ == "__main__":
    run()
