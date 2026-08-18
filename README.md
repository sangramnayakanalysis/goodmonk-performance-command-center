# GoodMonk Performance Command Center

**Version 1.0** — a production website monitoring platform for GoodMonk's
Shopify store: GTmetrix performance testing, automatic root cause
analysis, real browser-based customer journey testing, a generic alert
engine, an intelligent per-page scheduler, Google Sheets history, and a
live static dashboard — running entirely on Python + GitHub Actions, no
server required.

---

## 1. Project Overview

This started as a single-purpose GTmetrix speed-test runner and grew,
feature by feature, into a full monitoring platform:

| # | Feature | What it does |
|---|---|---|
| 0 | GTmetrix Performance Monitoring | Tests configured pages via the GTmetrix API v2, stores history in Google Sheets |
| 1 | Root Cause Analysis | Automatically flags *why* a page is slow (large images, high TTFB, heavy JS/CSS, high DOM size, etc.) |
| 2 | Customer Journey Monitoring | Real Playwright browser runs through Homepage → Collection → Product → Add to Cart → Cart → Checkout |
| 3 | Smart Alert System | A generic, deduplicated alert engine any module can raise alerts through — one email per new issue, one on recovery, never a duplicate |
| 4 | Intelligent Monitoring Scheduler | Runs hourly; decides per-page which pages are actually due, based on config-driven priority tiers |

Every feature above was built as a strictly additive extension of what
came before it — nothing was rewritten to add a later feature. See
`RELEASE-NOTES-v1.0.md` for the full history.

---

## 2. Architecture Diagram

```
GitHub Actions (hourly cron: "0 * * * *")
        │
        ▼
   main.py
        │
        ├─► page_scheduler.get_due_pages(config.PAGES)
        │        → due_pages, skipped_pages
        │        (reads/writes data/page_schedule_state.json)
        │
        ├─► scheduler.run_batch(pages=due_pages)
        │        → gtmetrix.run_single_page() per page, threaded
        │        → google_sheet.append_result/append_failure  (per-page history tabs)
        │        → root_cause.analyze()                        (if root_cause_enabled)
        │        → google_sheet.append_root_cause              (per-page "<Page>_RootCause" tabs)
        │        (reads/writes data/run_state.json — resume-on-interrupt)
        │
        ├─► journey.run_all_journeys(products=due ∩ journey_enabled)
        │        → playwright_runner (one shared browser, fresh context per product)
        │        → google_sheet.append_journey                 ("CustomerJourney" tab)
        │
        ├─► page_scheduler.record_run() per page
        │        → google_sheet.append_scheduler_run           ("SchedulerRuns" tab)
        │
        ├─► dashboard_data.build_all() + build_root_cause_summary()
        │      + build_journey_summary() + build_alerts_summary()
        │      + build_scheduler_summary()
        │        → dashboard/data/*.json  (8 files)
        │
        ├─► alert_rules.rules_for_*() → alerts.evaluate_domain()
        │        → notification_manager (dedup/recovery state)
        │        (reads/writes data/alert_state.json)
        │        → google_sheet.append_alert                   ("AlertHistory" tab)
        │
        └─► email_report.send_report(...)
                 → one HTML summary email with Performance, Root Cause,
                   Journey, Alerts, and Scheduler sections

   Workflow then commits dashboard/data/*.json + data/*.json (3 state
   files) back to the repo, and deploys dashboard/ to GitHub Pages.
```

**Layering principle:** each module only knows about the layer directly
below it. `notification_manager.py` (the alert dedup engine) has never
heard of GTmetrix, RCA, or Journey — it only knows about generic `Alert`
objects. `page_scheduler.py` has never heard of GTmetrix or Playwright —
it only knows about `Page.interval_hours`. This is why 4 features were
added without ever rewriting an earlier one.

---

## 3. Folder Structure

