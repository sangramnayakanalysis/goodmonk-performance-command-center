# Final Performance Report — v1.0

Consolidates every performance finding across all 4 features plus a
fresh look at the system as a whole, now that every module is in place
simultaneously.

## API usage — the number that actually matters for production cost

**GTmetrix:** at the current priority tiering (6 pages @ 1hr, 10 pages @
2hr), the hourly workflow performs roughly **264 GTmetrix tests/day
(~7,900/month)** — a ~16x increase over the pre-Feature-4 daily cadence
(16/day). **This has not changed since Feature 4's report and remains
unconfirmed against your actual GTmetrix plan.** This is the single most
important number to verify before this system runs unattended in
production — if your plan's monthly quota is below ~8,000 tests, either
the interval tiers need adjusting or a subset of pages need to move to a
longer interval.

**Google Sheets:** `dashboard_data.build_all()` does a full-history
re-read (`get_all_records()`) per page tab, every run — up to 24
times/day now instead of once. At current scale (16 page tabs, a few
hundred rows each) this is well within Google's per-minute quotas, but
the read cost grows linearly with history length and will eventually
matter if this runs for a year+ without any pagination/incremental-read
optimization. Not urgent, but flagged consistently since Feature 1's
audit — this has never been addressed.

**Playwright:** one browser process launched per run, reused across all
journey-enabled products in that run (correct, efficient design) — but
since journey-enabled products are all critical-tier (1hr interval), the
Chromium install step and browser launch now happen up to 24x/day
instead of once. Real wall-clock cost is unmeasured in this sandbox
(can't launch a real browser here); the design itself (shared browser,
fresh context per product) is sound and was verified correct by
orchestration testing, but actual timing needs to be observed from a
real run.

## Concurrency

`ThreadPoolExecutor(max_workers=config.MAX_WORKERS)` (default 4) for the
GTmetrix batch — unchanged since the original script, appropriate for an
I/O-bound workload, correctly bounded so it won't accidentally exceed a
GTmetrix plan's concurrent-test limit if `MAX_WORKERS` is set
appropriately for your plan.

## Dashboard generation cost

5 `build_*` functions now run every hour instead of once daily — each is
independently cheap (a handful of Sheets reads + JSON writes), and the
byte-identical-output regression tests confirm they don't redundantly
recompute or duplicate work across each other. No concerning growth
pattern found here.

## Memory/CPU

Nothing in this codebase holds unbounded in-memory state — `history.json`
is explicitly capped client-side (500 rows) in the dashboard, and every
Sheets read function accepts a `limit` parameter. No memory-growth risk
identified across any module.

## What changed in this hardening pass, performance-wise

Nothing performance-relevant — this pass was code-quality cleanup
(unused imports, one refactor) and documentation, not optimization. The
performance profile of v1.0 is identical to Feature 4's delivery.

## Bottom line

The architecture is efficient for what it does; the actual cost driver
is the *cadence decision* (hourly + priority tiering), not implementation
inefficiency. The only unresolved, concrete action item is: **confirm
GTmetrix plan quota supports ~7,900 tests/month before this runs
unattended.** Everything else here is either already fine or a
longer-term scaling concern worth revisiting well before it becomes
urgent, not before this v1.0 ships.
