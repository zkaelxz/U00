# Agent role guides

Last reviewed: 2026-11-27

Short briefs for the three jobs a session can have, so a new session needs no re-briefing. They point at the rules in `CLAUDE.md`, `docs/testing-and-ci.md` and `docs/engineering-standards.md` instead of restating them; if a rule changes there, change it there, not here. "The coordinator" is the session that sequences the work and talks to the owner; "the owner" is the person who decides what merges.

A session that writes code never reviews its own change.

## Coder

1. Read `CLAUDE.md`, then the area's doc and tests (`AGENTS.md`, "Where to look").
2. More than a small fix: write a short plan first (scope, files, tests) and keep to it. A change that outgrows the plan goes back to the coordinator.
3. Stay in scope: no new files, settings or abstractions the task didn't ask for. Pre-existing problems go in the summary, not the diff.
4. Test while iterating with the area's test file; before pushing run the command `python tools/check_change.py` prints for your diff. CI runs the full suite; don't.
5. When a coordinator session is running the work, check in with it before opening or merging a PR, before moving, renaming or deleting files or folders, and before any change on a PC, and tell it about anything that could affect another session's work. `python tools/pr_lane.py` (quoted in the merge report) does not replace the check-in.
6. Every PR gets an independent review (below) before the owner is shown it. Fix what it finds, have it re-reviewed, then re-run the area's tests and the guard set.
7. Show the owner what the change does and how it turned out. Merging follows `CLAUDE.md`, "Merging" and `docs/testing-and-ci.md`, "Merge flow"; where the project's instructions ask for the owner's OK on each fix, wait for it.
8. No comment, review or reply is posted on a GitHub PR or issue without the owner's OK; send the exact text to the coordinator first.
9. Privacy: the repo is public; see `docs/testing-and-ci.md`, "Privacy check". Made-up names and placeholders only, and commit under the owner's GitHub no-reply address.

## Reviewer

A fresh session (a subagent or `/code-review`) that did not write the change. The review policy is `docs/engineering-standards.md`, section 3; the existing roles in `.claude/agents/` (for example `security-reviewer`, which runs on Opus) apply it.

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

- Update the matching guide, `CLAUDE.md` and `AGENTS.md` in the same change whenever a rule changes (an owner decision, a merged rule change, a repeated mistake), and bump "Last reviewed".
- Any project notes kept outside the repo follow the same rule: only what is current (rules, preferences, open work, key facts); finished history moves to a dated archive.
