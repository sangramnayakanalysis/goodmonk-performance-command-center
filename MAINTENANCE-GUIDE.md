# Maintenance Guide — v1.0

Common day-2 operations, and where to make each kind of change.

## Adding a new page to monitor

Edit `config.PAGES` — add one `Page(...)` entry. Nothing else needs to
change. Set `priority`/`interval_hours` to match how often it should be
checked; leave the feature toggles (`journey_enabled`, etc.) at their
defaults unless you specifically want that page in the journey funnel or
want to disable RCA/alerts for it.

## Changing a page's monitoring frequency

Change only that page's `interval_hours` in `config.PAGES`. Any value
works — `0.5` (30 min) through `168` (weekly) or beyond. No other file
needs to change.

## Adding a new alert type

1. Add the type string to `config.ALERT_SEVERITY_MAP` with a default
   severity (or skip this — an unlisted type defaults to `"warning"`).
2. Add a rule function (or extend an existing one) in `alert_rules.py`
   that produces `Alert` objects with that `alert_type`.
3. Call `alerts.evaluate_domain()` (for something checked against a
   known set of pages/keys each run) or `alerts.raise_operational()` /
   `alerts.mark_recovered()` (for a simple fire/recover exception case)
   from wherever the new condition is detected.

Nothing in `notification_manager.py` ever needs to change for a new
alert type.

## Adding a new monitoring module (SSL, Lighthouse, API monitoring, etc.)

This is the scenario the whole architecture was built to support easily:
1. Write the new module (e.g. `ssl_check.py`) following the existing
   pattern — a function that returns a result object, isolated
   try/except at every I/O boundary, never raises out of its own
   top-level entry point.
2. Add a rule function to `alert_rules.py` that translates its results
   into `Alert` objects.
3. Wire it into `main.py`, following the exact pattern used for Journey
   in Feature 2 — a guarded import, an isolated try/except block, a call
   to `alerts.evaluate_domain()`.
4. Optionally add a Sheets tab function (`google_sheet.py`), a
   `dashboard_data.build_*_summary()` function, and an
   `email_report._build_*_html()` section, following the existing
   per-feature patterns exactly.

## Tuning journey selectors

Edit `config.JOURNEY_SELECTORS` — each key is a layered list of
Playwright locator strings tried in order (data-testid → aria-label →
role → text → CSS). This is the **first thing to check** if journey
tests are failing unexpectedly — see `README.md`'s Troubleshooting
section.

## Rotating credentials

If the GTmetrix API key or Google service account key is ever exposed
(committed by accident, shared in a chat, etc.), rotate it immediately:
generate a new key/credential from the provider, update the
corresponding GitHub Actions secret, and treat the old one as
permanently compromised.

## Investigating a failed run

1. Check the failed GitHub Actions run's logs directly — every module
   logs through `logger.py` with clear context.
2. Check the summary email (if configured) — it now includes GTmetrix,
   RCA, Journey, Alerts, and Scheduler sections in one place.
3. Check `dashboard/data/scheduler.json`'s `scheduler_health` field and
   `unhealthy_pages` list — flags pages with 3+ consecutive failures.
4. Check the relevant Google Sheets tab for the raw historical data.

## Clearing stuck state

If a page seems permanently "stuck" (e.g. an alert never recovers even
though the underlying issue is fixed), you can manually edit or clear
the relevant state file:
- `data/run_state.json` — resume-on-interrupt state for the *current*
  run only; safe to clear anytime between runs.
- `data/alert_state.json` — active alert dedup state; clearing this
  will cause every currently-active alert to re-fire as "new" on the
  next run (use `notification_manager.clear_all_state()` if doing this
  programmatically, or edit the JSON directly).
- `data/page_schedule_state.json` — per-page last/next run tracking;
  clearing this makes every page "due" on the next run.

All three are plain JSON and safe to hand-edit if needed, as long as the
top-level shape (`{"active": {}}`, `{"pages": {}}`, etc.) is preserved.

## Upgrading dependencies

`requirements.txt` uses upper-bound pins (`>=X,<Y`) — running
`pip install -U -r requirements.txt` will pick up patch/minor updates
automatically but never jump a major version unexpectedly. To upgrade
across a major version (e.g. Playwright 1.x → 2.x), do it deliberately:
bump the pin, test locally, then deploy.

## Where NOT to make changes casually

- `notification_manager.py` and `page_scheduler.py` are intentionally
  generic and have no domain knowledge — resist the urge to add
  GTmetrix/Journey-specific logic directly into either file. Domain
  logic belongs in `alert_rules.py` (for alerts) or in the calling code
  in `main.py` (for scheduling decisions).
- Don't add a second GitHub Actions workflow file — the single-workflow,
  scheduler-decides-what's-due design is deliberate (see Feature 4's
  report) and avoids the coordination complexity of multiple competing
  cron schedules.
