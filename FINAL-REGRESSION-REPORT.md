# Final Regression Report — v1.0

## Method

Diffed every file touched in this hardening pass against the Feature 4
baseline, and re-ran cross-feature integration tests exercising GTmetrix,
Root Cause Analysis, Customer Journey, Alert Engine, Scheduler, and
Dashboard generation together in one sequence.

## Files touched in this pass, and exactly what changed

| File | Change | Risk |
|---|---|---|
| `alerts.py` | Removed 1 unused import (`from gtmetrix import PageResult` inside `_cli()`) | None — dead code removal only |
| `dashboard_data.py` | Removed 1 unused variable (`active = ...`, immediately superseded by `latest_by_key`/`currently_active` two lines later) | None — the variable was never read |
| `journey.py` | Removed 1 unused module-level import (`datetime`) and 1 unused local import (`now_iso` in `to_sheet_row`, which never used it) | None — both confirmed unused by static analysis and by the fact `to_sheet_row` already imports what it needs from `now_date_str`/`now_time_str` |
| `main.py` | Hoisted 2 duplicate inline `from utils import read_json` to 1 top-level import | None — `read_json` behavior is identical either way |
| `scheduler.py` | Added a `_safe_alert_call()` helper; refactored 2 call sites to use it | **Genuine risk, found and fixed** — see the "bug introduced and caught" note in `FINAL-VERIFICATION-REPORT.md`. Final version verified correct in both alerts-available and alerts-unavailable configurations |
| `requirements.txt` | Added upper-bound version pins to all 5 dependencies | Low — pins only prevent future major-version jumps; doesn't change what's currently installed |
| `README.md` | Completely rewritten | N/A — documentation only, no code behavior affected |

**Every other file in the project (config.py, gtmetrix.py, root_cause.py,
journey_models.py, playwright_runner.py, alert_models.py, alert_rules.py,
notification_manager.py, page_scheduler.py, google_sheet.py,
email_report.py, logger.py, utils.py, the workflow YAML) is byte-for-byte
identical to the Feature 4 delivery.**

## Cross-feature integration regression test

Ran, in sequence, against the hardened codebase:
1. `root_cause.analyze()` against a synthetic multi-issue page → correctly detected 8 issues.
2. `journey.run_journey()` against a mocked browser with no matching Add to Cart selector → correctly identified as a permanent (non-retried) failure at the `add_to_cart` step.
3. `notification_manager.process_batch()` through a new → suppressed → recovered sequence → all three transitions correct.
4. `page_scheduler.get_due_pages()` against a 1-hour-elapsed state → correctly identified only the 1-hour-interval page as due.
5. All 5 `dashboard_data.build_*()` functions run in sequence → `summary.json`'s content was captured before running the other 4, then re-checked after — **byte-for-byte identical.**

All 5 passed. **No regression found in any module.**

## Static verification

- `pyflakes *.py` → 0 warnings (down from 4 before this pass — see Verification Report).
- `python -m py_compile` on all 17 files → 0 errors.
- Dashboard JS (`node --check`) → valid.
- Workflow YAML → valid, re-parsed with PyYAML.
- All 8 `dashboard/data/*.json` files → valid JSON, and confirmed to hold real historical data (2026-08-17 GTmetrix run) or honest empty states — not synthetic test fixtures. (See the repeated-pattern note in Feature 4's report — checked again for this final delivery, confirmed clean this time as well.)

## Conclusion

Zero regressions from the hardening pass itself. One genuine bug was
introduced *during* the pass by a refactor and caught by testing before
it ever reached a delivered state — see the Verification Report for the
full account. This is disclosed rather than omitted because "we tested
and found nothing" is a materially different (and less trustworthy)
claim than "we tested, found something, and fixed it before shipping."
