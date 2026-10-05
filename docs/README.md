# Documentation index

A short map of what lives in `docs/` and where the rest of the project's
planning material actually is. For a detailed, per-file description and
maintenance-role tag (source of truth, maintained status tracking,
reference, design proposal, audit record, superseded handoff), see
`FILE_ORGANIZATION.md`'s own `docs/` listing at the repo root — that file
is the authoritative, kept-current map; this page is just a shorter
front door plus the roadmap pointer, since the roadmap itself isn't a
file in this directory.

## Start here

Current state, what's in flight and what's next: **`STATUS.md`**. Repo rules: the root `CLAUDE.md`.
Shared review policy: `engineering-standards.md`; testing and CI: `testing-and-ci.md`.
The numbered roadmap (`docs/baihe-roadmap.md`) lives on the planning branch
`claude/baihe-subtitle-planning-95qyvq`, not in this repo.

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
- **`react-ui-guidelines.md`** — concise-UI rules for the React app.
- **`frontend-design-system.md`** — map of how the React frontend is built: layout, routing, tokens and themes, shared components, phone rules, testing and an add-a-screen checklist.
- **`engineering-standards.md`** — shared principles: precedence, scope,
  review policy, verification, git/safety.
- **`testing-and-ci.md`** — test commands, gotchas, current merge gate,
  CI-minutes notes.
- **`household-access.md`** — step-by-step guide to reach Baihe from
  household devices through Caddy (`deploy/caddy/Caddyfile.template`).
- **`windows-installer-design.md`** — the Windows installer/uninstaller:
  Step 80's design, as built in Step 80b (`installer/`).
- **`technical-notes.md`** — an engineering changelog of real bugs found
  and how they were fixed, kept separate from the main `README.md` so
  that stays focused on using the app.
- **`RELEASE.md`** — building the Windows installer and the frontend release zip.
- **`runbook.md`** — one-page maintainer steps: installer lock, tests, restore, certificate, benchmark.
- **`remote-access-decision.md`** — the remote-access design as built, including the route table `tests/test_api_permissions.py` enforces.
- **`asr-experiments.md`** — the off-by-default Qwen3-ASR batching and MOSS-Transcribe-Diarize options.
- **`sources-credential-audit.md`** — how the source adapters handle credentials and cookies.
- **`design/`** and **`specs/`** — the UI refresh spec (with before/after screenshots) and earlier API/UX/Step 141 proposals; the specs were written against the removed Streamlit tabs.
- **`STATUS.md`** — current state, in-flight work and what's next.
- **`archive/`** — historical records kept for reference, not sources of
  truth: the migration review, handoff and React/FastAPI phase log, the
  old roadmap master tracker, the Streamlit test triage, the superseded
  remote-access and browser-extension handoffs, the installer research
  notes, the Step 19 click-through audit, and the unbuilt Jellyfin/Plex metadata
  design.

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
