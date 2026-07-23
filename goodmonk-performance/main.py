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
import sys
from datetime import datetime, timedelta

import dashboard_data
import email_report
import scheduler
from logger import get_logger, setup_logging

log = get_logger("main")


def main() -> int:
    parser = argparse.ArgumentParser(description="GoodMonk Performance Command Center — run everything.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore saved run state; test every page.")
    parser.add_argument("--workers", type=int, default=None, help="Override MAX_WORKERS for this run.")
    parser.add_argument("--skip-email", action="store_true", help="Skip sending the summary email.")
    args = parser.parse_args()

    setup_logging()
    log.info("=== GoodMonk Performance Command Center run starting ===")

    results = scheduler.run_batch(resume=not args.no_resume, workers=args.workers)

    if not results:
        log.info("No results produced (nothing to run, or everything was already completed). Exiting.")
        return 0

    failed = sum(1 for r in results if not r.success)
    status = "completed" if failed == 0 else "completed_with_failures"

    # Next run: 9:00 AM IST daily, matching the GitHub Actions schedule.
    next_run = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    try:
        dashboard_data.build_all(last_run_status=status, next_run_iso=next_run.isoformat())
    except Exception as e:  # noqa: BLE001 — dashboard regeneration must never fail the whole run
        log.error("Failed to rebuild dashboard data: %s", e)

    if not args.skip_email:
        email_report.send_report(results)

    if failed == 0:
        scheduler.clear_run_state()

    log.info("=== Run finished. %d/%d pages succeeded. ===", len(results) - failed, len(results))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
