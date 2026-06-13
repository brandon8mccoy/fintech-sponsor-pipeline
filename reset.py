"""
Resets the pipeline for a clean run:
  1. Deletes pipeline.db (all scraped companies, contacts, dedup state)
  2. Clears all tabs in the Google Sheet
"""

import os
import pathlib
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── 1. Delete SQLite DB ───────────────────────────────────────────────────────

db_path = pathlib.Path("pipeline.db")
if db_path.exists():
    db_path.unlink()
    print("✓ Deleted pipeline.db")
else:
    print("  pipeline.db not found (already clean)")

# ── 2. Clear Google Sheet ─────────────────────────────────────────────────────

creds = Credentials.from_service_account_file(
    os.environ["GOOGLE_CREDENTIALS_PATH"], scopes=SCOPES
)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])

sheets = spreadsheet.worksheets()
print(f"\nFound {len(sheets)} tab(s) in the sheet: {[ws.title for ws in sheets]}")

# Keep at least one sheet (Sheets API requires minimum 1 tab)
for i, ws in enumerate(sheets):
    if i == 0:
        ws.clear()
        ws.update_title("Summary")
        print(f"  Cleared and renamed first tab → 'Summary'")
    else:
        spreadsheet.del_worksheet(ws)
        print(f"  Deleted tab: '{ws.title}'")

print("\n✓ Sheet reset complete. Ready for a fresh pipeline run.")
