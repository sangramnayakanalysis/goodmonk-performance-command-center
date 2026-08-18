# C2 Fix Summary — Git Rebase Conflicts No Longer Silently Swallowed

## The problem (as identified in the Independent Final Audit)

`.github/workflows/monitor.yml`'s commit step used:

```bash
git pull --rebase origin main || true
git push origin HEAD:main
```

If the rebase conflicted (realistic under concurrent writers to the same
state files), the failure was silently swallowed by `|| true`, and the
script proceeded to `git push` regardless of what state the working tree
was actually left in — a realistic path to pushing corrupted or
conflict-marker-laden state files to `main`, which then feeds directly
into C1's failure mode on the next run.

## The fix

`.github/workflows/monitor.yml` only — the swallowed failure is replaced
with explicit handling:

```bash
if ! git pull --rebase origin main; then
  echo "::error::git rebase failed — aborting rebase and failing this step rather than pushing an unknown repository state."
  git rebase --abort
  exit 1
fi

git push origin HEAD:main
```

- **On a clean rebase:** identical behavior to before — proceeds to
  push.
- **On a conflict:** the rebase is explicitly aborted (returning the
  repository to the clean, pre-rebase state — no partial/conflicted
  files), the step exits with a non-zero code, and **no push happens.**
  This step has no `continue-on-error: true` set on it (confirmed by
  inspection), so the step failure fails the job, which the existing
  `Notify on workflow failure` step (`if: failure()`) already catches —
  no new alerting mechanism was added; the existing one now actually
  gets a chance to fire for this failure mode, which it couldn't before
  because `|| true` prevented the step from ever reporting failure.

## What did NOT change

- Every other step in the workflow — untouched.
- The `concurrency` block — untouched.
- The commit/add logic before this point — untouched.

## Verification performed

Not reasoned about abstractly — **a real git conflict was constructed
and run against real git operations** in a throwaway local bare
repository:

1. Two clones of the same repo, one pushes a change to a file, the other
   commits a *conflicting* change to the same line without having pulled
   first.
2. Ran the exact fixed script logic. Result: a genuine
   `CONFLICT (content): Merge conflict in data.json` occurred, the
   script correctly detected the failure, `git rebase --abort` correctly
   restored a clean working tree (`git status` confirmed "nothing to
   commit, working tree clean," no conflict markers anywhere), the
   script's exit code was confirmed `1`, and — critically — **the remote
   `main` branch was confirmed to still contain only the first clone's
   legitimate commit, completely unaffected.**
3. A second run confirmed the happy path (no conflict) is completely
   unaffected: the rebase succeeded, the push happened, and the commit
   landed on the remote exactly as before this fix.

## Severity resolution

**Critical → Resolved.** A git rebase conflict can no longer result in
an unknown repository state being pushed to `main`. It now either
resolves cleanly (unchanged behavior) or fails loudly through the
existing failure-notification path (new, correct behavior).
