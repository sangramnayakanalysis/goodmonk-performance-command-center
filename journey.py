"""
journey.py
==========
Feature 2 (Customer Journey Monitoring): orchestration.

Runs the full Homepage -> Collection -> Product -> Select Variant ->
Add to Cart -> Cart -> Checkout funnel, once per configured critical
product (config.JOURNEY_PRODUCTS), reusing one shared browser instance
(playwright_runner.PlaywrightRunner) across all of them but giving each
product its own fresh browser context (so one product's cart contents
never leak into the next product's run).

Failure isolation matches the rest of this project's philosophy exactly:
one product's journey failing never stops the others, and this module's
entry point (run_all_journeys) never raises — it always returns a list of
JourneyResult, with failures recorded in the result rather than thrown.
"""

from __future__ import annotations

import time

import config
from journey_models import JourneyResult, StepResult
from logger import get_logger
from playwright_runner import (
    ElementNotFoundError,
    PlaywrightRunner,
    TransientBrowserError,
    check_broken_images,
    click_when_ready,
    drain_captured_events,
    find_first_visible,
    goto,
    take_screenshot,
)
from utils import now_iso

log = get_logger("journey")


def _with_retry(step_name: str, fn):
    """
    Runs `fn()` (a zero-arg callable performing one step's browser work),
    retrying only on TransientBrowserError, up to config.JOURNEY_MAX_RETRIES
    additional attempts with linear backoff. ElementNotFoundError (a
    permanent failure — the button genuinely isn't there) is NOT retried,
    exactly per the "do not retry permanent failures such as missing
    buttons" requirement — it's re-raised immediately on first occurrence.
    """
    last_exc = None
    for attempt in range(1, config.JOURNEY_MAX_RETRIES + 2):  # +1 initial try, +1 for range inclusivity
        try:
            result = fn()
            if attempt > 1:
                log.info("Step '%s' succeeded on retry attempt %d.", step_name, attempt)
            return result, attempt - 1
        except ElementNotFoundError:
            raise  # permanent — never retried
        except TransientBrowserError as e:
            last_exc = e
            log.warning("Step '%s' transient failure (attempt %d/%d): %s",
                        step_name, attempt, config.JOURNEY_MAX_RETRIES + 1, e)
            if attempt <= config.JOURNEY_MAX_RETRIES:
                time.sleep(config.JOURNEY_RETRY_BASE_DELAY_SECONDS * attempt)
    raise last_exc


def _run_navigate_step(page, step: dict, product) -> StepResult:
    url = step["url"] or product.url  # the "product" step has url=None in config, filled here
    result = StepResult(step_name=step["name"])
    try:
        (success, status, load_time, error), retries = _with_retry(
            step["name"], lambda: goto(page, url)
        )
        result.success = success
        result.http_status = status
        result.load_time_seconds = load_time
        result.error_message = error
        result.retried = retries
        result.page_title = page.title() if success else ""
        result.broken_images = check_broken_images(page) if success else []
    except (TransientBrowserError, ElementNotFoundError) as e:
        result.success = False
        result.error_message = str(e)
        result.permanent_failure = isinstance(e, ElementNotFoundError)
    drain_captured_events(page, result)
    return result


def _run_select_variant_step(page, step: dict, product) -> StepResult:
    """Best-effort: many products have only one variant and no selector
    at all — that's a normal, successful outcome, not a failure."""
    result = StepResult(step_name=step["name"])
    start = time.monotonic()
    selectors = config.JOURNEY_SELECTORS.get(step["selector_key"], [])
    try:
        locator = find_first_visible(page, selectors)
        locator.first.click(timeout=config.PLAYWRIGHT_ACTION_TIMEOUT_MS)
        result.success = True
    except ElementNotFoundError:
        # No variant selector found = single-variant product = nothing to
        # select. This is expected and NOT a failure.
        result.success = True
        result.error_message = "No variant selector present (likely a single-variant product) — skipped."
    except Exception as e:  # noqa: BLE001 — a variant-select hiccup shouldn't fail the whole journey
        result.success = True
        result.error_message = f"Variant selection skipped after an error (non-fatal): {e}"
    result.load_time_seconds = round(time.monotonic() - start, 2)
    drain_captured_events(page, result)
    return result


