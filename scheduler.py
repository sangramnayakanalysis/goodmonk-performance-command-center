"""
scheduler.py
============
Orchestrates a full monitoring run: fans pages out across a thread pool
(GTmetrix I/O is network-bound, so threads are the right tool — no GIL
contention concern here), writes each result to Google Sheets as it
completes, and tracks a local run-state file so a run that gets
interrupted (killed CI job, network outage) can be resumed without
reprocessing pages that already succeeded.

There is no Apps Script-style execution-time ceiling here — a GitHub
Actions job gets up to 6 hours by default — so this is about
resilience and speed (parallelism), not survival past a hard timeout.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

import config
import google_sheet
import root_cause
from gtmetrix import PageResult, run_single_page
from logger import get_logger
from utils import now_iso, read_json, write_json

log = get_logger("scheduler")

# Feature 3 addition: guarded import, same defensive pattern as main.py's
# `import journey` guard — if alerts.py fails to import for any reason,
# the entire existing GTmetrix pipeline (this file's core job) must keep
# working exactly as before; alert-raising just gets skipped with a log line.
try:
    import alerts as _alerts
    _ALERTS_AVAILABLE = True
except ImportError as e:  # pragma: no cover
    _alerts = None
    _ALERTS_AVAILABLE = False
    log.warning("Alert engine unavailable in scheduler.py (import failed): %s", e)

STATE_FILE = config.DATA_DIR / "run_state.json"


def _safe_alert_call(fn, *args, **kwargs) -> None:
    """
    Production-hardening cleanup: every alert-engine call from this file
    was already wrapped in its own try/except-pass so a hiccup in the
    (optional) alert engine could never affect the core GTmetrix write
    path — this just centralizes that repeated try/except-pass into one
    place instead of duplicating it at every call site. No behavior
    change: still silently swallows any exception raised while running
    `fn`. Callers must still guard with `if _ALERTS_AVAILABLE:` before
    referencing `_alerts.<method>` as the `fn` argument — `_alerts` is
    `None` when the alert engine failed to import, and Python evaluates
    that attribute access before this function ever runs.
    """
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — the alert engine itself must never break this path
        pass


def _load_completed_sheet_names() -> set[str]:
    state = read_json(STATE_FILE, default={})
    return set(state.get("completed_sheet_names", []))


def _save_state(completed: set[str], results: list[PageResult]) -> None:
    write_json(STATE_FILE, {
        "updated_at": now_iso(),
        "completed_sheet_names": sorted(completed),
        "last_results": [
            {**asdict(r), "metrics": asdict(r.metrics)} for r in results
        ],
    })


def run_batch(resume: bool = True, workers: int | None = None, pages: list | None = None) -> list[PageResult]:
    """
    Runs GTmetrix tests for every page in `pages` (defaults to
    config.PAGES — unchanged from before Feature 4) concurrently.

    Feature 4 addition: the new optional `pages` parameter lets a caller
    (main.py, via page_scheduler.get_due_pages()) pass in a filtered
    subset — e.g. only the pages an hourly run has actually decided are
    due right now. Every existing call site that doesn't pass `pages`
    keeps testing all of config.PAGES exactly as before.

    resume=True (default): pages already marked completed in the local
    run-state file from an interrupted run today are skipped, so a
    re-run after a crash doesn't burn GTmetrix credits re-testing pages
    that already succeeded. Pass resume=False to force a full clean run
    (this is what the daily scheduled GitHub Actions run should do —
    each day is a fresh baseline).
    """
    workers = workers or config.MAX_WORKERS
    pages = list(pages) if pages is not None else list(config.PAGES)
    all_requested_pages = list(pages)

    completed = _load_completed_sheet_names() if resume else set()
    if completed:
        pages = [p for p in pages if p.sheet_name not in completed]
        log.info("Resuming: skipping %d already-completed page(s) from a prior interrupted run.",
                  len(all_requested_pages) - len(pages))

    if not pages:
        log.info("Nothing to do — all pages already completed for this run.")
        return []

    log.info("Starting batch run for %d page(s) with %d worker(s).", len(pages), workers)

    results: list[PageResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_page = {
            pool.submit(run_single_page, p.name, p.sheet_name, p.url): p
            for p in pages
        }

        for future in as_completed(future_to_page):
            page = future_to_page[future]
            try:
                result = future.result()
            except Exception as e:  # noqa: BLE001 — safety net; run_single_page already catches internally
                log.error("Unexpected top-level failure for %s: %s", page.sheet_name, e)
                result = PageResult(page_name=page.name, sheet_name=page.sheet_name,
                                     url=page.url, success=False, error_message=str(e))

            _record_result(result)
            results.append(result)

            if result.success:
                completed.add(result.sheet_name)
            _save_state(completed, results)

    success_count = sum(1 for r in results if r.success)
    failed_count = len(results) - success_count
    log.info("Batch finished. Success: %d, Failed: %d.", success_count, failed_count)

    return results


def _record_result(result: PageResult) -> None:
    """Writes one page's outcome to Google Sheets. Isolated in its own
    try/except so a Sheets API hiccup on one page doesn't take down the
    rest of the batch — matches the "one bad page never stops the run"
    guarantee from the original script."""
    try:
        if result.success:
            google_sheet.append_result(result.sheet_name, result.metrics)
        else:
            google_sheet.append_failure(result.sheet_name, result.error_message)
    except Exception as e:  # noqa: BLE001 — must never propagate out of a batch worker
        log.error("Failed to write result for %s to Google Sheets: %s", result.sheet_name, e)
        if _ALERTS_AVAILABLE:
            _safe_alert_call(_alerts.raise_operational, "sheets", "google_sheets_failure", str(e),
                              root_cause=f"Writing result for {result.sheet_name}")
    else:
        if _ALERTS_AVAILABLE:
            _safe_alert_call(_alerts.mark_recovered, "sheets", "google_sheets_failure")

    # --- Feature 1 addition: Root Cause Analysis ---------------------------
    # Isolated in its own try/except, exactly like the existing block above.
    # A failure here can never affect the existing history write (already
    # completed above) or take down the rest of the batch.
    #
    # Feature 4 addition: gated on the page's `root_cause_enabled` flag —
    # config.PAGE_BY_SHEET_NAME defaults every existing page to True, so
    # this is a no-op change unless a page explicitly opts out in config.py.
    page_config = config.PAGE_BY_SHEET_NAME.get(result.sheet_name)
    rca_enabled = page_config.root_cause_enabled if page_config else True
    if result.success and rca_enabled:
        try:
            report = root_cause.analyze(result.page_name, result.sheet_name, result.metrics)
            google_sheet.append_root_cause(result.sheet_name, root_cause.to_sheet_row(report))
            if report.issue_count:
                log.info("Root cause: %s -> %d issue(s), top: %s",
                          result.sheet_name, report.issue_count, report.top_issue)
        except Exception as e:  # noqa: BLE001 — RCA must never affect the core monitoring run
            log.error("Root cause analysis failed for %s: %s", result.sheet_name, e)


def clear_run_state() -> None:
    write_json(STATE_FILE, {"updated_at": now_iso(), "completed_sheet_names": [], "last_results": []})
    log.info("Run state cleared.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the GoodMonk GTmetrix batch directly.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore any saved run state; run everything.")
    parser.add_argument("--workers", type=int, default=None, help="Override MAX_WORKERS for this run.")
    args = parser.parse_args()

    run_batch(resume=not args.no_resume, workers=args.workers)
