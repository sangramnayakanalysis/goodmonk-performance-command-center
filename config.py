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


@dataclass(frozen=True)
class Page:
    """One monitored page: a URL and the label it's tracked under."""
    name: str
    url: str
    sheet_name: str


# --- Pages to monitor --------------------------------------------------------
# Add a new page by adding one Page(...) entry — nothing else in the
# project needs to change. Unlimited pages supported; concurrency is
# controlled by MAX_WORKERS above.
PAGES: list[Page] = [
    Page("Homepage", "https://www.goodmonk.in/", "Homepage"),
    Page("Shop All", "https://www.goodmonk.in/collections/all", "Shop_All"),
    Page("FNM", "https://www.goodmonk.in/products/good-monk", "FNM"),
    Page("H50+", "https://www.goodmonk.in/products/good-monk-50-nutrition-mix", "H50+"),
    Page("Fiber Fix", "https://www.goodmonk.in/products/fiber-fix", "FF"),
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
    Page("Plant Protein Roti", "https://www.goodmonk.in/products/plant-protein-for-rotis", "Plant Protein Roti"),
]

# Sheet column headers, in write order — matches the original Apps Script
# layout plus Date/Time split into two columns for easier sorting/filtering.
HISTORY_HEADERS = [
    "Date", "Time", "Performance Score", "Grade", "LCP",
    "Onload", "Fully Loaded", "TTFB", "CLS", "TBT", "Report URL", "Status",
]
