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


# =============================================================================
# Feature 1 additions (Root Cause Analysis) — all new functions below.
# Nothing above this line was changed. These write to brand-new worksheet
# tabs ("<sheet_name>_RootCause") and never touch the existing per-page
# history tabs or their header (config.HISTORY_HEADERS) in any way.
# =============================================================================

def _get_or_create_sheet_with_headers(tab_name: str, headers: list[str]) -> gspread.Worksheet:
    """
    Generic version of `_get_or_create_sheet` / `_ensure_header` for tabs
    that use a header set other than `config.HISTORY_HEADERS`. Kept as a
    separate function (rather than editing `_get_or_create_sheet`) so the
    existing, working per-page history tabs are guaranteed untouched.
    """
    ss = _get_spreadsheet()

    try:
        ws = ss.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        log.info("Creating root-cause sheet: %s", tab_name)
        ws = ss.add_worksheet(title=tab_name, rows=1000, cols=len(headers))

    current = ws.row_values(1)
    if current != headers:
        ws.resize(cols=len(headers))
        end_col_letter = chr(ord("A") + len(headers) - 1)
        ws.update(f"A1:{end_col_letter}1", [headers], value_input_option="RAW")
        ws.format(f"A1:{end_col_letter}1", {"textFormat": {"bold": True}})
        log.info("Header updated for %s", ws.title)

    return ws


def append_root_cause(sheet_name: str, row: list) -> None:
    """
    Appends one Root Cause Analysis row to the "<sheet_name>_RootCause" tab.
    Isolated try/except at the call site (scheduler.py) means a failure here
    can never affect the existing GTmetrix history write for the same page.
    """
    tab_name = f"{sheet_name}_RootCause"
    ws = _get_or_create_sheet_with_headers(tab_name, config.ROOT_CAUSE_HEADERS)

    row = list(row)
    row.extend([""] * (len(config.ROOT_CAUSE_HEADERS) - len(row)))

    ws.append_row(row, value_input_option="RAW")
    log.info("Root-cause row added -> %s", tab_name)


def read_root_cause_history(sheet_name: str, limit: int = 50) -> list[dict]:
    """Returns the most recent `limit` root-cause rows for one page's tab."""
    tab_name = f"{sheet_name}_RootCause"
    ws = _get_or_create_sheet_with_headers(tab_name, config.ROOT_CAUSE_HEADERS)
    rows = ws.get_all_records()
    return rows[-limit:] if limit else rows


# =============================================================================
# Feature 2 additions (Customer Journey Monitoring) — all new functions
# below. Nothing above this line was changed (including the Feature 1
# additions directly above). Journey results all live in ONE shared new
# tab ("CustomerJourney") since a journey isn't tied to a single page the
# way GTmetrix/RCA results are — it spans several pages by definition.
# =============================================================================

JOURNEY_TAB_NAME = "CustomerJourney"


def append_journey(row: list) -> None:
    """Appends one journey run's flattened result to the shared
    "CustomerJourney" tab. Isolated at the call site (scheduler/main),
    exactly like append_root_cause — a failure here can never affect
    GTmetrix history, RCA, or any other existing write."""
    ws = _get_or_create_sheet_with_headers(JOURNEY_TAB_NAME, config.JOURNEY_HEADERS)

    row = list(row)
    row.extend([""] * (len(config.JOURNEY_HEADERS) - len(row)))

    ws.append_row(row, value_input_option="RAW")
    log.info("Journey row added -> %s", JOURNEY_TAB_NAME)


def read_journey_history(limit: int = 100) -> list[dict]:
    """Returns the most recent `limit` journey rows across all products."""
    ws = _get_or_create_sheet_with_headers(JOURNEY_TAB_NAME, config.JOURNEY_HEADERS)
    rows = ws.get_all_records()
    return rows[-limit:] if limit else rows


# =============================================================================
# Feature 3 additions (Smart Alert System) — all new functions below.
# Nothing above this line was changed. One shared new tab, "AlertHistory",
# for the same reason CustomerJourney is one shared tab — an alert isn't
# tied to a single page's history the way GTmetrix/RCA results are.
# =============================================================================

ALERT_TAB_NAME = "AlertHistory"


def append_alert(row: list) -> None:
    """Appends one alert event (a new alert firing, or a recovery) to the
    shared "AlertHistory" tab. Isolated at every call site (alerts.py) —
    a failure here can never affect GTmetrix/RCA/Journey history writes."""
    ws = _get_or_create_sheet_with_headers(ALERT_TAB_NAME, config.ALERT_HEADERS)

    row = list(row)
    row.extend([""] * (len(config.ALERT_HEADERS) - len(row)))

    ws.append_row(row, value_input_option="RAW")
    log.info("Alert row added -> %s", ALERT_TAB_NAME)


def read_alert_history(limit: int = 200) -> list[dict]:
    """Returns the most recent `limit` alert events."""
    ws = _get_or_create_sheet_with_headers(ALERT_TAB_NAME, config.ALERT_HEADERS)
    rows = ws.get_all_records()
    return rows[-limit:] if limit else rows


# =============================================================================
# Feature 4 addition (Intelligent Monitoring Scheduler) — new function
# below. Nothing above this line was changed. One shared new tab,
# "SchedulerRuns" — one row per workflow run, summarizing what the
# scheduler decided (not per-page — that level of detail lives in
# data/page_schedule_state.json and dashboard/data/scheduler.json).
# =============================================================================

SCHEDULER_TAB_NAME = "SchedulerRuns"


def append_scheduler_run(row: list) -> None:
    """Appends one workflow run's scheduling summary to the shared
    "SchedulerRuns" tab. Isolated at the call site (main.py) — a failure
    here can never affect any other Sheets write."""
    ws = _get_or_create_sheet_with_headers(SCHEDULER_TAB_NAME, config.SCHEDULER_HEADERS)

    row = list(row)
    row.extend([""] * (len(config.SCHEDULER_HEADERS) - len(row)))

    ws.append_row(row, value_input_option="RAW")
    log.info("Scheduler run row added -> %s", SCHEDULER_TAB_NAME)
