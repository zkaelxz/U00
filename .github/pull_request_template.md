<!--
Fill in each heading; delete this comment. CLAUDE.md, "How to work", says what a PR needs.
-->

## What changed

Plain words: what was fixed or added, and what the user will notice in the app. Not a copy of the commit log.
Name the task or finding this does, and the branch.

## Testing

- The commands you ran and their pass counts: the area's tests, the guard set (`python tools/check_change.py` prints the
  command for your diff) and, if `frontend/` changed, `npx tsc -b`, `npx vitest run` and `npm run lint`.
- Each removed or rewritten test and which of CLAUDE.md's reasons applies.
- Anything a mocked test can't cover (a real GPU, model, key, site or installer), named rather than left out.

## Docs

The user guide line updated in this PR (name the file), or "no user-visible change".

## Merge lane

The output of `python tools/pr_lane.py`, and whether this is a risky PR that waits for the owner's "Merge NNNN"
(`docs/testing-and-ci.md`, "Merge flow").

## Anything uncertain or out of scope

What you weren't sure about, left out on purpose, and follow-ups.
