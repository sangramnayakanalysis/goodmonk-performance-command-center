"""
alerts.py
=========
Feature 3 (Smart Alert System): the central Alert Engine facade. This is
the module every other file imports — nothing outside this file talks to
notification_manager.py or writes to the AlertHistory Sheets tab directly.

Usage from a domain module's own code:

    events = alerts.evaluate_domain("gtmetrix", *alert_rules.rules_for_gtmetrix(results))
    # events is a list[AlertEvent] — only "new" and "recovered" ones
    # (event.should_notify) are what you'd surface in an email/dashboard.

    alerts.raise_operational("dashboard", "dashboard_generation_failure", str(e))
    # ... and on the next successful run:
    alerts.mark_recovered("dashboard", "dashboard_generation_failure")

Also runnable directly as a tiny CLI for the one alert type that can't be
raised from inside a normal Python run: a GitHub Actions *workflow-level*
failure (the Python process itself never got to run, or crashed before
main.py's own exception handling could engage). See monitor.yml's
"Notify on workflow failure" step.

    python -m alerts --workflow-failure "<message>"
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import config
import google_sheet
import notification_manager
from alert_models import Alert, AlertEvent
from logger import get_logger

log = get_logger("alerts")


def evaluate_domain(domain: str, candidates: dict[str, Alert], universe_keys: set[str]) -> list[AlertEvent]:
    """
    Runs one domain's candidate alerts (from an alert_rules.py function)
    through deduplication/recovery, writes every "new" or "recovered"
    event to the AlertHistory Sheets tab, and returns all events (callers
    filter on `.should_notify` for email/dashboard use — Sheets gets the
    full history including suppressed-duplicate counts is intentionally
    NOT written for suppressed events, to keep the sheet from growing
    unbounded on an ongoing failure).
    """
    if not config.ALERT_ENABLED:
        return []

    events = notification_manager.process_batch(domain, candidates, universe_keys)

    for event in events:
        if not event.should_notify:
            continue
        try:
            google_sheet.append_alert(_to_sheet_row(event))
        except Exception as e:  # noqa: BLE001 — an alert-history write failure must never break the run
            log.error("Failed to write alert history row for %s: %s", event.alert.alert_key, e)

    return events


def raise_operational(module: str, alert_type: str, message: str, root_cause: str = "") -> Optional[AlertEvent]:
    """See notification_manager.raise_operational — this wraps it with
    the Sheets-write side effect, same pattern as evaluate_domain."""
    if not config.ALERT_ENABLED:
        return None
    event = notification_manager.raise_operational(module, alert_type, message, root_cause)
    if event and event.should_notify:
        try:
            google_sheet.append_alert(_to_sheet_row(event))
        except Exception as e:  # noqa: BLE001
            log.error("Failed to write alert history row for %s/%s: %s", module, alert_type, e)
    return event


def mark_recovered(module: str, alert_type: str) -> Optional[AlertEvent]:
    if not config.ALERT_ENABLED:
        return None
    event = notification_manager.mark_recovered(module, alert_type)
    if event:
        try:
            google_sheet.append_alert(_to_sheet_row(event))
        except Exception as e:  # noqa: BLE001
            log.error("Failed to write alert-recovery row for %s/%s: %s", module, alert_type, e)
    return event


def _to_sheet_row(event: AlertEvent) -> list:
    from utils import now_date_str, now_time_str
    a = event.alert
    return [
        now_date_str(), now_time_str(), a.alert_type, a.severity, a.module,
        a.affected_page or "", a.message, a.root_cause,
        "Recovered" if event.status == "recovered" else "New",
        event.occurrence_count,
    ]


def _cli() -> int:
    """Minimal CLI entry point for the one case that can't go through a
    normal Python run: the GitHub Actions job itself failing outside
    main.py's own exception handling (e.g. `pip install` failed, the
    Python process crashed/was killed). Called from monitor.yml's
    `if: failure()` step, which necessarily runs in a fresh step
    environment — it re-reads the same persistent alert_state.json and
    sends through the same dedup/email path as every other alert."""
    parser = argparse.ArgumentParser(description="Fire a GitHub workflow-failure alert.")
    parser.add_argument("--workflow-failure", metavar="MESSAGE", required=True)
    args = parser.parse_args()

    event = raise_operational("workflow", "github_workflow_failure", args.workflow_failure)
    if event and event.should_notify:
        try:
            import email_report
            # No page results are available in this fallback path (the
            # main run never got far enough to produce them) — send a
            # minimal, standalone alert-only email rather than skipping
            # notification entirely.
            email_report.send_report([], alert_events=[event.to_dict()])
        except Exception as e:  # noqa: BLE001 — this IS the failure path; it must not itself crash the job
            log.error("Failed to send workflow-failure email: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
