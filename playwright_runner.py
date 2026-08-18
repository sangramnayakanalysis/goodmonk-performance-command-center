"""
playwright_runner.py
=====================
Feature 2 (Customer Journey Monitoring): the browser mechanics layer.

Owns exactly one Playwright + Browser instance for the whole monitoring
run (per the "reuse one browser instance, create new contexts as needed"
requirement) — journey.py opens a fresh, isolated BrowserContext (and
therefore a fresh cookie jar / cart session) per product journey, but never
launches a second browser process.

Nothing GTmetrix-related is imported here and nothing in this file is
imported by gtmetrix.py/scheduler.py — this module is only ever used by
journey.py, keeping journey code and GTmetrix code fully separate per the
modular-design requirement.
"""

from __future__ import annotations

import time
from typing import Optional

import config
from journey_models import StepResult
from logger import get_logger

log = get_logger("playwright_runner")


class ElementNotFoundError(Exception):
    """A permanent failure: none of the configured selectors matched.
    Deliberately NOT a subclass of any "retry me" error — retrying a
    genuinely missing button wastes time and never succeeds."""


class TransientBrowserError(Exception):
    """A retryable failure: navigation timeout, network hiccup, browser
    context error. journey.py retries these up to JOURNEY_MAX_RETRIES."""


class PlaywrightRunner:
    """
    Context-manager wrapping one Playwright instance + one Browser.
    Usage:

        with PlaywrightRunner() as runner:
            ctx, page = runner.new_page()
            ... use page ...
            ctx.close()
    """

    def __init__(self):
        self._playwright = None
        self._browser = None

    def __enter__(self) -> "PlaywrightRunner":
        # Imported lazily so the rest of the project (GTmetrix pipeline,
        # RCA, dashboards) never requires the `playwright` package to be
        # installed to keep working — see journey.py's import guard.
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=config.PLAYWRIGHT_HEADLESS)
        log.info("Playwright browser launched (headless=%s).", config.PLAYWRIGHT_HEADLESS)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._playwright:
                self._playwright.stop()
        log.info("Playwright browser closed.")

    def new_page(self):
        """
        Returns (context, page). A fresh context = a fresh cookie jar/cart
        session, cheap compared to launching a whole new browser process.
        Attaches listeners that accumulate console errors, page errors
        (uncaught JS exceptions), and failed network requests onto the
        page object itself (`page._captured_*`) so callers can read them
        after any action without needing to pass listener state around.
        """
        ctx = self._browser.new_context()
        ctx.set_default_navigation_timeout(config.PLAYWRIGHT_NAV_TIMEOUT_MS)
        ctx.set_default_timeout(config.PLAYWRIGHT_ACTION_TIMEOUT_MS)
        page = ctx.new_page()

        page._captured_console_errors = []
        page._captured_js_errors = []
        page._captured_network_failures = []

        page.on("console", lambda msg: page._captured_console_errors.append(msg.text)
                 if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page._captured_js_errors.append(str(exc)))
        page.on("requestfailed", lambda req: page._captured_network_failures.append(
            f"{req.method} {req.url} — {req.failure}"
        ))

        return ctx, page


def goto(page, url: str) -> tuple[bool, Optional[int], float, str]:
    """Navigates `page` to `url`. Returns (success, http_status, load_time_seconds, error_message)."""
    start = time.monotonic()
    try:
        resp = page.goto(url, wait_until="load")
        load_time = round(time.monotonic() - start, 2)
        status = resp.status if resp else None
        if resp is not None and not resp.ok:
            return False, status, load_time, f"HTTP {status} loading {url}"
        return True, status, load_time, ""
    except Exception as e:  # noqa: BLE001 — normalized into TransientBrowserError by the caller
        load_time = round(time.monotonic() - start, 2)
        raise TransientBrowserError(f"Navigation to {url} failed: {e}") from e


def find_first_visible(page, selectors: list[str]):
    """
    Layered selector strategy: tries each Playwright locator string in
    order, returns the first Locator that both matches and is visible.
    Raises ElementNotFoundError (a PERMANENT failure — not retried) if
    none of the candidates match anything visible.
    """
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if locator.count() > 0 and locator.is_visible():
                return locator
        except Exception as e:  # noqa: BLE001 — a bad selector string must not abort the whole search
            log.debug("Selector candidate %r did not evaluate cleanly: %s", sel, e)
            continue
    raise ElementNotFoundError(f"None of the configured selectors matched a visible element: {selectors}")


def click_when_ready(page, locator) -> None:
    """Waits for the element to be enabled, then clicks it. Timeout ->
    TransientBrowserError (retryable); the element simply not existing is
    handled earlier, by find_first_visible, as a permanent failure."""
    try:
        locator.wait_for(state="visible", timeout=config.PLAYWRIGHT_ACTION_TIMEOUT_MS)
        if not locator.is_enabled():
            raise ElementNotFoundError("Matched element is present but disabled.")
        locator.click(timeout=config.PLAYWRIGHT_ACTION_TIMEOUT_MS)
    except ElementNotFoundError:
        raise
    except Exception as e:  # noqa: BLE001
        raise TransientBrowserError(f"Click failed: {e}") from e


def check_broken_images(page) -> list[str]:
    """Returns the src of every <img> on the page whose naturalWidth is 0
    (Playwright/Chromium's standard signal for a failed image load)."""
    try:
        return page.eval_on_selector_all(
            "img",
            "imgs => imgs.filter(i => i.complete && i.naturalWidth === 0).map(i => i.src)",
        )
    except Exception as e:  # noqa: BLE001 — a broken-image check must never break the step itself
        log.warning("Broken-image check failed (non-fatal): %s", e)
        return []


def take_screenshot(page, filename: str) -> Optional[str]:
    """Saves a PNG to config.JOURNEY_SCREENSHOT_DIR. Returns the path, or
    None if the screenshot itself fails (never raises — a failed
    screenshot must not mask the actual step failure it was taken for)."""
    try:
        path = config.JOURNEY_SCREENSHOT_DIR / filename
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception as e:  # noqa: BLE001
        log.warning("Screenshot capture failed (non-fatal): %s", e)
        return None


def drain_captured_events(page, into: StepResult) -> None:
    """Copies this page's accumulated console/js/network events into a
    StepResult. Called once per step so each StepResult reflects only
    what happened during that step, not the whole journey."""
    into.console_errors = list(getattr(page, "_captured_console_errors", []))
    into.js_errors = list(getattr(page, "_captured_js_errors", []))
    into.network_failures = list(getattr(page, "_captured_network_failures", []))
    # Reset for the next step within the same page/context.
    page._captured_console_errors = []
    page._captured_js_errors = []
    page._captured_network_failures = []
