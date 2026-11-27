---
name: merge-slice
description: Land a finished branch on baihe-subtitler using the repo's recipe (base merge, conflict rules, import and permission checks, focused tests, PR, lane check, auto-merge). Use when a branch is ready to land.
---

# /merge-slice `<branch>`

Lands one finished branch on `baihe-subtitler`. Work on one branch at a time: each merge changes the base for the next one.

## 0. Preconditions
- Merging is allowed only in the thread that owns the PR or in the merge thread started for it, and for a risky PR (schema, sign-in or auth, a real-PC test, release steps) only after the owner has typed "Merge NNNN" for it (root `CLAUDE.md`, "How to work"; `docs/testing-and-ci.md`, "Merge flow"). Without that, stop and ask.
- The branch's own report (from an agent or a person) lists its tests. Read it, and flag any doubts it raised before merging.

## 1. Merge the base into the branch (never rebase, never force-push)
Use a scratch worktree so the main checkout and other agents' worktrees are untouched:
```
git fetch origin baihe-subtitler <branch>
git worktree add <scratch>/merge-<branch> origin/<branch>   # detached HEAD
cd <scratch>/merge-<branch>
git merge --no-edit origin/baihe-subtitler
```

## 2. Resolve conflicts
- Resolve conflicts by hand. Where a branch edits an existing class or function in place, which is most common in `api/schemas/*.py`, take the base, then apply the branch's edit.
- Code conflicts in import blocks: keep both sides, then remove duplicate imports.
- If both sides changed the same logic and choosing one side would lose behaviour, stop and report.
- Check that no conflict markers are left: `git diff --check` and `grep -rn '^<<<<<<<\|^>>>>>>>' -- . ':!*.md'`.

## 3. Checks (all must pass)
```
python -c "import api.server"
python -m pytest -q -p no:cacheprovider tests/test_api_permissions.py tests/test_static_analysis.py tests/test_service_invariants.py <the branch's changed test files>
```
- Get the changed test files from `git diff --name-only origin/baihe-subtitler -- tests/`.
- If `frontend/` changed, run `cd frontend && npx tsc -b && npx vitest run`.
- Before pushing, also run the guard set: `python tools/check_change.py` prints the command for your diff. CI runs the full suite on every PR, so don't run it locally as well.
- Run anything that takes more than about 100 s in the background. Poll it with `kill -0 <pid>` (never `pgrep -f`).
- A new route must declare exactly one of `require_permission(...)`, `public_route()` or `local_only()`, and must have a row in the route table in `docs/route-permissions.md`. `tests/test_api_permissions.py` enforces both.
- A test that fails is real. Never skip, xfail or weaken a test to get the merge through.

## 4. Land it
```
git commit --no-edit            # only if the merge needed manual resolution
git push origin HEAD:<branch>
```
- Open a PR into `baihe-subtitler` with the GitHub MCP tools. The body covers: what changed in plain words, the tests run and their counts, and known gaps and doubts from the branch report.
- End the PR body with the session's attribution lines.
- Once the owner has OK'd the fix, mark it ready, run `python tools/pr_lane.py`, merge the base in again if the lane is overlap, and turn auto-merge (squash) on while fewer than two other PR runs are going; GitHub merges it once `test` and `browser-tests` are green. A risky PR (schema, sign-in or auth, a real-PC test, release steps) waits for the owner's "Merge NNNN". `docs/testing-and-ci.md`, "Merge flow", has the details.
- Remove the scratch worktree: `git worktree remove <path>`.

## 5. After merging
- If the branch added a service or route, check that each new module starts with a one-line docstring.
- Report: the PR number, the tests and counts, and follow-ups (gaps the branch listed, and routes still to build).
