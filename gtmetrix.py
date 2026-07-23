"""
gtmetrix.py
===========
GTmetrix API v2 client. Mirrors the exact call shape and metric set the
Apps Script version used (start test -> poll -> fetch report -> extract
metrics), so historical Sheets data stays consistent, but runs natively
in Python with no execution-time ceiling and real concurrency.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Optional

import requests

import config
from logger import get_api_logger, get_logger
from utils import RateLimitedError, retry_with_backoff

log = get_logger("gtmetrix")
api_log = get_api_logger()

_AUTH_HEADER = {
    "Authorization": "Basic "
    + base64.b64encode(f"{config.GTMETRIX_API_KEY}:".encode()).decode()
}


@dataclass
class Metrics:
    performance_score: Optional[float] = None
    grade: str = "N/A"
    lcp: Optional[float] = None            # seconds
    onload: Optional[float] = None         # seconds
    fully_loaded: Optional[float] = None   # seconds
    ttfb: Optional[float] = None           # seconds
    cls: Optional[float] = None
    tbt: Optional[float] = None            # seconds
    report_url: Optional[str] = None
    status: str = "OK"                     # "OK" | "Error"
    error_message: str = ""


@dataclass
class PageResult:
    page_name: str
    sheet_name: str
    url: str
    metrics: Metrics = field(default_factory=Metrics)
    success: bool = False
    error_message: str = ""


class GTmetrixError(Exception):
    """Raised for non-retryable / terminal GTmetrix failures for one page."""


def _raise_for_response(resp: requests.Response, context: str) -> None:
    if resp.status_code == 429:
        raise RateLimitedError(f"{context}: rate limited (429)")
    if resp.status_code >= 500:
        raise RuntimeError(f"{context}: server error {resp.status_code}: {resp.text[:300]}")
    if not resp.ok:
        # Non-retryable client error (bad request, auth failure, etc.)
        raise GTmetrixError(f"{context}: HTTP {resp.status_code}: {resp.text[:300]}")


@retry_with_backoff(
    max_retries=config.API_MAX_RETRIES,
    base_delay_seconds=config.API_RETRY_BASE_DELAY_SECONDS,
    rate_limit_wait_seconds=config.RATE_LIMIT_WAIT_SECONDS,
    label="startTest",
)
def start_test(url: str) -> str:
    """Starts a GTmetrix test. Returns the test ID."""
    endpoint = f"{config.GTMETRIX_API_BASE}/tests"
    payload = {
        "data": {
            "type": "test",
            "attributes": {
                "url": url,
                "location": config.GTMETRIX_LOCATION,
                "browser": config.GTMETRIX_BROWSER,
            },
        }
    }
    resp = requests.post(
        endpoint,
        json=payload,
        headers={**_AUTH_HEADER, "Content-Type": "application/vnd.api+json"},
        timeout=config.REQUEST_TIMEOUT_SECONDS,
    )
    api_log.debug("POST %s -> %s | %s", endpoint, resp.status_code, resp.text[:300])
    _raise_for_response(resp, f"startTest({url})")

    if resp.status_code != 202:
        raise GTmetrixError(f"startTest({url}): unexpected status {resp.status_code}")

    test_id = resp.json()["data"]["id"]
    log.info("Test started for %s -> test_id=%s", url, test_id)
    return test_id


@retry_with_backoff(
    max_retries=config.API_MAX_RETRIES,
    base_delay_seconds=config.API_RETRY_BASE_DELAY_SECONDS,
    rate_limit_wait_seconds=config.RATE_LIMIT_WAIT_SECONDS,
    label="getReport",
)
def _fetch_report(report_url: str) -> dict:
    resp = requests.get(report_url, headers=_AUTH_HEADER, timeout=config.REQUEST_TIMEOUT_SECONDS)
    api_log.debug("GET %s -> %s", report_url, resp.status_code)
    _raise_for_response(resp, f"getReport({report_url})")
    return resp.json()


def poll_for_result(test_id: str) -> dict:
    """
    Polls until the test completes, errors, or times out. Returns the
    full report JSON (already fetched), or raises GTmetrixError.
    """
    import time

    test_url = f"{config.GTMETRIX_API_BASE}/tests/{test_id}"

    for attempt in range(1, config.POLL_MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(test_url, headers=_AUTH_HEADER,
                                 timeout=config.REQUEST_TIMEOUT_SECONDS, allow_redirects=False)
        except requests.RequestException as e:
            log.warning("Polling network error (attempt %d/%d) for %s: %s",
                        attempt, config.POLL_MAX_ATTEMPTS, test_id, e)
            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue

        api_log.debug("Poll %d/%d %s -> %s", attempt, config.POLL_MAX_ATTEMPTS, test_url, resp.status_code)

        if resp.status_code == 303:
            report_url = resp.headers.get("Location")
            if not report_url:
                raise GTmetrixError(f"poll({test_id}): 303 with no Location header")
            if report_url.startswith("/"):
                report_url = "https://gtmetrix.com" + report_url
            return _fetch_report(report_url)

        if resp.status_code == 200:
            data = resp.json()
            state = data.get("data", {}).get("attributes", {}).get("state")
            log.info("Poll %d/%d test %s state=%s", attempt, config.POLL_MAX_ATTEMPTS, test_id, state)

            if state == "completed":
                report_link = (
                    data.get("links", {}).get("report")
                    or data.get("data", {}).get("links", {}).get("report")
                )
                if report_link:
                    return _fetch_report(report_link)
                # completed but link not populated yet — fall through to next poll
            elif state == "error":
                err = data.get("data", {}).get("attributes", {}).get("error", "Unknown GTmetrix error")
                raise GTmetrixError(f"Test {test_id} errored: {err}")

            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue

        if resp.status_code == 429:
            log.warning("Polling rate-limited for %s. Waiting %.0fs.", test_id, config.RATE_LIMIT_WAIT_SECONDS)
            import time as _t
            _t.sleep(config.RATE_LIMIT_WAIT_SECONDS)
            continue

        if resp.status_code >= 500:
            log.warning("Polling server error %s for %s (attempt %d/%d).",
                        resp.status_code, test_id, attempt, config.POLL_MAX_ATTEMPTS)
            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue

        raise GTmetrixError(f"poll({test_id}): unexpected HTTP {resp.status_code}: {resp.text[:300]}")

    raise GTmetrixError(f"poll({test_id}): timed out after {config.POLL_MAX_ATTEMPTS} attempts")


def extract_metrics(report_json: dict, report_url: str) -> Metrics:
    try:
        attrs = report_json["data"]["attributes"]
    except (KeyError, TypeError):
        return Metrics(status="Error", error_message="Malformed report JSON", report_url=report_url)

    def to_seconds(ms):
        if ms is None or ms == -1:
            return None
        return round(ms / 1000, 2)

    public_url = (
        report_json.get("data", {}).get("links", {}).get("report_url") or report_url
    )

    return Metrics(
        performance_score=attrs.get("performance_score"),
        grade=attrs.get("gtmetrix_grade") or "N/A",
        lcp=to_seconds(attrs.get("largest_contentful_paint")),
        onload=to_seconds(attrs.get("onload_time")),
        fully_loaded=to_seconds(attrs.get("fully_loaded_time")),
        ttfb=to_seconds(attrs.get("time_to_first_byte")),
        cls=attrs.get("cumulative_layout_shift"),
        tbt=to_seconds(attrs.get("total_blocking_time")),
        report_url=public_url,
        status="OK",
    )


def run_single_page(page_name: str, sheet_name: str, url: str) -> PageResult:
    """
    Full pipeline for one page: start -> poll -> extract. Never raises —
    always returns a PageResult, with success=False and error_message set
    on failure, so a ThreadPoolExecutor batch can never be taken down by
    one bad page.
    """
    result = PageResult(page_name=page_name, sheet_name=sheet_name, url=url)
    try:
        test_id = start_test(url)
        report_json = poll_for_result(test_id)
        report_url = f"{config.GTMETRIX_API_BASE}/reports/{test_id}"
        metrics = extract_metrics(report_json, report_url)
        if metrics.status == "Error":
            raise GTmetrixError(metrics.error_message or "metric extraction failed")
        result.metrics = metrics
        result.success = True
        log.info("OK  %-24s score=%s grade=%s lcp=%ss", sheet_name,
                  metrics.performance_score, metrics.grade, metrics.lcp)
    except Exception as e:  # noqa: BLE001 — page-level isolation boundary, by design
        result.success = False
        result.error_message = str(e)
        log.error("FAIL %-24s %s", sheet_name, e)
    return result
