---
name: merge-slice
description: Merge a finished migration branch into baihe-subtitler using the repo's recipe (base merge, append-only conflict helpers, import and permission checks, focused tests, PR, squash-merge). Use when a slice, service or React branch is ready to land.
---

# /merge-slice `<branch> [<service_stem> <router_stem>]`

Lands one finished branch on `baihe-subtitler`. Work on one branch at a time: each merge changes the base for the next one.

## 0. Preconditions
- Merging is allowed: the lead session merges once CI is green (root `CLAUDE.md`, "How to work"). Without that authority, stop and ask.
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
- New service + router slice: `python scripts/migration/resolve_slice.py <service_stem> <router_stem>`. It handles `api/server.py`, `FILE_ORGANIZATION.md`, and keeps both sides of any conflict in the `api/schemas/<module>.py` files (a branch that still edits the old single `api/schemas.py` has to be moved into the package by hand).
- Append-only conflicts, with no new router: `python scripts/migration/keepboth.py <files>`.
- **Don't use keep-both when a branch edits an existing class or function in place.** This is most common in `api/schemas/*.py` (see "Known flags" in `docs/archive/migration-handoff.md`). Resolve those by hand: take the base, then apply the branch's edit.
- Code conflicts in import blocks: keep both sides, then remove duplicate imports.
- If both sides changed the same logic and choosing one side would lose behaviour, stop and report.
- Check that no conflict markers are left: `git diff --check` and `grep -rn '^<<<<<<<\|^>>>>>>>' -- . ':!*.md'`.

## 3. Checks (all must pass)
```
python -c "import api.server"
python -m pytest -q -p no:cacheprovider tests/test_api_permissions.py tests/test_static_analysis.py tests/test_service_invariants.py <the branch's changed test files>
```
- Get the changed test files from `git diff --name-only origin/baihe-subtitler -- tests/`.
- If `frontend/` changed, run `cd frontend && npx tsc --noEmit && npx vitest run`.
- If `api/auth.py`, `api/server.py`, `background_jobs.py` or `db.py` changed, or the branch touches more than about 10 files, run the full suite instead: `python run_tests.py`.
- Run anything that takes more than about 100 s in the background. Poll it with `kill -0 <pid>` (never `pgrep -f`).
- A new route must declare exactly one of `require_permission(...)`, `public_route()` or `local_only()`, and must have a row in the route table in `docs/remote-access-decision.md`. `tests/test_api_permissions.py` enforces both.
- A test that fails is real. Never skip, xfail or weaken a test to get the merge through.

## 4. Land it
```
git commit --no-edit            # only if the merge needed manual resolution
git push origin HEAD:<branch>
```
- Open a PR into `baihe-subtitler` with the GitHub MCP tools. The body covers: what changed in plain words, the tests run and their counts, and known gaps and doubts from the branch report.
- End the PR body with the session's attribution lines.
- Squash-merge it once CI is green. If Actions minutes have run out, the full local suite is the gate.
- Remove the scratch worktree: `git worktree remove <path>`.

## 5. After merging
- If the branch added a service or route, check that `FILE_ORGANIZATION.md` has its entries.
- Report: the PR number, the tests and counts, and follow-ups (gaps the branch listed, and routes still to build).
