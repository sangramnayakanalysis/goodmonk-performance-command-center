"""
main.py
=======
Entry point. Run with:

    python main.py                # normal run — resumes an interrupted run if one exists
    python main.py --no-resume    # force a fresh run of every page
    python main.py --workers 8    # override concurrency for this run

This is what the GitHub Actions workflow calls. It:
  1. Runs the GTmetrix batch (scheduler.run_batch)
  2. Rebuilds the dashboard JSON from fresh Sheets data (dashboard_data.build_all)
  3. Sends the summary email (email_report.send_report)
  4. Clears run state on a fully successful run, so tomorrow starts clean
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import config
import dashboard_data
import email_report
import google_sheet
import scheduler
from logger import get_logger, setup_logging
from utils import read_json

log = get_logger("main")

# Feature 2 addition: guarded import. journey.py/playwright_runner.py defer
# their actual `import playwright` to the moment a browser is launched (see
# playwright_runner.PlaywrightRunner.__enter__), and that moment is already
# wrapped in its own try/except inside journey.run_all_journeys() — so a
# missing `playwright` package or missing browser binaries is handled there,
# not here. This top-level guard is a second, independent safety net: it
# ensures that even an unrelated problem in the new journey_models.py /
# playwright_runner.py / journey.py files at IMPORT time (a bug, a bad
# environment) can never prevent the existing, working pipeline (GTmetrix,
# RCA, dashboard, email, Sheets) from running.
try:
    import journey
    _JOURNEY_AVAILABLE = True
except ImportError as e:  # pragma: no cover — exercised only if the new modules fail to import at all
    journey = None
    _JOURNEY_AVAILABLE = False
    log.warning("Journey monitoring module unavailable (import failed): %s", e)

# Feature 3 addition: same guarded-import pattern as Feature 2's journey
# import above. A failure to import the alert engine must never prevent
# the existing pipeline (GTmetrix, RCA, Journey, dashboard, email, Sheets)
# from running — alerting just gets skipped for this run, with a log line.
try:
    import alert_rules
    import alerts
    _ALERTS_AVAILABLE = True
except ImportError as e:  # pragma: no cover
    alert_rules = None
    alerts = None
    _ALERTS_AVAILABLE = False
    log.warning("Alert engine unavailable (import failed): %s", e)

# Feature 4 addition: same guarded-import pattern as Features 2/3 above.
# A failure to import the scheduler must never prevent the existing
# pipeline from running — main() falls back to testing every configured
# page every run (the pre-Feature-4 behavior) if this import fails.
try:
    import page_scheduler
    _SCHEDULER_AVAILABLE = True
except ImportError as e:  # pragma: no cover
    page_scheduler = None
    _SCHEDULER_AVAILABLE = False
    log.warning("Intelligent scheduler unavailable (import failed): %s", e)


def main() -> int:
    parser = argparse.ArgumentParser(description="GoodMonk Performance Command Center — run everything.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore saved run state; test every page.")
    parser.add_argument("--workers", type=int, default=None, help="Override MAX_WORKERS for this run.")
    parser.add_argument("--skip-email", action="store_true", help="Skip sending the summary email.")
    args = parser.parse_args()

    setup_logging()
    log.info("=== GoodMonk Performance Command Center run starting ===")

    run_start = time.monotonic()

    # Feature 4 addition: the Intelligent Monitoring Scheduler decides
    # which configured pages are actually due this run, based on each
    # page's own config.py-defined interval_hours — nothing here is
    # hardcoded to "daily" or "hourly"; that cadence lives entirely in
    # config.PAGES and in the GitHub Actions cron expression that decides
    # how often this script gets invoked at all. If the scheduler module
    # is unavailable, this falls back to testing every configured page
    # every run — the exact pre-Feature-4 behavior.
    #
    # v1.0.1 fix (Independent Audit finding C1): this call was previously
    # unguarded — a corrupted data/page_schedule_state.json would raise
    # json.JSONDecodeError here and crash the entire process before any
    # page was tested, before the dashboard rebuilt, before the email
    # sent. Now isolated exactly like every other module's failure
    # boundary in this file: log it, raise an operational alert if the
    # alert engine is available, and fall back to due_pages=None — which
    # already means "scheduler unavailable, test every configured page"
    # everywhere else in this function. No other behavior changes.
    due_pages, skipped_pages = None, []
    if _SCHEDULER_AVAILABLE and config.SCHEDULER_ENABLED:
        try:
            due_pages, skipped_pages = page_scheduler.get_due_pages(config.PAGES)
            if _ALERTS_AVAILABLE:
                alerts.mark_recovered("scheduler", "scheduler_state_failure")
        except Exception as e:  # noqa: BLE001 — corrupted/unreadable scheduler state must never stop monitoring
            log.error("Failed to load scheduler state — falling back to running every configured page: %s", e)
            if _ALERTS_AVAILABLE:
                alerts.raise_operational("scheduler", "scheduler_state_failure", str(e),
                                          root_cause="page_scheduler.get_due_pages() / data/page_schedule_state.json")
            due_pages, skipped_pages = None, []

        if due_pages is not None:
            log.info("Scheduler: %d page(s) due, %d skipped this run.", len(due_pages), len(skipped_pages))
            if not due_pages:
                log.info("No pages due this run — nothing to do until the next scheduled interval elapses.")
                return 0

    results = scheduler.run_batch(resume=not args.no_resume, workers=args.workers, pages=due_pages)

    if not results:
        log.info("No results produced (nothing to run, or everything was already completed). Exiting.")
        return 0

    failed = sum(1 for r in results if not r.success)
    status = "completed" if failed == 0 else "completed_with_failures"

    # Feature 4: next_run now reflects the hourly GitHub Actions cadence
    # (see .github/workflows/monitor.yml's cron) rather than the old daily
    # 9:00 AM IST schedule — this is a necessary, intentional update to
    # match Feature 4's stated goal of hourly execution, not an incidental
    # rewrite. The per-page "true" next-due time (which varies by each
    # page's own interval_hours) is tracked separately, precisely, in
    # page_scheduler's state and surfaced in dashboard/data/scheduler.json.
    next_run = (datetime.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    try:
        dashboard_data.build_all(last_run_status=status, next_run_iso=next_run.isoformat())
        if _ALERTS_AVAILABLE:
            alerts.mark_recovered("dashboard", "dashboard_generation_failure")
    except Exception as e:  # noqa: BLE001 — dashboard regeneration must never fail the whole run
        log.error("Failed to rebuild dashboard data: %s", e)
        if _ALERTS_AVAILABLE:
            alerts.raise_operational("dashboard", "dashboard_generation_failure", str(e), root_cause="build_all()")

    # Feature 1 addition: root-cause dashboard JSON. Isolated in its own
    # try/except exactly like the call above it — a failure here can never
    # affect the existing dashboard files or the rest of the run.
    try:
        dashboard_data.build_root_cause_summary()
        if _ALERTS_AVAILABLE:
            alerts.mark_recovered("dashboard", "dashboard_generation_failure")
    except Exception as e:  # noqa: BLE001
        log.error("Failed to rebuild root-cause dashboard data: %s", e)
        if _ALERTS_AVAILABLE:
            alerts.raise_operational("dashboard", "dashboard_generation_failure", str(e), root_cause="build_root_cause_summary()")

    # Feature 2 addition: Customer Journey Monitoring. Runs after the
    # GTmetrix batch and dashboard rebuild, fully isolated — a Playwright
    # crash, a missing browser binary, or a broken journey run can never
    # affect anything above this block (GTmetrix results, RCA, the existing
    # dashboard files, or the run's exit code, which is still driven only
    # by `failed` from the GTmetrix batch above).
    journey_results = []
    if _JOURNEY_AVAILABLE:
        try:
            # Feature 4 addition: only run journeys for products that are
            # both journey_enabled AND due this run (same due_pages set
            # already computed above) — a page's interval_hours governs
            # its GTmetrix check and its journey check together. When the
            # scheduler is unavailable/disabled, due_pages is None and
            # this falls back to config.JOURNEY_PRODUCTS unfiltered — the
            # exact pre-Feature-4 behavior.
            journey_products = None
            if due_pages is not None:
                due_sheet_names = {p.sheet_name for p in due_pages}
                journey_products = [p for p in config.JOURNEY_PRODUCTS if p.sheet_name in due_sheet_names]

            journey_results = journey.run_all_journeys(products=journey_products)
            for jr in journey_results:
                try:
                    google_sheet.append_journey(journey.to_sheet_row(jr))
                except Exception as e:  # noqa: BLE001 — one journey's Sheets write must not affect others
                    log.error("Failed to write journey result for %s to Google Sheets: %s", jr.product_name, e)
                    if _ALERTS_AVAILABLE:
                        alerts.raise_operational("sheets", "google_sheets_failure", str(e), root_cause=f"Writing journey result for {jr.product_name}")
            dashboard_data.build_journey_summary()
            if _ALERTS_AVAILABLE:
                alerts.mark_recovered("journey", "playwright_failure")
                alerts.mark_recovered("dashboard", "dashboard_generation_failure")
        except Exception as e:  # noqa: BLE001 — journey monitoring must never fail the whole run
            log.error("Journey monitoring failed for this run (GTmetrix/RCA results above are unaffected): %s", e)
            if _ALERTS_AVAILABLE:
                alerts.raise_operational("journey", "playwright_failure", str(e))

    # Feature 4 addition: record this run's outcome per page in the
    # scheduler's own state (separate from Sheets history, RCA, Journey,
    # and Alert state — this only tracks "when did this page last run,
    # and was it healthy" for the purpose of computing when it's next
    # due). One record_run() call per page that actually ran this run —
    # combining its GTmetrix result with its Journey result (if it had
    # one), so a page isn't marked "recovered"/healthy on the strength of
    # a passing GTmetrix test alone if its journey just failed.
    scheduler_meta = {}
    if _SCHEDULER_AVAILABLE and config.SCHEDULER_ENABLED and due_pages is not None:
        try:
            name_to_sheet = {p.name: p.sheet_name for p in config.PAGES}
            gtmetrix_ok_by_sheet = {r.sheet_name: r.success for r in results}
            journey_ok_by_sheet = {
                name_to_sheet[jr.product_name]: jr.success
                for jr in journey_results if jr.product_name in name_to_sheet
            }
            for page in due_pages:
                gtmetrix_ok = gtmetrix_ok_by_sheet.get(page.sheet_name)
                journey_ok = journey_ok_by_sheet.get(page.sheet_name)
                combined_ok = gtmetrix_ok if journey_ok is None else (bool(gtmetrix_ok) and journey_ok)
                page_scheduler.record_run(
                    page.sheet_name, success=bool(combined_ok),
                    ran_gtmetrix=page.sheet_name in gtmetrix_ok_by_sheet,
                    ran_journey=page.sheet_name in journey_ok_by_sheet,
                )

            scheduler_meta = {
                "pages_checked": len(due_pages),
                "pages_skipped": len(skipped_pages),
                "skip_reasons": [f"{s['page']}: {s['reason']}" for s in skipped_pages],
                "journeys_run": len(journey_results),
                "duration_seconds": round(time.monotonic() - run_start, 1),
                "trigger": os.environ.get("GITHUB_EVENT_NAME", "manual/local"),
            }

            try:
                google_sheet.append_scheduler_run([
                    datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%H:%M:%S"),
                    scheduler_meta["pages_checked"], scheduler_meta["pages_skipped"],
                    "; ".join(scheduler_meta["skip_reasons"][:10]), scheduler_meta["journeys_run"],
                    scheduler_meta["duration_seconds"], scheduler_meta["trigger"],
                ])
            except Exception as e:  # noqa: BLE001 — Sheets write must never break the run
                log.error("Failed to write scheduler run summary to Google Sheets: %s", e)
                if _ALERTS_AVAILABLE:
                    alerts.raise_operational("sheets", "google_sheets_failure", str(e), root_cause="Writing scheduler run summary")

            dashboard_data.build_scheduler_summary(last_run_meta=scheduler_meta)
        except Exception as e:  # noqa: BLE001 — scheduler bookkeeping must never fail the run
            log.error("Scheduler state update failed for this run (all monitoring results above are unaffected): %s", e)

    # Feature 3 addition: Smart Alert System. Runs after every other
    # monitoring module has finished, fully isolated — an alert-engine
    # failure here can never affect GTmetrix/RCA/Journey results, the
    # dashboard files already written, or the run's exit code. Reads
    # GTmetrix results directly (already in memory) and RCA data back from
    # the dashboard/data/root_cause.json just built above, so nothing
    # upstream needed to change its return shape to support this.
    should_notify_events = []
    if _ALERTS_AVAILABLE:
        try:
            gtmetrix_candidates, gtmetrix_universe = alert_rules.rules_for_gtmetrix(results)
            events = alerts.evaluate_domain("gtmetrix", gtmetrix_candidates, gtmetrix_universe)
            should_notify_events += [e for e in events if e.should_notify]

            history_data = read_json(config.DASHBOARD_DATA_DIR / "history.json", default=[])
            history_by_page: dict[str, list[dict]] = {}
            for row in history_data:
                history_by_page.setdefault(row.get("_sheet_name", ""), []).append(row)
            drop_candidates, drop_universe = alert_rules.rules_for_score_drops(history_by_page)
            events = alerts.evaluate_domain("score_trend", drop_candidates, drop_universe)
            should_notify_events += [e for e in events if e.should_notify]

            rc_data = read_json(config.DASHBOARD_DATA_DIR / "root_cause.json", default=None)
            if rc_data:
                rca_candidates, rca_universe = alert_rules.rules_for_root_cause(rc_data.get("pages", []))
                events = alerts.evaluate_domain("root_cause", rca_candidates, rca_universe)
                should_notify_events += [e for e in events if e.should_notify]

            if journey_results:
                journey_candidates, journey_universe = alert_rules.rules_for_journey(journey_results)
                events = alerts.evaluate_domain("journey", journey_candidates, journey_universe)
                should_notify_events += [e for e in events if e.should_notify]

            dashboard_data.build_alerts_summary()
        except Exception as e:  # noqa: BLE001 — the alert engine itself must never fail the run
            log.error("Alert evaluation failed for this run (all monitoring results above are unaffected): %s", e)

    if not args.skip_email:
        # Feature 1 addition: pass today's root-cause reports (if the
        # dashboard rebuild above succeeded and produced any) so the email
        # can include a "Root Cause Highlights" section. Falls back to the
        # original call shape (no root-cause data) on any failure here —
        # the base email must always still send.
        root_cause_reports = None
        try:
            rc_data = read_json(config.DASHBOARD_DATA_DIR / "root_cause.json", default=None)
            if rc_data:
                root_cause_reports = [
                    p["latest_report"] for p in rc_data.get("pages", []) if p.get("latest_report")
                ]
        except Exception as e:  # noqa: BLE001 — must never block the email from sending
            log.warning("Could not load root-cause data for email (sending base report anyway): %s", e)

        email_report.send_report(
            results,
            root_cause_reports=root_cause_reports,
            journey_results=[jr.to_dict() for jr in journey_results] if journey_results else None,
            alert_events=[e.to_dict() for e in should_notify_events] if should_notify_events else None,
            scheduler_meta=scheduler_meta or None,
        )

    if failed == 0:
        scheduler.clear_run_state()

    log.info("=== Run finished. %d/%d pages succeeded. ===", len(results) - failed, len(results))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
