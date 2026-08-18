# Feature 4 — Intelligent Monitoring Scheduler — Complete Report

Covers all 6 requested deliverables: updated source code summary, scheduler architecture diagram, verification report, regression report, performance report, and production readiness report.

---

## 1. Updated Source Code — What Changed

### New file (1)
- `page_scheduler.py` — the due/skip decision engine. `is_due()` compares a page's `interval_hours` against elapsed time since `last_successful_run`; `get_due_pages()` splits any page list into (due, skipped-with-reason); `record_run()` persists per-page state to `data/page_schedule_state.json`; `get_scheduler_summary()` feeds the dashboard.

### Modified files (8)

| File | What changed | What did NOT change |
|---|---|---|
| `config.py` | `Page` dataclass gained 7 new fields, all with defaults (`priority`, `interval_hours`, `enabled`, `gtmetrix_enabled`, `journey_enabled`, `root_cause_enabled`, `alert_enabled`); the 16 `PAGES` entries gained explicit `priority=`/`interval_hours=` kwargs matching your exact spec; `JOURNEY_PRODUCTS` now derives from `page.journey_enabled` instead of a separate hardcoded set (verified identical resulting set); added `PAGE_BY_SHEET_NAME`, `SCHEDULER_*` config, `SCHEDULER_HEADERS` | Every page's `name`/`url`/`sheet_name` value — untouched. Every Feature 1-3 config constant — untouched |
| `scheduler.py` | `run_batch()` gained one new optional parameter `pages` (defaults to `config.PAGES` — old behavior exactly); `_record_result()`'s RCA block now checks the page's `root_cause_enabled` flag (defaults `True` for every existing page, so a no-op unless explicitly configured otherwise) | The write logic, retry/isolation behavior, resume mechanism itself — untouched |
| `journey.py` | `run_all_journeys()` gained one new optional parameter `products` (defaults to `config.JOURNEY_PRODUCTS` — old behavior exactly) | Every step handler, the retry logic, screenshot behavior — untouched |
| `google_sheet.py` | Appended `append_scheduler_run()`, reusing the existing `_get_or_create_sheet_with_headers` helper unchanged | Every existing function — untouched |
| `dashboard_data.py` | Appended `build_scheduler_summary()` | `build_all()`, `build_root_cause_summary()`, `build_journey_summary()`, `build_alerts_summary()` — untouched |
| `email_report.py` | Appended `_build_scheduler_html()`; `send_report()` gained one more optional parameter (`scheduler_meta`) — 5th generation of the same growing-signature pattern used in every prior feature | Every existing `_build_*_html` function — untouched |
| `main.py` | Computes due/skipped pages before the batch; passes `pages=due_pages` into `scheduler.run_batch()`; filters journey products to the due∩journey-enabled intersection; records combined GTmetrix+Journey outcome per page; writes scheduler summary to Sheets/dashboard/email; **`next_run` calculation changed from "tomorrow 9 AM" to "top of next hour"** — an intentional, disclosed change required by Feature 4's own stated goal of hourly execution, not incidental | `scheduler.run_batch`'s core call, dashboard-build calls, RCA/Journey/Alert isolation boundaries, exit-code logic — structurally untouched (each just gained new isolated blocks around it) |
| `.github/workflows/monitor.yml` | Cron changed from daily (`30 3 * * *`) to hourly (`0 * * * *`) — the actual cadence change this feature requires; `data/page_schedule_state.json` added to the committed-files list | Every existing step, the top-level `concurrency` block (already satisfied the overlap-prevention requirement — documented, not modified) |
| `.gitignore` | Documenting comment only | No functional change |

### Files touched zero times
`utils.py`, `logger.py`, `gtmetrix.py`, `root_cause.py`, `journey_models.py`, `playwright_runner.py`, `alert_models.py`, `alert_rules.py`, `notification_manager.py`, `alerts.py`, `requirements.txt`.

---

## 2. Scheduler Architecture Diagram

