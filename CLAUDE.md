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
  unreviewed bugs. Steps 11+ run gated by default (implementing session
  builds, tests, pushes, stops, and waits for a "check Step X"/roadmap
  handoff — no PR of its own). **Established practice (confirmed repeatedly
  by the user, and explicit as of 2026-09-27 — "auto merge is fine if
  test/review come back clean"): this planning session opens the PR itself
  via the GitHub API once its own independent review and test run are
  clean, and merges it the same turn once CI is also green — no separate
  "should I merge?" go-ahead needed per PR.** This only ever applies after
  the real gate (fetch the branch, read the full diff, run its own tests,
  confirm CI) — a red CI run, a failing local test, or a review finding
  that doesn't hold up still means stop and either fix (per the CI-red
  playbook) or send it back to the implementing session, never merge
  through it. "Clean" is the gate, not the ask.
  **Explicit exceptions, individually designated autonomous at the user's
  request (2026-09-26) for their genuinely low blast-radius: Step 25e (dead
  code only), Step 22b, Step 23c, and Steps 23d–23l (each a new, isolated
  source adapter — a broken one fails safely without touching other data).**
  A step must be explicitly named here (or in the roadmap's own NEXT
  pointer) to run autonomous past Step 10 — gated is the default for
  everything else, especially anything touching character/line/job data
  integrity, where autonomous mode has already caused real, severe bugs
  once. Still verify every autonomous merge same as any other, per this
  file's own rules below — autonomous means no *gate*, not no review.
- **Every step's manual check must name the concrete action, not just say one's needed.** Both the step's own "Exit:" section and its row in the roadmap's §2 manual-check table must spell out exactly what to try (e.g. "paste a transcript, close the tab, reopen once the job would have finished, confirm the lines aren't wiped"), never a vague "manual check: verify it works." This matters more, not less, for autonomous-mode steps — they land without a planning-chat review pass to catch a vague one before merge.
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
- **This planning session can spawn its own implementing sessions directly —
  it doesn't need the user to open a separate chat.** Use
  `mcp__Claude_Code_Remote__create_session` with `source_url` set to this
  repo, `source_revision: "baihe-subtitler"`, and the one-line handoff
  above as `prompt` (the new session inherits the model unless overridden).
  This means several ready-to-send, non-overlapping steps can be dispatched
  in parallel in one turn — don't sit on a step that's ready just because
  the user hasn't separately opened a chat for it; ask before spawning if
  it's unclear whether the user wants it run now, but the mechanism itself
  requires no separate human action. Each spawned session shows up in this
  session's own session list/notifications when it pushes or needs
  attention — treat its pushed branch exactly like any other implementing
  branch under the review-gate rules above.
- **Pass `permission_mode: "auto"` on every `create_session` call for an
  implementing session (confirmed with the user, 2026-09-27).** Without it,
  a spawned session defaults to a more restrictive mode and repeatedly
  prompts the user for approval on ordinary build/test/push actions the
  handoff already authorizes — the user's own directly-created sessions
  already run in `auto` mode, so this just gives a planning-spawned session
  the same standing authorization instead of a stricter default by
  accident. This doesn't widen what the session is allowed to do (still
  bounded by `docs/ai-setup/CLAUDE.md`'s own gated-mode rules) — it only
  stops it from asking permission for what's already authorized. A session
  already running under a stricter mode when this was written can't be
  changed retroactively; this only applies to future spawns.
- **Streamline session/handoff creation — don't add approval round-trips
  the user has already waived (2026-09-27).** Once a step is confirmed
  ready to send (per the roadmap's own NEXT pointer, or an explicit user
  "yes"), just send it — `create_session` for a fresh chat, or
  `create_trigger` with `persistent_session_id` (+ a near-future
  `run_once_at`; direct `SendMessage` to a `session_...` ID doesn't reach
  a cloud session) to resume an existing one from the "Known implementing/
  utility sessions" list below. Don't re-confirm "should I actually send
  this?" once the user has already said which step/session — that's the
  repetitive check they asked to cut. Reserve an actual pause for the
  things that genuinely need it: which of several *mutually exclusive*
  steps to send, a fresh finding the user hasn't seen yet, or a tool
  call that comes back denied (don't silently retry the identical call —
  say so and ask once, then act on the answer).
- **"Build Queue" chart — a reusable good practice, kept fresh on request.**
  When several steps are in flight across parallel implementing chats, a
  published Artifact chart (three columns: **Assigned** — handed off,
  grouped by autonomous batch vs. individually-gated one-liners, each card
  naming its real file footprint and any known overlap risk; **Ready
  now** — genuinely startable, not yet handed off; **Blocked/later** —
  with the *real* reason, sequential dependency or a soft dependency on
  another step's own hook, never just "not started") beats a status
  message, because it's the one artifact that visibly answers "what's
  moving, what's next, what's stuck, and why" at a glance. Keep it
  updated by republishing the same file path/URL (never a new artifact)
  whenever a recheck finds real state changed — a no-op recheck doesn't
  need a republish. This pattern (chart the queue, verify real state
  before updating it, name the actual blocking reason rather than a bare
  status) generalizes past this project to any multi-agent build
  coordinating several parallel or sequential workstreams.

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

## Known implementing/utility sessions (not named by step number)

Most implementing sessions are titled by step ("Step 25w", "Step 25v roadmap
build", etc.) and are self-explanatory. A handful of long-running sessions
predate that convention or do cross-cutting work instead of one roadmap step,
so they don't carry a step name — recorded here so a future planning session
recognizes them instead of re-discovering them via `list_sessions` each time:

- **"Multilingual VOD transcription workstation"**
  (`session_014zMSq3KbwPNoU1J2KseLrU`) — **the big, original implementing
  chat.** Running since 2026-09-22; has built and merged the large majority
  of this project's steps and PRs. Treat this as the default target for a
  new step handoff when the user wants it to go to "the existing chat"
  rather than a fresh one, unless they say otherwise.
- **"Source verification"** (`session_0178qeyaiQXHz2QMAepnCF2b`) — ad hoc
  live-verification passes against real source-adapter sites (not a single
  roadmap step). Opened PR #118 (4 real bugs found/fixed: kuaikan, manhuaku,
  52shuku, xbanxia).
- **"Functionality testing"** (`session_011vAVPUF2XG5JScPBd6t3z1`) — ad hoc
  functionality/doc-accuracy passes (e.g. fixed 5 README doc bugs against
  real current behavior, branch `docs-testing-cffi-note`).
- **"Bug log HTML viewer"** (`session_01DHZ7ijwZML3BtRXs1iUgaT`) — built an
  Artifact-based bug/case-study log viewer (branch
  `claude/bug-log-html-viewer-rerccl`), not a roadmap step.

To resume one of these (send it a new task, or relay a review finding back
to it) rather than starting fresh: `mcp__Claude_Code_Remote__create_trigger`
with `persistent_session_id` set to its session ID and a near-future
`run_once_at` (a direct `SendMessage` to a `session_...` ID fails — these
are cloud sessions, not local peer agents `ListAgents` can see). Check
`get_session`/`list_sessions` first if a session might have gone idle,
completed, or been retitled since this note was written — this list is a
memory aid, not guaranteed current.

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
