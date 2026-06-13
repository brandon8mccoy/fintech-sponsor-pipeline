"""
Google Sheets output module.

Sheet structure:
  Summary tab  — one row per event; "Contacts Actioned" is a live COUNTIF
  Event tabs   — one row per contact; "Contact Actioned" column uses checkboxes

Aesthetics:
  - Dark navy header row, white bold text, frozen
  - Alternating white / light-blue-gray row banding
  - Checkbox data validation on "Contact Actioned" column

Checkpoint safety:
  - Existing checkbox states are preserved across pipeline re-runs so reps
    don't lose their actioned tracking when new sponsors are added.
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
    "Company Name", "Website", "Company Size", "Contact Name",
    "Title", "Email", "LinkedIn URL", "Date Scraped", "Contact Actioned",
]
ACTIONED_COL_IDX = 9          # 1-based column index of "Contact Actioned" (col I)
EMAIL_COL_IDX    = 6          # 1-based column index of "Email" (col F)

SUMMARY_HEADERS = [
    "Event Name", "Event URL", "Event Date",
    "Companies Scraped", "ICP Companies", "ICP Contacts", "Contacts Actioned",
]

# ── Colours (Sheets API uses 0-1 float RGB) ───────────────────────────────────

_NAVY   = {"red": 0.11, "green": 0.17, "blue": 0.29}   # #1C2B4A
_WHITE  = {"red": 1.00, "green": 1.00, "blue": 1.00}
_LIGHT  = {"red": 0.95, "green": 0.97, "blue": 1.00}   # very-light blue-gray
_ACCENT = {"red": 0.86, "green": 0.93, "blue": 0.86}   # soft green for actioned header


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


def _read_checkbox_states(ws: gspread.Worksheet) -> dict[str, bool]:
    """Returns {email: bool} of existing Contact Actioned states."""
    try:
        rows = ws.get_all_values()
        if len(rows) < 2:
            return {}
        header = rows[0]
        try:
            email_i   = header.index("Email")
            actioned_i = header.index("Contact Actioned")
        except ValueError:
            return {}
        result = {}
        for row in rows[1:]:
            if len(row) > max(email_i, actioned_i):
                email   = row[email_i].strip()
                checked = row[actioned_i].upper() in ("TRUE", "1", "YES", "✓")
                if email:
                    result[email] = checked
        return result
    except Exception:
        return {}


# ── Formatting helpers (Sheets batchUpdate requests) ─────────────────────────

def _req_freeze(sheet_id: int, rows: int = 1) -> dict:
    return {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": rows}},
            "fields": "gridProperties.frozenRowCount",
        }
    }


def _req_header_format(sheet_id: int, num_cols: int, bg: dict = None) -> dict:
    bg = bg or _NAVY
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {"userEnteredFormat": {
                "backgroundColor": bg,
                "textFormat": {"bold": True, "foregroundColor": _WHITE, "fontSize": 10},
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "CLIP",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
        }
    }


def _req_accent_col_header(sheet_id: int, col_idx: int) -> dict:
    """Give the 'Contact Actioned' header a soft green to make it stand out."""
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": col_idx - 1, "endColumnIndex": col_idx},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _ACCENT,
                "textFormat": {"bold": True,
                               "foregroundColor": {"red": 0.1, "green": 0.3, "blue": 0.1},
                               "fontSize": 10},
                "horizontalAlignment": "CENTER",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
        }
    }


def _req_banding(sheet_id: int, num_cols: int) -> dict:
    return {
        "addBanding": {
            "bandedRange": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1,
                          "startColumnIndex": 0, "endColumnIndex": num_cols},
                "rowProperties": {
                    "headerColor":      _NAVY,
                    "firstBandColor":   _WHITE,
                    "secondBandColor":  _LIGHT,
                },
            }
        }
    }


def _req_col_widths(sheet_id: int, widths_px: list[int]) -> list[dict]:
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


def _req_checkbox_validation(sheet_id: int, col_idx: int, max_row: int = 2000) -> dict:
    """Add BOOLEAN data validation (renders as a checkbox) to an entire column."""
    return {
        "setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": max_row,
                      "startColumnIndex": col_idx - 1, "endColumnIndex": col_idx},
            "rule": {"condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True},
        }
    }


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

def write_event_tab(event_name: str, contacts: list[dict], companies: list[dict]) -> None:
    """Writes / refreshes one event tab, preserving existing checkbox states."""
    gc          = _get_client()
    spreadsheet = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    tab_name    = event_name[:100]
    ws          = _get_or_create_sheet(spreadsheet, tab_name)

    # Preserve existing checked states keyed by email
    checked_by_email = _read_checkbox_states(ws)
    ws.clear()

    today = date.today().isoformat()

    rows = [EVENT_HEADERS]
    for contact in contacts:
        company    = next((c for c in companies if c["name"] == contact.get("company_name")), {})
        first      = contact.get("first_name", "")
        last       = contact.get("last_name", "")
        full_name  = f"{first} {last}".strip()
        email      = contact.get("email", "")
        was_actioned = checked_by_email.get(email, False)

        rows.append([
            contact.get("company_name", ""),
            company.get("domain", ""),
            company.get("employee_count", ""),
            full_name,
            contact.get("title", ""),
            email,
            contact.get("linkedin_url", ""),
            today,
            was_actioned,   # TRUE/FALSE restored from prior run
        ])

    ws.update(rows, value_input_option="USER_ENTERED")

    # ── Formatting ──
    sid = ws.id
    _clear_banding(spreadsheet, sid)

    col_widths = [200, 170, 120, 170, 200, 220, 200, 115, 140]
    requests = [
        _req_freeze(sid),
        _req_header_format(sid, len(EVENT_HEADERS)),
        _req_accent_col_header(sid, ACTIONED_COL_IDX),
        _req_banding(sid, len(EVENT_HEADERS)),
        _req_checkbox_validation(sid, ACTIONED_COL_IDX),
        *_req_col_widths(sid, col_widths),
    ]
    spreadsheet.batch_update({"requests": requests})

    print(f"  Wrote {len(rows) - 1} contacts to tab '{tab_name}'")


def write_summary_tab(summary_rows: list[dict]) -> None:
    """Writes the Summary tab with a live COUNTIF for each event's actioned contacts."""
    gc          = _get_client()
    spreadsheet = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    ws          = _get_or_create_sheet(spreadsheet, "Summary")
    ws.clear()

    rows = [SUMMARY_HEADERS]
    for r in summary_rows:
        tab_name = r.get("event_name", "")[:100]
        # Escape single quotes in tab names for the COUNTIF formula
        safe_tab = tab_name.replace("'", "''")
        actioned_formula = f"=COUNTIF('{safe_tab}'!I:I,TRUE)"

        rows.append([
            tab_name,
            r.get("event_url", ""),
            r.get("event_date", ""),
            r.get("total_sponsors", 0),
            r.get("passed_icp", 0),
            r.get("contacts_found", 0),
            actioned_formula,
        ])

    ws.update(rows, value_input_option="USER_ENTERED")

    # ── Formatting ──
    sid = ws.id
    _clear_banding(spreadsheet, sid)

    col_widths = [220, 220, 110, 160, 140, 130, 160]
    requests = [
        _req_freeze(sid),
        _req_header_format(sid, len(SUMMARY_HEADERS)),
        _req_banding(sid, len(SUMMARY_HEADERS)),
        *_req_col_widths(sid, col_widths),
    ]
    spreadsheet.batch_update({"requests": requests})

    print(f"  Wrote summary tab with {len(summary_rows)} events")
