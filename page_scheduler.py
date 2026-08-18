"""
page_scheduler.py
==================
Feature 4 (Intelligent Monitoring Scheduler): decides which configured
pages are actually due for a check this run, and tracks per-page
scheduling state (last run, next run, failure streak) separately from
everything else — Sheets history, RCA, Journey, and Alert state are all
untouched by this module.

Design goal: this file must NEVER need to change to support a new
monitoring frequency. `Page.interval_hours` is a plain float number of
hours — 0.5 (30 min), 1, 2, 4, 6, 12, 24 (daily), or 168 (weekly) all
work identically, because the scheduler only ever does one comparison:
"has `interval_hours` worth of time passed since this page's last
successful run?" Adding a new cadence is purely a config.py edit.

State lives in data/page_schedule_state.json — same committed-across-
CI-runs pattern as data/run_state.json and data/alert_state.json.
Historical monitoring data (Sheets history, RCA, Journey results) is
never touched or overwritten by this file — it only tracks the latest
scheduling metadata needed to compute "is this page due right now."
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import config
from logger import get_logger
from utils import now_iso, read_json, write_json

log = get_logger("page_scheduler")

STATE_FILE = config.DATA_DIR / "page_schedule_state.json"


def _load_state() -> dict:
    return read_json(STATE_FILE, default={"pages": {}})


def _save_state(state: dict) -> None:
    write_json(STATE_FILE, state)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_due(page: "config.Page", entry: Optional[dict], now: datetime) -> tuple[bool, str]:
    """
    Returns (due, reason). `entry` is this page's current state dict (or
    None if it's never been run). `reason` is always populated — for a
    due page it explains why, for a skipped page it's the "Reason For
    Skip" surfaced in the email/dashboard per the requirement.
    """
    if not page.enabled:
        return False, "page disabled in config"
    if not page.gtmetrix_enabled:
        return False, "GTmetrix disabled for this page in config"

    last_success = _parse_iso(entry.get("last_successful_run")) if entry else None
    if last_success is None:
        return True, "never run before"

    elapsed = now - last_success
    interval = timedelta(hours=page.interval_hours)
    tolerance = timedelta(minutes=config.SCHEDULER_DUE_TOLERANCE_MINUTES)

    if elapsed + tolerance >= interval:
        return True, f"last success {elapsed.total_seconds() / 3600:.1f}h ago, interval is {page.interval_hours}h"
    remaining = interval - elapsed
    return False, f"not due for {remaining.total_seconds() / 3600:.1f}h more (interval {page.interval_hours}h)"


def get_due_pages(pages: list, now: Optional[datetime] = None) -> tuple[list, list[dict]]:
    """
    Splits `pages` (normally config.PAGES) into (due, skipped).
    `due` is a plain list[Page], ready to hand straight to
    scheduler.run_batch(pages=due). `skipped` is a list of
    {"page": Page.name, "reason": str} dicts — exactly the "Pages
    Skipped" / "Reason For Skip" data the email/dashboard need.

    Deliberately takes `pages` as a parameter rather than always reading
    config.PAGES directly, so it composes cleanly with journey-product
    filtering (`get_due_pages(config.JOURNEY_PRODUCTS, now)`) using the
    exact same due/interval logic — one function, no duplication.
    """
    now = now or datetime.now()
    state = _load_state()
    page_states = state.get("pages", {})

    due, skipped = [], []
    for page in pages:
        entry = page_states.get(page.sheet_name)
        due_now, reason = is_due(page, entry, now)
        if due_now:
            due.append(page)
        else:
            skipped.append({"page": page.name, "sheet_name": page.sheet_name, "reason": reason})

    return due, skipped


def record_run(sheet_name: str, success: bool, ran_gtmetrix: bool = False, ran_journey: bool = False) -> None:
    """
    Updates one page's scheduling state after it actually ran (called
    once per page that was in the `due` list, after scheduler.run_batch
    / journey.run_all_journeys has produced a real result for it — never
    for a skipped page, which has nothing new to record).
    """
    state = _load_state()
    page_states = state.setdefault("pages", {})
    entry = page_states.setdefault(sheet_name, {
        "last_run": None, "next_run": None, "last_status": None,
        "failure_count": 0, "last_successful_run": None,
        "last_journey_run": None, "last_gtmetrix_run": None,
    })

    now = now_iso()
    page = config.PAGE_BY_SHEET_NAME.get(sheet_name)
    interval_hours = page.interval_hours if page else 2.0

    entry["last_run"] = now
    entry["last_status"] = "success" if success else "failed"
    entry["next_run"] = (datetime.now() + timedelta(hours=interval_hours)).isoformat(timespec="seconds")

    if success:
        entry["failure_count"] = 0
        entry["last_successful_run"] = now
    else:
        entry["failure_count"] = entry.get("failure_count", 0) + 1

    if ran_gtmetrix:
        entry["last_gtmetrix_run"] = now
    if ran_journey:
        entry["last_journey_run"] = now

    _save_state(state)


def get_scheduler_summary() -> dict:
    """Returns the full current per-page schedule state, plus each
    page's static config (priority/interval) merged in — this is what
    dashboard_data.build_scheduler_summary() turns into scheduler.json."""
    state = _load_state()
    page_states = state.get("pages", {})

    summary = []
    for page in config.PAGES:
        entry = page_states.get(page.sheet_name, {})
        summary.append({
            "name": page.name, "sheet_name": page.sheet_name,
            "priority": page.priority, "interval_hours": page.interval_hours,
            "enabled": page.enabled,
            "last_run": entry.get("last_run"), "next_run": entry.get("next_run"),
            "last_status": entry.get("last_status"),
            "failure_count": entry.get("failure_count", 0),
            "last_successful_run": entry.get("last_successful_run"),
            "last_journey_run": entry.get("last_journey_run"),
            "last_gtmetrix_run": entry.get("last_gtmetrix_run"),
        })
    return {"generated_at": now_iso(), "pages": summary}
