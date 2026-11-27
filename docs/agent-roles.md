# Agent role guides

Last reviewed: 2026-11-27

Short briefs for the three jobs a session can have, so a new session needs no re-briefing. They point at the rules in `CLAUDE.md`, `docs/testing-and-ci.md` and `docs/engineering-standards.md` instead of restating them; if a rule changes there, change it there, not here. "The coordinator" is the session that sequences the work and talks to the owner; "the owner" is the person who decides what merges.

A session says which role it has in its first message. A session that writes code never reviews its own change.

## Coder

1. Read `CLAUDE.md`, then the area's doc and tests (`AGENTS.md`, "Where to look").
2. More than a small fix: write a short plan first (scope, files, tests) and keep to it. A change that outgrows the plan goes back to the coordinator.
3. Stay in scope: no new files, settings or abstractions the task didn't ask for. Pre-existing problems go in the summary, not the diff.
4. Test while iterating with the area's test file; before pushing run `python tools/check_change.py`. CI runs the full suite; don't.
5. Check in with the coordinator before: opening or merging a PR, moving, renaming or deleting files or folders, and any change on a PC. Also tell it about anything that could affect another session's work. Run `python tools/pr_lane.py` too; it does not replace the check-in.
6. Ask for an independent review (below) before the owner sees the change. Fix what it finds and have it re-reviewed, then run the tests.
7. Show the owner what the change does and how it turned out, and get their OK before anything merges.
8. Privacy: the repo is public. Made-up names and placeholders only, commit as the GitHub no-reply address.

## Reviewer

A fresh session (a subagent or `/code-review`) that did not write the change.

- Check correctness, scope (nothing beyond the task), tests that can fail, and the privacy rule (real names, nicknames, emails, usernames, PC names, LAN IPs, work titles, in code, tests, docs, commit messages, PR text).
- Check the rules in `CLAUDE.md`, "Rules learned from real bugs". Auth, routes, fetches and secrets: use `security-reviewer`.
- Report only findings you can point at (file and line, with the input that goes wrong). No quota, and no pre-existing issues the diff doesn't touch.
- Fixes go back to the coder and get re-reviewed. The reviewer does not edit the change.

## Test runner (shared test PC)

Runs real-hardware checks that mocked tests can't. It reports results; it does not change the product.

- Use the test copy of the app only, never the owner's live app, unless the owner asks in that session.
- Use the test copy's virtualenv python by its full path. The system launcher python ignores `PYTHONPATH`.
- Installs go into a separate test folder (`pip install --target`) for a comparison only, never into the live environment.
- Deletes, stops, key changes, tags and installs outside the test folder need the owner's typed OK in that session.
- Comparison only: a change that makes a run slower is not shipped. Never lower Whisper or voice-model precision (no int8 or smaller types) to gain speed.
- Pause GPU test runs while the owner is using the app.
- Report numbers (what, how many, how long) without sample names or work titles.

## Keeping these guides current

- Every session reads this file first. A session that learns a rule changed (a decision from the owner, a merged rule change, a repeated mistake) updates the matching guide, `CLAUDE.md` and `AGENTS.md` in the same change, and bumps the "Last reviewed" date.
- The shared project notes follow the same rule: keep them to what is current (rules, preferences, open work, key facts) and move finished history to the dated, append-only archive.
- The daily shared-notes check compares these guides and the notes with recent decisions and flags drift. A guide not reviewed for 30 days is stale until someone re-reads it against the code.
