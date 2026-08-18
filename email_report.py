"""
email_report.py
================
Sends an HTML summary email after every run: overall stats, a simple
inline bar-style visual (pure HTML/CSS table shading — email clients
don't run JS or reliably render Chart.js), and a list of failed pages.
Silently (but loudly, via logging) skips sending if SMTP isn't
configured — email is optional, a run should never fail because of it.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config
from gtmetrix import PageResult
from logger import get_logger
from utils import now_iso

log = get_logger("email_report")


def _score_color(score) -> str:
    if score is None:
        return "#94A3B8"
    if score >= config.ALERT_SCORE_THRESHOLD:
        return "#2FB673"
    if score >= config.ALERT_SCORE_THRESHOLD - 15:
        return "#E8A93B"
    return "#E05252"


def _build_html(results: list[PageResult]) -> str:
    success = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    scores = [r.metrics.performance_score for r in success if r.metrics.performance_score is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else "N/A"

    rows_html = ""
    for r in sorted(success, key=lambda r: (r.metrics.performance_score or 0)):
        color = _score_color(r.metrics.performance_score)
        rows_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{r.page_name}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">
            <span style="display:inline-block;padding:2px 10px;border-radius:12px;background:{color}22;color:{color};font-weight:600;">
              {r.metrics.performance_score}
            </span>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{r.metrics.grade}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{r.metrics.lcp}s</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">
            <a href="{r.metrics.report_url}" style="color:#2F6FE8;">Report</a>
          </td>
        </tr>"""

    failed_html = ""
    if failed:
        items = "".join(f"<li><b>{r.page_name}</b> — {r.error_message}</li>" for r in failed)
        failed_html = f"""
        <h3 style="color:#E05252;">Failed pages ({len(failed)})</h3>
        <ul style="color:#3A4256;">{items}</ul>"""

    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:auto;color:#1B2233;">
      <h2 style="margin-bottom:4px;">GoodMonk Performance Report</h2>
      <p style="color:#6B7488;margin-top:0;">{now_iso()}</p>

      <div style="display:flex;gap:12px;margin:16px 0;">
        <div style="flex:1;background:#F5F7FB;border-radius:10px;padding:14px;text-align:center;">
          <div style="font-size:26px;font-weight:700;">{avg_score}</div>
          <div style="color:#6B7488;font-size:12px;">Average Score</div>
        </div>
        <div style="flex:1;background:#F5F7FB;border-radius:10px;padding:14px;text-align:center;">
          <div style="font-size:26px;font-weight:700;color:#2FB673;">{len(success)}</div>
          <div style="color:#6B7488;font-size:12px;">Successful</div>
        </div>
        <div style="flex:1;background:#F5F7FB;border-radius:10px;padding:14px;text-align:center;">
          <div style="font-size:26px;font-weight:700;color:#E05252;">{len(failed)}</div>
          <div style="color:#6B7488;font-size:12px;">Failed</div>
        </div>
      </div>

      {failed_html}

      <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:14px;">
        <thead>
          <tr style="text-align:left;color:#6B7488;">
            <th style="padding:8px 12px;">Page</th>
            <th style="padding:8px 12px;">Score</th>
            <th style="padding:8px 12px;">Grade</th>
            <th style="padding:8px 12px;">LCP</th>
            <th style="padding:8px 12px;">Report</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>

      <p style="color:#94A3B8;font-size:12px;margin-top:20px;">
        Sent automatically by the GoodMonk Performance Command Center.
      </p>
    </div>
    """


def _build_root_cause_html(root_cause_reports: list) -> str:
    """
    Feature 1 addition. Renders a "Root Cause Highlights" section from a
    list of root_cause.RootCauseReport-like dicts (as produced by
    root_cause.RootCauseReport.to_dict()). Kept as a fully separate
    function — `_build_html` above is completely untouched.
    """
    with_issues = [r for r in root_cause_reports if r.get("issue_count")]
    if not with_issues:
        return ""

    with_issues.sort(key=lambda r: r["issue_count"], reverse=True)

    rows_html = ""
    for r in with_issues[: config.RCA_MAX_ISSUES_IN_EMAIL]:
        top = r.get("top_issue") or "—"
        rows_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{r.get('page_name', '')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{r.get('issue_count', 0)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{top}</td>
        </tr>"""

    return f"""
      <h3 style="color:#1B2233;margin-top:24px;">Root Cause Highlights</h3>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="text-align:left;color:#6B7488;">
            <th style="padding:8px 12px;">Page</th>
            <th style="padding:8px 12px;">Issues Found</th>
            <th style="padding:8px 12px;">Top Issue</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    """


