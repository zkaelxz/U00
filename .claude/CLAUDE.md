# Claude Code delegation policy

These instructions complement the root `CLAUDE.md`; the root file and the active roadmap remain authoritative when instructions overlap.

## Lead and delegation

- The main Claude session is the lead: it owns user communication, scope, task decomposition, integration, final verification, and the workflow required by the root `CLAUDE.md`.
- At the start of a substantial task, inspect the root instructions, `FILE_ORGANIZATION.md`, the relevant roadmap entry, and `git status`. Treat staged, unstaged, and untracked work as user-owned unless the user explicitly assigns it.
- Delegate meaningful, independent research, planning, review, and QA to the matching project subagent. Start independent read-only tasks concurrently in the background when useful. Keep small tasks and tightly coupled work in the lead session.
- Subagents do not inherit the conversation history. Give each a concise task packet: goal, roadmap step, current branch/state, relevant files, constraints, expected result, and whether it may edit. Require file/line evidence for findings.
- Read-only agents get no shell access. The lead hands them what they cannot fetch themselves:
  - `code-reviewer`: the diff, its base commit, and the changed-file list — inline for a small patch, or as a saved patch file for a large one.
  - `roadmap-planner`: the current roadmap text, or an exact path it can read. If neither is available, report that instead of guessing step IDs, status, or decisions.

## Parallel changes and safety

- Parallelize code edits only when file ownership is explicit, paths do not overlap, and prerequisites are already available. Assign one writer per file. The lead must not edit a worker-owned file until that worker finishes.
- Do not run tests against files that are actively changing unless the test is an independent baseline/reproduction. Run integration tests after the writers finish and changes are integrated.
- Do not create an isolated worktree unless its base is confirmed to be the active step branch and commit. If that cannot be verified, use one writer at a time.
- Review all worker changes and outputs; delegation is not approval to widen scope, stage unrelated files, commit, push, merge, or bypass user confirmation. Follow the root `CLAUDE.md` for the step's actual Git workflow.
- Prefer a few useful concurrent agents over fan-out for its own sake. Do not spawn nested agents unless they materially reduce work and the lead can track their ownership and completion.

## Reporting

Track each delegated task as `agent | scope/owned paths | status | blocker`. Summarize completed results with evidence, tests run and outcomes, unresolved questions, and any files changed. Distinguish confirmed findings from hypotheses.
