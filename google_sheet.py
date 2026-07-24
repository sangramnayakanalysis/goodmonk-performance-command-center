"""
google_sheet.py
================
Google Sheets integration via gspread.

Features
--------
✓ Automatically creates worksheets
✓ Automatically fixes headers
✓ Always writes data in correct columns
✓ Dashboard compatible
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
        creds = Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_FILE,
            scopes=_SCOPES,
        )
    elif config.GOOGLE_SERVICE_ACCOUNT_JSON:
        creds = Credentials.from_service_account_info(
            json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON),
            scopes=_SCOPES,
        )
    else:
        raise RuntimeError(
            "Google credentials not configured."
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

        log.info("Creating sheet: %s", sheet_name)

        ws = ss.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=len(config.HISTORY_HEADERS),
        )

    _ensure_header(ws)

    return ws


def _ensure_header(ws: gspread.Worksheet):

    expected = config.HISTORY_HEADERS

    current = ws.row_values(1)

    if current != expected:

        ws.resize(cols=len(expected))

        ws.update(
            "A1:L1",
            [expected],
            value_input_option="RAW",
        )

        ws.format(
            "A1:L1",
            {
                "textFormat": {
                    "bold": True
                }
            },
        )

        log.info("Header updated for %s", ws.title)


def append_result(sheet_name: str, metrics: Metrics):

    ws = _get_or_create_sheet(sheet_name)

    row = [

        now_date_str(),                 # A
        now_time_str(),                 # B

        metrics.performance_score,      # C
        metrics.grade,                  # D

        metrics.lcp,                    # E
        metrics.onload,                 # F
        metrics.fully_loaded,           # G

        metrics.ttfb,                   # H
        metrics.cls,                    # I
        metrics.tbt,                    # J

        metrics.report_url,             # K
        "OK",                           # L
    ]

    row.extend([""] * (len(config.HISTORY_HEADERS) - len(row)))

    ws.append_row(
        row,
        value_input_option="RAW",
    )

    log.info("SUCCESS row added -> %s", sheet_name)


def append_failure(
    sheet_name: str,
    error_message: str,
):

    ws = _get_or_create_sheet(sheet_name)

    row = [

        now_date_str(),         # A
        now_time_str(),         # B

        "",                     # C
        "",                     # D
        "",                     # E
        "",                     # F
        "",                     # G
        "",                     # H
        "",                     # I
        "",                     # J

        error_message,          # K
        "Failed",               # L
    ]

    row.extend([""] * (len(config.HISTORY_HEADERS) - len(row)))

    ws.append_row(
        row,
        value_input_option="RAW",
    )

    log.info("FAILED row added -> %s", sheet_name)


def read_history(sheet_name: str):

    ws = _get_or_create_sheet(sheet_name)

    return ws.get_all_records()
