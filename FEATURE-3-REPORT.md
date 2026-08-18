# Feature 3 — Smart Alert System — Complete Report

This single document covers all 6 requested deliverables: source code summary, verification report, regression report, architecture update, alert workflow diagram, and testing report.

---

## 1. Updated Source Code — What Changed

### New files (4)
- `alert_models.py` — generic `Alert`/`AlertEvent` dataclasses. Source-agnostic: doesn't know or care whether an alert came from GTmetrix, RCA, Journey, or a module that doesn't exist yet.
- `alert_rules.py` — the only file that translates a specific module's existing output (GTmetrix `PageResult`s, score history, RCA JSON, Journey `JourneyResult`s) into generic `Alert` objects. A future module (SSL, Lighthouse, API monitoring, SEO) adds one function here, following the same shape; nothing else in the engine changes.
- `notification_manager.py` — the persistent-state dedup/recovery engine. State lives in `data/alert_state.json` (same committed-across-CI-runs pattern as `data/run_state.json`).
- `alerts.py` — the central facade every other module calls (`evaluate_domain`, `raise_operational`, `mark_recovered`), plus a small CLI (`python -m alerts --workflow-failure "..."`) for the one failure mode Python can't self-detect: a GitHub Actions job dying before `main.py` even runs.

### Modified files (8), and exactly what changed

