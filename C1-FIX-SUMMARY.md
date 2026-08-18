# C1 Fix Summary — Scheduler State Corruption No Longer Crashes the System

## The problem (as identified in the Independent Final Audit)

`main.py` called `page_scheduler.get_due_pages()` with no exception
handling. If `data/page_schedule_state.json` was ever corrupted
(truncated write, bad manual edit, or — per the audit's C2 finding — a
swallowed git rebase conflict), the resulting `json.JSONDecodeError`
propagated all the way out of `main()` uncaught, crashing the entire
process before a single page was tested, before the dashboard rebuilt,
before any email sent.

## The fix

`main.py` only — the `get_due_pages()` call is now wrapped in a
try/except:

- **On success:** identical behavior to before. If the alert engine is
  available, a `mark_recovered("scheduler", "scheduler_state_failure")`
  call is made (a no-op unless this exact failure was previously firing).
- **On failure:** the error is logged, an operational alert is raised
  (`alerts.raise_operational("scheduler", "scheduler_state_failure", ...)`,
  guarded exactly like every other optional alert call already in this
  file), and `due_pages` falls back to `None` — which is the **existing,
  already-tested** signal meaning "scheduler unavailable, run every
  configured page," used identically elsewhere in this same function
  when the scheduler module fails to import at all. No new code path was
  invented for the fallback; an existing one was reused.

## What did NOT change

- `page_scheduler.py` itself — untouched.
- The normal (non-corrupted) scheduling behavior — untouched, verified
  by regression test.
- Every other module — untouched.

## Verification performed

Not simulated with a mock exception — **a real corrupted file was
written to disk** (`{ this is not valid JSON <<<<<<< HEAD`) at the
actual `data/page_schedule_state.json` path, and `main()` was run
against it directly:

- `main()` returned `0` — no crash, no traceback.
- `scheduler.run_batch()` was confirmed called with `pages=None` (the
  "test everything" fallback).
- `alerts.raise_operational("scheduler", "scheduler_state_failure", ...)`
  was confirmed called exactly once with the real exception message.
- A second test with a valid (but empty) state file confirmed normal
  scheduling behavior is completely unaffected — all 16 configured pages
  correctly identified as due (never run before), exactly as before this
  fix.

## Severity resolution

**Critical → Resolved.** A corrupted scheduler state file can no longer
take down the monitoring system. Monitoring degrades to "test every
configured page" and continues.
