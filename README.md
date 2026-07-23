# GoodMonk Performance Command Center

A production-ready website speed monitoring system for GoodMonk — GTmetrix
tests, Google Sheets history, and a live static dashboard — running entirely
on Python + GitHub Actions, with no execution-time ceiling and no Apps
Script continuation-trigger complexity.

This replaces the previous Google Apps Script implementation. Same
functionality (same pages, same metrics, same append-only Sheets history),
rebuilt for real concurrency and unlimited runtime.

---

## Architecture

```
config.py          Central config: pages list, thresholds, env loading
gtmetrix.py         GTmetrix API v2 client (start/poll/report/extract), retries
google_sheet.py      Append-only Google Sheets writer/reader (gspread)
scheduler.py         Concurrent batch runner + local resume state
dashboard_data.py    Builds dashboard/data/*.json from fresh Sheets history
email_report.py      HTML summary email after each run
logger.py             Structured logging (execution/error/api/daily logs)
utils.py               Retry decorator, JSON helpers, time helpers
main.py                 Entry point — wires everything together

dashboard/            Static site (GitHub Pages) — HTML/CSS/JS + Chart.js
  index.html
  css/style.css
  js/app.js
  data/*.json          Generated output the dashboard reads (committed by CI)

.github/workflows/
  monitor.yml           Daily 9:00 AM IST run + manual dispatch + Pages deploy
```

**Why this fixes the Apps Script problems:**
- No 6-minute execution ceiling — GitHub Actions jobs get up to 6 hours.
- Real concurrency (`ThreadPoolExecutor`, configurable `MAX_WORKERS`) instead
  of one page at a time.
- No continuation-trigger bookkeeping, no `LockService`, no self-referencing
  trigger IDs — a single Python process runs the whole batch.
- If a run *is* interrupted (killed job, network outage), `scheduler.py`
  keeps a local `data/run_state.json` of which pages already succeeded, so a
  re-run doesn't reprocess (and re-spend GTmetrix credits on) pages that
  already completed.

---

## 0. Getting this onto GitHub

**Option A — command line (recommended):**

```bash
cd goodmonk-performance
git init
git add .
git commit -m "Initial commit: GoodMonk Performance Command Center"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

Create the empty repo on GitHub first (github.com → New repository — don't
initialize it with a README, or `git push` will conflict with it).

**Option B — web upload:** on the new repo's page, `Add file → Upload
files`, then drag the whole `goodmonk-performance` folder in. GitHub
preserves the subfolder structure (`.github/workflows/`, `dashboard/`,
etc.) from a folder drag-and-drop.

**Two settings you must change after the first push** (both one-time,
under the repo's **Settings** tab):

1. **Settings → Actions → General → Workflow permissions** → select
   **"Read and write permissions"**. Without this, the workflow's `git
   push` step (which commits updated dashboard JSON after every run) will
   fail with a 403 — this is the single most common setup mistake.
2. **Settings → Pages → Build and deployment → Source** → select
   **"GitHub Actions"** (not "Deploy from a branch"). `monitor.yml` deploys
   directly; no `gh-pages` branch is needed.

Then add your secrets (§3a below) and either wait for the daily 9:00 AM
IST run or trigger one manually: **Actions tab → GoodMonk Performance
Monitor → Run workflow**.

**What NOT to upload:** `.env` and `service-account.json`, if you created
either locally — both are already in `.gitignore`, so a normal `git add .`
won't pick them up. If you're using the web-upload option, just don't drag
those two files in.

---

## 1. Prerequisites

- A GTmetrix account with an API key (Settings → API).
- A Google Cloud project with the **Google Sheets API** enabled, and a
  **service account** with a JSON key.
- The target Google Sheet shared with that service account's email
  (`...@...iam.gserviceaccount.com`) as an **Editor**.
- A GitHub repository (public repo needed for free GitHub Pages, or any repo
  on a plan that supports Pages for private repos).

---

## 2. Local setup

```bash
git clone <your-repo-url>
cd goodmonk-performance
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- `GTMETRIX_API_KEY` — your GTmetrix key.
- `GOOGLE_SHEET_ID` — the long ID in your Sheet's URL
  (`https://docs.google.com/spreadsheets/d/THIS_PART/edit`).
- `GOOGLE_SERVICE_ACCOUNT_FILE` — path to your downloaded service-account
  JSON key file (keep it out of git — already in `.gitignore`).
- Email fields are optional — leave blank to disable report emails.

Run a batch locally:

```bash
python main.py                # normal run (resumes an interrupted run if any)
python main.py --no-resume    # force a full clean run of every page
python main.py --workers 8    # more concurrency
```

Preview the dashboard locally (any static file server works):

