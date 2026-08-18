# Independent Final Audit — GoodMonk Performance Command Center

**Reviewer stance:** independent, adversarial, pre-production approval review.
No implementation history is treated as a defense. Every finding below is
based on reading the actual source in this delivery, not on prior reports
produced during development of this project.

---

## Critical Findings

### C1 — A single corrupted state file crashes the entire run, with no isolation

**File:** `main.py`, line ~101; `page_scheduler.py`, `_load_state()`

```python
due_pages, skipped_pages = None, []
if _SCHEDULER_AVAILABLE and config.SCHEDULER_ENABLED:
    due_pages, skipped_pages = page_scheduler.get_due_pages(config.PAGES)
```

This call is **not wrapped in any try/except**. `get_due_pages()` calls
`page_scheduler._load_state()`, which calls `utils.read_json()`:

```python
def read_json(path: Path, default: Any = None) -> Any:
    if not Path(path).exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
```

If `data/page_schedule_state.json` contains anything that isn't valid
JSON — truncated content, stray characters, or (see C2 below) literal
git conflict markers — `json.load()` raises `json.JSONDecodeError`.
Nothing catches it between here and `main()`'s own top level:

```python
if __name__ == "__main__":
    sys.exit(main())
```

There is no try/except around `main()` either. The process crashes with
an unhandled traceback. **Zero pages get tested, no dashboard rebuild,
no email — not even a failure email**, because the crash happens before
`email_report.send_report()` is ever reached.

**Why it's a problem:** every other module in this codebase is
deliberately isolated (one bad page can't stop the batch, one bad Sheets
write can't stop RCA, one bad alert can't stop the email). This one
specific call breaks that pattern completely — a single malformed file
takes down the *entire* run, contradicting the project's own stated
design philosophy throughout every other module.

**When it happens:** any time `data/page_schedule_state.json` becomes
unparseable — see C2 for a concrete, realistic mechanism.

**Business impact:** a total, silent outage of monitoring — no alerts
fire (the alert engine never runs), no one is notified, and the failure
would only be discovered by a human noticing the dashboard stopped
updating, or by GitHub's own default "workflow run failed" UI (not an
application-level alert).

**Recommended solution:** wrap the `get_due_pages()` call in main.py in
a try/except that falls back to `due_pages = None` (which already means
"scheduler unavailable, test everything" elsewhere in the same function)
rather than letting the exception propagate. Equivalently, `read_json()`
itself could catch `json.JSONDecodeError` and return `default`, which
would fix this for every caller at once — but that's a broader behavior
change worth deciding deliberately, not simply reintroduced under
pressure to fix a Critical.

**Severity: Critical.**

---

### C2 — A swallowed git rebase conflict can push corrupted state JSON to `main`, directly enabling C1

**File:** `.github/workflows/monitor.yml`

```bash
git commit -m "chore: update performance data [skip ci]"
git pull --rebase origin main || true
git push origin HEAD:main
```

If `git pull --rebase` hits a conflict (realistic: two workflow runs
racing despite the `concurrency` guard — e.g., one triggered by cron,
one triggered manually at nearly the same moment, or a human pushing a
manual edit to `data/*.json` at the wrong time), `git rebase` exits
non-zero, is caught by `|| true`, and **the script continues anyway**
without checking whether the working tree is left in a conflicted
state. The subsequent `git push origin HEAD:main` then pushes whatever
`HEAD` currently is — which, mid-conflicted-rebase, is not necessarily
a clean commit. In the worst case this can result in a file on `main`
containing literal `<<<<<<< HEAD` / `=======` / `>>>>>>>` conflict
markers.

**Why it's a problem:** this exact failure mode produces the corrupted
JSON that C1 has no defense against. These two findings are not
independent — C2 is a realistic *cause*, C1 is the *effect*, and neither
was tested together (or apart) against a real git remote in this
environment.

**When it happens:** concurrent writers to `main` touching the same
`data/*.json` files — most likely a manual workflow dispatch overlapping
with a scheduled run in a narrow race window before the `concurrency`
group fully engages, or any manual local commit to those files.

