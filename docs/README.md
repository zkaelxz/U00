# Documentation index

A short map of what lives in `docs/` and where the rest of the project's
planning material actually is. For a detailed, per-file description and
maintenance-role tag (source of truth, maintained status tracking,
reference, design proposal, audit record, superseded handoff), see
`FILE_ORGANIZATION.md`'s own `docs/` listing at the repo root — that file
is the authoritative, kept-current map; this page is just a shorter
front door plus the roadmap pointer, since the roadmap itself isn't a
file in this directory.

## Start here: the roadmap

**The numbered, build-order roadmap (`docs/baihe-roadmap.md`) does not
live in this repo.** It's tracked on a separate planning branch and
fetched read-only; the fetch command is in the root `CLAUDE.md`.
Shared rules and precedence: `engineering-standards.md`; testing and CI:
`testing-and-ci.md`.

That file is the actual source of truth for what's merged, what's
running, what's blocked, and what's deferred — its own `NEXT` pointer at
the top and its §4 status table are kept current by the planning session
and by every implementing session as they finish a step. **This index
deliberately doesn't duplicate specific counts (how many steps are
merged, how many need a manual check, etc.)** — those numbers are only
accurate at the moment someone re-verifies every row against real git
state (`git merge-base --is-ancestor <sha> origin/baihe-subtitler`, not a
roadmap-table cell taken on faith), and a copy here would just be another
place for that count to go stale. Ask for a fresh read of the roadmap
itself rather than trusting a number written down anywhere else,
including in this file.

If you were told to "do Step X," that's Step X in the roadmap doc above.
Roadmap sections worth knowing about by number: §2 is the build order,
§4 is the merged/running/blocked status table plus decision-needed
items, §5 explains how a step gets reviewed, §8 is a permanent,
never-delete record of an external-tool review (13 third-party repos
checked for adoptable techniques) folded in directly rather than kept
only on a side branch.

## What's in this directory

- **`adding-source.md`** — how to write a new `sources/adapters/*.py`
  adapter, including the pre-coding site checklist.
- **`content-sources.md`** — every source the Sources tab can reach and
  what was actually checked; updated per adapter.
- **`known-working-sources.md`** — a short "can I point the app at this
  site" status board, companion to `content-sources.md`'s full detail.
- **`browser-extension.md`** — the Translate-the-page-you're-reading
  feature (Step 34/34b/96): what it does, what was verified against a
  real site.
- **`handoff-browser-extension.md`** — that feature's original,
  pre-build reasoning; superseded now that it's built — kept only as
  historical record (its own banner says so and points at
  `browser-extension.md`).
- **`migration-react-fastapi.md`** — the React + FastAPI migration:
  phase tables (backend phases 0-10 plus the React frontend), what
  differs from the original Python-only design, and the original
  foundation write-up kept as a labelled historical section. Merged into
  `baihe-subtitler`.
- **`migration-review.md`** — the whole-app migration review: per-tab/
  stage plan, invariants, sequence, decisions.
- **`migration-handoff.md`** — durable migration status, recipe and
  queue for the next session.
- **`migration-frontend-plan.md`** — the React frontend slice plan.
- **`react-ui-guidelines.md`** — concise-UI rules for the React app and a per-screen change list.
- **`baihe-roadmap-master.md`** — master index: status snapshot, bug
  tracker, to-do queue, deferred/review-later steps.
- **`engineering-standards.md`** — shared principles: precedence, scope,
  review policy, verification, git/safety.
- **`testing-and-ci.md`** — test commands, gotchas, current merge gate,
  CI-minutes notes.
- **`remote-access-design.md`** — M8-H (Tailscale Serve access) design
  proposal; nothing built.
- **`windows-installer-design.md`** — Step 80's installer/uninstaller
  architecture; design only, nothing built.
- **`windows-installer-research-notes.md`** — follow-up research
  stress-testing that design against prior art; discussion only, no
  roadmap step id.
- **`technical-notes.md`** — an engineering changelog of real bugs found
  and how they were fixed, kept separate from the main `README.md` so
  that stays focused on using the app.
- **`ux-click-through-audit.md`** — Step 19's live click-through UX
  audit of every workflow against the roadmap's own spec.
- **`migration-screenshots/`** — before/after screenshots referenced by
  `migration-review.md`.

## `docs/secondary-review-notes.md` — not present here

A second Claude Code session wrote this file during a 2026-09-27
cross-check of a third-party bug audit. Its content (confirmed
real-bug verdicts, decision-needed items, the external-tool review) was
folded into the roadmap directly, and the file was then deleted from
this repo's own history (commit `5b376b7`, "folded into
docs/baihe-roadmap.md"). The user later instructed that it must never be
deleted going forward. Its full original text is still retrievable, and
is **not** to be deleted, from branch `notes/secondary-review-2026-09-27`
at commit `6301ab7` (the last commit before the deletion on that
branch) — that branch's current tip no longer has the file in its
working tree, only that earlier commit does. The roadmap's own §8 is the
durable, actively-read copy of everything that mattered from it; the
branch/commit above is the redundant original, kept per that
instruction, not a second place to look for anything new.

## Is there a broader docs index anywhere else?

No — this file is it. No other `README.md` under `docs/` exists on any
branch checked. If one turns up on another branch, cross-link it here
and in `FILE_ORGANIZATION.md` rather than letting the two drift apart.
