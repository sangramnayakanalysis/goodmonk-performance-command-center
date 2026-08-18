# Deployment Checklist — v1.0.1

## Pre-deployment verification (all completed for this release)

- [x] C1 fix applied and verified against a real corrupted state file
- [x] C2 fix applied and verified against a real git rebase conflict
- [x] Full cross-feature regression suite passed (GTmetrix, RCA, Journey,
      Alert Engine, Scheduler, Dashboard, Email, Resume Logic)
- [x] `diff -rq` against v1.0 confirms only 2 files changed
- [x] All 17 Python files compile (`py_compile`)
- [x] Zero `pyflakes` warnings
- [x] `dashboard/js/app.js` syntax valid
- [x] `monitor.yml` YAML syntax valid
- [x] All 8 `dashboard/data/*.json` files valid JSON, confirmed real data
      (not synthetic test fixtures)
- [x] All 3 `data/*.json` state files valid JSON, clean pre-deployment
      state
- [x] No debug code, no `print()` statements, no bare `except:`, no
      `TODO`/`FIXME` markers
- [x] No stray temp files, no `__pycache__`, no leftover test artifacts

## What you (the deployer) still need to do — not verifiable from this environment

These are the same items flagged as untested/unverified in the
Independent Final Audit and were **not** resolved by this Critical-only
fix pass:

- [ ] **Confirm your actual GTmetrix plan quota** supports the estimated
      ~7,900 tests/month at the current hourly/2-hourly priority tiering
      before trusting this to run unattended.
- [ ] **Run this against real infrastructure at least once** before
      trusting it fully: a real GitHub Actions execution, a real GTmetrix
      call, a real Google Sheets write, a real Playwright run against
      your actual site.
- [ ] **Check the `CustomerJourney` Sheets tab after the first real run**
      — if journey steps fail at `add_to_cart` or `checkout`, tune
      `config.JOURNEY_SELECTORS` against your real theme before trusting
      journey/checkout alerts.
- [ ] **Verify GitHub Actions repo settings** are correctly configured:
      Workflow permissions → "Read and write," Pages source → "GitHub
      Actions," and all 8 secrets present (see `DEPLOYMENT-GUIDE.md` from
      the v1.0 delivery — unchanged for this release).
- [ ] **Deliberately trigger the "Notify on workflow failure" path once**
      (e.g., by temporarily removing a required secret) to confirm the
      C2 fix's failure-notification handoff actually fires correctly in
      your real GitHub Actions environment, not just in this sandbox's
      simulated git repository.

## Standing risks not addressed in this release (tracked, not blocking)

From the Independent Final Audit, not fixed in this Critical-only pass:
resume logic doesn't survive a hard-killed runner (H1); unbounded Google
Sheets/`history.json` growth, worsening at hourly cadence (H2, H3); and
several Medium/Low code-quality and testing-coverage items. None of
these block this release per your explicit scope instruction, but none
of them should be considered resolved either.

## Rollback plan

Unchanged from v1.0 — every commit is a normal git commit, including the
automated state-file commits. Revert to the prior commit and re-push if
needed; the C1 fix means a bad/corrupted state file after a rollback
would degrade to "test everything" rather than crash, which is itself a
safety improvement for the rollback path too.
