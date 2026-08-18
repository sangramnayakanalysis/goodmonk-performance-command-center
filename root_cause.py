"""
root_cause.py
==============
Feature 1: Root Cause Analysis.

Brand-new module. Nothing in the existing pipeline (gtmetrix.py's core
start/poll/extract flow, scheduler.py's batch logic, google_sheet.py's
existing tabs, dashboard_data.py's existing 4 JSON files, email_report.py's
existing template) is modified in behavior by this file — it only *reads*
a `Metrics` object (including the new, additive `raw_audit` field) and
produces a structured report.

Design notes
------------
GTmetrix's basic report tier gives you the 8 summary metrics the original
project already stored, plus (depending on plan) a handful of extra
page-weight / resource-breakdown fields and, on some tiers, a structured
recommendations list — all captured by `gtmetrix.extract_raw_audit_fields`.
It does NOT give you a full Lighthouse-style per-resource audit (e.g. "this
exact image cost you 400ms") via the basic `/tests` + report endpoint used
here. So this module does two things, honestly kept distinct:

1. Threshold-based heuristic findings — derived from the summary metrics
   and whatever raw_audit fields happen to be present for this GTmetrix
   plan/report. These are the majority of the ~20 categories requested.
2. Pass-through of GTmetrix's own structured `recommendations`/`audits`
   list, when the plan tier provides one — surfaced as-is rather than
   re-interpreted, since GTmetrix's own text is authoritative there.

If a plan/report doesn't expose a given raw_audit field, the corresponding
heuristic is simply skipped (not flagged, not guessed) — this module never
fabricates a finding from data it doesn't have.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

import config
from gtmetrix import Metrics
from logger import get_logger
from utils import now_date_str, now_iso, now_time_str

log = get_logger("root_cause")

# --- Issue categories --------------------------------------------------------
# Stable string identifiers — used in Sheets/JSON/email and safe to filter/
# group on. Kept as plain strings (not an Enum) so future categories can be
# added without touching this module's public shape.
CATEGORY_HIGH_TTFB = "high_ttfb"
CATEGORY_SLOW_SERVER_RESPONSE = "slow_server_response"
CATEGORY_HIGH_LCP = "high_lcp"
CATEGORY_HIGH_CLS = "high_cls"
CATEGORY_HIGH_TBT = "high_tbt"
CATEGORY_SLOW_FULLY_LOADED = "slow_fully_loaded"
CATEGORY_HEAVY_PAGE_WEIGHT = "heavy_page_weight"
CATEGORY_LARGE_IMAGES = "large_images"
CATEGORY_HEAVY_JAVASCRIPT = "heavy_javascript"
CATEGORY_HEAVY_CSS = "heavy_css"
CATEGORY_SLOW_FONTS = "slow_fonts"
CATEGORY_HEAVY_THIRD_PARTY = "heavy_third_party_scripts"
CATEGORY_LARGE_DOM = "large_dom"
CATEGORY_HIGH_REQUEST_COUNT = "high_request_count"
CATEGORY_GTMETRIX_RECOMMENDATION = "gtmetrix_recommendation"

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass
class RootCauseIssue:
    category: str
    severity: str
    title: str
    detail: str
    value: Optional[float] = None
    threshold: Optional[float] = None
    recommendation: str = ""


@dataclass
class RootCauseReport:
    page_name: str
    sheet_name: str
    generated_at: str
    performance_score: Optional[float]
    issues: list[RootCauseIssue] = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def top_issue(self) -> Optional[str]:
        if not self.issues:
            return None
        # Critical issues first, then in the order they were detected.
        ordered = sorted(self.issues, key=lambda i: 0 if i.severity == SEVERITY_CRITICAL else 1)
        return ordered[0].title

    @property
    def categories(self) -> list[str]:
        return [i.category for i in self.issues]

    def to_dict(self) -> dict:
        return {
            "page_name": self.page_name,
            "sheet_name": self.sheet_name,
            "generated_at": self.generated_at,
            "performance_score": self.performance_score,
            "issue_count": self.issue_count,
            "top_issue": self.top_issue,
            "issues": [asdict(i) for i in self.issues],
        }

    def to_human_readable(self) -> str:
        """Plain-text summary suitable for a Sheets cell or an email section."""
        if not self.issues:
            return f"{self.page_name}: no root-cause issues detected."
        lines = [f"{self.page_name} — {self.issue_count} issue(s) found:"]
        for i in sorted(self.issues, key=lambda i: 0 if i.severity == SEVERITY_CRITICAL else 1):
            marker = "🔴" if i.severity == SEVERITY_CRITICAL else ("🟡" if i.severity == SEVERITY_WARNING else "ℹ")
            lines.append(f"  {marker} {i.title} — {i.detail}")
            if i.recommendation:
                lines.append(f"      → {i.recommendation}")
        return "\n".join(lines)


def _issue(category, severity, title, detail, value=None, threshold=None, recommendation="") -> RootCauseIssue:
    return RootCauseIssue(
        category=category, severity=severity, title=title, detail=detail,
        value=value, threshold=threshold, recommendation=recommendation,
    )


def analyze(page_name: str, sheet_name: str, metrics: Metrics) -> RootCauseReport:
    """
    Runs every available heuristic against one page's `Metrics` (including
    its `raw_audit` dict) and returns a structured `RootCauseReport`.

    Never raises: any single rule that can't evaluate (missing data) is
    simply skipped, exactly like the rest of this project's "one bad thing
    never breaks the batch" philosophy (see gtmetrix.run_single_page,
    scheduler._record_result).
    """
    issues: list[RootCauseIssue] = []
    raw = metrics.raw_audit or {}

    def add(condition, *args, **kwargs):
        try:
            if condition:
                issues.append(_issue(*args, **kwargs))
        except Exception as e:  # noqa: BLE001 — one bad rule must never break the whole analysis
            log.warning("Root-cause rule failed to evaluate for %s: %s", sheet_name, e)

    # --- Core Web Vitals / summary-metric rules (always available) --------
    add(
        metrics.ttfb is not None and metrics.ttfb > config.RCA_TTFB_THRESHOLD_SECONDS,
        CATEGORY_HIGH_TTFB, SEVERITY_CRITICAL if (metrics.ttfb or 0) > config.RCA_TTFB_THRESHOLD_SECONDS * 1.5 else SEVERITY_WARNING,
        "High TTFB", f"Time to First Byte is {metrics.ttfb}s (threshold {config.RCA_TTFB_THRESHOLD_SECONDS}s).",
        value=metrics.ttfb, threshold=config.RCA_TTFB_THRESHOLD_SECONDS,
        recommendation="Investigate server/app response time, hosting tier, or CDN/edge caching for this URL.",
    )
    add(
        raw.get("backend_duration") is not None and raw["backend_duration"] > config.RCA_TTFB_THRESHOLD_SECONDS,
        CATEGORY_SLOW_SERVER_RESPONSE, SEVERITY_WARNING,
        "Slow server response", f"Backend duration is {raw.get('backend_duration')}s.",
        value=raw.get("backend_duration"), threshold=config.RCA_TTFB_THRESHOLD_SECONDS,
        recommendation="Profile the origin server / Shopify app response time for this page.",
    )
    add(
        metrics.lcp is not None and metrics.lcp > config.RCA_LCP_THRESHOLD_SECONDS,
        CATEGORY_HIGH_LCP, SEVERITY_CRITICAL if (metrics.lcp or 0) > config.RCA_LCP_THRESHOLD_SECONDS * 1.6 else SEVERITY_WARNING,
        "High LCP", f"Largest Contentful Paint is {metrics.lcp}s (threshold {config.RCA_LCP_THRESHOLD_SECONDS}s).",
        value=metrics.lcp, threshold=config.RCA_LCP_THRESHOLD_SECONDS,
        recommendation="Check the LCP element — usually the hero image or first product image; preload/compress it.",
    )
    add(
        metrics.cls is not None and metrics.cls > config.RCA_CLS_THRESHOLD,
        CATEGORY_HIGH_CLS, SEVERITY_WARNING,
        "High CLS (layout shift)", f"Cumulative Layout Shift is {metrics.cls} (threshold {config.RCA_CLS_THRESHOLD}).",
        value=metrics.cls, threshold=config.RCA_CLS_THRESHOLD,
        recommendation="Reserve explicit width/height (or aspect-ratio) for images and embeds above the fold.",
    )
    add(
        metrics.tbt is not None and metrics.tbt > config.RCA_TBT_THRESHOLD_SECONDS,
        CATEGORY_HIGH_TBT, SEVERITY_WARNING,
        "High Total Blocking Time", f"TBT is {metrics.tbt}s (threshold {config.RCA_TBT_THRESHOLD_SECONDS}s) — a proxy for render-blocking/heavy JavaScript.",
        value=metrics.tbt, threshold=config.RCA_TBT_THRESHOLD_SECONDS,
        recommendation="Defer/async non-critical JS, split large bundles, and audit third-party scripts.",
    )
    add(
        metrics.fully_loaded is not None and metrics.fully_loaded > config.RCA_FULLY_LOADED_THRESHOLD_SECONDS,
        CATEGORY_SLOW_FULLY_LOADED, SEVERITY_INFO,
        "Slow fully-loaded time", f"Fully Loaded Time is {metrics.fully_loaded}s (threshold {config.RCA_FULLY_LOADED_THRESHOLD_SECONDS}s).",
        value=metrics.fully_loaded, threshold=config.RCA_FULLY_LOADED_THRESHOLD_SECONDS,
        recommendation="Review the full resource waterfall in the linked GTmetrix report for the longest-pole resource.",
    )

    # --- Resource-weight rules (only fire if this GTmetrix plan/report tier
    #     actually returned the field — see extract_raw_audit_fields) -------
    if "page_bytes_kb" in raw:
        page_mb = raw["page_bytes_kb"] / 1024
        add(
            page_mb > config.RCA_PAGE_BYTES_THRESHOLD_MB,
            CATEGORY_HEAVY_PAGE_WEIGHT, SEVERITY_WARNING,
            "Heavy total page weight", f"Total page weight is {page_mb:.2f}MB (threshold {config.RCA_PAGE_BYTES_THRESHOLD_MB}MB).",
            value=round(page_mb, 2), threshold=config.RCA_PAGE_BYTES_THRESHOLD_MB,
            recommendation="Audit the resource breakdown below for the heaviest category and target that first.",
        )
    if "image_bytes_kb" in raw:
        image_mb = raw["image_bytes_kb"] / 1024
        add(
            image_mb > config.RCA_IMAGE_BYTES_THRESHOLD_MB,
            CATEGORY_LARGE_IMAGES, SEVERITY_WARNING,
            "Large / unoptimized images", f"Image weight is {image_mb:.2f}MB (threshold {config.RCA_IMAGE_BYTES_THRESHOLD_MB}MB).",
            value=round(image_mb, 2), threshold=config.RCA_IMAGE_BYTES_THRESHOLD_MB,
            recommendation="Serve next-gen formats (WebP/AVIF), compress product images, and confirm responsive `srcset` sizes.",
        )
    if "js_bytes_kb" in raw:
        add(
            raw["js_bytes_kb"] > config.RCA_JS_BYTES_THRESHOLD_KB,
            CATEGORY_HEAVY_JAVASCRIPT, SEVERITY_WARNING,
            "Heavy JavaScript", f"JS weight is {raw['js_bytes_kb']:.0f}KB (threshold {config.RCA_JS_BYTES_THRESHOLD_KB:.0f}KB).",
            value=raw["js_bytes_kb"], threshold=config.RCA_JS_BYTES_THRESHOLD_KB,
            recommendation="Audit installed Shopify apps/scripts for unused JS; defer non-critical bundles.",
        )
    if "css_bytes_kb" in raw:
        add(
            raw["css_bytes_kb"] > config.RCA_CSS_BYTES_THRESHOLD_KB,
            CATEGORY_HEAVY_CSS, SEVERITY_INFO,
            "Heavy CSS", f"CSS weight is {raw['css_bytes_kb']:.0f}KB (threshold {config.RCA_CSS_BYTES_THRESHOLD_KB:.0f}KB).",
            value=raw["css_bytes_kb"], threshold=config.RCA_CSS_BYTES_THRESHOLD_KB,
            recommendation="Remove unused theme CSS and split page-specific styles from the global stylesheet.",
        )
    if "font_bytes_kb" in raw:
        add(
            raw["font_bytes_kb"] > config.RCA_FONT_BYTES_THRESHOLD_KB,
            CATEGORY_SLOW_FONTS, SEVERITY_INFO,
            "Heavy web fonts", f"Font weight is {raw['font_bytes_kb']:.0f}KB (threshold {config.RCA_FONT_BYTES_THRESHOLD_KB:.0f}KB).",
            value=raw["font_bytes_kb"], threshold=config.RCA_FONT_BYTES_THRESHOLD_KB,
            recommendation="Subset fonts to used characters, use `font-display: swap`, and limit font-weight variants.",
        )
    if "dom_elements" in raw:
        add(
            raw["dom_elements"] > config.RCA_DOM_ELEMENTS_THRESHOLD,
            CATEGORY_LARGE_DOM, SEVERITY_INFO,
            "Large DOM size", f"{raw['dom_elements']} DOM elements (threshold {config.RCA_DOM_ELEMENTS_THRESHOLD}).",
            value=raw["dom_elements"], threshold=config.RCA_DOM_ELEMENTS_THRESHOLD,
            recommendation="Simplify nested theme sections/apps that inject large amounts of markup.",
        )
    if "page_requests" in raw:
        add(
            raw["page_requests"] > config.RCA_REQUEST_COUNT_THRESHOLD,
            CATEGORY_HIGH_REQUEST_COUNT, SEVERITY_INFO,
            "High request count", f"{raw['page_requests']} requests (threshold {config.RCA_REQUEST_COUNT_THRESHOLD}).",
            value=raw["page_requests"], threshold=config.RCA_REQUEST_COUNT_THRESHOLD,
            recommendation="Consolidate/lazy-load third-party embeds and Shopify apps that each add their own requests.",
        )

    third_party = raw.get("third_party_summary")
    if third_party:
        try:
            total_kb = sum(
                (entry.get("size", 0) or 0) / 1024
                for entry in third_party if isinstance(entry, dict)
            )
            add(
                total_kb > config.RCA_THIRD_PARTY_BYTES_THRESHOLD_KB,
                CATEGORY_HEAVY_THIRD_PARTY, SEVERITY_WARNING,
                "Heavy third-party scripts", f"Third-party resources total ~{total_kb:.0f}KB (threshold {config.RCA_THIRD_PARTY_BYTES_THRESHOLD_KB:.0f}KB).",
                value=round(total_kb, 1), threshold=config.RCA_THIRD_PARTY_BYTES_THRESHOLD_KB,
                recommendation="Review installed tracking/marketing pixels and Shopify apps for ones that can be deferred or removed.",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Third-party summary parsing failed for %s: %s", sheet_name, e)

    # --- Pass through GTmetrix's own structured recommendations, if this
    #     report tier included them. Surfaced verbatim, not re-interpreted.
    recommendations = raw.get("recommendations")
    if recommendations:
        for rec in recommendations[:10]:  # cap defensively — never let one report flood the list
            if not isinstance(rec, dict):
                continue
            title = rec.get("title") or rec.get("id") or "GTmetrix recommendation"
            impact = rec.get("impact") or rec.get("severity") or SEVERITY_INFO
            severity = SEVERITY_CRITICAL if str(impact).lower() in ("high", "critical") else (
                SEVERITY_WARNING if str(impact).lower() in ("medium", "warning") else SEVERITY_INFO
            )
            issues.append(_issue(
                CATEGORY_GTMETRIX_RECOMMENDATION, severity, str(title),
                rec.get("description", "") or "See the full GTmetrix report for details.",
                recommendation=rec.get("recommendation", ""),
            ))

    return RootCauseReport(
        page_name=page_name,
        sheet_name=sheet_name,
        generated_at=now_iso(),
        performance_score=metrics.performance_score,
        issues=issues,
    )


def to_sheet_row(report: RootCauseReport) -> list:
    """Flattens a RootCauseReport into a row matching config.ROOT_CAUSE_HEADERS."""
    top_titles = "; ".join(i.title for i in report.issues[:5])
    categories = ", ".join(sorted(set(report.categories)))
    return [
        now_date_str(),
        now_time_str(),
        report.performance_score,
        report.issue_count,
        top_titles,
        categories,
        json.dumps(report.to_dict(), default=str),
    ]
