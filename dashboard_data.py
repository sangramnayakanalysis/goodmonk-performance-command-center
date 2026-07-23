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


if __name__ == "__main__":
    build_all()