**Business impact:** silent total monitoring outage (via C1), potentially
recurring every hour until someone manually inspects and fixes the
corrupted file in the repo — the system does not detect or repair this on
its own.

**Recommended solution:** replace `|| true` with explicit conflict
detection (`git rebase --abort` and a clear failure if the rebase didn't
succeed cleanly), so a conflict either resolves correctly or fails loudly
via the step's own exit code (which the workflow's `Notify on workflow
failure` step is already designed to catch) — instead of silently
proceeding in an unknown state.

**Severity: Critical** (chained with C1; either finding alone would be
High, the combination is Critical because it's a concrete, describable
path from a plausible trigger to a total outage).

---

## High Findings

### H1 — Resume logic does not survive an actual runner crash or cancellation, only a graceful Python exception

**File:** `.github/workflows/monitor.yml`, `scheduler.py`

State (`data/run_state.json`, and by extension `data/alert_state.json`
and `data/page_schedule_state.json`) is only made durable by the single
"Commit updated dashboard data + run state" step, which runs **after**
the main run step completes (successfully or with a caught exception).
If the GitHub Actions **runner itself** is killed mid-run — a manual
cancellation via the Actions UI, the job hitting its timeout, or GitHub
infrastructure reclaiming the runner — no subsequent step executes at
all. Whatever pages were already tested (and whatever GTmetrix quota was
already spent on them) during that run are **not** recorded anywhere,
because they were only ever held in local process memory / local disk
on a VM that no longer exists.

**Why it's a problem:** the "resume logic" claim only covers the
specific case where `python main.py` itself catches its own error and
exits non-zero gracefully (handled by `continue-on-error: true`) — it
does **not** cover the more common real-world interruption: someone
cancelling a stuck run, or a run exceeding whatever timeout is
configured (no explicit `timeout-minutes` is set on this job, so it
defaults to GitHub's own 6-hour ceiling — also never exercised or timed
against a real GTmetrix account in this project's whole history).

**When it happens:** any hard interruption of the runner process itself,
as opposed to a caught Python exception.

**Business impact:** wasted GTmetrix API quota (pages get silently
re-tested next run since the failed run's partial progress was never
committed), and a false impression that "resume" fully protects against
interruption when it only protects against one specific class of it.

**Recommended solution:** either accept this as a known, documented
limitation (cheap, honest), or add an explicit `timeout-minutes` to the
job and/or a periodic mid-run commit strategy if wasted quota from
repeated full page re-tests becomes a real cost concern.

**Severity: High.**

---

### H2 — Google Sheets full-history re-read, unbounded, now happening up to 24×/day

**File:** `dashboard_data.py`, `build_all()`; `google_sheet.py`, `read_history()`

```python
def read_history(sheet_name: str):
    ws = _get_or_create_sheet(sheet_name)
    return ws.get_all_records()
```

No `limit`, no pagination, no incremental read — every single dashboard
rebuild reads **every row ever written** for **every one of the 16
configured pages**, regardless of whether that page was even tested this
run. At the project's own stated cadence (6 pages hourly, 10 pages every
2 hours), the 6 critical-tier sheet tabs will individually exceed 2,000
rows within roughly 3 months of continuous operation, and keep growing
without bound, forever, since nothing archives or truncates Sheets
history. This same unbounded read now happens up to 24 times per day
instead of the original once-daily cadence.

**Why it's a problem:** `gspread`'s `get_all_records()` cost (network
transfer + parsing) grows with sheet size, and this happens on the
*hot path* of every single run, 24× more often than the architecture
was originally exercised at. This was flagged as a distant, low-urgency
concern in earlier project documentation — an adversarial review
disagrees with that framing: at 24 reads/day of a monotonically growing
sheet, this is a near-term degradation, not a someday-maybe one.

**When it happens:** progressively, starting from day one and worsening
every day the system runs, accelerating specifically because of the
Feature 4 cadence change.

