# Deployment Guide — v1.0

Step-by-step first deployment to GitHub.

## 1. Prerequisites

- A GitHub repository (public or private) with this code pushed to `main`.
- A GTmetrix account with API access — get your API key from
  GTmetrix → Account → API.
- A Google Cloud service account with Sheets API access, and a target
  Google Sheet shared with that service account's email as Editor.
- (Optional but recommended) an SMTP account for email alerts — any
  provider works (Gmail app password, SendGrid, etc.).

## 2. Repository secrets

**Settings → Secrets and variables → Actions → New repository secret.**
Add:

| Secret | Required? | Value |
|---|---|---|
| `GTMETRIX_API_KEY` | Yes | Your GTmetrix API key |
| `GOOGLE_SHEET_ID` | Yes | The ID from your Sheet's URL (`.../d/<THIS_PART>/edit`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Yes | The **entire contents** of your service account's JSON key file, pasted as-is |
| `SMTP_HOST` | Only for email | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | Only for email | e.g. `587` |
| `SMTP_USER` | Only for email | Your SMTP username |
| `SMTP_PASSWORD` | Only for email | Your SMTP password / app password |
| `EMAIL_FROM` | Only for email | Sender address |
| `EMAIL_TO` | Only for email | Recipient address(es), comma-separated |

If you skip the SMTP secrets, the system runs fully — it just won't send
emails (`email_report.py` detects missing config and skips cleanly).

## 3. Workflow permissions

**Settings → Actions → General → Workflow permissions** → select
**"Read and write permissions."** This is required — the workflow
commits updated dashboard data and state files back to the repo after
every run.

## 4. GitHub Pages

**Settings → Pages → Build and deployment → Source** → select
**"GitHub Actions."** No branch selection needed; `monitor.yml` handles
deployment directly.

## 5. First run

Go to **Actions → GoodMonk Performance Monitor → Run workflow** and
trigger it manually (don't wait for the hourly cron). Watch the run —
it should:
1. Install dependencies + Playwright's Chromium.
2. Test every page (first run: nothing has a `last_successful_run` yet,
   so the scheduler considers every page due, regardless of tier).
3. Write to Google Sheets (check your sheet — it should now have a tab
   per page, plus `CustomerJourney`, `AlertHistory`, `SchedulerRuns`).
4. Commit `dashboard/data/*.json` and the 3 `data/*.json` state files.
5. Deploy to Pages.
6. Send a summary email, if SMTP is configured.

Your dashboard will be live at
`https://<your-username>.github.io/<repo-name>/`.

## 6. Verify before trusting hourly cadence

Before letting this run unattended for real:
- **Confirm your GTmetrix plan supports ~7,900 tests/month** (see
  `FINAL-PERFORMANCE-REPORT.md`) — if not, adjust `interval_hours` on
  some pages in `config.py` before the next scheduled trigger.
- **Check the `CustomerJourney` Sheets tab after the first run** — if
  journeys are failing at `add_to_cart` or `checkout`, this is very
  likely a selector mismatch (see `config.JOURNEY_SELECTORS`), not a
  real site problem. Tune the selectors against your actual theme.
- **Let it run for 2+ real cycles** before trusting score/grade-drop
  alerts — they need two real historical data points per page.

## 7. Ongoing operation

Nothing further to do — the hourly cron handles everything. To force a
full clean run (ignore resume state), use **Actions → Run workflow**
with the "Force a full clean run" checkbox.

## Rollback

Every commit to `main` (including the automated dashboard-data commits)
is a normal git commit — to roll back, revert to a prior commit and
re-push. The 3 state files (`data/*.json`) will roll back with it,
which is safe (the scheduler/alert engine will simply recompute
correctly from whatever state they find).
