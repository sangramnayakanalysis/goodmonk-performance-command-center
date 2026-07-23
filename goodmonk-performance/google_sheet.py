"""
google_sheet.py
================
Google Sheets integration via gspread. Append-only: every call adds a
new row to a page's sheet (creating the sheet + header row on first
use), and never overwrites or deletes existing history — matching the
original Apps Script behaviour exactly.
"""

from __future__ import annotations

import json
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

import config
from gtmetrix import Metrics
from logger import get_logger
from utils import now_date_str, now_time_str

log = get_logger("google_sheet")

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

_client: Optional[gspread.Client] = None
_spreadsheet: Optional[gspread.Spreadsheet] = None


def _get_client() -> gspread.Client:
    global _client
    if _client is not None:
        return _client

    if config.GOOGLE_SERVICE_ACCOUNT_FILE:
        creds = Credentials.from_service_account_file(config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES)
    elif config.GOOGLE_SERVICE_ACCOUNT_JSON:
        info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
    else:
        raise RuntimeError(
            "No Google credentials configured. Set GOOGLE_SERVICE_ACCOUNT_FILE "
            "(path) or GOOGLE_SERVICE_ACCOUNT_JSON (raw JSON, used in CI)."
        )

    _client = gspread.authorize(creds)
    return _client


def _get_spreadsheet() -> gspread.Spreadsheet:
    global _spreadsheet
    if _spreadsheet is None:
        _spreadsheet = _get_client().open_by_key(config.GOOGLE_SHEET_ID)
    return _spreadsheet


def _get_or_create_sheet(sheet_name: str) -> gspread.Worksheet:
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        log.info("Sheet '%s' not found — creating it.", sheet_name)
        ws = ss.add_worksheet(title=sheet_name, rows=1000, cols=len(config.HISTORY_HEADERS))
        ws.append_row(config.HISTORY_HEADERS, value_input_option="RAW")
        ws.format(f"A1:{gspread.utils.rowcol_to_a1(1, len(config.HISTORY_HEADERS))}",
                  {"textFormat": {"bold": True}})
    return ws


def append_result(sheet_name: str, metrics: Metrics) -> None:
    """Appends one successful test result. Never overwrites prior rows."""
    ws = _get_or_create_sheet(sheet_name)
    row = [
        now_date_str(), now_time_str(),
        metrics.performance_score, metrics.grade,
        metrics.lcp, metrics.onload, metrics.fully_loaded,
        metrics.ttfb, metrics.cls, metrics.tbt,
        metrics.report_url, metrics.status,
    ]
    ws.append_row(row, value_input_option="RAW")
    log.info("Appended row to '%s'.", sheet_name)


def append_failure(sheet_name: str, error_message: str) -> None:
    """Appends a failure marker row so a failed run is visible in the
    same historical sheet, not just in local logs."""
    ws = _get_or_create_sheet(sheet_name)
    row = [now_date_str(), now_time_str(), None, None, None, None, None, None, None, None,
           error_message, "Failed"]
    ws.append_row(row, value_input_option="RAW")
    log.info("Appended FAILURE row to '%s': %s", sheet_name, error_message)


def read_history(sheet_name: str) -> list[dict]:
    """Reads all rows for one page's sheet as a list of dicts. Used by
    dashboard_data.py to build trend data without keeping a separate
    local copy of history."""
    ws = _get_or_create_sheet(sheet_name)
    records = ws.get_all_records()
    return records
