# Feature 2 — Customer Journey Monitoring — Verification Report

## Files created (5 new)
- `journey_models.py` — `StepResult`/`JourneyResult` dataclasses (pure data, no browser or Sheets code)
- `playwright_runner.py` — low-level browser mechanics (shared browser, fresh context per journey, layered selector matching, console/JS/network capture, screenshots)
- `journey.py` — orchestration (retry-vs-permanent-failure logic, the 7-step funnel, cross-product isolation)
- `FEATURE-2-VERIFICATION-REPORT.md` — this file
- (`.env.example`, `README.md`, `.gitignore`, `requirements.txt`, `.github/workflows/monitor.yml` all received additive edits — see below, not new files)

## Files modified (8), and exactly what changed

| File | What changed | What did NOT change |
|---|---|---|
| `config.py` | Appended a new block: Playwright timeouts/retry settings, `JOURNEY_PRODUCTS`, `JOURNEY_SELECTORS` (layered strategy dict), `JOURNEY_STEPS`, `JOURNEY_HEADERS` | Every line before it — Feature 1's RCA config, `PAGES`, `HISTORY_HEADERS`, everything from the original project — untouched |
| `google_sheet.py` | Appended 3 new functions: `append_journey`, `read_journey_history`, plus reuse of the existing (Feature-1-added) `_get_or_create_sheet_with_headers` helper — no changes to that helper itself | `append_result`, `append_failure`, `read_history`, `append_root_cause`, `read_root_cause_history` — untouched |
| `dashboard_data.py` | Appended a new function `build_journey_summary()` | `build_all()` and `build_root_cause_summary()` — untouched |
| `email_report.py` | Appended `_build_journey_html()`; `send_report()` gained one more new optional parameter `journey_results: list \| None = None` | `_build_html()`, `_build_root_cause_html()` — untouched. All 3 call shapes (`send_report(results)`, `send_report(results, root_cause_reports=...)`, and the new one) verified working |
| `main.py` | Added a guarded top-level `import journey`; added one new isolated try/except block that runs journeys, writes them to Sheets, and rebuilds `journey.json`; the email call gained one more argument | The GTmetrix batch call, `dashboard_data.build_all()`, the Feature-1 RCA block, `clear_run_state()`, exit-code logic — untouched |
| `dashboard/js/app.js` | Added `state.journey`; `loadAll()` gained one more `fetchJSONOptional("journey.json", null)` line (never throws); added `renderJourneyCards()`; `renderAll()` gained one more call | Every other function — untouched |
| `dashboard/index.html` | Inserted one new `<section id="journey-grid">` block between the Root Cause and Trends sections | Every existing element/ID — untouched |
| `dashboard/css/style.css` | Appended `.journey-timeline`/`.journey-dot` classes at the end of the file | Every existing rule — untouched |

**Files touched by Feature 2 but with pre-existing Feature-1 content unaffected:** `.env.example`, `README.md`, `.gitignore`, `requirements.txt`, `.github/workflows/monitor.yml` — all received purely additive edits (new env vars documented, new README section, new gitignore entry for `journey_screenshots/`, `playwright` added to requirements, one new browser-install step + one new artifact-upload step added to the workflow). Every existing line in each of these 5 files is untouched — confirmed by diff.

**Files touched zero times:** `utils.py`, `logger.py`, `gtmetrix.py`, `scheduler.py`, `root_cause.py`, `data/run_state.json`.

## Design decisions worth flagging explicitly