```
config.py
  PAGES: list[Page]  (each has interval_hours, priority, enabled, ...)
        │
        ▼
┌─────────────────────── page_scheduler.py ───────────────────────┐
│                                                                    │
│  get_due_pages(pages, now)                                        │
│    for each page:                                                 │
│      entry = state["pages"].get(page.sheet_name)                  │
│      is_due(page, entry, now) →                                   │
│        not enabled?           → SKIP "disabled"                   │
│        not gtmetrix_enabled?  → SKIP "gtmetrix disabled"          │
│        never run before?      → DUE  "never run before"           │
│        now - last_success ≥ interval_hours?  → DUE                │
│                                else            → SKIP "not due     │
│                                                    for Nh more"    │
│                                                                    │
│         reads/writes data/page_schedule_state.json                │
│         (last_run, next_run, last_status, failure_count,          │
│          last_successful_run, last_journey_run,                   │
│          last_gtmetrix_run — per page, NOT a history log)         │
└─────────────────────────┬──────────────────────────────────────-─┘
                           │  (due_pages, skipped_pages)
                           ▼
              main.py
                │
                ├─► scheduler.run_batch(pages=due_pages)        (Feature 1's engine, untouched)
                │        ↓ results: list[PageResult]
                │
                ├─► journey.run_all_journeys(                   (Feature 2's engine, untouched)
                │       products = due ∩ journey_enabled)
                │        ↓ journey_results
                │
                ├─► for page in due_pages:
                │       combined_ok = gtmetrix_ok AND (journey_ok if applicable)
                │       page_scheduler.record_run(page, combined_ok, ...)
                │
                ├─► google_sheet.append_scheduler_run(...)      (new "SchedulerRuns" tab)
                ├─► dashboard_data.build_scheduler_summary(...)  (new scheduler.json)
                │
                ├─► alert_rules / alerts.evaluate_domain(...)   (Feature 3's engine, untouched —
                │       universe is naturally just this run's    composes automatically: only
                │       due_pages, not all 16, because results    due pages are ever evaluated)
                │       only contains due pages)
                │
                └─► email_report.send_report(
                        ..., scheduler_meta={pages_checked, pages_skipped,
                                              skip_reasons, duration_seconds})

GitHub Actions (hourly cron, "0 * * * *")
  → checkout → install deps → install Playwright → run main.py
  → commit dashboard/data/*.json + data/*.json (all 3 state files)
  → deploy to Pages
  (top-level `concurrency: group: pages, cancel-in-progress: false`
   queues an overlapping trigger rather than killing/canceling it —
   already present since Feature 1, unchanged)
```

**Why Feature 3's Alert Engine needed zero changes:** its "universe-scoped" dedup design (built in Feature 3, before this scheduler existed) already assumed a run might only check a subset of pages — recovery is only inferred for keys *actually checked this run*. Feeding it a naturally smaller `results`/`journey_results` list (because `scheduler.run_batch`/`journey.run_all_journeys` were only given the due subset) is exactly the shape it was designed for. This is a genuine validation that Feature 3's architecture anticipated Feature 4 correctly, not a coincidence.

---

## 3. Verification Report

### `page_scheduler.py` — core due/skip logic

| Test | Result |
|---|---|
| **Spec's exact worked example**: Homepage last ran 08:00, 1hr interval, now 09:00 → DUE. Other page last ran 08:00, 2hr interval, now 09:00 → SKIP, correct reason text | ✅ Pass |
| Never-run page is always due | ✅ Pass |
| A page becomes due at exactly the interval boundary (2h page, exactly 2h elapsed) | ✅ Pass |
| A disabled page (`enabled=False`) is never due, with the correct skip reason, regardless of interval | ✅ Pass |
| `record_run`: failure count increments on consecutive failures, resets to 0 on the next success; `last_successful_run` is never overwritten by a failed run | ✅ Pass |
| **Scalability**: 30-minute and weekly (168h) intervals both work correctly with the exact same `is_due()` code — zero scheduler code changes, only `interval_hours` values | ✅ Pass |

### Priority-tier correctness

