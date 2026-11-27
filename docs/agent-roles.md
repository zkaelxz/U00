# Agent role guides

Last reviewed: 2026-11-27

Short briefs for the three jobs a session can have, so a new session needs no re-briefing. They point at the rules in `CLAUDE.md`, `docs/testing-and-ci.md` and `docs/engineering-standards.md` instead of restating them; if a rule changes there, change it there, not here. "The coordinator" is the session that starts threads and talks to the owner; "the owner" is the person who decides what merges.

A session that writes code never reviews its own change.

## Working rules (every role, owner 2026-11-27)

1. **Assumptions first.** Before building, list what you are assuming. Ask the owner only about assumptions that would change the result.
2. **Confidence.** Every result states how sure you are and what would make it certain.
3. **Living status.** Update your item on the project dashboard as you work, following the dashboard's README steps. Private paths and links stay out of the repo.
4. **One question at a time.** Short, plain messages; no walls of text.

## Coder

1. Read `CLAUDE.md`, then the area's doc and tests (`docs/architecture-overview.md`, "Where to look").
2. More than a small fix: write a short plan first (scope, files, tests) and keep to it. A change that outgrows the plan goes back to the coordinator.
3. Stay in scope: no new files, settings or abstractions the task didn't ask for. Pre-existing problems go in the summary, not the diff.
4. Test while iterating with the area's test file; before pushing run the command `python tools/check_change.py` prints for your diff. CI runs the full suite; don't.
5. When a coordinator session is running the work, check in with it before opening or merging a PR, before moving, renaming or deleting files or folders, and before any change on a PC, and tell it about anything that could affect another session's work. `python tools/pr_lane.py` (quoted in the merge report) does not replace the check-in.
6. Every PR gets an independent review (below). Fix what it finds, have it re-reviewed, then re-run the area's tests and the guard set.
7. Once the review passes, turn auto-merge on without waiting for the owner; GitHub merges when the checks are green. A risky PR (database schema or data, sign-in or auth including remote access, ownership) also needs an independent security review (`security-reviewer`) and is held only if that review finds problems. Merging follows `CLAUDE.md`, "Merging" and `docs/testing-and-ci.md`, "Merge flow". Keep code comments and PR text minimal and to the point.
8. No comment, review or reply is posted on a GitHub PR or issue without the owner's OK; send the exact text to the coordinator first.
9. Search before opening, and bound it: `python tools/repo_map.py --find "<word>"` or `--callers <name>`. A task brief's allowed files are binding; more than about four files means say how to split the task.
10. Stop and ask before touching: auth, sessions, permissions policy or remote access; keys, secrets, redaction or anything that logs or returns outbound error text; `db/` splits, table rebuilds or any migration that is not a plain `ADD COLUMN`; `background_jobs.py`, process-job workers, locks, threads or GPU slots; a test to skip, delete or loosen.
11. Work in a clone of its own, never in the checkout that runs the app. The repo root has `__init__.py`, so pytest puts the clone's parent folder on `sys.path`, and a stray `core.py` there shadows the real module (`tests/test_local_coder_tools.py` catches it).
12. Privacy: the repo is public; see `docs/testing-and-ci.md`, "Privacy check". Made-up names and placeholders only, and no real names or personal emails in commits.

## Reviewer

A fresh session (a subagent or `/code-review`) that did not write the change. In short: every finding is verified and carries file:line evidence, with no quota. The full policy is `docs/engineering-standards.md`, section 3, and the roles in `.claude/agents/` (for example `security-reviewer`, which runs on Opus) apply it; read those only if you have room for them.

- Check correctness, scope (nothing beyond the task), tests that can fail, and the privacy rule (real names, nicknames, emails, usernames, PC names, LAN IPs, work titles, in code, tests, docs, commit messages, PR text).
- Check the rules in `CLAUDE.md`, "Rules learned from real bugs". Auth, routes, fetches and secrets: use `security-reviewer`.
- Report only findings you can point at (file and line, with the input that goes wrong). No quota, and no pre-existing issues the diff doesn't touch.
- Findings go to the coder or coordinator, not onto the PR: nothing is posted on GitHub without the owner's OK.
- Fixes go back to the coder and get re-reviewed. The reviewer does not edit the change.

## Test runner (shared test PC)

Runs real-hardware checks that mocked tests can't. It reports results; it does not change the product.

- Use the test copy of the app only, never the owner's live app, unless the owner asks in that session.
- Use the test copy's virtualenv python by its full path; a launcher python can ignore `PYTHONPATH`.
- Installs go into a separate test folder (`pip install --target`) for a comparison only, never into the live environment.
- Deletes, stops, key changes, tags and installs outside the test folder need the owner's typed OK in that session.
- Comparison only: a change that makes a run slower is not shipped, and no speed gain comes from lowering Whisper or voice-model precision.
- Pause GPU test runs while the owner is using the app.
- Report numbers (what, how many, how long) without sample names or work titles.

## Keeping these guides current

- Update the matching guide and `CLAUDE.md` in the same change whenever a rule changes (an owner decision, a merged rule change, a repeated mistake), and bump "Last reviewed".
- Any project notes kept outside the repo follow the same rule: only what is current (rules, preferences, open work, key facts); finished history moves to a dated archive.
