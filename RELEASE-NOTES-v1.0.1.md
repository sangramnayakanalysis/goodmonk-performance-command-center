# Release Notes — v1.0.1

**Critical bug-fix release**, following the Independent Final Audit of
v1.0. No new features, no refactoring, no architecture changes — this
release exists solely to close 2 Critical production-blocking findings.

## Fixed

- **C1 — Corrupted scheduler state could crash the entire monitoring
  run.** `main.py`'s call to `page_scheduler.get_due_pages()` is now
  isolated: a corrupted or unreadable `data/page_schedule_state.json`
  is logged, raises an operational alert (if the alert engine is
  available), and falls back to running every configured page — instead
  of crashing the process before anything runs. Verified against a real
  corrupted file on disk, not a simulated exception. See
  `C1-FIX-SUMMARY.md`.

- **C2 — A git rebase conflict during the automated commit step could
  silently push an unknown repository state.** The workflow's
  `git pull --rebase origin main || true` (which swallowed any conflict
  and pushed regardless) is replaced with explicit handling: a failed
  rebase is now aborted and the step fails loudly, which the existing
  workflow-failure notification already catches. A successful rebase
  behaves exactly as before. Verified against a real git conflict in a
  throwaway repository, in both the conflict and no-conflict cases. See
  `C2-FIX-SUMMARY.md`.

## Not changed

Everything else. Confirmed by direct file diff against v1.0: exactly 2
files were modified (`main.py`, `.github/workflows/monitor.yml`). Every
other module — GTmetrix, Root Cause Analysis, Customer Journey, Alert
Engine, Scheduler internals, Dashboard, Google Sheets, Email — is
byte-for-byte identical to v1.0.

## Still open (not addressed in this release, by design)

This was a Critical-only fix pass. The following findings from the
Independent Final Audit remain open and are tracked for a future
release, not resolved here:

- **H1** — resume logic doesn't survive a hard-killed or cancelled
  GitHub Actions runner, only a graceful Python exception.
- **H2 / H3** — unbounded Google Sheets history re-reads and unbounded
  `history.json` growth, both worsening specifically because of the
  hourly cadence introduced in Feature 4.
- 4 Medium and 5 Low findings — see `INDEPENDENT-FINAL-AUDIT.md` (from
  the prior review) for the full list.
- The Independent Final Audit's core caveat also still stands
  unchanged: this system has never been run against real GTmetrix,
  real Google Sheets, real SMTP, real Playwright, or a real GitHub
  Actions execution. This release did not change that — the two fixes
  in it were the first things in this project's history verified against
  real infrastructure-adjacent behavior (a real corrupted file, a real
  git conflict), but that is a narrow, specific verification, not a
  general one.

## Upgrade notes

No configuration changes, no new environment variables, no new
dependencies, no schema changes to any Sheets tab or state file. Drop-in
replacement for v1.0 — pull and redeploy.