def _run_add_to_cart_step(page, step: dict, product) -> StepResult:
    result = StepResult(step_name=step["name"])
    selectors = config.JOURNEY_SELECTORS.get(step["selector_key"], [])
    start = time.monotonic()
    try:
        def _click():
            locator = find_first_visible(page, selectors)
            click_when_ready(page, locator)
        _, retries = _with_retry(step["name"], _click)
        result.success = True
        result.retried = retries
    except ElementNotFoundError as e:
        result.success = False
        result.permanent_failure = True
        result.error_message = str(e)
    except TransientBrowserError as e:
        result.success = False
        result.error_message = str(e)
    result.load_time_seconds = round(time.monotonic() - start, 2)
    result.page_title = page.title()
    drain_captured_events(page, result)
    return result


def _run_verify_cart_step(page, step: dict, product) -> StepResult:
    result = StepResult(step_name=step["name"])
    try:
        (success, status, load_time, error), retries = _with_retry(
            step["name"], lambda: goto(page, step["url"])
        )
        result.success = success
        result.http_status = status
        result.load_time_seconds = load_time
        result.error_message = error
        result.retried = retries
        result.page_title = page.title() if success else ""
    except (TransientBrowserError, ElementNotFoundError) as e:
        result.success = False
        result.error_message = str(e)
        result.permanent_failure = isinstance(e, ElementNotFoundError)
    drain_captured_events(page, result)
    return result


def _run_checkout_step(page, step: dict, product) -> StepResult:
    """Clicks through to Shopify checkout and verifies the resulting URL
    looks like a checkout URL. Never completes payment — Shopify's
    checkout step itself is the destination, not an order placed."""
    result = StepResult(step_name=step["name"])
    selectors = config.JOURNEY_SELECTORS.get(step["selector_key"], [])
    start = time.monotonic()
    try:
        def _click():
            locator = find_first_visible(page, selectors)
            click_when_ready(page, locator)
        _, retries = _with_retry(step["name"], _click)
        page.wait_for_load_state("load", timeout=config.PLAYWRIGHT_NAV_TIMEOUT_MS)
        landed_on_checkout = "checkout" in (page.url or "").lower()
        result.success = landed_on_checkout
        result.retried = retries
        if not landed_on_checkout:
            result.error_message = f"Clicked checkout but did not land on a checkout URL (got: {page.url})"
    except ElementNotFoundError as e:
        result.success = False
        result.permanent_failure = True
        result.error_message = str(e)
    except TransientBrowserError as e:
        result.success = False
        result.error_message = str(e)
    result.load_time_seconds = round(time.monotonic() - start, 2)
    result.page_title = page.title()
    drain_captured_events(page, result)
    return result


_STEP_HANDLERS = {
    "navigate": _run_navigate_step,
    "select_variant": _run_select_variant_step,
    "add_to_cart": _run_add_to_cart_step,
    "verify_cart": _run_verify_cart_step,
    "checkout": _run_checkout_step,
}