1. **One shared browser, fresh context per product** — `PlaywrightRunner` launches exactly one Chromium process per run (`main.py` → `journey.run_all_journeys()` → one `with PlaywrightRunner()`), and each product's journey gets its own `browser.new_context()` (fresh cookies/cart session) via `runner.new_page()`. This satisfies the "reuse one browser instance, create new contexts as needed" requirement directly.
2. **Retry vs. permanent failure is a real distinction in the code, not just a comment** — `ElementNotFoundError` (a selector genuinely didn't match anything visible) is raised by `find_first_visible()` and is deliberately **not** caught by `_with_retry()`'s except clause, so it propagates on the first attempt. `TransientBrowserError` (navigation/click timeouts, network errors) **is** caught and retried up to `JOURNEY_MAX_RETRIES` with linear backoff. This was specifically unit-tested (see below) rather than just asserted.
3. **Journey step handlers are a dict-based dispatch table** (`_STEP_HANDLERS`), and `run_journey()` skips unknown step actions with a warning rather than crashing — so adding a future step type only means adding one handler function and one new dict entry, never touching the orchestration loop itself (the "support future journey steps" requirement).
4. **Screenshots never touch git.** `config.JOURNEY_SCREENSHOT_DIR` is a local-only directory, added to `.gitignore`, and `monitor.yml` uploads it as a build artifact (`actions/upload-artifact@v4`, `if: always()`, `if-no-files-found: ignore` so an all-green run with no failure screenshots doesn't fail the upload step). The email's journey section includes a real link to that run's artifacts page when `GITHUB_SERVER_URL`/`GITHUB_REPOSITORY`/`GITHUB_RUN_ID` are present (i.e. when actually running in Actions), falling back to plain text locally.
5. **Checkout is verified, never completed.** The checkout step clicks through and checks that the resulting URL contains "checkout" — no payment form is ever submitted.

## Tests run and results

1. **Static: full compile check** — `python3 -m py_compile` across **all 12** Python files in the project (9 from before + 3 new). **Pass**, zero syntax errors.
2. **Unit: orchestration logic, with a mocked Playwright Page** (no real browser available in this environment — see limitation below):
   - A fully successful journey correctly runs all 7 configured steps.
   - A **permanent failure** (add-to-cart button not matched by any configured selector) was confirmed to: mark `permanent_failure=True`, have `retried == 0`, and trigger **zero** `time.sleep()` calls — i.e., it genuinely was not retried, verified via mock assertion, not just by reading the code.
   - A **transient failure** (simulated network reset on the first navigation attempt) was confirmed to retry once and then succeed (`retried == 1`, `success == True`), with a backoff sleep observed.
   - A **cross-product isolation** test: one product's journey was made to raise an unhandled exception mid-run; confirmed all 3 configured products were still attempted and 3 results were still returned (2 successes, 1 recorded failure) — the crash did not stop the batch.
3. **Integration: dashboard regeneration** — ran the existing `build_all()` and the Feature-1 `build_root_cause_summary()` (both mocked at the Sheets layer), captured all 4 pre-existing JSON files, then ran the new `build_journey_summary()` (also mocked) and re-captured them. **Result: byte-for-byte identical** — confirms the new journey code cannot mutate any existing dashboard output. `journey.json` was written correctly (1 product, correct `latest_status`, correct `overall_success_rate` computation).
4. **Integration: email backward compatibility across all 3 generations of `send_report()`'s signature** — the original 1-argument call, the Feature-1 2-argument call, and the new Feature-2 3-argument call were all exercised in the same test run with zero exceptions. Verified `_build_journey_html()` produces a non-empty section that correctly mentions "artifact" when a failure is present, and returns an empty string for an empty result list (so a run with no journeys configured/enabled adds nothing to the email).
5. **Workflow YAML validity** — parsed the modified `monitor.yml` with PyYAML to confirm it's still valid YAML and to print the full step order, confirming the two new steps ("Install Playwright browser (Chromium)" and "Upload journey screenshots (if any)") are correctly positioned without disturbing the order or content of any existing step.
6. **Full-project diff review** — diffed every modified file against its Feature-1 state. Every diff hunk is a pure insertion (`+` lines only around unchanged context) — confirmed for `config.py`, `main.py`, `dashboard/index.html`, and spot-checked the rest.

## What was NOT tested (requires your real environment)

- **A real browser against the real site.** This sandbox has no network access to `goodmonk.in` and cannot download Playwright's Chromium binary, so nothing here exercised an actual page load, an actual click, or an actual GoodMonk Shopify theme. Everything above was validated by mocking Playwright's `Page`/`Locator`/`BrowserContext` objects to prove the **orchestration logic** (retry rules, step sequencing, failure isolation, data flow into Sheets/dashboard/email) is correct — but the **selector strings themselves are unverified defaults**. Please run `python main.py --workers 1` (with `pip install playwright && playwright install chromium` done locally first) and check the `CustomerJourney` Sheets tab / `dashboard/data/journey.json` before trusting the output — I'd specifically expect the `add_to_cart` and `checkout` selectors to need at least one tuning pass against GoodMonk's real theme markup.
- **A real Google Sheets write to the new `CustomerJourney` tab** — mocked in testing, same as Feature 1's Sheets calls; the API contract used is identical to the already-proven `append_root_cause` pattern.
- **A real GitHub Actions run** — the new `playwright install --with-deps chromium` and artifact-upload steps are syntactically valid YAML and correctly ordered, but their actual behavior (e.g., whether `--with-deps` pulls everything Chromium needs on `ubuntu-latest` without further tweaks) can only be confirmed by an actual workflow run.

## Confirmation

- ✅ Existing GTmetrix monitoring — untouched (zero-byte diff on `gtmetrix.py`, `scheduler.py`).
- ✅ Existing Root Cause Analysis (Feature 1) — untouched, verified byte-identical `root_cause.json` output pattern still works alongside the new journey build.
- ✅ Existing Dashboard — untouched, verified byte-identical JSON output in integration test #3.
- ✅ Existing Google Sheets — untouched (diff confirms no existing function body changed).
- ✅ Existing Email Reports — untouched, verified all three `send_report()` call shapes work.
- ✅ Journey Monitoring — works correctly per the orchestration unit tests above (retry logic, permanent-failure handling, cross-product isolation, full 7-step funnel execution).
- ✅ Playwright integration — API usage validated against the installed `playwright` package; actual browser execution not testable in this sandbox (see limitation above).
- ✅ Dashboard integration — new section renders from `journey.json`, degrades cleanly (empty-state message, no crash) if the file is missing.
- ✅ Google Sheets integration — new `append_journey`/`read_journey_history` functions follow the exact proven pattern from Feature 1's RCA Sheets functions.
- ✅ Email integration — new section works, includes the required artifact-location note.
- ✅ No regression introduced.

**Recommendation before Feature 3:** run this once against your real GoodMonk site (locally, with `--workers 1` and `JOURNEY_PRODUCTS` maybe trimmed to one product for the first try) and check whether the default selectors in `config.JOURNEY_SELECTORS` actually match your live theme's Add to Cart / Checkout buttons. I'd rather you catch a selector mismatch now than have Feature 3's alert system fire "journey failed" alerts that are actually just selector-tuning issues.
