# Feature 1 — Root Cause Analysis — Verification Report

## Files created (4 new)
- `root_cause.py` — the RCA engine (analysis rules, `RootCauseReport`/`RootCauseIssue` dataclasses, sheet-row/JSON/human-readable output)
- `.env.example` additions — 14 new optional `RCA_*` threshold variables, documented
- `README.md` — new §10 documenting the feature
- `FEATURE-1-VERIFICATION-REPORT.md` — this file

## Files modified (9), and exactly what changed in each

| File | What changed | What did NOT change |
|---|---|---|
| `config.py` | Appended `RCA_*` threshold constants + `ROOT_CAUSE_HEADERS` | Every existing constant, `PAGES`, `HISTORY_HEADERS` — untouched, verified byte-identical in testing |
| `gtmetrix.py` | Added `Metrics.raw_audit: dict` field (defaults to `{}`); added new `extract_raw_audit_fields()` function; `extract_metrics()` now also populates `raw_audit`, wrapped in its own try/except | `start_test`, `poll_for_result`, `run_single_page`, all existing `Metrics` fields, all existing retry/error logic — untouched |
| `scheduler.py` | Imported `root_cause`; appended a new, isolated try/except block at the end of `_record_result()` that runs RCA and writes to the new Sheets tab | The existing `google_sheet.append_result`/`append_failure` call and its own try/except — untouched, still the first thing that runs |
| `google_sheet.py` | Appended 3 new functions: `_get_or_create_sheet_with_headers`, `append_root_cause`, `read_root_cause_history` | `_get_or_create_sheet`, `_ensure_header`, `append_result`, `append_failure`, `read_history` — untouched |
| `dashboard_data.py` | Added `import json`; appended new `build_root_cause_summary()` function | `build_all()` — untouched, not called by the new function, not modified in any way |
| `email_report.py` | Appended `_build_root_cause_html()`; `send_report()` gained one new optional parameter `root_cause_reports: list \| None = None` | `_build_html()`, `_score_color()` — untouched. Old call shape `send_report(results)` still works identically |
| `main.py` | Added `import config`; added one new isolated try/except block that loads `root_cause.json` and passes it to `send_report`; added one new isolated try/except block calling `dashboard_data.build_root_cause_summary()` | The `scheduler.run_batch()` call, the `dashboard_data.build_all()` call, `clear_run_state()`, exit-code logic — untouched |
| `dashboard/js/app.js` | Added `state.rootCause`; added `fetchJSONOptional()` helper; `loadAll()` gained one new line loading `root_cause.json` via the optional fetcher (never throws); added `renderRootCauseCards()`; `renderAll()` gained one new call | All other functions, `fetchJSON()`, the 4-file `Promise.all()` — untouched |
| `dashboard/index.html` | Inserted one new `<section id="root-cause-grid">` block between the existing Page Vitals and Trends sections | Every existing element/ID — untouched |
| `dashboard/css/style.css` | Appended `.rc-issue*` classes at the end of the file, using the project's existing CSS variables (`--panel-alt`, `--radius-sm`, `--text`, `--text-muted`) | Every existing rule — untouched |

**Files touched zero times:** `utils.py`, `logger.py`, `requirements.txt`, `.github/workflows/monitor.yml`, `data/run_state.json`. No new pip dependency was needed for this feature — `root_cause.py` uses only the standard library plus the project's own modules.

## Tests run and results

1. **Static: full compile check** — `python3 -m py_compile` on all 9 touched/new Python files. **Pass**, zero syntax errors.
2. **Unit: RCA engine correctness** — ran `root_cause.analyze()` against a synthetic "heavy/slow" page (all raw_audit fields present, all thresholds breached) → correctly produced **15 issues**, correctly ranked "High TTFB" as the top (critical-severity) issue, correctly parsed a synthetic GTmetrix `recommendations` entry. Ran the same function against a synthetic "healthy" page (all metrics well under threshold, empty `raw_audit`) → correctly produced **0 issues**. Ran it against a `Metrics` object built the old way (`Metrics(status="Error", ...)`, no `raw_audit` passed) → `raw_audit` correctly defaulted to `{}`, no exception. **Pass.**
3. **Integration: dashboard regeneration** — mocked `google_sheet.read_history` and ran the **existing, untouched** `dashboard_data.build_all()`; captured the byte content of all 4 existing JSON files; then ran the **new** `build_root_cause_summary()` (mocked `google_sheet.read_root_cause_history`) and re-captured the same 4 files. **Result: byte-for-byte identical before and after** — confirms the new function cannot mutate the existing dashboard output. The new `root_cause.json` was written correctly (16 pages, correct nested `latest_report`).
4. **Integration: email backward compatibility** — called `email_report.send_report(results)` (old call shape, no new argument) — ran cleanly, identical behavior to before (skips send when SMTP unconfigured, exactly as it always has). Called `send_report(results, root_cause_reports=[...])` (new call shape) — ran cleanly. Verified `_build_root_cause_html()` returns a populated section for a page with issues and an **empty string** for a page with `issue_count: 0` (so healthy runs don't add empty clutter to the email).
5. **Config integrity** — asserted every pre-existing `config.py` value (`ALERT_SCORE_THRESHOLD`, `ALERT_LCP_THRESHOLD_SECONDS`, `PAGES` length, `HISTORY_HEADERS` contents) is unchanged, alongside confirming the new `RCA_*` constants load correctly.
6. **Frontend diff check** — confirmed `dashboard/js/app.js`, `dashboard/index.html`, `dashboard/css/style.css` changes are purely additive (reviewed via `diff`); the new `fetchJSONOptional()` path was specifically designed so a missing/absent `root_cause.json` (e.g., immediately after this deploy, before the first RCA-enabled run has ever executed) **cannot** break the existing dashboard load — it falls back to `null` instead of rejecting the `Promise.all()`.

## What was NOT tested (requires your real credentials/environment)
- An actual live GTmetrix report's `data.attributes` shape — I don't have visibility into which of the extra fields (`page_bytes`, `js_bytes`, `dom_elements`, `recommendations`, etc.) your specific GTmetrix plan tier actually returns. The extraction code is defensive (every field is `.get()`-based, never assumed present), but **the first live run against your real GTmetrix account is the real test of how rich the RCA output ends up being.** I'd recommend running `python main.py --workers 1` locally against one page first and inspecting the new `<Sheet>_RootCause` tab and `dashboard/data/root_cause.json` before relying on it.
- An actual Google Sheets write (I mocked `gspread` calls — the real API contract is the same shape `append_result`/`_ensure_header` already use successfully, but a live write against your real sheet is worth a manual spot-check).
- The actual GitHub Actions run (workflow file is untouched, so this should behave exactly as before, but worth confirming on the next scheduled/manual run).

## Confirmation

- ✅ Existing GTmetrix monitoring — untouched, verified via full diff of `gtmetrix.py`'s core functions (`start_test`, `poll_for_result`, `run_single_page`) plus a passing unit test of `extract_metrics()`'s existing return shape.
- ✅ Existing Dashboard — untouched, verified byte-identical JSON output in integration test #3.
- ✅ Existing Google Sheets — untouched, verified via diff (no existing function body changed).
- ✅ Existing Email — untouched, verified old call shape still works identically in integration test #4.
- ✅ Existing GitHub Actions — untouched (zero-byte diff on `monitor.yml`).
- ✅ New feature (Root Cause Analysis) — works correctly per unit + integration tests above.
- ✅ No regression introduced.

**Ready for you to review, then run once locally against a real page before I move to Feature 2 (Customer Journey Monitoring).**