def _build_journey_html(journey_results: list) -> str:
    """
    Feature 2 addition. Renders a "Customer Journey" section from a list
    of journey_models.JourneyResult.to_dict()-shaped dicts. Kept as a
    fully separate function — `_build_html` and `_build_root_cause_html`
    above are both completely untouched.
    """
    import os

    if not journey_results:
        return ""

    rows_html = ""
    any_failed = False
    for j in journey_results:
        ok = j.get("success")
        any_failed = any_failed or not ok
        color = "#2FB673" if ok else "#E05252"
        status_text = "OK" if ok else f"Failed at: {j.get('failed_step') or 'unknown step'}"
        rows_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{j.get('product_name', '')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">
            <span style="display:inline-block;padding:2px 10px;border-radius:12px;background:{color}22;color:{color};font-weight:600;">
              {status_text}
            </span>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{j.get('total_duration_seconds', '—')}s</td>
        </tr>"""

    screenshot_note = ""
    if any_failed:
        # Screenshots are never committed to the repo (see .gitignore) —
        # they're uploaded as a GitHub Actions artifact by monitor.yml.
        # Build a real link to this run's artifacts page when running in
        # Actions (these env vars are set automatically); fall back to
        # plain text locally.
        server = os.environ.get("GITHUB_SERVER_URL")
        repo = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")
        if server and repo and run_id:
            artifact_url = f"{server}/{repo}/actions/runs/{run_id}"
            screenshot_note = (
                f'<p style="color:#6B7488;font-size:12px;">Failure screenshots for this run are attached as a '
                f'<a href="{artifact_url}" style="color:#2F6FE8;">GitHub Actions artifact</a> — '
                f'they are not stored in the repository.</p>'
            )
        else:
            screenshot_note = (
                '<p style="color:#6B7488;font-size:12px;">Failure screenshots for this run are available as a '
                "GitHub Actions artifact — they are not stored in the repository.</p>"
            )

    return f"""
      <h3 style="color:#1B2233;margin-top:24px;">Customer Journey</h3>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="text-align:left;color:#6B7488;">
            <th style="padding:8px 12px;">Product</th>
            <th style="padding:8px 12px;">Status</th>
            <th style="padding:8px 12px;">Duration</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      {screenshot_note}
    """


def _build_alerts_html(alert_events: list) -> str:
    """
    Feature 3 addition. Renders an "Alerts" section from a list of
    alert_models.AlertEvent-shaped dicts (event.to_dict()). Only "new" and
    "recovered" events should ever be passed in — callers filter on
    event.should_notify before building this list, so an ongoing
    (suppressed) alert never clutters the email — that's the whole point
    of deduplication. Kept fully separate from every other _build_*_html
    function above.
    """
    if not alert_events:
        return ""

    severity_color = {"critical": "#E05252", "high": "#E8A93B", "warning": "#E8A93B", "info": "#5B8CFF"}
    severity_order = {"critical": 0, "high": 1, "warning": 2, "info": 3}
    alert_events = sorted(alert_events, key=lambda e: severity_order.get((e.get("severity") or "warning").lower(), 4))

    rows_html = ""
    for e in alert_events[: config.RCA_MAX_ISSUES_IN_EMAIL * 2]:  # a slightly larger cap than RCA — alerts are already deduplicated
        is_recovery = e.get("status") == "recovered"
        color = "#2FB673" if is_recovery else severity_color.get((e.get("severity") or "warning").lower(), "#E8A93B")
        status_label = "RECOVERED" if is_recovery else (e.get("severity") or "warning").upper()
        screenshot_note = " · screenshot in workflow artifact" if e.get("screenshot_path") else ""
        rows_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">
            <span style="display:inline-block;padding:2px 10px;border-radius:12px;background:{color}22;color:{color};font-weight:600;">
              {status_label}
            </span>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{e.get('title', '')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{e.get('affected_page') or '—'}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E5E9F0;">{e.get('root_cause') or e.get('message', '')}{screenshot_note}</td>
        </tr>"""

    return f"""
      <h3 style="color:#1B2233;margin-top:24px;">Alerts</h3>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="text-align:left;color:#6B7488;">
            <th style="padding:8px 12px;">Status</th>
            <th style="padding:8px 12px;">Alert</th>
            <th style="padding:8px 12px;">Page</th>
            <th style="padding:8px 12px;">Detail</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    """


def _build_scheduler_html(scheduler_meta: dict) -> str:
    """
    Feature 4 addition. Renders a small "Scheduler" section summarizing
    this run's checked/skipped pages and duration. `scheduler_meta` shape:
    {"pages_checked": int, "pages_skipped": int, "skip_reasons": [str],
     "duration_seconds": float}. Kept fully separate from every other
    _build_*_html function above.
    """
    if not scheduler_meta:
        return ""

    checked = scheduler_meta.get("pages_checked", 0)
    skipped = scheduler_meta.get("pages_skipped", 0)
    duration = scheduler_meta.get("duration_seconds")
    reasons = scheduler_meta.get("skip_reasons") or []

    reasons_html = ""
    if reasons:
        items = "".join(f"<li>{r}</li>" for r in reasons[:10])
        reasons_html = f'<ul style="color:#6B7488;font-size:12px;margin-top:4px;">{items}</ul>'

    return f"""
      <h3 style="color:#1B2233;margin-top:24px;">Scheduler</h3>
      <p style="color:#3A4256;font-size:14px;">
        Pages checked: <b>{checked}</b> · Pages skipped: <b>{skipped}</b>
        {f' · Duration: <b>{duration:.1f}s</b>' if duration is not None else ''}
      </p>
      {reasons_html}
    """


def send_report(results: list[PageResult], root_cause_reports: list | None = None,
                 journey_results: list | None = None, alert_events: list | None = None,
                 scheduler_meta: dict | None = None) -> None:
    if not config.EMAIL_ENABLED:
        log.info("Email not configured (SMTP_HOST/SMTP_USER/SMTP_PASSWORD/EMAIL_TO) — skipping report email.")
        return

    failed = sum(1 for r in results if not r.success)
    if results:
        subject = f"GoodMonk Performance Report — {len(results) - failed}/{len(results)} pages OK"
    else:
        # Feature 3 addition: only reachable via alerts.py's CLI fallback
        # (a workflow-level failure with no page results available at
        # all) — every existing caller always passes a non-empty
        # `results`, so this branch never changes prior behavior.
        subject = "GoodMonk Alert — Workflow Failure" if alert_events else "GoodMonk Performance Report"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = ", ".join(config.EMAIL_TO)

    html = _build_html(results)

    # Feature 1 addition: append the root-cause section, if any was passed
    # in. Existing callers that pass nothing get byte-for-byte the same
    # email as before this feature was added.
    if root_cause_reports:
        try:
            rca_html = _build_root_cause_html(root_cause_reports)
            if rca_html:
                html += rca_html
        except Exception as e:  # noqa: BLE001 — RCA section must never break the existing email
            log.warning("Failed to build root-cause email section (sending base report anyway): %s", e)

    # Feature 2 addition: append the journey section, if any journey results
    # were passed in. Same isolation pattern as the RCA section above —
    # existing callers/behavior are completely unaffected.
    if journey_results:
        try:
            journey_html = _build_journey_html(journey_results)
            if journey_html:
                html += journey_html
        except Exception as e:  # noqa: BLE001 — journey section must never break the existing email
            log.warning("Failed to build journey email section (sending base report anyway): %s", e)

    # Feature 3 addition: append the alerts section, if any alert events
    # (already filtered to should_notify by the caller) were passed in.
    # Same isolation pattern as RCA/journey above.
    if alert_events:
        try:
            alerts_html = _build_alerts_html(alert_events)
            if alerts_html:
                html += alerts_html
        except Exception as e:  # noqa: BLE001 — alerts section must never break the existing email
            log.warning("Failed to build alerts email section (sending base report anyway): %s", e)

    # Feature 4 addition: append the scheduler section, if scheduler
    # metadata was passed in. Same isolation pattern as every other
    # optional section above.
    if scheduler_meta:
        try:
            scheduler_html = _build_scheduler_html(scheduler_meta)
            if scheduler_html:
                html += scheduler_html
        except Exception as e:  # noqa: BLE001 — scheduler section must never break the existing email
            log.warning("Failed to build scheduler email section (sending base report anyway): %s", e)

    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.EMAIL_FROM, config.EMAIL_TO, msg.as_string())
        log.info("Report email sent to %s.", ", ".join(config.EMAIL_TO))
    except Exception as e:  # noqa: BLE001 — email failure must never fail the whole run
        log.error("Failed to send report email: %s", e)