**Business impact:** slower runs over time, increased Google Sheets API
quota consumption, and — if it grows enough — approaching Google
Sheets' own practical size/row limits on the affected tabs (Sheets has a
10-million-cell ceiling per spreadsheet across all tabs combined; with 16
pages × several tab types × growing rows × ~10 columns, this is not
close yet, but it is a real, calculable ceiling that has never been
calculated against actual projected growth in any document in this
project).

**Recommended solution:** read only the rows needed (recent N per page
for the dashboard, which already only displays a capped window
client-side) rather than the full sheet; this needs an actual
implementation decision, not a comment acknowledging it.

**Severity: High** (elevated from the "not urgent" framing used earlier
in this project's own documentation, specifically because Feature 4's
cadence change was never re-evaluated against this pre-existing concern).

---

### H3 — `dashboard/data/history.json` grows without bound and is committed to git forever

**File:** `dashboard_data.py`, `build_all()`

The same unbounded read from H2 is written, effectively unbounded, into
`history.json`, which is committed to the repository on every run
(`git add dashboard/data/*.json`). The dashboard's own JavaScript caps
*display* at 500 rows client-side, but that cap is applied **after**
fetching the full file — the file itself, and the git history of every
single hourly commit of it, grows forever. A public or private GitHub
repo with years of hourly commits to a monotonically growing JSON file
will have a materially bloated `.git` directory, slower clones, and
slower `git diff`/`git log` operations on that path.

**Severity: High** (same underlying cause as H2, distinct and compounding
consequence — repository health, not just runtime performance).

---

## Medium Findings

### M1 — Untested "queued, not canceled" overlap-protection claim

**File:** `.github/workflows/monitor.yml`

```yaml
concurrency:
  group: "pages"
  cancel-in-progress: false
```

This is asserted, in this project's own documentation, to fully satisfy
"prevent overlapping executions, queue the next one, never cancel a
running job." That's the *documented* behavior of this GitHub Actions
feature, but it has never been exercised against a real scenario in this
project's history — no evidence exists (in this delivery or anywhere
traceable) that a genuinely overlapping trigger was ever actually
produced and observed queuing correctly rather than, say, silently
being dropped, or queuing in a way that compounds run duration under
sustained GTmetrix slowness (see M2).

**Severity: Medium** — likely correct (this is well-documented GitHub
behavior), but "likely correct based on documentation" and "verified
correct by testing" are different claims, and only the former is true
here.

### M2 — Compounding backlog risk under sustained GTmetrix degradation

**File:** `gtmetrix.py` (`POLL_MAX_ATTEMPTS=24`, `POLL_INTERVAL_SECONDS=15`
→ up to ~6 minutes worst-case per page before timeout, before
`API_MAX_RETRIES` retry backoff is even factored in), `config.py`
(`MAX_WORKERS=4`)

If GTmetrix is degraded (slow, intermittently rate-limiting) during a
run where many pages are due (worst case: the very first run ever, or
any run after a period of the workflow being disabled/failing, where a
large backlog of "due" pages has accumulated with no cap), a single
run could plausibly take 20–30+ minutes. Since the workflow triggers
hourly and overlapping runs queue rather than cancel (per M1), a
sustained GTmetrix degradation could cause runs to back up faster than
they drain, with **no circuit breaker, no maximum-pages-per-run cap, and
no backlog-draining strategy** anywhere in the scheduler.

**Why it's a problem:** `page_scheduler.get_due_pages()` has no concept
of "too many due pages, throttle this run" — it returns however many
pages are due, unconditionally. Combined with M1's queuing behavior,
this is a plausible (if not yet observed) path to a growing, self
compounding backlog.

**Severity: Medium** — real architectural gap, not yet observed in
practice because this system has never run against a real degraded
GTmetrix API for a sustained period.

### M3 — No environment-variable / config validation beyond "is it present"

**File:** `config.py`

