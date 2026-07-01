"""
Google Sheets output module.

Sheet structure:
  Summary tab  — one row per event (Event Name, URL, Date, Companies Scraped)
  Event tabs   — one row per company (Company Name, Domain, Event, Event Date, Date Scraped)

ICP filtering and contact enrichment happen downstream in Clay.
"""

import os
from datetime import date
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Column definitions ────────────────────────────────────────────────────────

EVENT_HEADERS = [
    "Company Name", "Domain", "Event", "Event Date", "Date Scraped",
]

SUMMARY_HEADERS = [
    "Event Name", "Event URL", "Event Date", "Companies Scraped",
]

# ── Colours (Sheets API uses 0-1 float RGB) ───────────────────────────────────

_NAVY  = {"red": 0.11, "green": 0.17, "blue": 0.29}   # #1C2B4A
_WHITE = {"red": 1.00, "green": 1.00, "blue": 1.00}
_LIGHT = {"red": 0.95, "green": 0.97, "blue": 1.00}   # very-light blue-gray


# ── Auth / helpers ────────────────────────────────────────────────────────────

def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_CREDENTIALS_PATH"],
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


def _get_or_create_sheet(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=2000, cols=len(EVENT_HEADERS) + 2)


# ── Formatting helpers (Sheets batchUpdate requests) ─────────────────────────

def _req_freeze(sheet_id: int, rows: int = 1) -> dict:
    return {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": rows}},
            "fields": "gridProperties.frozenRowCount",
        }
    }


def _req_header_format(sheet_id: int, num_cols: int) -> dict:
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _NAVY,
                "textFormat": {"bold": True, "foregroundColor": _WHITE, "fontSize": 10},
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "CLIP",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
        }
    }


def _req_banding(sheet_id: int, num_cols: int) -> dict:
    return {
        "addBanding": {
            "bandedRange": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1,
                          "startColumnIndex": 0, "endColumnIndex": num_cols},
                "rowProperties": {
                    "firstBandColor":  _WHITE,
                    "secondBandColor": _LIGHT,
                },
            }
        }
    }


def _req_col_widths(sheet_id: int, widths_px: list) -> list:
    reqs = []
    for i, px in enumerate(widths_px):
        reqs.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        })
    return reqs


def _clear_banding(spreadsheet: gspread.Spreadsheet, sheet_id: int) -> None:
    """Delete any existing banded ranges on this sheet so we don't stack duplicates."""
    try:
        meta = spreadsheet.fetch_sheet_metadata()
        for sheet in meta.get("sheets", []):
            if sheet["properties"]["sheetId"] == sheet_id:
                for band in sheet.get("bandedRanges", []):
                    spreadsheet.batch_update({
                        "requests": [{"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}}]
                    })
    except Exception:
        pass


# ── Public writers ────────────────────────────────────────────────────────────

def write_event_tab(event_name: str, companies: list[dict], event_date: str) -> None:
    """Writes / refreshes one event tab with raw company list."""
    gc          = _get_client()
    spreadsheet = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    tab_name    = event_name[:100]
    ws          = _get_or_create_sheet(spreadsheet, tab_name)
    ws.clear()

    today = date.today().isoformat()

    rows = [EVENT_HEADERS]
    for company in companies:
        rows.append([
            company.get("name", ""),
            company.get("domain", ""),
            event_name,
            event_date,
            today,
        ])

    ws.update(rows, value_input_option="USER_ENTERED")

    # ── Formatting ──
    sid = ws.id
    _clear_banding(spreadsheet, sid)

    col_widths = [220, 200, 180, 120, 120]
    requests = [
        _req_freeze(sid),
        _req_header_format(sid, len(EVENT_HEADERS)),
        _req_banding(sid, len(EVENT_HEADERS)),
        *_req_col_widths(sid, col_widths),
    ]
    spreadsheet.batch_update({"requests": requests})

    print(f"  Wrote {len(rows) - 1} companies to tab '{tab_name}'")


def write_summary_tab(summary_rows: list[dict]) -> None:
    """Writes the Summary tab with one row per event."""
    gc          = _get_client()
    spreadsheet = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    ws          = _get_or_create_sheet(spreadsheet, "Summary")
    ws.clear()

    rows = [SUMMARY_HEADERS]
    for r in summary_rows:
        rows.append([
            r.get("event_name", "")[:100],
            r.get("event_url", ""),
            r.get("event_date", ""),
            r.get("total_sponsors", 0),
        ])

    ws.update(rows, value_input_option="USER_ENTERED")

    # ── Formatting ──
    sid = ws.id
    _clear_banding(spreadsheet, sid)

    col_widths = [220, 260, 120, 160]
    requests = [
        _req_freeze(sid),
        _req_header_format(sid, len(SUMMARY_HEADERS)),
        _req_banding(sid, len(SUMMARY_HEADERS)),
        *_req_col_widths(sid, col_widths),
    ]
    spreadsheet.batch_update({"requests": requests})

    print(f"  Wrote summary tab with {len(summary_rows)} events")
