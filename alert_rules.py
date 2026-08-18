"""
alert_rules.py
===============
Feature 3 (Smart Alert System): the only file that knows how to translate
each *specific* monitoring module's output (GTmetrix PageResults, Root
Cause reports, Journey results) into generic `Alert` objects.

This is deliberately the single seam between "domain-specific data" and
"generic alert engine" — notification_manager.py and alerts.py never
import gtmetrix.py, root_cause.py, or journey.py, and this file never
touches persistent state or sends anything. A future module (SSL,
Lighthouse, API monitoring...) adds one function here, following the same
shape, and the rest of the engine needs zero changes.

Every function returns (candidate_alerts: dict[str, Alert], universe_keys:
set[str]) — ready to hand straight to notification_manager.process_batch().
"""

from __future__ import annotations

import config
from alert_models import Alert
from root_cause import (
    SEVERITY_CRITICAL as RCA_CRITICAL,
    SEVERITY_INFO as RCA_INFO,
    SEVERITY_WARNING as RCA_WARNING,
)

_RCA_SEVERITY_RANK = {RCA_INFO: 0, RCA_WARNING: 1, RCA_CRITICAL: 2}

# Maps root_cause.py's category constants to this engine's alert types.
# Categories not listed here simply never become alerts (e.g. a category
# root_cause.py adds later needs a mapping added here to alert on it —
# until then it still shows up in the RCA report/email/dashboard, just
# not as a separate alert).
_RCA_CATEGORY_TO_ALERT_TYPE = {
    "high_ttfb": "high_ttfb",
    "slow_server_response": "slow_server_response",
    "high_lcp": "high_lcp",
    "high_cls": "high_cls",
    "large_images": "large_images",
    "heavy_javascript": "heavy_javascript",
    "heavy_css": "heavy_css",
    "large_dom": "high_dom_size",
}


def rules_for_gtmetrix(results: list) -> tuple[dict[str, Alert], set[str]]:
    """
    GTmetrix page-failure alerts. `results` is the list[PageResult] from
    scheduler.run_batch(). If every configured page failed in the same
    run, that's treated as one systemic "gtmetrix_api_failure" alert
    instead of N separate page alerts (config.ALERT_ALL_PAGES_FAILED_IS_SYSTEMIC).
    """
    universe = {r.sheet_name for r in results}
    candidates: dict[str, Alert] = {}

    all_failed = results and all(not r.success for r in results)
    if all_failed and config.ALERT_ALL_PAGES_FAILED_IS_SYSTEMIC:
        # One alert covers the whole batch; still report it under every
        # page's key so per-page recovery works correctly once things
        # start succeeding again.
        for r in results:
            candidates[r.sheet_name] = Alert(
                alert_key=f"gtmetrix:api_failure:{r.sheet_name}", alert_type="gtmetrix_api_failure",
                module="gtmetrix", title="GTmetrix API Failure",
                message=f"Every configured page failed this run ({len(results)}/{len(results)}) — likely a systemic GTmetrix API or network issue.",
                affected_page=r.page_name, root_cause=r.error_message,
            )
        return candidates, universe

    for r in results:
        if r.success:
            continue
        alert_type = "homepage_failed" if r.page_name == "Homepage" else "page_failed"
        candidates[r.sheet_name] = Alert(
            alert_key=f"gtmetrix:{alert_type}:{r.sheet_name}", alert_type=alert_type,
            module="gtmetrix", title=f"{r.page_name} Failed",
            message=f"GTmetrix test failed for {r.page_name}: {r.error_message}",
            affected_page=r.page_name, root_cause=r.error_message,
        )
    return candidates, universe


