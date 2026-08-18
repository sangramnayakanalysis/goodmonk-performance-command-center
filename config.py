"""
config.py
=========
Single source of truth for configuration. Everything environment- or
secret-specific comes from `.env` (never hardcoded) via python-dotenv.
Everything page-specific (the URL list) is defined here, in one place,
so adding a page is a one-line change and nothing else in the project
needs to know about it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# --- Paths -----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
DASHBOARD_DIR = BASE_DIR / "dashboard"
DASHBOARD_DATA_DIR = DASHBOARD_DIR / "data"

for _dir in (DATA_DIR, LOGS_DIR, REPORTS_DIR, DASHBOARD_DATA_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(key, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable '{key}'. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


# --- GTmetrix ----------------------------------------------------------------
GTMETRIX_API_KEY = _env("GTMETRIX_API_KEY", required=True)
GTMETRIX_API_BASE = "https://gtmetrix.com/api/2.0"
GTMETRIX_LOCATION = _env("GTMETRIX_LOCATION", "24")   # Mumbai by default
GTMETRIX_BROWSER = _env("GTMETRIX_BROWSER", "3")      # Chrome by default

# Networking / retry behaviour
REQUEST_TIMEOUT_SECONDS = int(_env("REQUEST_TIMEOUT_SECONDS", "30"))
API_MAX_RETRIES = int(_env("API_MAX_RETRIES", "3"))
API_RETRY_BASE_DELAY_SECONDS = float(_env("API_RETRY_BASE_DELAY_SECONDS", "3"))
RATE_LIMIT_WAIT_SECONDS = float(_env("RATE_LIMIT_WAIT_SECONDS", "30"))

# Polling behaviour
POLL_MAX_ATTEMPTS = int(_env("POLL_MAX_ATTEMPTS", "24"))
POLL_INTERVAL_SECONDS = float(_env("POLL_INTERVAL_SECONDS", "15"))

# Concurrency — no more Apps Script 6-minute wall. Tune to your GTmetrix
# plan's concurrent-test limit (a paid plan typically supports several).
MAX_WORKERS = int(_env("MAX_WORKERS", "4"))

# --- Google Sheets -----------------------------------------------------------
GOOGLE_SHEET_ID = _env("GOOGLE_SHEET_ID", required=True)
# Either a path to a service-account JSON key file...
GOOGLE_SERVICE_ACCOUNT_FILE = _env("GOOGLE_SERVICE_ACCOUNT_FILE", "")
# ...or the JSON contents themselves (used in CI, where a secret holds the
# whole key rather than a file path). google_sheet.py tries the file first,
# then falls back to this.
GOOGLE_SERVICE_ACCOUNT_JSON = _env("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# --- Email ---------------------------------------------------------------
SMTP_HOST = _env("SMTP_HOST", "")
smtp_port = _env("SMTP_PORT", "").strip()
SMTP_PORT = int(smtp_port) if smtp_port else 587
SMTP_USER = _env("SMTP_USER", "")
SMTP_PASSWORD = _env("SMTP_PASSWORD", "")
EMAIL_FROM = _env("EMAIL_FROM", SMTP_USER)
EMAIL_TO = [addr.strip() for addr in _env("EMAIL_TO", "").split(",") if addr.strip()]
EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and EMAIL_TO)

# --- Alert thresholds (used for traffic-light status + email flags) --------
ALERT_SCORE_THRESHOLD = float(_env("ALERT_SCORE_THRESHOLD", "80"))
ALERT_LCP_THRESHOLD_SECONDS = float(_env("ALERT_LCP_THRESHOLD_SECONDS", "2.5"))

# --- Root Cause Analysis thresholds ------------------------------------------
# Added for Feature 1 (Root Cause Analysis). Purely additive — nothing above
# this block changes. Every threshold is env-overridable and defaults to a
# generally-accepted "good practice" figure so RCA works out of the box even
# if none of these are set in .env.
RCA_TTFB_THRESHOLD_SECONDS = float(_env("RCA_TTFB_THRESHOLD_SECONDS", "0.8"))
RCA_LCP_THRESHOLD_SECONDS = float(_env("RCA_LCP_THRESHOLD_SECONDS", "2.5"))
RCA_CLS_THRESHOLD = float(_env("RCA_CLS_THRESHOLD", "0.1"))
RCA_TBT_THRESHOLD_SECONDS = float(_env("RCA_TBT_THRESHOLD_SECONDS", "0.2"))
RCA_FULLY_LOADED_THRESHOLD_SECONDS = float(_env("RCA_FULLY_LOADED_THRESHOLD_SECONDS", "5.0"))
RCA_PAGE_BYTES_THRESHOLD_MB = float(_env("RCA_PAGE_BYTES_THRESHOLD_MB", "3.0"))
RCA_IMAGE_BYTES_THRESHOLD_MB = float(_env("RCA_IMAGE_BYTES_THRESHOLD_MB", "1.5"))
RCA_JS_BYTES_THRESHOLD_KB = float(_env("RCA_JS_BYTES_THRESHOLD_KB", "500"))
RCA_CSS_BYTES_THRESHOLD_KB = float(_env("RCA_CSS_BYTES_THRESHOLD_KB", "150"))
RCA_FONT_BYTES_THRESHOLD_KB = float(_env("RCA_FONT_BYTES_THRESHOLD_KB", "300"))
RCA_THIRD_PARTY_BYTES_THRESHOLD_KB = float(_env("RCA_THIRD_PARTY_BYTES_THRESHOLD_KB", "400"))
RCA_REQUEST_COUNT_THRESHOLD = int(_env("RCA_REQUEST_COUNT_THRESHOLD", "80"))
RCA_DOM_ELEMENTS_THRESHOLD = int(_env("RCA_DOM_ELEMENTS_THRESHOLD", "1500"))
RCA_MAX_ISSUES_IN_EMAIL = int(_env("RCA_MAX_ISSUES_IN_EMAIL", "5"))

# Sheet column headers for the new per-page "<Sheet>_RootCause" tabs.
# A brand-new tab per page — the existing HISTORY_HEADERS tabs are untouched.
ROOT_CAUSE_HEADERS = [
    "Date", "Time", "Performance Score", "Issue Count",
    "Top Issues", "Categories", "Report JSON",
]


@dataclass(frozen=True)
class Page:
    """One monitored page: a URL and the label it's tracked under.

    Feature 4 additions (all with defaults, so every pre-existing
    Page(...) call site — and any future one written the old two/three-
    positional-arg way — keeps working unmodified): per-page scheduling
    priority/interval, and per-page feature toggles so any monitoring
    module can be turned on/off for an individual page without touching
    that module's code.
    """
    name: str
    url: str
    sheet_name: str
    priority: str = "normal"           # "critical" | "normal" — informational label; interval_hours is what actually drives scheduling
    interval_hours: float = 2.0        # how often the scheduler considers this page "due" — see page_scheduler.py
    enabled: bool = True               # master switch — False means this page is skipped by every module entirely
    gtmetrix_enabled: bool = True
    journey_enabled: bool = False      # opt-in — matches the pre-Feature-4 behavior where only a curated subset ran journeys
    root_cause_enabled: bool = True
    alert_enabled: bool = True


# --- Pages to monitor --------------------------------------------------------
# Add a new page by adding one Page(...) entry — nothing else in the
# project needs to change. Unlimited pages supported; concurrency is
# controlled by MAX_WORKERS above.
#
# Feature 4: priority/interval_hours are config-driven per the project's
# monitoring-priority requirement — Homepage, Shop All, FNM, H50+, Fiber
# Fix, and Plant Protein Roti (the only plant-protein product in this
# list) run every 1 hour; every other page defaults to every 2 hours.
# To add a 30-minute, 4-hour, 6-hour, 12-hour, daily, or weekly page,
# change ONLY interval_hours here — page_scheduler.py never needs to
# change (it works in hours as a plain float; a weekly cadence is just
# interval_hours=168).
PAGES: list[Page] = [
    Page("Homepage", "https://www.goodmonk.in/", "Homepage", priority="critical", interval_hours=1),
    Page("Shop All", "https://www.goodmonk.in/collections/all", "Shop_All", priority="critical", interval_hours=1),
    Page("FNM", "https://www.goodmonk.in/products/good-monk", "FNM", priority="critical", interval_hours=1, journey_enabled=True),
    Page("H50+", "https://www.goodmonk.in/products/good-monk-50-nutrition-mix", "H50+", priority="critical", interval_hours=1, journey_enabled=True),
    Page("Fiber Fix", "https://www.goodmonk.in/products/fiber-fix", "FF", priority="critical", interval_hours=1, journey_enabled=True),
    Page("Berries", "https://www.goodmonk.in/products/instant-fruit-drink-mix-mixed-berries", "Berries"),
    Page("Orange", "https://www.goodmonk.in/products/instant-fruit-drink-mix-orange", "Orange"),
    Page("Pineapple", "https://www.goodmonk.in/products/instant-fruit-drink-mix-pineapple", "Pineapple"),
    Page("Mango", "https://www.goodmonk.in/products/instant-fruit-drink-mix-natural-mango-powder-50-less-sugar-with-8-vitamins-minerals", "Mango"),
    Page("Assorted", "https://www.goodmonk.in/products/instant-fruit-drink-mix-assorted", "Assorted"),
    Page("Milk Mix Strawberry", "https://www.goodmonk.in/products/good-monk-superhero-milk-mix-strawberry", "MM_Strawberry"),
    Page("Milk Mix Vanilla", "https://www.goodmonk.in/products/good-monk-superhero-milk-mix-vanilla", "MM_Vanilla"),
    Page("Milk Mix Chocolate", "https://www.goodmonk.in/products/good-monk-superhero-milk-mix", "MM_Chocolate"),
    Page("Slimbiotics", "https://www.goodmonk.in/products/good-monk-slimbiotics", "Slimbiotics"),
    Page("Weight Management", "https://www.goodmonk.in/products/good-monk-weight-management-program", "Weight Management"),
    Page("Plant Protein Roti", "https://www.goodmonk.in/products/plant-protein-for-rotis", "Plant Protein Roti", priority="critical", interval_hours=1),
]

# Convenience lookup — used by scheduler.py/main.py to find a page's
# config (e.g. root_cause_enabled) from just a sheet_name, without
# re-scanning the list every time.
PAGE_BY_SHEET_NAME: dict[str, Page] = {p.sheet_name: p for p in PAGES}

# Sheet column headers, in write order — matches the original Apps Script
# layout plus Date/Time split into two columns for easier sorting/filtering.
HISTORY_HEADERS = [
    "Date", "Time", "Performance Score", "Grade", "LCP",
    "Onload", "Fully Loaded", "TTFB", "CLS", "TBT", "Report URL", "Status",
]

# =============================================================================
# Feature 2 additions (Customer Journey Monitoring) — everything below this
# line is new. Nothing above it changed.
# =============================================================================

# --- Playwright runtime settings --------------------------------------------
JOURNEY_ENABLED = _env("JOURNEY_ENABLED", "true").strip().lower() != "false"
PLAYWRIGHT_HEADLESS = _env("PLAYWRIGHT_HEADLESS", "true").strip().lower() != "false"
PLAYWRIGHT_NAV_TIMEOUT_MS = int(_env("PLAYWRIGHT_NAV_TIMEOUT_MS", "30000"))
PLAYWRIGHT_ACTION_TIMEOUT_MS = int(_env("PLAYWRIGHT_ACTION_TIMEOUT_MS", "10000"))

# --- Retry behaviour (journey-specific — distinct from the GTmetrix
#     API retry settings above, since "retryable" means something different
#     for a browser: transient/navigation/timeout errors are retried,
#     a genuinely missing button is not). -------------------------------
JOURNEY_MAX_RETRIES = int(_env("JOURNEY_MAX_RETRIES", "2"))
JOURNEY_RETRY_BASE_DELAY_SECONDS = float(_env("JOURNEY_RETRY_BASE_DELAY_SECONDS", "2"))

# --- Screenshots -------------------------------------------------------------
# Written locally during the workflow run only, then uploaded as a GitHub
# Actions artifact by monitor.yml — never committed to the repo (see
# .gitignore). JOURNEY_SCREENSHOT_ON_SUCCESS is off by default: the
# requirement only asks for failure screenshots as a baseline, with success
# screenshots as an explicit opt-in (they add wall-clock time every run).
JOURNEY_SCREENSHOT_DIR = BASE_DIR / "journey_screenshots"
JOURNEY_SCREENSHOT_ON_SUCCESS = _env("JOURNEY_SCREENSHOT_ON_SUCCESS", "false").strip().lower() == "true"
JOURNEY_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# --- Journey target pages ----------------------------------------------------
# Homepage/collection are shared entry points for every journey run.
# JOURNEY_PRODUCTS is the set of products the funnel is actually tested
# against — deliberately a subset of PAGES (running the full
# homepage→checkout funnel for all 16 products every run would be slow and
# mostly redundant; the funnel mechanics are the same regardless of which
# product is used).
#
# Feature 4: now genuinely config-driven via each Page's `journey_enabled`
# flag (set on FNM, H50+, and Fiber Fix above — the same 3 products this
# list already contained before Feature 4) rather than a separate
# hardcoded sheet_name set — to change which products get journey-tested,
# flip `journey_enabled` on the relevant Page(...) entries above; nothing
# else in the project needs to change.
JOURNEY_HOMEPAGE_URL = _env("JOURNEY_HOMEPAGE_URL", "https://www.goodmonk.in/")
JOURNEY_COLLECTION_URL = _env("JOURNEY_COLLECTION_URL", "https://www.goodmonk.in/collections/all")
JOURNEY_CART_URL = _env("JOURNEY_CART_URL", "https://www.goodmonk.in/cart")

JOURNEY_PRODUCTS: list[Page] = [p for p in PAGES if p.journey_enabled] or PAGES[2:3]

# --- Layered selector strategy ------------------------------------------------
# Each journey element maps to an ORDERED list of Playwright locator strings.
# playwright_runner.find_first_visible() tries each in order and uses the
# first one that both exists AND is visible — so a theme that has a
# data-testid gets matched precisely, while a theme that has none still
# gets matched by its accessible role or button text. Pure CSS fallbacks are
# last resort. Edit this dict — not journey.py or playwright_runner.py — to
# tune matching for GoodMonk's actual theme.
JOURNEY_SELECTORS: dict[str, list[str]] = {
    "variant_selector": [
        "[data-testid='variant-selector'] option, [data-testid='variant-selector']",
        "select[name='id']",
        "select[data-option-index]",
        "fieldset input[type='radio'][name*='option']",
        ".product-form__input select",
    ],
    "add_to_cart_button": [
        "[data-testid='add-to-cart']",
        "button[name='add']",
        "[aria-label*='Add to cart' i]",
        "role=button[name=/add to cart/i]",
        "button:has-text('Add to cart')",
        ".product-form__submit, .product-form__cart-submit",
    ],
    "cart_drawer_or_page_indicator": [
        "[data-testid='cart-drawer']",
        "#CartDrawer, .cart-drawer",
        "[aria-label*='Cart' i][aria-expanded='true']",
        "a[href='/cart']",
    ],
    "checkout_button": [
        "[data-testid='checkout-button']",
        "button[name='checkout']",
        "[aria-label*='Checkout' i]",
        "role=button[name=/checkout/i]",
        "button:has-text('Checkout'), a:has-text('Checkout')",
        "#checkout, .cart__checkout-button",
    ],
}

# Ordered journey definition. `action` is interpreted by journey.py:
#   "navigate"       — go to `url`, verify it loaded
#   "select_variant" — best-effort; skipped cleanly if the product has no
#                       variant selector (i.e. a single-variant product)
#   "add_to_cart"    — click the add-to-cart button, verify a cart
#                       indicator changes
#   "verify_cart"    — navigate to the cart page, verify the product is in it
#   "checkout"       — click through to Shopify checkout, verify navigation
#                       lands on a checkout URL (never completes payment)
JOURNEY_STEPS: list[dict] = [
    {"name": "homepage", "action": "navigate", "url": JOURNEY_HOMEPAGE_URL},
    {"name": "collection", "action": "navigate", "url": JOURNEY_COLLECTION_URL},
    {"name": "product", "action": "navigate", "url": None},  # filled in per-product at run time
    {"name": "select_variant", "action": "select_variant", "selector_key": "variant_selector"},
    {"name": "add_to_cart", "action": "add_to_cart", "selector_key": "add_to_cart_button"},
    {"name": "cart", "action": "verify_cart", "url": JOURNEY_CART_URL},
    {"name": "checkout", "action": "checkout", "selector_key": "checkout_button"},
]

# Sheet headers for the new, single shared "CustomerJourney" tab.
JOURNEY_HEADERS = [
    "Date", "Time", "Product", "Overall Status", "Failed Step",
    "Duration (s)", "Console Errors", "JS Errors", "Network Failures",
    "Broken Images", "Screenshot Taken", "Details JSON",
]

# =============================================================================
# Feature 3 additions (Smart Alert System) — everything below this line is
# new. Nothing above it changed.
# =============================================================================

ALERT_ENABLED = _env("ALERT_ENABLED", "true").strip().lower() != "false"

# Points a page's GTmetrix score must drop between consecutive runs to
# trigger a "performance_score_drop" alert (separate from RCA's static
# ALERT_SCORE_THRESHOLD, which flags an absolute low score regardless of
# trend — this one flags a *change*).
ALERT_SCORE_DROP_THRESHOLD = float(_env("ALERT_SCORE_DROP_THRESHOLD", "10"))
ALERT_GRADE_DROP_ENABLED = _env("ALERT_GRADE_DROP_ENABLED", "true").strip().lower() != "false"

# Root Cause issues below this severity never become alerts (an "info"
# level RCA issue, e.g. slightly heavy CSS, isn't worth an email — a
# "warning" or "critical" one is). Matches root_cause.py's SEVERITY_* values.
ALERT_MIN_RCA_SEVERITY = _env("ALERT_MIN_RCA_SEVERITY", "warning")

# If every single page in a GTmetrix batch fails, that's treated as a
# systemic API/site problem (gtmetrix_api_failure), not N separate
# page-level alerts.
ALERT_ALL_PAGES_FAILED_IS_SYSTEMIC = _env("ALERT_ALL_PAGES_FAILED_IS_SYSTEMIC", "true").strip().lower() != "false"

# Alert severities, in ascending order — used to compare/sort severities
# and to validate a severity string passed into the engine.
ALERT_SEVERITY_ORDER = ["info", "warning", "high", "critical"]

# Central registry: every supported alert type -> its default severity.
# A future module (SSL, Lighthouse, API monitoring, SEO...) can emit any
# of these string keys directly, or a brand-new key (defaults to
# "warning" if not listed here) — the engine never needs to change for a
# new alert TYPE, only for a new alert SOURCE module, which supplies its
# own alerts via alert_rules.py.
ALERT_SEVERITY_MAP: dict[str, str] = {
    "website_down": "critical",
    "homepage_failed": "critical",
    "journey_failed": "high",
    "checkout_failed": "critical",
    "page_failed": "high",
    "performance_score_drop": "warning",
    "performance_grade_drop": "warning",
    "high_lcp": "warning",
    "high_cls": "warning",
    "high_ttfb": "warning",
    "large_images": "warning",
    "heavy_javascript": "warning",
    "heavy_css": "info",
    "high_dom_size": "info",
    "slow_server_response": "warning",
    "gtmetrix_api_failure": "critical",
    "playwright_failure": "critical",
    "google_sheets_failure": "critical",
    "dashboard_generation_failure": "high",
    "github_workflow_failure": "critical",
    "unexpected_exception": "critical",
}

# Sheet headers for the new, single shared "AlertHistory" tab.
ALERT_HEADERS = [
    "Date", "Time", "Alert Type", "Severity", "Module", "Affected Page",
    "Message", "Root Cause", "Status", "Occurrence Count",
]

# =============================================================================
# Feature 4 additions (Intelligent Monitoring Scheduler) — everything
# below this line is new. Nothing above it changed (each Page(...) entry
# above gained new keyword arguments with defaults, but every existing
# positional name/url/sheet_name value is untouched).
# =============================================================================

SCHEDULER_ENABLED = _env("SCHEDULER_ENABLED", "true").strip().lower() != "false"

# How much clock drift to tolerate when deciding if a page is "due" — a
# page due at exactly the top of the hour shouldn't get skipped for
# being checked 90 seconds early by a slightly-off cron trigger.
SCHEDULER_DUE_TOLERANCE_MINUTES = float(_env("SCHEDULER_DUE_TOLERANCE_MINUTES", "5"))

# Sheet headers for the new, single shared "SchedulerRuns" tab — one row
# per workflow run, summarizing what the scheduler decided.
SCHEDULER_HEADERS = [
    "Date", "Time", "Pages Checked", "Pages Skipped", "Skip Reasons",
    "Journeys Run", "Duration (s)", "Trigger",
]