```
.
├── main.py                  Entry point — orchestrates every module in order
├── config.py                Single source of truth: pages, thresholds, env loading
├── scheduler.py             GTmetrix batch runner (threaded, resumable)
├── gtmetrix.py               GTmetrix API v2 client
├── root_cause.py              Root Cause Analysis engine
├── journey.py                   Customer Journey orchestration
├── journey_models.py             Journey data structures
├── playwright_runner.py           Browser mechanics (shared browser, layered selectors)
├── alerts.py                        Alert Engine facade + CLI
├── alert_rules.py                    Domain → generic alert translation
├── alert_models.py                    Alert/AlertEvent data structures
├── notification_manager.py             Dedup/recovery state machine
├── page_scheduler.py                    Due/skip decision engine
├── google_sheet.py           Google Sheets read/write (gspread)
├── dashboard_data.py          Builds dashboard/data/*.json
├── email_report.py             HTML summary email
├── logger.py                    Structured logging
├── utils.py                       Retry decorator, JSON I/O, time helpers
│
├── dashboard/                Static site (GitHub Pages)
│   ├── index.html
│   ├── css/style.css
│   ├── js/app.js
│   └── data/*.json            Generated — 8 files, committed by CI
│
├── data/                      Persistent state — committed by CI
│   ├── run_state.json           Resume-on-interrupt (Feature 0)
│   ├── alert_state.json          Alert dedup/recovery (Feature 3)
│   └── page_schedule_state.json   Per-page last/next run (Feature 4)
│
├── logs/                      Runtime logs (gitignored)
├── reports/                   Reserved, currently unused (see §12, known gaps)
├── journey_screenshots/       Failure/success screenshots (gitignored, uploaded as CI artifact)
│
└── .github/workflows/
    └── monitor.yml             Single hourly workflow — test, alert, deploy
```

---

## 4. Features

### GTmetrix Performance Monitoring
Tests every due page via GTmetrix API v2 (start → poll → fetch report),
with retry/backoff and rate-limit handling, concurrent via a thread pool.
Results append to a per-page Google Sheets tab (`<PageName>`).

### Root Cause Analysis
After every successful test, `root_cause.py` runs threshold-based rules
against the metrics (and whatever extra resource-breakdown fields your
GTmetrix plan tier returns) to flag categories like High TTFB, Large
Images, Heavy JavaScript, High DOM Size, and more. Results go to a
`<PageName>_RootCause` Sheets tab, the dashboard, and the email.

### Customer Journey Monitoring
`journey.py` drives a real Chromium browser (via Playwright) through
Homepage → Collection → Product → Select Variant → Add to Cart → Cart →
Checkout for each configured critical product, checking page loads,
console/JS errors, network failures, broken images, and button
clickability at every step — with screenshots on failure. Never submits
real payment. Selectors are fully configurable (`config.JOURNEY_SELECTORS`)
using a layered data-testid → aria-label → role → text → CSS fallback
strategy, since nothing is hardcoded to a specific theme.

### Smart Alert System
A generic engine (`alerts.py`, `alert_rules.py`, `notification_manager.py`)
turns any module's output into deduplicated alerts: one email when an
issue first appears, silence on every subsequent run while it's still
broken, one recovery email when it resolves, and a fresh alert if it
breaks again later. 21 alert types across performance, journey, and
operational failures (Sheets/dashboard/Playwright/GitHub-workflow-level).

### Intelligent Monitoring Scheduler
GitHub Actions runs once an hour; `page_scheduler.py` decides which
configured pages are actually due, based on each page's own
`interval_hours` in `config.py`. No page-count or interval limit, and no
scheduler code ever needs to change to support a new cadence (30 min, 2h,
6h, daily, weekly — just a number).

---

## 5. Installation

```bash
git clone <your-repo-url>
cd goodmonk-performance-command-center
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install --with-deps chromium               # only needed for Journey monitoring
cp .env.example .env
```