```bash
cd dashboard
python -m http.server 8000
# open http://localhost:8000
```

---

## 3. GitHub Actions setup (production)

### 3a. Add repository secrets
`Settings → Secrets and variables → Actions → New repository secret`:

| Secret | Value |
|---|---|
| `GTMETRIX_API_KEY` | your GTmetrix API key |
| `GOOGLE_SHEET_ID` | your Sheet ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **entire contents** of your service-account JSON key file (paste as-is) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` | optional, for report emails |

In CI, `GOOGLE_SERVICE_ACCOUNT_JSON` is used instead of a file path — no key
file ever touches the repo.

### 3b. Enable GitHub Pages via Actions
`Settings → Pages → Build and deployment → Source: GitHub Actions`.
No `gh-pages` branch needed — `monitor.yml` deploys directly.

### 3c. That's it
`.github/workflows/monitor.yml` runs daily at 9:00 AM IST, and can also be
triggered manually from the **Actions** tab (`Run workflow`, with an
optional "force full clean run" checkbox). Each run:
1. Tests every configured page concurrently.
2. Appends results to Google Sheets (never overwrites history).
3. Rebuilds `dashboard/data/*.json` and commits it back to the repo.
4. Sends the summary email (if configured).
5. Deploys the updated dashboard to GitHub Pages.

---

## 4. Adding / removing pages

Edit the `PAGES` list in `config.py` — one line per page:

```python
Page("New Product", "https://www.goodmonk.in/products/new-product", "New_Product"),
```

Nothing else needs to change. This scales the same way whether you have 16
URLs or 500 — concurrency is controlled by `MAX_WORKERS`, and GitHub Actions
has no per-page limit like Apps Script's trigger quota.

---

## 5. Dashboard features

- **Live status bar** with an animated pulse/heartbeat line — its color and
  speed reflect overall site health (green = healthy, amber = warning, red
  = critical pages present).
- **KPI strip**: total URLs, average score, best/worst page, healthy /
  warning / critical counts.
- **Page vitals grid**: one card per page, latest score/grade/LCP, traffic-
  light color coding.
- **Trend charts** (Chart.js): performance score, LCP, and load-time trends,
  toggleable between daily / weekly / monthly aggregation.
- **History table**: full run history, searchable by page name, filterable
  by status, exportable to CSV or Excel.
- **Dark mode** (default) with a light-mode toggle, persisted per browser.
- **Auto-refresh** every 30 seconds — no page reload, just re-fetches the
  JSON files (which only actually change once a day, after a workflow run,
  but this also picks up a manual re-run immediately).
- Fully responsive down to mobile.

---

## 6. Logging

- `logs/execution.log` — everything, rotating (5 files × 5MB).
- `logs/error.log` — warnings and errors only.
- `logs/api.log` — raw GTmetrix API request/response activity.
- `logs/YYYY-MM-DD.log` — one file per day for quick review.
- All of the above also print to stdout, so the GitHub Actions run log shows
  live progress under **Actions → (run) → Run GTmetrix monitoring batch**.

---

## 7. Error handling

- Every GTmetrix API call retries (`API_MAX_RETRIES`, default 3) with
  backoff; a 429 gets a longer, dedicated cooldown
  (`RATE_LIMIT_WAIT_SECONDS`) instead of a quick retry.
- One page failing (timeout, bad response, unreachable site) never stops
  the batch — it's recorded as a `Failed` row in that page's own sheet and
  the run continues with everything else.
- A Google Sheets API hiccup while recording one page's result is isolated
  and logged; it doesn't take down the rest of the batch.
- If the whole job gets killed mid-run (network outage, GitHub Actions
  incident), `data/run_state.json` remembers which pages already
  succeeded, so the next run resumes instead of re-testing everything.

---

## 8. Future modules

The project is intentionally modular so new checks can be added as their
own module without touching existing ones — each would follow the same
shape as `gtmetrix.py` (a client + `run_single_page`-style function),
plumbed into `scheduler.py` and given its own dashboard card:

- Google PageSpeed Insights
- Lighthouse CI
- Core Web Vitals (field data, via CrUX API)
- Broken link checker
- SEO monitoring
- Uptime monitoring
- SSL certificate expiry monitoring
- Image optimization audit
- JavaScript error monitoring
- Competitor website comparison

---

## 9. Security notes

- No secret ever lives in code — everything comes from `.env` locally or
  repository Secrets in CI.
- `.env` and `service-account.json` are git-ignored by default.
- If you're migrating from the old Apps Script version, **rotate your
  GTmetrix API key** before going live here if it was ever shared, pasted
  into a chat, or committed anywhere — treat any previously-exposed key as
  compromised.