| Test | Result |
|---|---|
| Exactly the 6 specified pages (Homepage, Shop All, FNM, H50+, Fiber Fix, Plant Protein Roti) load with `interval_hours=1`; all 10 others load with `interval_hours=2` | ✅ Pass, verified programmatically against `config.PAGES` |
| 1 hour after a synchronized run: only critical pages are due | ✅ Pass |
| 2 hours after a synchronized run: all 16 pages are due | ✅ Pass |

### Integration with GTmetrix + Journey

| Test | Result |
|---|---|
| Journey products filtered to due ∩ journey-enabled — verified all 3 configured journey products (FNM, H50+, Fiber Fix) are correctly included when they're due, since they happen to all be critical-tier pages | ✅ Pass |
| Combined success logic: a page that **passes GTmetrix but fails its journey** is correctly recorded as `failed` overall in scheduler state (not silently marked healthy on the strength of the GTmetrix pass alone) | ✅ Pass — this was the highest-risk edge case and is explicitly verified, not just asserted |
| A page with no journey configured (e.g. Shop All) correctly uses its GTmetrix result alone | ✅ Pass |

### Resume-logic composition

| Test | Result |
|---|---|
| `scheduler.run_batch`'s existing `data/run_state.json` resume mechanism, now given a filtered `pages=due_pages` list, correctly resumes only within that subset — an already-completed page from an interrupted attempt at the same hourly trigger is correctly skipped, the rest are correctly retried | ✅ Pass |

### Dashboard/Sheets/Email integration

| Test | Result |
|---|---|
| `build_scheduler_summary()` run after the 4 existing `build_*` functions (mocked at the Sheets layer) → the 7 existing dashboard JSON files came out **byte-for-byte identical** | ✅ Pass |
| `scheduler.json` correctly reflects each page's priority/interval/last-run/next-run/failure-count | ✅ Pass |
| All 5 generations of `send_report()`'s signature (original → +RCA → +journey → +alerts → +scheduler) exercised with zero exceptions | ✅ Pass |
| `_build_scheduler_html()` correctly returns empty string for `None`/`{}`, non-empty for real metadata | ✅ Pass |
| Workflow YAML re-parses cleanly after the cron change | ✅ Pass |

---

## 4. Regression Report

