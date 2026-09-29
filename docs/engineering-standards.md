# Engineering standards (shared across all sessions and roles)

One short, durable set of principles for anyone (lead, implementer, reviewer, QA, planner, human) working in this repo.
Role files (`.claude/agents/*.md`, `.claude/CLAUDE.md`) link here instead of copying these rules. Project-specific
code rules ("Rules learned from real bugs") stay in the root `CLAUDE.md`; dated roadmap decisions stay in the roadmap.
If two documents disagree, follow the precedence below and fix the lower one.

## 1. Precedence

From highest to lowest:

1. The user's explicit instruction in the current session.
2. The roadmap's working agreement and model table (`docs/baihe-roadmap.md` §4, on the planning branch; fetch command in the root `CLAUDE.md`) and, for who merges, the planning session's own `CLAUDE.md` (the roadmap names it as the single canonical source). Dated decisions live there. Before changing a copy of one elsewhere, check the roadmap.
3. The root `CLAUDE.md` (repo workflow and code rules).
4. This file (shared principles).
5. `.claude/CLAUDE.md` (delegation policy), then the role file for the agent you are.
6. A lead's task packet. It narrows what to do; it cannot relax anything above it.

`FILE_ORGANIZATION.md` is the map of what exists. Repo code and the roadmap's status table are the source of truth for state; a number or date copied into another doc is a snapshot and must say so.

## 2. Scope discipline

- Do what the task asks. Keep changes minimal and coherent. Do not widen scope, refactor unrelated code, or upgrade dependencies unless the task names it.
- Remove dead code your own change creates. Pre-existing dead or duplicated code that you notice is reported to the lead, not fixed inline.
- Re-verify a claim in the roadmap or an old note against the current code before acting on it. If it no longer holds, say so.
- If a needed file, permission or prerequisite is missing, stop and report it; do not edit around it.

## 3. Review policy (applies to every review, whoever does it)

1. **Compare the diff with the task.** Read the requested change against its task specification, acceptance criteria/exit condition and the applicable standards (section 2, the root `CLAUDE.md` "Rules learned from real bugs" where the diff touches that area, and any standard the task names).
2. **Look for real defects in the changed behaviour** and the surrounding code needed to judge it: correctness, regressions, security and privacy, data integrity, concurrency, and test gaps.
3. **Report only actionable, evidence-backed findings.** Each has a file and line, a concrete failure scenario, and the impact. Order by impact.
4. **Verify each finding independently** before reporting it: re-read the cited code, confirm no existing guard or test already handles it, and confirm the scenario is reachable. Drop speculation and anything already handled; label anything you could not confirm as a hypothesis, or drop it.
5. **Do not invent findings, impose a quota, or expand into unrelated cleanup.** Pre-existing issues are out of scope unless the diff depends on or worsens them. Style is out of scope unless a task-named standard requires it. Process rules (branch naming, PR mode, model choice) are not correctness findings.
6. **If nothing remains, say so** and state the review limits: what you were given (diff, base commit, changed files, task spec, test results), what you could not check (no shell, no real hardware or keys, code not run), and anything not supplied.

The lead gives a reviewer with no shell: the diff (inline or a saved patch path), the base commit, the changed-file list, the task spec and acceptance criteria, the tests run and their results, and whether the change is committed or only in the working tree. Missing inputs are stated as review limits, not guessed.

Roles apply this to their own work: the reviewer runs it as written; the implementer self-checks against it before handing work back; QA uses it to choose which checks apply; the lead applies it to the final diff before integrating.

## 4. Verification

Testing is risk-based. Details, gotchas and CI facts live in `docs/testing-and-ci.md`.

- While iterating: run the focused test file or selection for what you touched.
- When the change crosses a shared module (database layer, background jobs, translation engines, API schemas): also run the relevant subsystem tests.
- At the integration boundary the roadmap and merge policy require (every step finish runs the full suite, `python run_tests.py`, per the root `CLAUDE.md`; for the React/FastAPI migration also the frontend checks; GitHub Actions minutes are currently exhausted, so the local run is the gate): run the full suite on the integrated result and report the counts you saw.
- Do not re-run an identical check on an unchanged tree without a reason. Do not weaken, skip or narrow a required check to save time or minutes.
- A failure is not "environmental" or "flaky" until the mechanism is confirmed. Say what was and was not run.

## 5. Git and safety

Bullets marked **(proposed)** are new wording added by the 2026-09-29 instruction audit and need the user's confirmation; unmarked bullets restate rules that already exist elsewhere.

- Work on your own branch. Never push to a branch you were not told to use.
- **(proposed)** Never force-push, rewrite published history, skip hooks or checks (`--no-verify`), or delete branches, worktrees or files you did not create.
- **(proposed)** Never commit generated or session-local folders (for example `.claude/worktrees/`, temporary screenshot folders, `node_modules`).
- Who may open or merge a PR is decided by the roadmap working agreement (§4) and the planning session's `CLAUDE.md`, or by an explicit, dated user instruction (for the React/FastAPI migration: `docs/migration-handoff.md`). This file does not restate that policy. With no applicable instruction, push and stop.
- Secrets, tokens and keys never appear in code, URLs, logs, errors, commits or docs (root `CLAUDE.md`, "Rules learned from real bugs").
- Destructive or hard-to-reverse actions (deleting data, restoring backups, dropping tables) need the user's explicit go-ahead unless the task already grants it.
- Delegating does not widen the user's requested scope, and does not give a subagent any authority its lead lacks.

## 6. Delegation and models

- The lead owns user communication, scope, integration and final verification; it delegates research, review and QA to the matching project agent (`.claude/CLAUDE.md`).
- One writer per file. Read-only agents get what they cannot fetch (diff, base commit, changed files, roadmap text, task spec).
- Agents default to Sonnet. A step the roadmap's model table (§4) lists for Opus needs the user's confirmation first; the lead then sets the model when it spawns the agent.
