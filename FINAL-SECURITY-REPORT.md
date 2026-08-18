# Final Security Report — v1.0

## Method

Re-ran the same grep-based sweep from the original project audit, across
the full v1.0 codebase (17 Python files, up from 9 at the original
audit), plus manual review of every new secret-adjacent code path added
across Features 1–4 (SMTP credentials, GitHub Actions secrets, the new
`alerts.py` CLI, journey screenshot handling).

## Findings

| Check | Result |
|---|---|
| Hardcoded secrets in source | ✅ None found — `grep` for API-key/password/token/secret patterns with actual embedded values returned zero matches |
| `.env` / `service-account.json` gitignored | ✅ Confirmed |
| New state files (`data/alert_state.json`, `data/page_schedule_state.json`) contain secrets | ✅ No — both only ever contain alert/schedule metadata (timestamps, status strings, occurrence counts), never credentials or page content |
| `print()` statements that could leak data to CI logs uncontrolled | ✅ None — every module uses the structured `logger.py` setup |
| Bare `except:` clauses (can hide security-relevant errors) | ✅ None — every exception handler is `except Exception as e` or a specific exception type, always logged |
| `eval`/`exec` usage | ✅ None |
| SQL/shell injection surface | ✅ None — no SQL, no shell execution with untrusted input anywhere in the codebase |
| GitHub Actions secrets handling | ✅ All 8 secrets (`GTMETRIX_API_KEY`, `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, 6 SMTP-related) passed via `${{ secrets.* }}`, never hardcoded, never echoed |
| New workflow step (`Notify on workflow failure`) secret handling | ✅ Same secrets pattern as the main run step, no new exposure |
| Journey screenshots — could they leak sensitive data? | ✅ Low risk — screenshots are of your own storefront's public pages, never committed to git, uploaded only as a time-limited (14-day retention) GitHub Actions artifact accessible only to repo collaborators |
| Log verbosity — GTmetrix API response bodies logged at DEBUG | ⚠️ Unchanged since the original audit — informational, not a credential leak, but still worth knowing `api_log.debug(resp.text[:300])` exists in `gtmetrix.py` |
| Alert/journey error messages stored in Sheets and surfaced in email | ⚠️ Low risk, same category as the original audit's DOM-escaping note — error text (e.g. a Playwright exception message) is inserted into HTML email/dashboard without escaping. Since these originate from your own site's error responses rather than arbitrary user input, this remains a theoretical rather than exploited risk, but is unresolved since Feature 2 |
| Alert engine's own state — can it be tampered with to suppress alerts? | ℹ️ New consideration for v1.0: `data/alert_state.json` and `data/page_schedule_state.json` are committed to the repo like any other file — anyone with write access to the repo (already a trusted set, same access level needed to change `config.py` or secrets) could edit them to suppress alerts or force pages "due." This is an inherent property of the git-as-database design used throughout this project (same true of `data/run_state.json` since Feature 1) — not a new vulnerability introduced by later features, but worth naming explicitly now that alert suppression has real consequences |

## What's genuinely good here

- Zero hardcoded secrets across 4 features and 4110 lines of code — a real, verified property, not an assumption.
- Consistent, disciplined exception handling throughout — every failure mode is caught, logged, and isolated rather than silently swallowed or allowed to crash the whole pipeline.
- The guarded-import pattern (used for `journey`, `alert_rules`/`alerts`, `page_scheduler` in `main.py`, and `alerts` in `scheduler.py`) means a missing optional dependency degrades gracefully rather than becoming an attack surface via a crashed, half-configured process.

## Unresolved from prior audits (not new, not fixed in this pass)

- HTML-escaping of dynamic values in `dashboard/js/app.js`'s `innerHTML` usage (flagged Feature 1, still open).
- API response body logging verbosity (flagged in the original pre-feature audit, still open).

Neither was addressed in this hardening pass because both are low-severity
and fixing them would mean touching rendering/logging code paths across
multiple files for a "should fix eventually" item rather than a real
production blocker — consistent with this phase's brief to avoid
unnecessary changes to working code. Both are listed explicitly here so
they're not silently dropped.

## Conclusion

No critical or high-severity security issues found. Two low-severity
items remain open from earlier audits (disclosed, not fixed, deliberately).
One new structural consideration (state-file tamperability) is named for
the first time in this report — it's inherent to the project's git-based
persistence design rather than a bug, and the mitigation is standard repo
access control, not a code change.
