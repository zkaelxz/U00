# U00 — rules for AI sessions working in this repo

This repo is the **planning session** for the Baihe Subtitler project. It is
docs-only — no application code lives here. **The actual app is not a
separate repo — it's the `baihe-subtitler` branch of this same `zkaelxz/U00`
repo.** (This was wrong in an earlier version of this file and caused a real
mistake: don't try `add_repo`/a separate clone for it — this session already
has full GitHub API access to it, same as this planning branch, because it's
the same repo.) Separate **implementing sessions** work their own per-step
branches (`step-<id>-<short-name>`, or a session's own auto-assigned branch
name) off `baihe-subtitler`, open PRs into it, and this planning session
reviews and merges them — see "What this session does and doesn't do" below.

Read `docs/baihe-roadmap.md` before doing anything else in this repo. It is
the single source of truth: every build step, its exit condition, the PR
flow, the model-recommendation table, and the sourcing record for every
external project checked so far.

## What this session does and doesn't do

- **Write and revise `docs/baihe-roadmap.md`.** That's the job. Research,
  gap-audit, resequence, and record decisions here.
- **Never write application code.** If a request implies code changes to
  `baihe-subtitler` itself, that belongs in the implementing session — write
  the plan/step here, then say so, don't reach across.
- **This session DOES review, test, and merge implementing-session PRs** —
  that's not "writing application code," it's the review gate §5 describes.
  For every PR: `git fetch`/clone for real (never trust a report or commit
  message alone), read the actual diff, run the branch's own touched tests
  yourself, and only then merge via the GitHub API once CI is green and the
  review holds up. Steps 1e–10 run in autonomous mode (the implementing
  session builds, tests, opens *and merges* its own PR, no gate) — this is
  exactly why an ad hoc QC pass across that range once found two severe,
  unreviewed bugs. Steps 11+ run gated (implementing session builds, tests,
  pushes, stops, and waits for "create a PR for this step" — it opens the PR
  but does not merge; this session reviews and merges).
- **Verify a PR's real number via the GitHub API before citing it anywhere**
  — an implementing session's own self-reported PR number has been wrong
  more than once (reported #47/#48 when the real, already-merged PRs were
  #45/#46; reported "#55" for one step when #55 actually belonged to a
  different one, and the reporting branch had no PR open at all). Never
  write a PR number into the roadmap without having fetched that exact PR
  and confirmed its head branch/title match.
- **Never assume roadmap/branch/PR state from memory.** Always `git fetch`
  and check real state before updating the status table or the "NEXT"
  pointer at the top of the roadmap. This caught a real staleness bug once
  already (Steps 1c-pre/1c showing "Not started" when they were actually
  reviewed and PR-pending) — don't reintroduce it.