**Method:** diffed every modified file against the Feature 3 baseline. Every diff hunk is either a pure addition, or one of the explicitly disclosed necessary extensions (growing function signatures with defaults, the `PAGES` entries gaining kwargs with unchanged core values, and the `next_run` cadence calculation — required by this feature's own stated goal).

**Full compile check:** all 17 project Python files compile cleanly.

**Dashboard JSON regression:** ran all 4 existing `build_*` functions (Features 1–3, mocked at the Sheets layer), captured all 7 pre-existing JSON files, then ran the new `build_scheduler_summary()` and re-captured them. **Result: byte-for-byte identical.**

**Email regression:** all 5 generations of `send_report()`'s signature verified working with zero exceptions.

**A repeated note on data hygiene:** as with Features 1–3, my own regression testing again produced synthetic fixture data in the generated dashboard JSON files (`scheduler.json` specifically this time). This was caught and corrected before packaging — every `dashboard/data/*.json` file in the delivered zip now correctly holds either your real 2026-08-17 GTmetrix data or an honest "no runs yet" empty state for every feature that has no real history. I want to be direct about this: this is now a **4-for-4 pattern** across every feature I've delivered, and while I've caught it every time before packaging, you should not assume I'll catch it a 5th time — I'd recommend spot-checking `dashboard/data/*.json` yourself after any future feature I deliver, and specifically checking that `summary.json`'s `average_score` still shows `62.4` (your real historical run) rather than a suspiciously round test number.

**Conclusion:** no regression in GTmetrix, Root Cause Analysis, Customer Journey, Alert Engine, Dashboard, Google Sheets, or Email functionality.

---

## 5. Performance Report

### GTmetrix API usage — the core optimization this feature was for

Before Feature 4 (daily cadence, all 16 pages every run): **16 tests/day**, ~480/month.

After Feature 4 (hourly cadence, priority-tiered):
- 6 critical pages × 24 runs/day = 144 tests/day
- 10 normal pages × 12 runs/day (every 2 hours) = 120 tests/day
- **Total: 264 tests/day, ~7,900/month.**

This is a **~16x increase** in GTmetrix API usage versus the daily-cadence baseline — inherent to the requirement itself (hourly critical monitoring is the whole point), not a scheduler inefficiency. The scheduler's actual optimization is relative to the naive alternative: testing all 16 pages every hour unconditionally would be 16 × 24 = 384 tests/day (~11,500/month) — the priority tiering saves **~31%** of that naive figure by only running normal-priority pages every other hour. **You should confirm your GTmetrix plan supports ~7,900 tests/month before relying on this cadence** — this was flagged as an open question back before Feature 1 started and remains the single most important number to verify before deploying Feature 4.

### Playwright/Journey cost

The 3 journey products (FNM, H50+, Fiber Fix) are all critical-tier, so journeys now run **every hour** (24×/day) instead of once daily — a real increase in CI runtime and Playwright browser-launch overhead per run. The `Install Playwright browser (Chromium)` workflow step (from Feature 2) now also runs hourly rather than daily, adding install time to every single trigger — worth watching your GitHub Actions minutes usage, though `pip`/apt caching should keep this reasonably fast after the first run.

### Google Sheets API usage

Each run now does a full-history re-read (`dashboard_data.build_all()`) regardless of how many pages were actually due — this was already flagged as a scaling consideration in the original audit and in Feature 1/2's reports, and Feature 4 makes it more prominent since it now happens up to 24×/day instead of once. At current scale (16 sheet tabs, growing by a few hundred rows/month) this remains well within Google's API quotas, but is worth revisiting if page count or history length grows significantly.

### Wall-clock duration per run

A typical hourly run now processes 6–16 pages (vs. always 16 before), so **most hourly runs should complete faster** than the old daily run did — this is the scheduler doing its job. The `scheduler_meta.duration_seconds` field (new, in `scheduler.json`/email) gives you real per-run timing data going forward; I don't have real timing data yet since this hasn't run against your live site.

---

## 6. Production Readiness Report

| Area | Status | Notes |
|---|---|---|
| Core scheduling logic | ✅ Ready | Extensively tested against the spec's exact example plus 8 additional scenarios |
| Backward compatibility | ✅ Ready | Verified via diff + regression tests; every existing feature's output unaffected |
| GTmetrix quota | ⚠️ **Needs your confirmation** | ~7,900 tests/month at this cadence — verify against your actual plan before relying on hourly critical monitoring |
| Playwright/CI cost | ⚠️ Worth monitoring | Journeys + browser install now run hourly instead of daily |
| Real-world selector accuracy | ⚠️ Still open from Feature 2 | Never resolved — the journey selectors are still generic defaults, now exercised 24×/day instead of once |
| Score/grade-drop alerts | ⚠️ Still needs real data | Flagged in Feature 3's report — needs ≥2 real historical points per page; will now accumulate much faster (hourly vs. daily), so this should resolve itself quickly once deployed |
| Data hygiene in delivered files | ✅ Fixed for this delivery | But see the repeated-pattern warning in §4 — recommend an independent spot-check |
| Sheets tab count | ℹ️ Informational | This feature adds a 4th shared tab (`SchedulerRuns`) on top of `CustomerJourney` and `AlertHistory` from Features 2–3, plus one `_RootCause` tab per page from Feature 1 — 16 pages × several tab types is a growing but still modest total sheet count |
| Workflow YAML | ✅ Ready | Re-validated, cron correctly hourly, overlap protection already in place and unmodified |

**Recommendation before Feature 5 (or before considering this "done"):** deploy this to a real environment and let it run for at least a few real hourly cycles before fully trusting it — specifically to (1) confirm actual GTmetrix quota consumption matches the estimate above, (2) see real `scheduler_meta.duration_seconds` numbers, and (3) confirm the combined GTmetrix+Journey success logic behaves as expected against real (not simulated) journey failures.
