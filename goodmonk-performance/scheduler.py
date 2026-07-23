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
from gtmetrix import PageResult, run_single_page
from logger import get_logger
from utils import now_iso, read_json, write_json

log = get_logger("scheduler")

STATE_FILE = config.DATA_DIR / "run_state.json"


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


def run_batch(resume: bool = True, workers: int | None = None) -> list[PageResult]:
    """
    Runs GTmetrix tests for every page in config.PAGES concurrently.

    resume=True (default): pages already marked completed in the local
    run-state file from an interrupted run today are skipped, so a
    re-run after a crash doesn't burn GTmetrix credits re-testing pages
    that already succeeded. Pass resume=False to force a full clean run
    (this is what the daily scheduled GitHub Actions run should do —
    each day is a fresh baseline).
    """
    workers = workers or config.MAX_WORKERS
    pages = list(config.PAGES)

    completed = _load_completed_sheet_names() if resume else set()
    if completed:
        pages = [p for p in pages if p.sheet_name not in completed]
        log.info("Resuming: skipping %d already-completed page(s) from a prior interrupted run.",
                  len(config.PAGES) - len(pages))

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


def clear_run_state() -> None:
    write_json(STATE_FILE, {"updated_at": now_iso(), "completed_sheet_names": [], "last_results": []})
    log.info("Run state cleared.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the GoodMonk GTmetrix batch directly.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore any saved run state; run everything.")
    parser.add_argument("--workers", type=int, default=None, help="Override MAX_WORKERS for this run.")
    args = parser.parse_args()

    run_batch(resume=not args.no_resume, workers=args.workers)