Then edit `.env` — see §6 below for every variable.

---

## 6. Environment Variables

All variables are documented with defaults in `.env.example`. Required
(no default, the app refuses to start without these):

| Variable | Purpose |
|---|---|
| `GTMETRIX_API_KEY` | Your GTmetrix API key |
| `GOOGLE_SHEET_ID` | The target Google Sheet's ID |

Everything else — GTmetrix location/browser/retry tuning, email/SMTP,
alert thresholds, RCA thresholds, journey timeouts, scheduler tolerance —
is optional with sensible defaults. Structured settings (which pages
exist, their priority/interval, journey selectors, the alert-type
severity registry) live directly in `config.py`, not `.env`, since
they're not simple scalars.

---

## 7. Configuration

**Pages** — `config.PAGES`, one `Page(...)` entry per monitored URL:

```python
Page("Homepage", "https://www.goodmonk.in/", "Homepage",
     priority="critical", interval_hours=1,
     journey_enabled=False, root_cause_enabled=True,
     gtmetrix_enabled=True, alert_enabled=True, enabled=True)
```

Add a page by adding one entry. Change its monitoring frequency by
changing only `interval_hours` — nothing else in the project needs to
change.

**Journey selectors** — `config.JOURNEY_SELECTORS`, a layered list per
interactive element (data-testid → aria-label → role → text → CSS).

**Alert severities** — `config.ALERT_SEVERITY_MAP`, alert type → default
severity; any alert type not listed defaults to `"warning"`.

**Thresholds** — RCA thresholds (`RCA_*`), alert thresholds (`ALERT_*`),
GTmetrix retry/timeout settings, all `.env`-overridable scalars near the
top of `config.py`.

---

## 8. Running Locally

```bash
python main.py                # normal run — resumes an interrupted run, respects the scheduler
python main.py --no-resume    # force a fresh run of every due page
python main.py --workers 8    # override GTmetrix concurrency for this run
python main.py --skip-email   # test without sending an email
```

Preview the dashboard:
```bash
cd dashboard && python -m http.server 8000
# open http://localhost:8000
```

---

## 9. Running with GitHub Actions

`.github/workflows/monitor.yml` runs once every hour and can also be
triggered manually (**Actions → GoodMonk Performance Monitor → Run
workflow**, with an optional "force full clean run" checkbox).

**One-time setup after first push:**
1. **Settings → Actions → General → Workflow permissions** → "Read and
   write permissions" (needed for the workflow's `git push` step).
2. **Settings → Pages → Build and deployment → Source** → "GitHub
   Actions".