- **`git merge-base --is-ancestor <branch> baihe-subtitler` alone is NOT
  enough to tell if a branch's work is merged — it misses squash merges.**
  GitHub's "Squash and merge" creates one new commit (with `(#N)` appended
  to its subject) instead of making the original branch commits ancestors
  of the base — so a squash-merged branch will falsely read "not merged" on
  a bare ancestor check. This caused a real, serious multi-step staleness
  bug once already: seven §4 rows (Steps 1f, 6h, 8b, 9h, 9i, 9e, 10e) said
  "Not started" for merged work, across several separate sessions, because
  each check trusted an ancestor check or an old note instead of the full
  method below. The reliable way to confirm a PR's real status: `git log
  origin/baihe-subtitler --format="%s" | grep -i "<step name>"` for a name
  search, or `grep -F "(#<N>)"` (the parens matter — a bare number search
  false-matches unrelated PR numbers, dates, and line counts) for a PR
  number — either catches squash merges via their `(#N)` suffix. Or, for a
  batch check, `git log origin/baihe-subtitler --merges --format="%s"`
  (regular merges) *combined with* a grep for `(#N)` suffixes in the full,
  non-merges log (squash merges), not either alone. When in doubt, use the
  GitHub API's `pull_request_read` `get` method on the specific PR number
  and read its real `merged` field — never the `list_pull_requests` list
  response's `merged` field, which has been observed to read `false` for
  PRs that are, confirmed directly, actually merged.
- **Keep the roadmap's three tracking structures in sync**: the `### Step`
  headers, the §4 status table, and the §2 manual-check table. Their counts
  must always match.
- **Handoff prompts to the implementing session should be one line**, e.g.
  "Build Step 4k from the roadmap." Its own `docs/ai-setup/CLAUDE.md` (copied
  into its repo root) already tells it to fetch this planning branch, read
  the roadmap, find the matching step, and follow the gated-mode build/
  test/push/stop workflow — repeating that boilerplate in every handoff is
  redundant. Only add extra detail beyond the one-liner when the step name
  alone is genuinely ambiguous, or the user asks for more.

## Research discipline

- **Read the actual code, not just the README.** A README describes intent;
  the repo's real files, model cards, and license files are what's true.
  Fetch raw source, check the actual license file, verify the file tree
  before trusting a repo's claims. This was learned from direct correction
  twice in this project and applies to every subsequent research pass.
- **Verify a repo is real before citing it.** Check star/fork ratio, commit
  history, and that the file tree actually contains what the README claims.
  One candidate (`FoundMantisWay/translator-app-enhancer`) turned out to be
  a likely star-farmed lure repo with no source code — flag and skip
  anything that pattern-matches.
- **State what was checked vs. not checked, explicitly**, per project, in
  §6 "Sources" — including "checked and not added, with reasons" for every
  rejected candidate. Don't silently drop a project from consideration.
- **When the user hands over new links, or when it seems warranted, proactively
  suggest browsing `github.com/topics/<tag>`** or a targeted
  "topic + github + year" search for inspiration — don't wait for the user
  to supply links every time.

## License rule

- GPL-3.0/AGPL-3.0 code reuse is legitimately unlocked now that this repo
  (and its `baihe-subtitler` branch) is private (GPL's copyleft trigger is
  distribution; AGPL adds a network-interaction trigger — neither applies
  to genuinely private, undistributed, single-user use). **But check for
  redundancy before adopting anything under these licenses**: if a
  permissively-licensed source already in the roadmap gets equivalent or
  better capability, taking on copyleft code for no incremental gain isn't
  worth it — this was the actual outcome the one time this was checked in
  full (manga-image-translator, pyvideotrans, and six others — none ended
  up adopted, each for an independent reason beyond license).
- If the repo's visibility ever changes back to public, re-apply the
  original public-repo rule: no GPL/AGPL code, ideas only.

## Product preferences (established through the conversation, don't relitigate)

- **Fewer options over more.** When multiple engines/tools cover the same
  capability, the user wants one or two well-chosen defaults, not an
  exhaustive menu (explicitly dropped VoxCPM once OmniVoice + GPT-SoVITS
  were in: "I don't need so many options").
- **Novel/audiobook narration TTS is a higher priority than video/audio
  dubbing.**
- **Quality bar: as close to ElevenLabs voice quality as possible, but
  free/local.** This is the standard to measure new TTS candidates against.
- **Branches stay open until the whole project is finished** — don't
  suggest auto-deleting head branches after merge.
- **Destructive or data-affecting actions need a separate, explicit
  confirmation, defaulting to No** — this pattern was set by the
  Uninstaller design (never touch `library/` without asking separately from
  the main uninstall confirmation) and should be the default shape for any
  future guardrail like it.

## Where things are

- `docs/baihe-roadmap.md` — the roadmap itself.
- `docs/phase1-architecture.md` — the earlier architecture doc the roadmap
  was originally gap-audited against.
- `docs/ai-setup/` — staging area for files (`CLAUDE.md`,
  `.claude/hooks/session-start.sh`, `.claude/settings.json`,
  `.github/pull_request_template.md`) that get copied verbatim into
  `baihe-subtitler`'s root by the implementing session as part of Step
  1c-pre. If you edit one, the copy already pushed to a
  `step-1c-pre-*`-style branch predates the edit and needs picking up
  separately — check with the user rather than assuming it's already in
  sync.
