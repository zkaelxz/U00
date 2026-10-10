# Lead-session troubleshooting guide

What went wrong in the hard sessions of 2026-10-10, generalised into checks a
lead or fix session runs before it spends an hour. Each rule names the incident
that taught it. Read the matching entry before diagnosing; if the symptom is
new, add an entry when it is solved.

## The rule of five for any red check

Before touching code, answer five questions in writing:

1. **What exactly failed?** The test id, the assertion text, the job name. Not
   "CI is red".
2. **Does it fail on the base branch at the same commit?** `git merge-tree`
   and a look at the last green base run. A failure shared by two unrelated
   PRs is a base problem and gets one root-cause session on base, not two
   patches.
3. **What did this PR change that the failing code reads?** Imports, size
   allowlists, fixtures, copied file lists. The answer is usually one line.
4. **What is the smallest change that makes the failure impossible,** not
   unlikely? A helper beats a guard; a guard beats a retry; a retry is not a
   fix.
5. **Which test proves it?** Run that one test and the quick guards, paste
   the pass count. "Flake" is not a root cause; a job is re-run at most once,
   and only when it died before any test body ran.

## Post-mortems

### 1. Guard tests failed only in the lead's checkout (stray `library/` folder)

**Symptom.** `tests/test_file_organization.py` and the audit hook in
`conftest.py` failed in the lead checkout and passed in CI and in every fix
session.

**What went wrong.** The checkout had an empty, git-ignored `library/`
folder from an earlier real run. The conftest audit treats a real library as
a hazard and fails. The sandbox denied `rm`, `mv` and `git worktree`, so the
lead spent two turns on workarounds (a second clone in the scratchpad) before
asking the owner, who removed it in one command.

**Lesson.** A failure that only reproduces in one checkout is an environment
difference, never code. Compare `git status --ignored` between the two
before anything else. When the fix is a denied command, ask the owner for
that one command in the first message, not after a workaround.

### 2. Size-allowlist overruns from edits that were not the task

**Symptom.** `test_split_guards` failed on `workspace_job_service.py` and
`restructure_service.py` after a base merge, in PRs that had not touched
those files' logic.

**What went wrong.** The allowlist is a ratchet: it only shrinks, and a file
near its ceiling goes over from a resolver's rename (`segment_splitting.`
prefixes) or one added comment. The first fix session shortened a comment;
the second replaced the prefixes with a from-import.

**Lesson.** Before pushing, run `python -m pytest -q tests/test_split_guards.py`
after the base merge, not before it. A frozen file never grows; the diff of a
resolver must leave every frozen file at or below its base size. If a rename
makes a file grow, use a from-import instead of touching the allowlist.

### 3. Import-safety test broke on a move-only split (#1053)

**Symptom.** `tests/test_db.py::TestImportTimeSafety` failed after `core`
began importing the new `segment_splitting` module.

**What went wrong.** The test copies `db.py` and its dependencies to a temp
dir from a hand-kept list. A split adds a module to `core`'s imports and the
list does not know it. The move-quality CI check passed because the code was
indeed only moved.

**Lesson.** A "move-only" split still changes the import graph. After any
split, run `test_db.py` and `test_static_analysis.py`, and update
`_copy_db_and_deps` (or make it compute the list from `core`'s imports;
follow-up).

### 4. The "flake" that was a race (#1051, #1050)

**Symptom.** A job watcher test and two Playwright specs failed on one PR
and passed on re-run.

**What went wrong.** The watcher test raced the result file's removal with
the job's "done" status; Playwright hit a Retry button that only exists at
wide layout. Both were real orderings the product can hit.

**Lesson.** Re-running is allowed once, to confirm that the failure
reproduces, not to make it go away. If it passes on re-run, the first run
still happened: open a root-cause session with the failing output attached.

### 5. One-try-under-lock looked fixed and was not (#1058)

**Symptom.** The Opus review of the jobs store rewrite flagged SQL retries
under `background_jobs._lock`; the fix session moved retries out, and the
docstring now says "outside the lock". A later read showed one write with a
5 s busy wait still under the lock.

**What went wrong.** The review verified the retry loop moved and trusted the
docstring for the first try.

**Lesson.** A docstring claim about locks is checked by reading the call
site, not the function. For every `with _lock:` ask what blocking call runs
inside: sqlite busy wait, a network call, a subprocess. Each one gets a
timeout under the lock or moves out.

### 6. Merging with a short SHA

**Symptom.** `merge_pull_request` rejected the merge with an unclear error.

**Lesson.** `expectedHeadSha` must be the full 40-character SHA from the PR's
head, read fresh after the last push. Never from memory or an abbreviated
log.

### 7. Resolver churn after every merge

**Symptom.** Each squash merge made the next two PRs conflict in the same
files, and each resolver run cost a session.

**Lesson.** Merge in dependency order (splits first, then the PRs that touch
the split files), and run `git merge-tree --write-tree` on every open PR
right after a merge so conflicts are resolved in one batch, not one at a
time as they are discovered.

### 8. Cost: a session that inherited the lead's model

**Symptom.** A build session ran on the lead's model (expensive) because
`create_session` was called without `model`.

**Lesson.** Every `create_session` names its model. Sonnet 5.5 builds, Opus
5.5 only for reviews and concurrency or data-integrity work. The session
list is checked for the model column before the session is left to run.

## Checklist before any push from a fix session

```
python -m pytest -q tests/test_<area>.py
python -m pytest -q tests/test_static_analysis.py tests/test_api_permissions.py tests/test_split_guards.py tests/test_file_organization.py
cd frontend && npx tsc --noEmit && npx vitest run    # when frontend files changed
```

Then re-read the diff for: a frozen file that grew, a comment with a PR or
step id, a route without a permission, an HTTP call without `timeout=`, an
error message that could carry a path or key, a lock holding a blocking call.

### 9. Review sessions stalled on "open the issue?"

**Symptom.** Five whole-codebase review sessions finished their reading and
then sat in `need_input`, asking whether to open the GitHub issue. Their
drafts lived only in their containers; archiving them lost the work.

**Lesson.** A session that must post to GitHub is created with
`permission_mode: acceptEdits` and its brief says the post is pre-approved.
Before archiving a `need_input` session, read its last message: if it holds
a deliverable, relaunch with the fix to the brief first.

### 10. Findings that lived only in a session's chat

**Symptom.** Fix and review sessions reported "noted but not fixed" items,
unrelated test failures and open questions in their closing chat message.
That message is visible only in the session's own transcript. One review
session wrote its whole report to its container's scratchpad and never
posted it.

**Lesson.** The PR body, a PR comment or an issue is the only durable place.
Every brief ends with: "Anything found but not fixed, unsure, or seen
failing goes in the PR body under 'Found but not fixed', never only in
chat." When a session closes, read its PR body for that section before
archiving; if the section is missing and the summary mentions something,
relaunch a short session to add it.
