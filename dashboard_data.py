"""
dashboard_data.py
==================
Builds the JSON files the static dashboard (GitHub Pages, no server)
reads. Pulls fresh history from Google Sheets for every configured
page, computes aggregate stats and trends, and writes everything
atomically into dashboard/data/*.json.

Output files:
  summary.json   — KPI strip: totals, averages, best/worst, last run
  pages.json     — one entry per page: latest metrics + status color
  trends.json    — daily/weekly/monthly aggregated series for charts
  history.json   — full flattened history (used for search/filter/export)
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from statistics import mean

import config
import google_sheet
from logger import get_logger
from utils import now_iso, write_json

log = get_logger("dashboard_data")


def _status_color(score, lcp) -> str:
    """Traffic-light classification used throughout the dashboard.
    None (no successful run yet) is "grey" (unknown), distinct from "red"
    (critical) — a page that hasn't run yet isn't the same as one that's
    actually performing badly."""
    if score is None:
        return "grey"
    if score < config.ALERT_SCORE_THRESHOLD or (lcp is not None and lcp > config.ALERT_LCP_THRESHOLD_SECONDS):
        return "red" if score < config.ALERT_SCORE_THRESHOLD - 15 else "yellow"
    return "green"


def _period_key(date_str: str, granularity: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    if granularity == "daily":
        return date_str
    if granularity == "weekly":
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return d.strftime("%Y-%m")  # monthly


def build_all(last_run_status: str = "completed", next_run_iso: str | None = None) -> None:
    all_rows: list[dict] = []
    page_entries: list[dict] = []

    for page in config.PAGES:
        try:
            rows = google_sheet.read_history(page.sheet_name)
        except Exception as e:  # noqa: BLE001 — one broken sheet must not blank the whole dashboard
            log.error("Could not read history for %s: %s", page.sheet_name, e)
            rows = []

        for r in rows:
            r["_page_name"] = page.name
            r["_sheet_name"] = page.sheet_name
            r["_url"] = page.url
        all_rows.extend(rows)

        ok_rows = [r for r in rows if r.get("Status") == "OK" and r.get("Performance Score") not in (None, "")]
        latest = ok_rows[-1] if ok_rows else None

        score = float(latest["Performance Score"]) if latest else None
        lcp = float(latest["LCP"]) if latest and latest.get("LCP") not in (None, "") else None

        page_entries.append({
            "name": page.name,
            "sheet_name": page.sheet_name,
            "url": page.url,
            "latest": latest,
            "status_color": _status_color(score, lcp),
            "total_runs": len(rows),
            "failed_runs": sum(1 for r in rows if r.get("Status") == "Failed"),
        })

    # --- summary.json --------------------------------------------------
    ok_rows_all = [r for r in all_rows if r.get("Status") == "OK" and r.get("Performance Score") not in (None, "")]
    scores = [float(r["Performance Score"]) for r in ok_rows_all]
    best = max(page_entries, key=lambda p: (p["latest"] or {}).get("Performance Score", -1) if p["latest"] else -1, default=None)
    worst = min(
        (p for p in page_entries if p["latest"]),
        key=lambda p: p["latest"].get("Performance Score", 101),
        default=None,
    )

    summary = {
        "generated_at": now_iso(),
        "last_run_status": last_run_status,
        "next_scheduled_run": next_run_iso,
        "total_urls": len(config.PAGES),
        "average_score": round(mean(scores), 2) if scores else None,
        "best_page": best["name"] if best else None,
        "worst_page": worst["name"] if worst else None,
        "healthy_count": sum(1 for p in page_entries if p["status_color"] == "green"),
        "warning_count": sum(1 for p in page_entries if p["status_color"] == "yellow"),
        "critical_count": sum(1 for p in page_entries if p["status_color"] == "red"),
        "no_data_count": sum(1 for p in page_entries if p["status_color"] == "grey"),
    }
    write_json(config.DASHBOARD_DATA_DIR / "summary.json", summary)

    # --- pages.json ------------------------------------------------------
    write_json(config.DASHBOARD_DATA_DIR / "pages.json", page_entries)

    # --- trends.json -------------------------------------------------
    trends = {}
    for granularity in ("daily", "weekly", "monthly"):
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in ok_rows_all:
            date_str = str(r.get("Date"))
            try:
                key = _period_key(date_str, granularity)
            except ValueError:
                continue
            buckets[key].append(r)

        series = []
        for key in sorted(buckets.keys()):
            rows = buckets[key]
            s = [float(r["Performance Score"]) for r in rows if r.get("Performance Score") not in (None, "")]
            l = [float(r["LCP"]) for r in rows if r.get("LCP") not in (None, "")]
            f = [float(r["Fully Loaded"]) for r in rows if r.get("Fully Loaded") not in (None, "")]
            series.append({
                "period": key,
                "avg_score": round(mean(s), 2) if s else None,
                "avg_lcp": round(mean(l), 2) if l else None,
                "avg_fully_loaded": round(mean(f), 2) if f else None,
            })
        trends[granularity] = series
    write_json(config.DASHBOARD_DATA_DIR / "trends.json", trends)

    # --- history.json (flattened, for search/filter/export) ------------
    write_json(config.DASHBOARD_DATA_DIR / "history.json", all_rows)

    log.info("Dashboard data written: %d page(s), %d total history row(s).", len(page_entries), len(all_rows))


# =============================================================================
# Feature 1 addition (Root Cause Analysis) — new function, new output file.
# `build_all()` above is completely untouched: its 4 existing JSON files
# keep their exact current shape. This writes a 5th, new file.
# =============================================================================

def build_root_cause_summary(rows_per_page: int = 10) -> None:
    """
    Reads each page's "<sheet>_RootCause" tab (written by scheduler.py via
    root_cause.py) and writes dashboard/data/root_cause.json — the latest
    root-cause report per page plus a small trend of recent issue counts.

    Isolated per-page (one broken sheet must not blank the whole file) and
    isolated as a whole (called from main.py in its own try/except, exactly
    like the existing `build_all()` call is) so a failure here can never
    affect the existing dashboard JSON files.
    """
    import google_sheet  # local import: keeps this optional feature's
                          # dependency separate from build_all()'s imports

    page_reports = []
    for page in config.PAGES:
        try:
            rows = google_sheet.read_root_cause_history(page.sheet_name, limit=rows_per_page)
        except Exception as e:  # noqa: BLE001
            log.error("Could not read root-cause history for %s: %s", page.sheet_name, e)
            rows = []

        latest = rows[-1] if rows else None
        recent_issue_counts = [
            int(r["Issue Count"]) for r in rows if str(r.get("Issue Count", "")).isdigit()
        ]

        page_reports.append({
            "name": page.name,
            "sheet_name": page.sheet_name,
            "latest_report": json.loads(latest["Report JSON"]) if latest and latest.get("Report JSON") else None,
            "recent_issue_counts": recent_issue_counts,
        })

    write_json(config.DASHBOARD_DATA_DIR / "root_cause.json", {
        "generated_at": now_iso(),
        "pages": page_reports,
    })
    log.info("Root-cause dashboard data written: %d page(s).", len(page_reports))


# =============================================================================
# Feature 2 addition (Customer Journey Monitoring) — new function, new
# output file. build_all() and build_root_cause_summary() above are both
# completely untouched.
# =============================================================================

def build_journey_summary(history_limit: int = 50) -> None:
    """
    Reads the shared "CustomerJourney" Sheets tab and writes
    dashboard/data/journey.json: the latest run per product, a simple
    success/fail timeline per product, and an overall success rate.

    Isolated exactly like build_root_cause_summary — a failure here can
    never affect any of the other 5 dashboard JSON files.
    """
    import google_sheet  # local import — keeps this optional feature's
                          # dependency separate from build_all()'s imports

    try:
        rows = google_sheet.read_journey_history(limit=history_limit)
    except Exception as e:  # noqa: BLE001
        log.error("Could not read journey history: %s", e)
        rows = []

    by_product: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_product[r.get("Product", "Unknown")].append(r)

    products = []
    for name, product_rows in by_product.items():
        latest = product_rows[-1] if product_rows else None
        timeline = [
            {"date": r.get("Date"), "time": r.get("Time"), "success": r.get("Overall Status") == "Success"}
            for r in product_rows[-history_limit:]
        ]
        products.append({
            "product_name": name,
            "latest_status": latest.get("Overall Status") if latest else None,
            "latest_failed_step": latest.get("Failed Step") if latest else None,
            "latest_duration_seconds": latest.get("Duration (s)") if latest else None,
            "latest_details": json.loads(latest["Details JSON"]) if latest and latest.get("Details JSON") else None,
            "timeline": timeline,
            "total_runs": len(product_rows),
            "successful_runs": sum(1 for r in product_rows if r.get("Overall Status") == "Success"),
        })

    total_runs = sum(p["total_runs"] for p in products)
    total_success = sum(p["successful_runs"] for p in products)

    write_json(config.DASHBOARD_DATA_DIR / "journey.json", {
        "generated_at": now_iso(),
        "products": products,
        "overall_success_rate": round(100 * total_success / total_runs, 1) if total_runs else None,
    })
    log.info("Journey dashboard data written: %d product(s).", len(products))


# =============================================================================
# Feature 3 addition (Smart Alert System) — new function, new output file.
# build_all(), build_root_cause_summary(), and build_journey_summary()
# above are all completely untouched.
# =============================================================================

def build_alerts_summary(history_limit: int = 200) -> None:
    """
    Reads the shared "AlertHistory" Sheets tab and writes
    dashboard/data/alerts.json: recent alerts, currently-active alerts by
    severity, recently-recovered alerts, counts, and a simple daily trend.

    Isolated exactly like the other build_*_summary functions — a failure
    here can never affect any of the other dashboard JSON files.
    """
    import google_sheet  # local import — same pattern as the other build_*_summary functions

    try:
        rows = google_sheet.read_alert_history(limit=history_limit)
    except Exception as e:  # noqa: BLE001
        log.error("Could not read alert history: %s", e)
        rows = []

    # A "New" alert is still active unless a later "Recovered" row exists
    # for the same alert type + affected page — reduce to latest status
    # per (type, page) key.
    latest_by_key: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("Alert Type"), r.get("Affected Page"))
        latest_by_key[key] = r  # rows are chronological, so the last write wins

    currently_active = [r for r in latest_by_key.values() if r.get("Status") == "New"]
    recently_recovered = [r for r in rows[-history_limit:] if r.get("Status") == "Recovered"][-20:]

    severity_counts = {"critical": 0, "high": 0, "warning": 0, "info": 0}
    for r in currently_active:
        sev = (r.get("Severity") or "warning").lower()
        if sev in severity_counts:
            severity_counts[sev] += 1

    daily_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("Status") == "New" and r.get("Date"):
            daily_counts[str(r["Date"])] += 1
    trend = [{"date": d, "count": c} for d, c in sorted(daily_counts.items())][-30:]

    write_json(config.DASHBOARD_DATA_DIR / "alerts.json", {
        "generated_at": now_iso(),
        "recent_alerts": rows[-30:][::-1],  # most recent first
        "active_alerts": currently_active,
        "recovered_alerts": recently_recovered[::-1],
        "severity_counts": severity_counts,
        "total_active": len(currently_active),
        "trend": trend,
    })
    log.info("Alerts dashboard data written: %d active, %d recent.", len(currently_active), len(rows))


# =============================================================================
# Feature 4 addition (Intelligent Monitoring Scheduler) — new function,
# new output file. build_all(), build_root_cause_summary(),
# build_journey_summary(), and build_alerts_summary() above are all
# completely untouched.
# =============================================================================

def build_scheduler_summary(last_run_meta: dict | None = None) -> None:
    """
    Writes dashboard/data/scheduler.json: per-page schedule state (last
    run, next run, failure streak), plus this run's checked/skipped
    counts if `last_run_meta` is provided by main.py.

    Isolated exactly like the other build_*_summary functions — a
    failure here can never affect any of the other dashboard JSON files.
    """
    import page_scheduler  # local import — same pattern as the other build_*_summary functions

    try:
        summary = page_scheduler.get_scheduler_summary()
    except Exception as e:  # noqa: BLE001
        log.error("Could not read scheduler state: %s", e)
        summary = {"generated_at": now_iso(), "pages": []}

    pages = summary["pages"]
    next_runs = [p["next_run"] for p in pages if p.get("next_run")]
    last_successes = [p["last_successful_run"] for p in pages if p.get("last_successful_run")]
    unhealthy = [p for p in pages if (p.get("failure_count") or 0) >= 3]

    write_json(config.DASHBOARD_DATA_DIR / "scheduler.json", {
        "generated_at": now_iso(),
        "pages": pages,
        "next_scheduled_run_overall": min(next_runs) if next_runs else None,
        "last_successful_run_overall": max(last_successes) if last_successes else None,
        "unhealthy_pages": [{"name": p["name"], "failure_count": p["failure_count"]} for p in unhealthy],
        "scheduler_health": "critical" if unhealthy else "healthy",
        "last_run": last_run_meta or {},
    })
    log.info("Scheduler dashboard data written: %d page(s), %d unhealthy.", len(pages), len(unhealthy))


if __name__ == "__main__":
    build_all()