3. **Settings → Secrets and variables → Actions**, add: `GTMETRIX_API_KEY`,
   `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the whole
   service-account key file's contents), and optionally `SMTP_HOST`,
   `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`.

Each run: tests due pages → writes Sheets history → runs due journeys →
evaluates alerts → rebuilds the dashboard → commits the 8 dashboard JSON
files and 3 state files → deploys to Pages → sends the summary email.

Overlap protection is built in (`concurrency: group: "pages",
cancel-in-progress: false`) — if an hourly trigger fires while a
previous run is still going, it queues rather than killing it.

---

## 10. Dashboard

Static, no backend — `dashboard/index.html` fetches 8 JSON files from
`dashboard/data/` and re-fetches every 30 seconds. Sections: vitals bar,
KPI strip, per-page vitals grid, Root Cause cards, Customer Journey
cards (with a success-rate timeline), Alerts (active/recent, severity
color-coded), Monitoring Scheduler (per-page frequency, next run,
failure streaks), performance trend charts (daily/weekly/monthly), and a
searchable/exportable history table. Dark mode by default. Every section
degrades gracefully (empty-state message, never a crash) if its JSON
file doesn't exist yet.

---

## 11. Google Sheets

One spreadsheet, multiple tabs, all auto-created on first write:

| Tab pattern | Written by | Contents |
|---|---|---|
| `<PageName>` (one per page) | `scheduler.py` | GTmetrix history — Date, Time, Score, Grade, Core Web Vitals, Report URL, Status |
| `<PageName>_RootCause` (one per page) | `root_cause.py` | RCA issue count, top issues, categories, full structured JSON |
| `CustomerJourney` (shared) | `journey.py` | One row per product per journey run — status, failed step, errors, screenshot flag |
| `AlertHistory` (shared) | `alerts.py` | One row per "new" or "recovered" alert event — never per suppressed duplicate |
| `SchedulerRuns` (shared) | `page_scheduler.py` (via `main.py`) | One row per workflow run — pages checked/skipped, reasons, duration |

Every tab is created and header-managed automatically. No existing tab's
structure is ever modified by a later feature.

---

## 12. Alerts

21 supported alert types (Website Down, Homepage/Journey/Checkout
Failed, Performance Score/Grade Drop, High LCP/CLS/TTFB, Large Images,
Heavy JS/CSS, High DOM Size, Slow Server Response, and 7 operational
types covering GTmetrix/Playwright/Sheets/Dashboard/Workflow failures
and unexpected exceptions), each with a default severity
(info/warning/high/critical) in `config.ALERT_SEVERITY_MAP`.
**Deduplication:** a failing check fires exactly one email, stays silent
on every subsequent run while still broken, fires exactly one recovery
email when it resolves. State lives in `data/alert_state.json`.

---

## 13. Scheduler

`config.PAGES` — 6 pages at `interval_hours=1` (critical), 10 at
`interval_hours=2` (normal), by default. The workflow runs hourly
regardless; `page_scheduler.py` decides per-run which pages are actually
due. Change a page's cadence by changing only its `interval_hours`.
State lives in `data/page_schedule_state.json` — last run, next run,
last status, consecutive failure count, per page.

---

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Workflow fails at "Install dependencies" | Missing/expired secret | Check repo Secrets are all set |
| `git push` step fails with 403 | Workflow permissions not set | Settings → Actions → Workflow permissions → Read and write |
| Dashboard shows "Couldn't load dashboard data" | First run hasn't completed yet | Normal before the first successful workflow run |
| Journey steps fail immediately at `add_to_cart`/`checkout` | Selectors don't match your live theme | Tune `config.JOURNEY_SELECTORS` — see §16, this is a known open item |
| No emails arriving | SMTP not configured | `EMAIL_ENABLED` requires all of `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD`/`EMAIL_TO` set |
| Same alert emailing every run | Check `data/alert_state.json` is actually being committed by CI (not accidentally gitignored) | See `.gitignore`'s documented exceptions |
| GTmetrix tests failing with 429 | Rate limit / plan concurrency exceeded | Lower `MAX_WORKERS`, check monthly quota (see `FINAL-PERFORMANCE-REPORT.md`) |

---

## 15. Deployment Guide

See `DEPLOYMENT-GUIDE.md` for the full step-by-step first-deploy walkthrough.

---

## 16. Future Improvements

Honestly incomplete or worth revisiting — see `FINAL-PRODUCTION-READINESS-REPORT.md`
for the full, undiluted list. Headlines:
- **Journey selectors are unverified against the real GoodMonk theme** —
  sensible generic defaults, never tested against a live browser in this
  environment. This is the single most important thing to validate
  before trusting Journey/Checkout alerts.
- **GTmetrix quota at hourly cadence is unconfirmed** — ~7,900
  tests/month estimated; verify against your actual plan.
- **Score/grade-drop alerts need real historical data** — will resolve
  itself after a few real hourly runs accumulate history.
- **No automated CI test suite** — all testing in this project was done
  manually with mocks during development; there's no `pytest`/GitHub
  Actions test job that runs on every commit.
- `reports/` directory is provisioned in `config.py` but nothing writes
  to it — leftover from original scaffolding, never implemented.
