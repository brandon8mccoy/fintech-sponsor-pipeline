"""
Google Sheets output module — speaker targeting pipeline.

Sheet structure:
  Summary tab  — one row per event (scraped / ICP / emails found)
  Event tabs   — one row per ICP-passed speaker with Contact Actioned checkbox

Aesthetics:
  - Dark navy header row, white bold text, frozen
  - Alternating white / light-blue-gray row banding
  - Checkbox on "Contact Actioned" column
  - Checkbox states preserved across pipeline re-runs
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
    "Speaker Name", "Title", "Company", "Domain",
    "Email", "LinkedIn URL", "Event Date", "Date Scraped", "Contact Actioned",
]
ACTIONED_COL_IDX = 9   # 1-based index of "Contact Actioned" (col I)
EMAIL_COL_IDX    = 5   # 1-based index of "Email" (col E)

SUMMARY_HEADERS = [
    "Event Name", "Event URL", "Event Date",
    "Speakers Scraped", "ICP Speakers", "Emails Found", "Contacts Actioned",
]

# ── Colours ───────────────────────────────────────────────────────────────────

_NAVY   = {"red": 0.11, "green": 0.17, "blue": 0.29}
_WHITE  = {"red": 1.00, "green": 1.00, "blue": 1.00}
_LIGHT  = {"red": 0.95, "green": 0.97, "blue": 1.00}
_ACCENT = {"red": 0.86, "green": 0.93, "blue": 0.86}


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


def _read_checkbox_states(ws: gspread.Worksheet) -> dict:
    """Returns {speaker_name: bool} of existing Contact Actioned states."""
    try:
        rows = ws.get_all_values()
        if len(rows) < 2:
            return {}
        header = rows[0]
        try:
            name_i     = header.index("Speaker Name")
            actioned_i = header.index("Contact Actioned")
        except ValueError:
            return {}
        result = {}
        for row in rows[1:]:
            if len(row) > max(name_i, actioned_i):
                name    = row[name_i].strip()
                checked = row[actioned_i].upper() in ("TRUE", "1", "YES", "✓")
                if name:
                    result[name] = checked
        return result
    except Exception:
        return {}


# ── Formatting helpers ────────────────────────────────────────────────────────

def _req_freeze(sheet_id: int) -> dict:
    return {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
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


def _req_accent_col_header(sheet_id: int, col_idx: int) -> dict:
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
                    "firstBandColor":  _WHITE,
                    "secondBandColor": _LIGHT,
                },
            }
        }
    }


def _req_col_widths(sheet_id: int, widths_px: list) -> list:
    return [
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        }
        for i, px in enumerate(widths_px)
    ]


def _req_checkbox_validation(sheet_id: int, col_idx: int, max_row: int = 2000) -> dict:
    return {
        "setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": max_row,
                      "startColumnIndex": col_idx - 1, "endColumnIndex": col_idx},
            "rule": {"condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True},
        }
    }


def _clear_banding(spreadsheet: gspread.Spreadsheet, sheet_id: int) -> None:
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

def write_event_tab(event_name: str, speakers: list[dict], event_date: str) -> None:
    """Writes / refreshes one event tab with ICP-passed speakers, preserving checkbox states."""
    gc          = _get_client()
    spreadsheet = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    tab_name    = event_name[:100]
    ws          = _get_or_create_sheet(spreadsheet, tab_name)

    checked_by_name = _read_checkbox_states(ws)
    ws.clear()

    today = date.today().isoformat()
    rows  = [EVENT_HEADERS]

    for speaker in speakers:
        name         = speaker.get("name", "")
        was_actioned = checked_by_name.get(name, False)
        rows.append([
            name,
            speaker.get("title", ""),
            speaker.get("company", ""),
            speaker.get("domain", ""),
            speaker.get("email", ""),
            speaker.get("linkedin_url", ""),
            event_date,
            today,
            was_actioned,
        ])

    ws.update(rows, value_input_option="USER_ENTERED")

    sid = ws.id
    _clear_banding(spreadsheet, sid)

    col_widths = [180, 200, 180, 160, 220, 200, 110, 110, 140]
    requests = [
        _req_freeze(sid),
        _req_header_format(sid, len(EVENT_HEADERS)),
        _req_accent_col_header(sid, ACTIONED_COL_IDX),
        _req_banding(sid, len(EVENT_HEADERS)),
        _req_checkbox_validation(sid, ACTIONED_COL_IDX),
        *_req_col_widths(sid, col_widths),
    ]
    spreadsheet.batch_update({"requests": requests})

    print(f"  Wrote {len(rows) - 1} speakers to tab '{tab_name}'")


def write_summary_tab(summary_rows: list[dict]) -> None:
    """Writes the Summary tab with a live COUNTIF for actioned contacts."""
    gc          = _get_client()
    spreadsheet = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    ws          = _get_or_create_sheet(spreadsheet, "Summary")
    ws.clear()

    rows = [SUMMARY_HEADERS]
    for r in summary_rows:
        tab_name    = r.get("event_name", "")[:100]
        safe_tab    = tab_name.replace("'", "''")
        actioned_col = chr(ord("A") + ACTIONED_COL_IDX - 1)
        actioned_formula = f"=COUNTIF('{safe_tab}'!{actioned_col}:{actioned_col},TRUE)"

        rows.append([
            tab_name,
            r.get("event_url", ""),
            r.get("event_date", ""),
            r.get("total_speakers", 0),
            r.get("icp_speakers", 0),
            r.get("emails_found", 0),
            actioned_formula,
        ])

    ws.update(rows, value_input_option="USER_ENTERED")

    sid = ws.id
    _clear_banding(spreadsheet, sid)

    col_widths = [220, 220, 110, 150, 130, 120, 160]
    requests = [
        _req_freeze(sid),
        _req_header_format(sid, len(SUMMARY_HEADERS)),
        _req_banding(sid, len(SUMMARY_HEADERS)),
        *_req_col_widths(sid, col_widths),
    ]
    spreadsheet.batch_update({"requests": requests})

    print(f"  Wrote summary tab with {len(summary_rows)} events")