def run_journey(runner: PlaywrightRunner, product) -> JourneyResult:
    """
    Runs the full funnel (config.JOURNEY_STEPS) for one product, in one
    fresh browser context. Never raises — any unexpected exception is
    caught and recorded as a failed step, exactly like
    gtmetrix.run_single_page's isolation boundary.
    """
    started = now_iso()
    start_time = time.monotonic()
    result = JourneyResult(product_name=product.name, product_url=product.url, started_at=started)

    ctx = None
    try:
        ctx, page = runner.new_page()

        for step in config.JOURNEY_STEPS:
            handler = _STEP_HANDLERS.get(step["action"])
            if handler is None:
                log.warning("Unknown journey step action '%s' — skipping (future-step-safe no-op).", step["action"])
                continue

            step_result = handler(page, step, product)
            result.steps.append(step_result)

            if not step_result.success:
                result.success = False
                result.failed_step = step["name"]
                # Screenshot on failure — required. Screenshot on success is
                # opt-in (config.JOURNEY_SCREENSHOT_ON_SUCCESS) since it adds
                # time to every run and every step, not just failures.
                step_result.screenshot_path = take_screenshot(
                    page, f"{product.sheet_name}_{step['name']}_FAILED.png"
                )
                log.error("Journey FAILED for %s at step '%s': %s",
                          product.name, step["name"], step_result.error_message)
                break  # a broken funnel step means every downstream step is meaningless
            else:
                if config.JOURNEY_SCREENSHOT_ON_SUCCESS:
                    step_result.screenshot_path = take_screenshot(
                        page, f"{product.sheet_name}_{step['name']}_OK.png"
                    )
        else:
            result.success = True
            log.info("Journey OK for %s — all %d steps passed.", product.name, len(config.JOURNEY_STEPS))

    except Exception as e:  # noqa: BLE001 — top-level isolation boundary, matches run_single_page's pattern
        result.success = False
        result.failed_step = result.failed_step or "unexpected_error"
        result.steps.append(StepResult(step_name="unexpected_error", success=False, error_message=str(e)))
        log.error("Unexpected error running journey for %s: %s", product.name, e)
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001 — context cleanup must never raise into the caller
                pass

    result.finished_at = now_iso()
    result.total_duration_seconds = round(time.monotonic() - start_time, 2)
    return result


def to_sheet_row(result: JourneyResult) -> list:
    """Flattens a JourneyResult into a row matching config.JOURNEY_HEADERS."""
    from utils import now_date_str, now_time_str  # local import, mirrors root_cause.py's pattern
    import json as _json

    return [
        now_date_str(),
        now_time_str(),
        result.product_name,
        "Success" if result.success else "Failed",
        result.failed_step or "",
        result.total_duration_seconds,
        "; ".join(result.all_console_errors[:5]),
        "; ".join(result.all_js_errors[:5]),
        "; ".join(result.all_network_failures[:5]),
        "; ".join(result.all_broken_images[:5]),
        "Yes" if result.had_screenshot else "No",
        _json.dumps(result.to_dict(), default=str),
    ]


def run_all_journeys(products: list | None = None) -> list[JourneyResult]:
    """
    Runs one journey per product (defaults to config.JOURNEY_PRODUCTS —
    unchanged from before Feature 4), sharing a single Playwright browser
    instance for all of them. Never raises: if Playwright itself fails to
    launch, or any one journey blows up unexpectedly, this returns
    whatever it managed to collect (possibly an empty list) rather than
    propagating — the caller (main.py) already wraps this call in its own
    try/except as a second layer of safety, but this function is designed
    to not need it.

    Feature 4 addition: the new optional `products` parameter lets a
    caller pass in a filtered subset (e.g. only journey-enabled products
    the hourly scheduler has decided are due right now). Every existing
    call site that doesn't pass `products` keeps testing all of
    config.JOURNEY_PRODUCTS exactly as before.
    """
    if not config.JOURNEY_ENABLED:
        log.info("Journey monitoring disabled (JOURNEY_ENABLED=false) — skipping.")
        return []

    products = list(products) if products is not None else list(config.JOURNEY_PRODUCTS)

    results: list[JourneyResult] = []
    try:
        with PlaywrightRunner() as runner:
            for product in products:
                try:
                    results.append(run_journey(runner, product))
                except Exception as e:  # noqa: BLE001 — one product's journey must never stop the rest
                    log.error("Journey run crashed unexpectedly for %s (skipping, continuing with next product): %s",
                              product.name, e)
                    results.append(JourneyResult(
                        product_name=product.name, product_url=product.url,
                        started_at=now_iso(), finished_at=now_iso(),
                        success=False, failed_step="unexpected_error",
                    ))
    except Exception as e:  # noqa: BLE001 — e.g. Playwright/browser failed to launch at all
        log.error("Could not start Playwright — journey monitoring skipped for this run: %s", e)

    ok = sum(1 for r in results if r.success)
    log.info("Journey batch finished. %d/%d products completed the full funnel successfully.", ok, len(results))
    return results