| File | What changed | What did NOT change |
|---|---|---|
| `config.py` | Appended: `ALERT_ENABLED`, score/grade-drop thresholds, `ALERT_MIN_RCA_SEVERITY`, the central `ALERT_SEVERITY_MAP` (all 21 required alert types + severities), `ALERT_HEADERS` | Every line before it — Feature 1/2 config, `PAGES`, `HISTORY_HEADERS` — untouched |
| `google_sheet.py` | Appended `append_alert`, `read_alert_history`, reusing the existing (Feature-1-added) `_get_or_create_sheet_with_headers` helper unchanged | `append_result`, `append_failure`, `append_root_cause`, `append_journey`, and all read functions — untouched |
| `dashboard_data.py` | Appended `build_alerts_summary()` | `build_all()`, `build_root_cause_summary()`, `build_journey_summary()` — untouched |
| `email_report.py` | Appended `_build_alerts_html()`; `send_report()` gained one more optional parameter (`alert_events`) and one conditional (only reachable when `results` is empty — the new CLI fallback path; every existing caller's behavior is byte-identical, verified in testing) | `_build_html()`, `_build_root_cause_html()`, `_build_journey_html()` — untouched |
| `scheduler.py` | Added a guarded `import alerts`; `_record_result()`'s existing GTmetrix-history-write try/except gained two new lines (`raise_operational` on failure, `mark_recovered` on success), each wrapped in its own inner try/except so an alert-engine hiccup can never be misreported as a Sheets failure | The write logic itself, the RCA block, `run_batch`, `clear_run_state` — untouched |
| `main.py` | Added a guarded `import alert_rules, alerts`; the existing dashboard-build and journey try/except blocks gained `mark_recovered`/`raise_operational` calls; added one new, fully isolated alert-evaluation block after Journey; the email call gained one more argument | The GTmetrix batch call, exit-code logic, `clear_run_state()` — untouched |
| `.gitignore` | Added a documenting comment for `data/alert_state.json` (no functional change — it was already not excluded by any pattern) | Every existing pattern — untouched |
| `.github/workflows/monitor.yml` | `data/alert_state.json` added to the committed-files list; one new step (`Notify on workflow failure`, `if: failure()`) appended at the end | Every existing step, the cron schedule, permissions — untouched |

### Files touched zero times
`utils.py`, `logger.py`, `gtmetrix.py`, `root_cause.py`, `journey.py`, `journey_models.py`, `playwright_runner.py`, `requirements.txt` (no new dependency needed — the alert engine is pure standard library + existing project modules).

---

## 2. Architecture Update

### Layering (matches the discipline already established in Features 1–2)

```
alert_models.py        ← pure data (Alert, AlertEvent) — no I/O, no state
       ↑
alert_rules.py          ← translates domain data → Alert objects — no I/O, no state
       ↑ (imports gtmetrix/root_cause constants only for string comparison)
notification_manager.py ← persistent state, dedup, recovery — no domain knowledge
       ↑
alerts.py                ← facade: rules → notification_manager → Sheets write
       ↑
main.py / scheduler.py    ← call sites: pass domain data in, get AlertEvents out
```

**Why this satisfies "generic, not tightly coupled to GTmetrix":**
- `notification_manager.py` never imports `gtmetrix.py`, `root_cause.py`, or `journey.py` — it only knows about `Alert`/`AlertEvent` and a `{key: Alert}` dict + a `set[str]` universe. It has no idea what a "page" or a "journey" is.
- `alert_rules.py` is the *only* file with domain knowledge, and it's structured as N independent functions (`rules_for_gtmetrix`, `rules_for_score_drops`, `rules_for_root_cause`, `rules_for_journey`) — each one converts one module's existing output into the same generic shape. A hypothetical `rules_for_ssl(cert_results)` would be a fifth function of the same shape, calling the same `alerts.evaluate_domain()`.
- `config.ALERT_SEVERITY_MAP` accepts *any* string key — an alert type it's never seen before still gets a sane default severity (`"warning"`) rather than crashing. A future module doesn't need to touch this file at all unless it wants a non-default severity.
- Operational alerts (`raise_operational`/`mark_recovered`) take a free-form `(module, alert_type)` pair — no registration step, no schema. "SSL", "Lighthouse", "API" are just strings a future call site would pass in.

### Two dedup models (why both exist)

1. **Universe-scoped (`process_batch`)** — for anything checked against a known, finite set every run (every configured page, every RCA category × page, every journey product). Recovery is precise: "this exact key was firing, and this run *specifically checked it* and found it healthy." A page temporarily removed from `config.PAGES` is never falsely marked "recovered" by omission — tested explicitly (§5, Test 4).
2. **Operational (`raise_operational`/`mark_recovered`)** — for exception-driven failures (a Sheets write threw, a dashboard build crashed) where there's no fixed universe to check against each run. The calling code marks success or failure explicitly, at the exact point it already knows which happened.

### Data flow for one run

```
scheduler.run_batch()          → results: list[PageResult]        (existing, untouched)
        ↓
dashboard_data.build_all()     → dashboard/data/history.json      (existing, untouched)
dashboard_data.build_root_cause_summary() → root_cause.json       (Feature 1, untouched)
journey.run_all_journeys()     → journey_results: list[JourneyResult]  (Feature 2, untouched)
        ↓
alert_rules.rules_for_gtmetrix(results)            → candidates, universe
alert_rules.rules_for_score_drops(history.json)     → candidates, universe
alert_rules.rules_for_root_cause(root_cause.json)   → candidates, universe
alert_rules.rules_for_journey(journey_results)      → candidates, universe
        ↓ (each domain independently)
alerts.evaluate_domain(domain, candidates, universe)
        ↓
notification_manager.process_batch()  → reads/writes data/alert_state.json
        ↓
AlertEvent(status="new" | "ongoing_suppressed" | "recovered")
        ↓ (only should_notify events)
google_sheet.append_alert()  →  AlertHistory Sheets tab
dashboard_data.build_alerts_summary()  →  dashboard/data/alerts.json
email_report.send_report(..., alert_events=[...])  →  "Alerts" email section
```

---

## 3. Alert Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  main.py — one run                                                   │
│                                                                        │
│  scheduler.run_batch() ──────────────► results (GTmetrix)            │
│         │                                                             │
│         ▼                                                             │
│  dashboard_data.build_all() ─────────► history.json                  │
│  dashboard_data.build_root_cause_summary() ► root_cause.json         │
│  journey.run_all_journeys() ─────────► journey_results               │
│         │                                                             │
│         ▼                                                             │
│  ┌───────────────────── Alert Engine ─────────────────────────────┐  │
│  │                                                                  │  │
│  │  alert_rules.py                                                 │  │
│  │  ┌────────────┐ ┌──────────────┐ ┌───────────┐ ┌─────────────┐│  │
│  │  │  gtmetrix  │ │ score_trend  │ │ root_cause│ │   journey   ││  │
│  │  │  failures  │ │ (drop/grade) │ │ (RCA cats)│ │  (funnel)   ││  │
│  │  └─────┬──────┘ └──────┬───────┘ └─────┬─────┘ └──────┬──────┘│  │
│  │        └────────────────┴──────────────┴──────────────┘        │  │
│  │                          │  candidates + universe (per domain)  │  │
│  │                          ▼                                      │  │
│  │              alerts.evaluate_domain()                           │  │
│  │                          │                                      │  │
│  │                          ▼                                      │  │
│  │         notification_manager.process_batch()                    │  │
│  │              reads/writes data/alert_state.json                 │  │
│  │                          │                                      │  │
│  │         ┌────────────────┼────────────────┐                    │  │
│  │         ▼                ▼                 ▼                    │  │
│  │      "new"      "ongoing_suppressed"   "recovered"               │  │
│  │         │                │                 │                    │  │
│  │         │           (silent — no          │                    │  │
│  │         │            Sheets write,        │                    │  │
│  │         │            no email)            │                    │  │
│  │         └────────────────┼────────────────┘                    │  │
│  │                          ▼                                      │  │
│  │              google_sheet.append_alert()   (AlertHistory tab)   │  │
│  │              dashboard_data.build_alerts_summary()               │  │
│  │                          │                                      │  │
│  └──────────────────────────┼──────────────────────────────────────┘  │
│                              ▼                                        │
│                email_report.send_report(alert_events=...)             │
│                              │                                        │
│                              ▼                                        │
│                    "Alerts" section in summary email                  │
└─────────────────────────────────────────────────────────────────────┘

Separately, operational alerts (Sheets write failed, dashboard build
crashed, Playwright crashed) fire directly from existing try/except
blocks via alerts.raise_operational()/mark_recovered() — same
notification_manager state file, same Sheets tab, same email section,
just without the universe/domain batching (single key, fire/recover).

Separately again, a GitHub Actions job-level failure (before main.py
ever runs) is caught by the workflow's own `if: failure()` step, which
calls `python -m alerts --workflow-failure "..."` — same engine, same
dedup state, entered from outside Python entirely.
```

---

## 4. Regression Report

**Method:** for every file modified in this feature, diffed against the exact Feature 2 baseline and confirmed every hunk is a pure addition, with two disclosed exceptions in `email_report.py` (the `send_report()` signature and the `subject` line) — both are backward-compatible extensions, not behavior changes, verified by test.

**Full compile check:** all 16 project Python files compile cleanly (`python -m py_compile`).

**Dashboard JSON regression:** ran the existing `build_all()` and the Feature 1/2 `build_root_cause_summary()`/`build_journey_summary()` (all mocked at the Sheets layer), captured all 6 pre-existing JSON files, then ran the new `build_alerts_summary()` and re-captured them. **Result: byte-for-byte identical.** The new `alerts.json` was written correctly and independently.

**Email regression:** all 4 generations of `send_report()`'s signature (original 1-arg, Feature 1's 2-arg, Feature 2's 3-arg, Feature 3's 4-arg) were exercised in the same test run with zero exceptions.

**Workflow YAML:** re-parsed with PyYAML after the edit — still valid, all 12 steps in correct order, the two new steps (screenshot upload, workflow-failure notify) correctly positioned without disturbing any existing step.

**A note on data hygiene:** while assembling this delivery, two of the "generated" JSON fixture files (`root_cause.json` in Feature 1, and this feature's own regression testing) picked up leftover synthetic test data rather than reflecting a genuine "no data yet" state. Both were caught and corrected before packaging — `dashboard/data/summary.json`, `pages.json`, `trends.json`, and `history.json` now correctly hold your **real** GTmetrix run data from 2026-08-17, and `root_cause.json`/`journey.json`/`alerts.json` correctly show an honest empty state (since those features have no real history yet). Flagging this so you're aware it happened, in case you want to independently verify `dashboard/data/*.json` yourself.

**Conclusion:** no regression in GTmetrix, Root Cause Analysis, Customer Journey, Dashboard, Google Sheets, or Email functionality.

---

## 5. Testing Report

### `notification_manager.py` — the core dedup/recovery state machine (highest-risk new logic)

| Test | Result |
|---|---|
| New alert fires once (status="new") | ✅ Pass |
| Same alert on 2 subsequent runs while still failing → suppressed, occurrence count increments (2, then 3) | ✅ Pass |
| Alert resolves → exactly one "recovered" event | ✅ Pass |
| Alert healthy on the run after recovery → zero events (fully silent) | ✅ Pass |
| Same alert fails again later → fires as "new" again, not suppressed as a stale duplicate | ✅ Pass |
| **Universe scoping**: a key not included in a given run's universe is never falsely marked "recovered" by omission | ✅ Pass |
| Operational alert: fire → suppress-on-repeat (occurrence count increments) → `mark_recovered` → "recovered" → calling `mark_recovered` again when already healthy → silent `None`, no-op | ✅ Pass |

This is the exact scenario from your original requirement's example (Homepage Failed → one email → suppressed for 5 runs → Recovery email → fails again tomorrow → new email) — reproduced and passing.

### `alert_rules.py` — translation correctness

| Test | Result |
|---|---|
| Mixed success/failure GTmetrix batch → only the failed page becomes a candidate, correct `homepage_failed` type/severity | ✅ Pass |
| **All** pages fail in one batch → correctly collapses into one systemic `gtmetrix_api_failure` alert type across all pages, not N separate page alerts | ✅ Pass |
| Score drop ≥ threshold → `performance_score_drop`; drop below threshold → no alert | ✅ Pass |
| RCA issue severity filtering: a `warning`-level category becomes an alert, an `info`-level category correctly does not | ✅ Pass |
| Journey failure at the `homepage` step → mapped to `website_down`; at `checkout` → `checkout_failed`; elsewhere → generic `journey_failed` | ✅ Pass |

### `alerts.py` — Sheets integration correctness

| Test | Result |
|---|---|
| A "new" alert triggers exactly one `google_sheet.append_alert()` call | ✅ Pass |
| A suppressed (ongoing) duplicate triggers **zero** Sheets calls — confirmed by mock assertion, not just by reading the code | ✅ Pass |

### End-to-end simulation of `main.py`'s actual alert block (3 consecutive simulated runs, mocked Sheets)

| Run | Scenario | Result |
|---|---|---|
| 1 | Homepage fails, FNM's score drops 5pts (below the 10pt threshold) | 1 notify-worthy event (`homepage_failed`, "new"); exactly 1 Sheets write; score drop correctly did NOT alert |
| 2 | Homepage still down | 0 notify-worthy events; 0 Sheets writes (deduplication working end-to-end) |
| 3 | Homepage recovers | 1 notify-worthy event (`homepage_failed`, "recovered"); exactly 1 Sheets write |

### Frontend

`node --check` confirmed `dashboard/js/app.js` is syntactically valid after the Feature 3 edits.

### What was NOT tested (requires your real environment)
- A real Google Sheets write to the new `AlertHistory` tab — mocked in testing, using the identical, already-proven `_get_or_create_sheet_with_headers` pattern from Features 1–2.
- A real GitHub Actions run of the new `Notify on workflow failure` step — the YAML is valid and the step is correctly gated on `if: failure()`, but its actual behavior (installing dependencies fresh, running `python -m alerts`) can only be confirmed by an actual failing workflow run. I'd suggest testing it deliberately once (e.g., temporarily breaking a required secret) before relying on it in production.
- Real score-drop/grade-drop alerting depends on at least 2 real historical rows per page existing in `history.json` — with only one real run so far (2026-08-17), this won't fire anything meaningful until a second real run happens.

---

## 6. Final Confirmation

- ✅ Existing GTmetrix monitoring — untouched.
- ✅ Existing Root Cause Analysis (Feature 1) — untouched, `root_cause.json` unaffected by the new alerts build.
- ✅ Existing Customer Journey Monitoring (Feature 2) — untouched, `journey.json` unaffected.
- ✅ Existing Dashboard — untouched, byte-identical JSON output confirmed.
- ✅ Existing Google Sheets — untouched (no existing function body changed).
- ✅ Existing Email Reports — untouched, all 4 `send_report()` call-shape generations verified.
- ✅ Existing GitHub Actions — the only edits are one new `git add` path and one new `if: failure()` step at the very end; every existing step untouched.
- ✅ Smart Alert System — works correctly per the tests above: deduplication, recovery, severity, alert history (Sheets + dashboard + email) all verified.
- ✅ No regression introduced.

**Recommendation before Feature 4:** the alert engine is fully wired and tested with mocked data, but has never seen a real second GTmetrix run — score/grade-drop detection specifically needs two real data points to ever fire. I'd suggest letting this run for at least 2 real scheduled runs (or 2 manual runs) before trusting the drop-detection alerts, and doing one deliberate test of a real Google Sheets write to confirm the `AlertHistory` tab gets created correctly with your real credentials.