`_env(key, required=True)` only checks that a value exists — it never
validates shape or range. `interval_hours` could be set to `0` (results
in "always due," probably harmless) or a negative number (undefined
behavior — `timedelta(hours=-1)` is valid Python but produces a
nonsensical "due in the past by more time every run" comparison that has
never been tested). `ALERT_MIN_RCA_SEVERITY` could be set to a typo'd
string not in `_RCA_SEVERITY_RANK`, which would silently `.get(..., 0)`
to the lowest rank rather than erroring — meaning a misconfigured
environment variable degrades silently to "alert on everything" rather
than failing loudly.

**Severity: Medium** — low likelihood (this is admin-controlled
configuration, not external input), but genuinely no validation exists,
and the failure mode for bad config is silent misbehavior rather than a
clear startup error.

### M4 — Duplicate/redundant local `import time` in `gtmetrix.py`

**File:** `gtmetrix.py`, `poll_for_result()`

```python
import time
...
if resp.status_code == 429:
    ...
    import time as _t
    _t.sleep(config.RATE_LIMIT_WAIT_SECONDS)
```

`time` is already imported at the top of the function; the second,
aliased import a few lines later is redundant dead weight — harmless at
runtime (Python caches the import) but a genuine, verifiable code smell
that a "code quality review" should have caught and didn't.

**Severity: Low-Medium** — cosmetic, zero functional risk, but it's a
concrete miss in a project whose own documentation repeatedly claims a
clean static-analysis pass; this specific line was never flagged by
`pyflakes` (since both imports resolve and the name is used), which is
itself worth noting: **static analysis tools did not catch every code
smell — only unused names, not redundant-but-used ones.**

---

## Low Findings

### L1 — HTML-escaping gap in the dashboard, still open

`dashboard/js/app.js` builds DOM via `innerHTML` from Sheets-sourced
strings (page names, error messages, alert messages) without escaping.
Low risk since these originate from your own GTmetrix/Playwright error
text rather than arbitrary external input, but a theoretical stored-XSS
vector remains unresolved across every prior review of this project.

### L2 — GTmetrix API response bodies logged at DEBUG level

`gtmetrix.py`'s `api_log.debug(resp.text[:300])` — not a credential
leak, but unnecessary verbosity that's been noted and never addressed
since the very first review of this codebase.

### L3 — `reports/` directory provisioned, never used

`config.REPORTS_DIR` is created on startup; nothing anywhere in the
17-file codebase ever writes to it. Confirmed by direct grep, not
assumption. Either implement it or remove the dead scaffolding.

### L4 — `email_report.send_report()`'s 5-parameter signature

Functional, tested, but a genuine maintainability smell — a 6th
reporting concern would make this actively awkward. A bundled context
object would be cleaner. Not a bug; a design debt.

### L5 — `config.py` at 379 lines mixing many unrelated concerns

Paths, GTmetrix tuning, Sheets, email, 4 different features' thresholds,
and page/journey/alert/scheduler structured data all live in one file.
Functional and internally well-commented, but the single largest file
in the project by concept-count, not just line-count.

---

## Untested / Unverified — Explicitly, Not Assumed

The following have **never been exercised against real infrastructure**
in this project's entire development history — every "test" performed
throughout used mocked GTmetrix/Sheets/SMTP/Playwright objects in a
sandboxed environment with no network access to any of the real services
involved:

- A real GTmetrix API call, response, rate-limit, or outage.
- A real Google Sheets write, read, or auth failure, at any scale.
- A real Playwright browser launch against goodmonk.in — the journey
  selectors in `config.JOURNEY_SELECTORS` are generic defaults, never
  validated against the actual live theme, across 3 features built on
  top of that assumption.
- A real SMTP send, at any volume.
- A real GitHub Actions run of this workflow, on real infrastructure,
  at any point — cron timing, concurrency queuing, artifact upload,
  Pages deployment, and the workflow-failure notification step are all
  YAML-syntax-valid and logically reasoned about, but zero of them have
  actually executed on GitHub's infrastructure.
