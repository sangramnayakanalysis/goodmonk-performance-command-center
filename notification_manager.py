"""
notification_manager.py
========================
Feature 3 (Smart Alert System): persistent state, deduplication, and
recovery detection. This is the module that turns "a candidate Alert was
generated" into "should this actually notify someone" — the distinction
the whole feature exists for.

State lives in data/alert_state.json, following the exact same pattern
scheduler.py already uses for data/run_state.json: written locally,
deliberately NOT gitignored, and committed back to the repo by
monitor.yml after every run so state survives across fresh CI VMs.

Two dedup models, matching two different shapes of "what does recovery
mean":

1. Domain/universe-scoped (`process_batch`) — for anything checked
   against a known, finite set of things every run (every configured
   page, every configured journey product, every RCA category per page).
   Recovery is precise: "this exact key was firing, and this run
   evaluated it and found it healthy."

2. Operational (`raise_operational` / `mark_recovered`) — for exception-
   driven failures (Sheets write failed, dashboard build crashed,
   Playwright crashed) where there's no fixed "universe" to check against
   each run — only "did this specific operation fail this time." The
   calling code explicitly marks success or failure at the point where
   it already knows which happened (its own try/except).
"""

from __future__ import annotations

from typing import Optional

import config
from alert_models import Alert, AlertEvent
from logger import get_logger
from utils import now_iso, read_json, write_json

log = get_logger("notification_manager")

STATE_FILE = config.DATA_DIR / "alert_state.json"


def _load_state() -> dict:
    return read_json(STATE_FILE, default={"active": {}})


def _save_state(state: dict) -> None:
    write_json(STATE_FILE, state)


def process_batch(domain: str, current_alerts: dict[str, Alert], universe_keys: set[str]) -> list[AlertEvent]:
    """
    Evaluates one domain's alerts against persistent state.

    `current_alerts` — {alert_key: Alert} for everything that IS a
    problem right now, out of everything this run actually checked.
    `universe_keys` — every key this run checked, whether it's currently a
    problem or not (this is what makes recovery detection precise: a key
    that was firing but isn't in `universe_keys` this run is left alone,
    not assumed recovered — e.g. a page temporarily removed from config).

    Returns AlertEvents for state transitions worth acting on: "new"
    (wasn't firing, now is), "ongoing_suppressed" (was firing, still is —
    this is the dedup in action), and "recovered" (was firing, universe
    checked it and it's now healthy).
    """
    state = _load_state()
    active = state.setdefault("active", {})
    now = now_iso()
    events: list[AlertEvent] = []

    for key in universe_keys:
        full_key = f"{domain}:{key}"
        previously_firing = full_key in active

        if key in current_alerts:
            alert = current_alerts[key]
            if not previously_firing:
                active[full_key] = {
                    "alert_type": alert.alert_type, "module": alert.module,
                    "severity": alert.severity, "affected_page": alert.affected_page,
                    "first_seen_at": now, "last_seen_at": now, "occurrence_count": 1,
                }
                events.append(AlertEvent(alert=alert, status="new", detected_at=now, first_seen_at=now))
                log.info("ALERT NEW [%s] %s: %s", alert.severity.upper(), alert.title, full_key)
            else:
                entry = active[full_key]
                entry["last_seen_at"] = now
                entry["occurrence_count"] = entry.get("occurrence_count", 1) + 1
                events.append(AlertEvent(
                    alert=alert, status="ongoing_suppressed", detected_at=now,
                    first_seen_at=entry.get("first_seen_at", now),
                    occurrence_count=entry["occurrence_count"],
                ))
                # No log line here at INFO level — this branch running
                # correctly means "stayed silent as designed", logged at
                # DEBUG only so normal logs aren't spammed by the very
                # thing deduplication exists to quiet down.
                log.debug("Alert still firing (suppressed, no duplicate email): %s", full_key)
        else:
            if previously_firing:
                entry = active.pop(full_key)
                recovered_alert = Alert(
                    alert_key=key, alert_type=entry["alert_type"], module=entry["module"],
                    title=f"Recovered: {entry['alert_type'].replace('_', ' ').title()}",
                    message=f"{key} is healthy again after {entry.get('occurrence_count', 1)} consecutive alerting run(s).",
                    severity=entry["severity"], affected_page=entry.get("affected_page"),
                )
                events.append(AlertEvent(
                    alert=recovered_alert, status="recovered", detected_at=now,
                    first_seen_at=entry.get("first_seen_at", now),
                    occurrence_count=entry.get("occurrence_count", 1),
                ))
                log.info("ALERT RECOVERED: %s", full_key)
            # else: healthy and was never firing — nothing to do, and
            # deliberately no event/log line (this is the overwhelmingly
            # common case every run and must stay silent).

    _save_state(state)
    return events


def raise_operational(module: str, alert_type: str, message: str, root_cause: str = "") -> Optional[AlertEvent]:
    """
    Fires (or suppresses, if already firing) an exception-driven
    operational alert. Call this from the `except` branch of an existing
    isolated try/except — it never itself raises, matching every other
    failure-isolation boundary in this project.
    """
    key = f"operational:{module}:{alert_type}"
    state = _load_state()
    active = state.setdefault("active", {})
    now = now_iso()

    alert = Alert(
        alert_key=f"{module}:{alert_type}", alert_type=alert_type, module=module,
        title=alert_type.replace("_", " ").title(), message=message, root_cause=root_cause,
    )

    if key in active:
        entry = active[key]
        entry["last_seen_at"] = now
        entry["occurrence_count"] = entry.get("occurrence_count", 1) + 1
        _save_state(state)
        log.debug("Operational alert still firing (suppressed): %s", key)
        return AlertEvent(alert=alert, status="ongoing_suppressed", detected_at=now,
                           first_seen_at=entry.get("first_seen_at", now), occurrence_count=entry["occurrence_count"])

    active[key] = {
        "alert_type": alert_type, "module": module, "severity": alert.severity,
        "affected_page": None, "first_seen_at": now, "last_seen_at": now, "occurrence_count": 1,
    }
    _save_state(state)
    log.info("ALERT NEW [%s] %s: %s", alert.severity.upper(), alert.title, message)
    return AlertEvent(alert=alert, status="new", detected_at=now, first_seen_at=now)


def mark_recovered(module: str, alert_type: str) -> Optional[AlertEvent]:
    """
    Call this at the point where an operation that previously failed just
    succeeded. If it wasn't previously firing, this is a silent no-op
    (the overwhelmingly common case — most runs never fail).
    """
    key = f"operational:{module}:{alert_type}"
    state = _load_state()
    active = state.setdefault("active", {})
    now = now_iso()

    if key not in active:
        return None

    entry = active.pop(key)
    _save_state(state)
    alert = Alert(
        alert_key=f"{module}:{alert_type}", alert_type=alert_type, module=module,
        title=f"Recovered: {alert_type.replace('_', ' ').title()}",
        message=f"{module}/{alert_type} succeeded again after {entry.get('occurrence_count', 1)} consecutive failure(s).",
        severity=entry.get("severity", "warning"),
    )
    log.info("ALERT RECOVERED: %s", key)
    return AlertEvent(alert=alert, status="recovered", detected_at=now,
                       first_seen_at=entry.get("first_seen_at", now), occurrence_count=entry.get("occurrence_count", 1))


def clear_all_state() -> None:
    """Mirrors scheduler.clear_run_state()'s pattern — not called
    automatically anywhere; available for manual/test use."""
    _save_state({"active": {}})
    log.info("Alert state cleared.")
