# Claude Code delegation policy

These instructions complement the root `CLAUDE.md`; the root file and the active roadmap remain authoritative when instructions overlap. Shared principles, precedence, the review policy and the git/safety rules are in `docs/engineering-standards.md`; testing guidance is in `docs/testing-and-ci.md`. This file does not restate them.

## Lead and delegation

- The main Claude session is the lead: it owns user communication, scope, task decomposition, integration, final verification, and the workflow required by the root `CLAUDE.md`.
- At the start of a substantial task, inspect the root instructions, `FILE_ORGANIZATION.md`, the relevant roadmap entry, and `git status`. Treat staged, unstaged, and untracked work as user-owned unless the user explicitly assigns it.
- Delegate meaningful, independent research, planning, review, and QA to the matching project subagent. Start independent read-only tasks concurrently in the background when useful. Keep small tasks and tightly coupled work in the lead session.
- Subagents do not inherit the conversation history. Give each a concise task packet: goal, roadmap step, current branch/state, relevant files, constraints, expected result, and whether it may edit. Require file/line evidence for findings.
- Read-only agents get no shell access. The lead hands them what they cannot fetch themselves:
  - `ux-designer`: the assigned screens, the guideline sections in play, and phone (~390×844, touch) plus desktop screenshot paths captured by the lead or `qa-runner`. It cannot run the app; without screenshots its spec must say which viewports were only reasoned about.
  - `code-reviewer`: the diff, its base commit, and the changed-file list — inline for a small patch, or as a saved patch file for a large one — plus the task specification and acceptance criteria, the tests run and their results, and whether the change is committed or only in the working tree (see the review policy, `docs/engineering-standards.md` §3).
  - `roadmap-planner`: the current roadmap text, or an exact path it can read. If neither is available, report that instead of guessing step IDs, status, or decisions.

## Parallel changes and safety

- Parallelize code edits only when file ownership is explicit, paths do not overlap, and prerequisites are already available. Assign one writer per file. The lead must not edit a worker-owned file until that worker finishes.
- Do not run tests against files that are actively changing unless the test is an independent baseline/reproduction. Run integration tests after the writers finish and changes are integrated.
- Do not create an isolated worktree unless its base is confirmed to be the active step branch and commit. If that cannot be verified, use one writer at a time.
- Review all worker changes and outputs; delegation is not approval to widen scope, stage unrelated files, commit, push, merge, or bypass user confirmation. Follow the root `CLAUDE.md` for the step's actual Git workflow.
- Prefer a few useful concurrent agents over fan-out for its own sake. Do not spawn nested agents unless they materially reduce work and the lead can track their ownership and completion.

## Reporting

Track each delegated task as `agent | scope/owned paths | status | blocker`. Summarize completed results with evidence, tests run and outcomes, unresolved questions, and any files changed. Distinguish confirmed findings from hypotheses.

## Specialized agents (added 2026-09-29)

- `api-slice-builder`: one FastAPI slice in the repo pattern. The task packet gives the service to expose, the routes wanted and the permission for each route (from `docs/remote-access-decision.md`), plus the branch name.
- `react-page-builder`: one React page or panel against existing routes. The task packet gives the UX spec path if there is one, the routes and the screenshot output path.
- `security-reviewer`: read-only. Needs the same inputs as `code-reviewer`. Run it before merging anything that adds routes or touches `api/auth.py`, URL fetching, file serving or keys.
- `merge-integrator`: lands branches with `/merge-slice`. The task packet must say that merging is authorized, and give the branch order and each branch's stems and known doubts.
- `security-auditor`: read-only audit of the whole system against `docs/remote-access-decision.md`. It is required before anyone other than the user gets access, and should be rerun after large auth or routing changes. `security-reviewer` covers a single diff.
- `parity-auditor`: read-only drift report across the CLI, services and API, and React (and Streamlit, for reference). Run it before Streamlit deletions and after a batch of slices.
- `bug-investigator`: reproduces a bug as an `xfail(strict=True)` test and gives the root cause at file:line. It edits `tests/` only and never fixes the bug; hand the fix to `implementer`.
- `test-author`: writes tests only, independent of the implementer. It never changes product code, and a behaviour that doesn't match the spec becomes an `xfail(strict=True)` test plus a bug report.
- `docs-steward`: brings `FILE_ORGANIZATION.md`, the migration docs' status lines and the route table up to date with the code, citing a PR or commit for each change. It edits docs only and reports code-side gaps.
- `source-vetter`: vets a content source's ToS, robots.txt and technical posture with primary sources only, using web tools. It never builds adapters.

Hook: `.claude/hooks/file-organization-check.py` (PostToolUse on Write) warns when a new top-level, `tabs/`, `services/` or `api/routers/` module isn't listed in `FILE_ORGANIZATION.md`. It only warns and never blocks.
