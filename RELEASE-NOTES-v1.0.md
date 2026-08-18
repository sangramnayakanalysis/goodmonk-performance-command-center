# Release Notes — v1.0

**GoodMonk Performance Command Center — first production release.**

## Summary

What began as a daily GTmetrix speed-test runner is now a full website
monitoring platform: performance testing, automatic root cause analysis,
real-browser customer journey testing, a deduplicated alert engine, and
an intelligent per-page scheduler — all running hourly via a single
GitHub Actions workflow, with zero server infrastructure.

## What's included

- **GTmetrix Performance Monitoring** — threaded, retried, resumable
  GTmetrix API v2 testing per configured page.
- **Root Cause Analysis** — automatic detection of ~15 performance issue
  categories (large images, high TTFB, heavy JS/CSS, high DOM size, and
  more) from each GTmetrix result.
- **Customer Journey Monitoring** — Playwright-driven Homepage → Collection
  → Product → Add to Cart → Cart → Checkout testing, with configurable
  layered selectors, screenshots on failure, and console/JS/network error
  capture.
- **Smart Alert System** — a generic, deduplicated alert engine (21
  built-in alert types) with new/suppressed/recovered state tracking, so
  a broken page emails you once, not every hour it stays broken.
- **Intelligent Monitoring Scheduler** — hourly workflow execution with
  per-page, config-driven monitoring frequency (default: 6 critical
  pages hourly, 10 normal pages every 2 hours).
- **Dashboard** — a live static site (GitHub Pages) with 8 data sources:
  performance vitals, root cause cards, journey status, alerts, and
  scheduler health, refreshing every 30 seconds.
- **Google Sheets integration** — 5 tab types (per-page history,
  per-page root cause, shared journey log, shared alert log, shared
  scheduler run log), all auto-created, none ever overwritten.
- **Email reports** — one HTML summary per run, with sections for every
  module above.

## Production hardening performed for this release

- Removed 4 genuinely unused imports/variables (found via static
  analysis, not guessed).
- Deduplicated repeated inline imports and a repeated try/except
  pattern.
- **Found and fixed one real bug introduced during that same hardening
  pass** (a refactor that would have crashed when the alert engine is
  unavailable) — caught by testing before release, documented in full in
  `FINAL-VERIFICATION-REPORT.md`.
- Pinned dependency versions with upper bounds.
- Completely rewrote `README.md`.
- Verified zero regressions across every module via a full cross-feature
  integration test pass.

## Known limitations (see `FINAL-PRODUCTION-READINESS-REPORT.md` for the full, undiluted list)

- **Journey selectors have never been run against the real goodmonk.in
  theme** — they're sensible generic defaults, not verified ones. This
  is the most important thing to check before trusting Journey/Checkout
  alerts.
- **GTmetrix quota (~7,900 tests/month at this cadence) is an estimate,
  not a confirmed fact** — verify against your actual plan before
  relying on hourly critical-page monitoring.
- **No automated test suite** — testing throughout this project's
  development was thorough but manual; nothing runs automatically on
  future commits.
- Score/grade-drop alerts need 2+ real historical data points per page
  to ever fire — will resolve naturally after the system has run for a
  while.

## Versioning going forward

This is v1.0 — the first version considered feature-complete and
deployable. Future changes should follow the same additive discipline
established across Features 1–4: extend, don't rewrite; isolate new
failure modes; test the failure path, not just the happy path (see the
bug caught in this very release for why that specific discipline
matters).