def rules_for_score_drops(history_rows_by_page: dict[str, list[dict]]) -> tuple[dict[str, Alert], set[str]]:
    """
    Score/grade drop alerts, comparing each page's latest two successful
    runs. `history_rows_by_page` — {sheet_name: [row dicts in chronological
    order]}, e.g. built from dashboard/data/history.json grouped by page.
    """
    grade_order = ["F", "E", "D", "C", "B", "A"]  # worst -> best, matches GTmetrix grades
    universe = set(history_rows_by_page.keys())
    candidates: dict[str, Alert] = {}

    for sheet_name, rows in history_rows_by_page.items():
        ok_rows = [r for r in rows if r.get("Status") == "OK" and r.get("Performance Score") not in (None, "")]
        if len(ok_rows) < 2:
            continue
        prev, latest = ok_rows[-2], ok_rows[-1]
        try:
            prev_score, latest_score = float(prev["Performance Score"]), float(latest["Performance Score"])
        except (TypeError, ValueError):
            continue

        drop = prev_score - latest_score
        if drop >= config.ALERT_SCORE_DROP_THRESHOLD:
            page_name = latest.get("_page_name", sheet_name)
            candidates[sheet_name] = Alert(
                alert_key=f"score_drop:{sheet_name}", alert_type="performance_score_drop",
                module="gtmetrix", title=f"Performance Score Drop — {page_name}",
                message=f"{page_name}'s score dropped from {prev_score} to {latest_score} ({drop:.1f} points).",
                affected_page=page_name,
            )
            continue  # one alert per page per run — don't also fire a grade-drop on top

        if config.ALERT_GRADE_DROP_ENABLED:
            prev_grade, latest_grade = prev.get("Grade"), latest.get("Grade")
            if prev_grade in grade_order and latest_grade in grade_order:
                if grade_order.index(latest_grade) < grade_order.index(prev_grade):
                    page_name = latest.get("_page_name", sheet_name)
                    candidates[sheet_name] = Alert(
                        alert_key=f"grade_drop:{sheet_name}", alert_type="performance_grade_drop",
                        module="gtmetrix", title=f"Performance Grade Drop — {page_name}",
                        message=f"{page_name}'s grade dropped from {prev_grade} to {latest_grade}.",
                        affected_page=page_name,
                    )

    return candidates, universe


def rules_for_root_cause(page_reports: list[dict]) -> tuple[dict[str, Alert], set[str]]:
    """
    `page_reports` — dicts shaped like dashboard/data/root_cause.json's
    per-page entries, each with "sheet_name" and "latest_report"
    (root_cause.RootCauseReport.to_dict()'s shape: "page_name", "issues"
    — a list of {"category","severity","title","detail","recommendation"}).
    Deliberately dict-based rather than importing root_cause.RootCauseReport
    directly: scheduler.py analyzes and writes RCA reports per-page without
    ever returning the report objects themselves back up to main.py (only
    the Sheets row), and re-deriving from the JSON we already build for the
    dashboard avoids changing that existing, working return contract.
    """
    universe: set[str] = set()
    candidates: dict[str, Alert] = {}
    min_rank = _RCA_SEVERITY_RANK.get(config.ALERT_MIN_RCA_SEVERITY, 1)

    for page in page_reports:
        sheet_name = page.get("sheet_name")
        report = page.get("latest_report")
        if not sheet_name or not report:
            continue

        for alert_type in set(_RCA_CATEGORY_TO_ALERT_TYPE.values()):
            universe.add(f"{sheet_name}:{alert_type}")

        for issue in report.get("issues", []):
            alert_type = _RCA_CATEGORY_TO_ALERT_TYPE.get(issue.get("category"))
            if not alert_type:
                continue
            issue_severity = issue.get("severity", RCA_INFO)
            if _RCA_SEVERITY_RANK.get(issue_severity, 0) < min_rank:
                continue
            key = f"{sheet_name}:{alert_type}"
            candidates[key] = Alert(
                alert_key=f"rca:{key}", alert_type=alert_type, module="root_cause",
                title=f"{issue.get('title', alert_type)} — {report.get('page_name', sheet_name)}",
                message=issue.get("detail", ""),
                affected_page=report.get("page_name", sheet_name), root_cause=issue.get("recommendation", ""),
                severity="critical" if issue_severity == RCA_CRITICAL else "warning",
            )

    return candidates, universe


def rules_for_journey(results: list) -> tuple[dict[str, Alert], set[str]]:
    """`results` — list[journey_models.JourneyResult] from journey.run_all_journeys()."""
    universe = {r.product_name for r in results}
    candidates: dict[str, Alert] = {}

    for r in results:
        if r.success:
            continue
        if r.failed_step == "homepage":
            alert_type, title = "website_down", "Website Down"
            message = f"Homepage failed to load during the customer journey check for {r.product_name}."
        elif r.failed_step == "checkout":
            alert_type, title = "checkout_failed", f"Checkout Failed — {r.product_name}"
            message = f"Checkout step failed for {r.product_name}."
        else:
            alert_type, title = "journey_failed", f"Customer Journey Failed — {r.product_name}"
            message = f"Journey failed for {r.product_name} at step '{r.failed_step}'."

        failed_step_result = next((s for s in r.steps if s.step_name == r.failed_step), None)
        candidates[r.product_name] = Alert(
            alert_key=f"journey:{alert_type}:{r.product_name}", alert_type=alert_type,
            module="journey", title=title, message=message, affected_page=r.product_name,
            root_cause=failed_step_result.error_message if failed_step_result else "",
            screenshot_path=failed_step_result.screenshot_path if failed_step_result else None,
        )

    return candidates, universe
