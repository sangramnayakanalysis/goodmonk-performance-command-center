"""
alert_models.py
================
Feature 3 (Smart Alert System): plain data structures.

Kept separate from alert_rules.py (what generates an Alert),
notification_manager.py (whether an Alert should actually fire, given
persistent state), and alerts.py (the facade other modules call) — the
same layering discipline used for journey_models.py/journey.py/
playwright_runner.py in Feature 2.

`Alert` is deliberately generic: it doesn't know or care whether it came
from GTmetrix, Root Cause Analysis, Customer Journey, or a module that
doesn't exist yet (SSL monitoring, Lighthouse, API monitoring...). Any
source only needs to construct an `Alert` with a stable `alert_key` — the
engine (notification_manager.py) does the rest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import config


def severity_for(alert_type: str) -> str:
    """Looks up the default severity for a known alert type; unlisted
    (e.g. future-module) alert types default to "warning" rather than
    raising — the whole point of a generic engine is that it must not
    reject an alert type it hasn't seen before."""
    return config.ALERT_SEVERITY_MAP.get(alert_type, "warning")


@dataclass
class Alert:
    """One candidate alert, as produced by an alert_rules.py function.
    Not yet checked against dedup/cooldown state — that happens in
    notification_manager.py."""
    alert_key: str          # stable dedup identity, e.g. "gtmetrix:page_failed:FNM"
    alert_type: str         # one of config.ALERT_SEVERITY_MAP's keys, or a future module's own string
    module: str             # "gtmetrix", "root_cause", "journey", "dashboard", "sheets", "workflow", ...
    title: str
    message: str
    severity: str = ""      # filled from severity_for(alert_type) if left blank
    affected_page: Optional[str] = None
    root_cause: str = ""
    screenshot_path: Optional[str] = None

    def __post_init__(self):
        if not self.severity:
            self.severity = severity_for(self.alert_type)


@dataclass
class AlertEvent:
    """The outcome of running an Alert through notification_manager: did
    it actually fire (and why), or was it suppressed as a duplicate of
    something already firing?"""
    alert: Alert
    status: str              # "new" | "ongoing_suppressed" | "recovered"
    detected_at: str
    first_seen_at: str
    occurrence_count: int = 1

    @property
    def should_notify(self) -> bool:
        """Only "new" and "recovered" events should ever reach an email/
        Sheets write — "ongoing_suppressed" is exactly the deduplication
        this feature exists to provide."""
        return self.status in ("new", "recovered")

    def to_dict(self) -> dict:
        d = {"status": self.status, "detected_at": self.detected_at,
             "first_seen_at": self.first_seen_at, "occurrence_count": self.occurrence_count}
        d.update(asdict(self.alert))
        return d