- Any true concurrent/simultaneous execution of two workflow runs (the
  overlap-protection mechanism is asserted correct by documentation, not
  demonstrated).
- Sustained operation at any real timescale — everything about growth
  (Sheets size, `history.json` size, git repo size) is calculated, not
  observed.

**This is not a project that has been production-tested. It is a
project that has been carefully, extensively unit/integration-tested
against mocks, in a sandbox with no access to any of its real
dependencies.** Those are meaningfully different claims, and prior
project documentation has not always been precise about that
distinction.

---

## Scores /10

| Category | Score | Basis |
|---|---|---|
| Architecture | 7 | Genuinely clean layering; undermined by C1's single point of total failure, which directly contradicts the architecture's own stated isolation philosophy |
| Code Quality | 7 | Static-analysis-clean, but a manual read still found a redundant import (M4) that automated tooling missed, and 5 files show real complexity/parameter-count debt (L4, L5) |
| Reliability | 4 | C1+C2 together describe a realistic, concrete path to a silent total outage; H1 means "resume" is weaker than documented; none of this has been tested against real infrastructure |
| Performance | 5 | H2/H3 are real, worsening-not-static problems directly caused by the Feature 4 cadence change and never re-evaluated against it |
| Maintainability | 7 | Strong module boundaries; L4/L5 are real, named debt |
| Scalability | 5 | Scheduler cadence itself scales cleanly; the Sheets/history storage layer underneath it does not, and this gap widens specifically because the cadence got faster |
| Security | 7 | No hardcoded secrets, disciplined exception handling; L1/L2 remain open across every review this project has had |
| DevOps | 5 | Single clean workflow file; C2's swallowed rebase failure and the never-tested-on-real-infrastructure status (concurrency, cron, deployment) are real gaps in what "DevOps-ready" should mean |
| Testing | 3 | Extensive mock-based testing exists and is real, but **zero** of it ran against actual GTmetrix, Sheets, Playwright, SMTP, or GitHub Actions infrastructure. No CI test suite exists. This is the largest gap between how "tested" this project has been described as, and how tested it actually is |
| Documentation | 8 | Thorough and generally honest about known gaps in prior reports — though the Sheets-scaling urgency (H2) was consistently under-stated as "not urgent" across every prior review, including the one immediately before this one |
| **Overall** | **5.6** | A well-architected system with real, concrete, verifiable defects in exactly the areas — failure isolation and infrastructure-level testing — that matter most for something meant to run unattended in production |

---

## Final Decision

# ⚠ Approved with Conditions

This is not a system with cosmetic issues — C1 and C2 together describe
a realistic, traceable path from a plausible trigger (a rebase race
under the existing concurrency setup) to a silent, total, undetected
monitoring outage, in a project whose entire purpose is to detect and
alert on outages. That combination cannot ship as-is.

**Conditions for approval:**

1. **Fix C1** — wrap the unguarded `page_scheduler.get_due_pages()` call
   in `main.py` so a corrupted state file degrades to "test everything"
   rather than crashing the whole process. This is a small, targeted
   fix, not a redesign.
2. **Fix C2** — replace the `|| true` swallow on `git pull --rebase`
   with explicit conflict handling, so a conflict either resolves
   correctly or fails loudly through the already-existing
   workflow-failure notification path.
3. **Run this against real infrastructure at least once** — one real
   GitHub Actions execution, one real GTmetrix call, one real Sheets
   write, one real Playwright run against goodmonk.in — before trusting
   any of the claims about journeys, cron, concurrency, or deployment
   that currently rest entirely on reasoning rather than observation.
4. **Get a real answer on GTmetrix quota** before hourly cadence runs
   unattended for real — this has been an open question since before
   the first feature was built and has never been resolved.

Everything else in this report (H1, H2, H3, and every Medium/Low finding)
should be tracked and addressed on a normal timeline, not treated as
release blockers — but should not be quietly dropped either, especially
H2/H3, which get measurably worse the longer they're deferred.
