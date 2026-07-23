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


def send_report(results: list[PageResult]) -> None:
    if not config.EMAIL_ENABLED:
        log.info("Email not configured (SMTP_HOST/SMTP_USER/SMTP_PASSWORD/EMAIL_TO) — skipping report email.")
        return

    failed = sum(1 for r in results if not r.success)
    subject = f"GoodMonk Performance Report — {len(results) - failed}/{len(results)} pages OK"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = ", ".join(config.EMAIL_TO)
    msg.attach(MIMEText(_build_html(results), "html"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.EMAIL_FROM, config.EMAIL_TO, msg.as_string())
        log.info("Report email sent to %s.", ", ".join(config.EMAIL_TO))
    except Exception as e:  # noqa: BLE001 — email failure must never fail the whole run
        log.error("Failed to send report email: %s", e)
