# UI refresh spec: visual direction and per-page changes

Status: design spec, docs only. Branch `react-design-kit` (base `baihe-subtitler` 6ee7132). It builds on `docs/react-ui-guidelines.md` (rules 1–15) and revises rules 4 and 8 (§2.4). The kit is `frontend/src/components/{Toggle,Button,Badge,Card}.tsx`, `labels.ts` (humanized labels, status tones) and `uiClasses.ts` (`buttonClass`, `badgeClass`), plus the design-kit block in `frontend/src/index.css`. It lands in the same PR as this spec, and Settings' four job options are the first page to use it. Other builders are editing `Library.tsx`, `DramaDetailPanel.tsx`, `Translate.tsx`, `Diagnostics.tsx`, the Settings extension section, the Workspace stages and the Review player at the same time. Every per-page item below is therefore a **follow-up task** to apply after their work lands.

Written 2026-09-29 by the `ux-designer` agent, working from the user's feedback: "UI does not need to match Streamlit exactly but needs to look nice — take OpenNovel for example"; too much is hidden behind folds and extra clicks; checkboxes should be toggles; raw values ("streamer_vod", "zh", "claude") are shown; "Open workspace · Read" look like hyperlinks; the Library is a stack of folds; the Diagnostics support report is a raw blob.

**What was seen and what was reasoned.** The agent looked at 24 screenshots in `docs/design/screens/before/`: 12 screens, each at 1280×800 and 390×844, **dark theme only, first viewport only**, all from the seeded e2e library, where the dramas have no transcript lines. **Reasoned from code only:**
- the light theme
- everything below the first viewport: Library tools, the rest of the Source stage below Transcribe, the Translate Advanced body, the lower Diagnostics sections, the Settings key forms, Sources search results and the series panel
- the Reader and Review with real lines
- every running-job, done and error state
- 360 px width

Claims marked **(inferred)** were not confirmed at runtime.

---

## 1. Visual direction

The target is a dark-first reading app in the spirit of OpenNovel: calm surfaces, generous whitespace, content before controls.

- **Layers.** Page background `--bg` → cards `--surface` → raised items (inputs, menus, setting rows, hover) `--surface-2`. Borders are 1px `--border`, and cards use `--shadow-1`. There are no nested boxes inside boxes: a Section inside a Card is drawn borderless (see `ws-translate-desktop.png`, where "Novel reference" is a box inside a box).
- **Accent is rare.** Purple (`--accent`) is used only for the one primary button, the active nav or stage, focus rings, the current stage marker and "on" toggles. Everything else is neutral.
- **Hierarchy.** Page title `--text-2xl`/600. Card title `--text-lg`/600. Body `--text-md` with line-height 1.5. Meta `--text-sm` `--muted`. Pills `--text-xs`. Numbers use `font-variant-numeric: tabular-nums`.
- **Spacing.**
  - Card padding: `--space-4` on phone, `--space-4`/`--space-5` on desktop.
  - Gap between cards: `--space-4` on phone, `--space-5` on desktop.
  - Gap between page regions: `--space-6`.
  - Forms inside a card: `--space-3`.
- **Widths.** The app is capped at 1200px (existing `body`). Form pages (Translate, Settings, Diagnostics) use `.page-narrow` (860px). Reader text is capped at 72ch and centred.
- **Page header pattern** (a CSS class, no new component): `header.page-head` holds the title, one muted meta line, and at most one primary action on the right. On phone the action wraps under the title at full width.
- **Breakpoints** (these already exist in code): phone ≤640px (`useMediaQuery('(max-width: 640px)')`), tablet 641–1023, wide ≥1024 (`Sources.tsx:52`).
- **Motion.** Only the kit's `--ease` transitions (150ms) on hover and toggle. The existing global `prefers-reduced-motion` rule in `index.css` removes them. No entrance animations.

## 2. Tokens and component rules

### 2.1 Tokens (in `index.css`; dark values override inside `prefers-color-scheme: dark`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #fcfcfd | #16161a | page |
| `--surface` | #f4f4f7 | #202026 | Card, Section |
| `--surface-2` | #ffffff | #28282f | raised: setting rows, `.btn-secondary`, menus |
| `--border` | #e2e2e8 | #33333c | all 1px lines |
| `--text` / `--muted` | #1f1f23 / #6b6b76 | #ececf1 / #9d9daa | body / meta |
| `--accent` / `--accent-text` / `--accent-soft` | #7c5cbf / #6a48b0 / 14% mix | same / #b8a3ea / 14% mix | primary, links, pill-accent |
| `--ok` `--warn` `--bad` `--info` + `-soft` | #1e7b3a #8a5a00 #b3261e #1d5fa8 | #8fd6a0 #f0c36d #f2b8b5 #9cc3f5 | Badge tones, inline errors |
| `--space-1..8` | 4 8 12 16 24 32 48 64 | | spacing only from this scale |
| `--radius-sm/md/lg/pill` | 6 / 6 / 14 / 999 | | inputs and buttons / same / Card / Badge, Toggle |
| `--text-xs..2xl` | .75 .875 1 1.125 1.375 1.75rem | | type scale |

The legacy tokens `--radius`, `--font-sm` and `--font-md` stay until their callers are migrated. New code must not use them.

### 2.2 Component usage rules (proposed as guideline rules 16–22)

16. **Card or Section.**
    - Use a `Card` (always open) for what people use on most visits: the primary run form of a stage, the drama list, Continue reading, API key status, Setup checks, Support report.
    - Use a `Section` (`<details>`) for **rare, advanced or destructive** content: Advanced options, Series, Cost by drama, Presets, Voice bank, Backup & storage, Log, Danger zone.
    - A closed Section's summary shows the current state in one line. A Section with nothing in it is not rendered.
17. **Button hierarchy.**
    - Each Card has at most one `.btn-primary`, and it is its main action.
    - Alternatives use `.btn-secondary`.
    - Quiet or tertiary actions (Estimate cost, Detect speakers only, Details, Clear) use `.btn-ghost`.
    - Destructive actions use `.btn-danger`, placed last and set apart.
    - `.btn-sm` is only for dense rows (list rows, card footers, the Review toolbar). It still gets 44px on touch (`index.css` design-kit media block).
18. **Navigation that acts like a button is a `ButtonLink`.** This covers "Open workspace", "Read", "Go to Source" and "Resume". Underlined text links are only for links inside a sentence. `button.link` is retired except inside prose.
19. **A `Toggle` for every boolean setting or option**, inside `Field` (label + optional help). Inside forms and settings lists, use the `.setting-list` rows. Keep `<input type="checkbox">` only for (a) selecting items in a list (Library selection, glossary rows) and (b) acknowledgements ("I understand this replaces existing English").
20. **Never render a raw API value.**
    - Status, media type, language code and engine id go through `humanize(kind, raw)` (`components/labels.ts`) or `<Badge kind=… value=…/>`. That includes `<option>` labels; the option `value` stays raw.
    - Delete the ad-hoc `.replace(/_/g, ' ')` sites: `LibraryList.tsx:182`, `DramaCards.tsx:24`, `Library.tsx:291`, `detailsForm.ts:127`.
    - Merge the second `humanize` in `sources/sourcesFormat.ts:146` into `labels.ts`.
    - The status filter options come from raw strings (`LibraryList.tsx:10`, rendered at `:82`).
21. **Badges carry status, type and language.**
    - Status badges use `statusTone`.
    - Colour is never the only signal: the pill always has text.
    - Don't put meaning in `Badge title=`, because hover-only text doesn't exist on touch.
    - `Section`'s count (`Section.tsx:63`) moves from `.badge` to `Badge`.
22. **A disabled primary names the fix, and the fix is one tap away.**
    - This extends rule 2: the "Still needed: …" item is a link or ButtonLink to the missing field or stage.
    - No help lives only in `title=`. Examples that break this today: `TranslateStage.tsx:357,361` (help text for the genre guidance and pronoun checkboxes) and `LineRow.tsx:181`. Use `Field help` (tap to open) or visible text.
    - Keyboard-shortcut hints may stay in `title` because `review/ShortcutSheet.tsx` lists them.

### 2.3 Accessibility checks for the kit

- `Toggle` is `role="switch"` with `aria-checked`, and its label comes from `Field`'s `<label htmlFor>`.
  - Focus uses the global `:focus-visible` ring.
  - The off track (`--text` 18% on `--surface`) is about 2:1 against a dark card. That's acceptable only because the white thumb gives more than 3:1. **Verify** in both themes.
- `a.btn` keeps link semantics: new tab and copy-link still work.
- Primary contrast: white on #7c5cbf is about 5:1. Muted on surface is 4.8:1 (light) and about 5.9:1 (dark). Both pass AA.

### 2.4 Revisions to existing guidelines

- **Rule 4** becomes: "Advanced, rare and destructive options are collapsed. Common actions and frequently used content are visible (rule 16)."
- **Rule 8** loses the "one collapsed More section" escape. Content used on most visits (Recently active, Reading history) becomes visible and clickable (§3.1). Rare lists stay folded.
- **Rules 1 and 2** stand.

---

## 3. Per-page changes

Click counts start from the page named and count taps or clicks, not typing. Each current count comes from code plus screenshots; each proposed count is the design target.

### 3.1 Library (`pages/Library.tsx`, `components/LibraryList.tsx`, `components/DramaCards.tsx`): structural redesign

Evidence:
- The page is a stack of `<details class="panel fold">` (`Library.tsx:56-69, 203, 273`).
- The desktop title is a `button.link` that only selects the row (`LibraryList.tsx:177`). The detail panel then renders **after** the list (`Library.tsx:419`), so with many dramas you have to scroll.
- Recently active and Series items are plain text, not links (`Library.tsx:131-141`).
- Screenshots `library-*.png` show raw "not started", "zh", "ko" and "video drama".

| Task | Current (clicks) | Proposed (clicks) |
|---|---|---|
| Open a drama's workspace | desktop 2 + scroll (select, "Open workspace"); phone 1 | 1 (card title or tile), landing on its current stage (§3.3) |
| Read | desktop 2; phone 1 | 1 (card "Read") |
| Resume recent work or reading | 2, and Recently active items aren't clickable | 1 ("Continue" shelf) |
| New drama | 2 (open fold, Create) | 1 + Enter (header "New drama" → Sheet, Enter creates) |
| Search all lines | 2 (open fold, Search) | 1 (scope "Lines" beside search) + Enter |
| Series / Cost / Presets / Voice bank / Backup | 1 each (fold) | 1 each (stay Sections under "Library tools") |

Layout, top to bottom:

1. **`page-head`.** "Library", with meta line "3 dramas · 0 of 0 lines translated · $0.00 spent" (the current `StatsStrip` text). On the right, **"New drama"** `.btn-primary`: the page's only primary. It opens `Sheet` with the English title, original title, language and type (humanized options, last-used remembered) and a Section for Credits, series and preset (the current `CreateForm` body moved as-is). On success, keep today's behaviour (select the new drama) and show a status line with an "Open workspace" ButtonLink.
2. **Continue shelf.** A Card, only rendered when `getHistory`/`getRecent` return items. Up to 4 compact items: title, "Page 12 · 34%" (from history `percent_complete`) or the humanized status, and a "Resume" ButtonLink `.btn-sm`. A reading item goes to Read; a recent item goes to the workspace. Desktop: one row of 4. Phone: 2 stacked plus a "Show more" ghost button. **No horizontal scroller.**
3. **Toolbar.**
   - Contents: search input, a segmented scope control "Titles | Lines" (native radios styled `.segmented`), a Status select, a Quick filter select (humanized options), a view switch "Grid | List" (persisted, rule 12), and "Select" `.btn-ghost`.
   - Phone: search at full width. Row 2 holds Status and Filter at 50/50. Row 3 holds scope on the left and Select on the right.
   - With scope = Lines, the grid area shows `LineSearch` hits as a list (drama title as a link, line number, zh/en).
4. **Drama grid (Grid view, the default).**
   - Columns: `repeat(auto-fill, minmax(200px, 1fr))`, which gives about 5 per row at 1200px.
   - Each card has a **title tile** (3:4 ratio, a tinted `--surface-2`/`--accent-soft` block showing the first CJK character of `title_zh`, or initials from `title_en`, at `--text-2xl`, `aria-hidden`). Below it:
     - the title (2-line clamp; this is the link)
     - the original title (muted, 1 line)
     - `Badge status` (toned) and `Badge language`
     - up to 2 tag pills plus "+N"
     - a footer with "Read" (ghost sm, ButtonLink) and "Details" (ghost sm)
   - **No primary buttons in the grid.** The title is the main action.
   - Phone: one column of row cards. The tile is 56×75 on the left, text on the right, and footer buttons at 44px.
   - Select mode: a 44px checkbox over the tile's top-left corner, and tapping the card toggles it (keeps `SelectCards`).
5. **List view.** The existing desktop table, with humanized cells and badges. The title becomes a real link to the workspace, a "Details" ghost sm button sits in the row, and the checkbox column is kept. On phone, List view falls back to row cards.
6. **"Library tools".** A 2-column grid of Sections, all collapsed, each hidden when empty: Series (rows link to their dramas' filter **(inferred: needs a series filter; if `listDramas` has none, keep as text)**), Cost by drama, Presets, Voice bank, Backup & storage (`AdminSection`, unchanged). This stays folded because it's rare or destructive.

States:
- Loading: 6 skeleton cards in `--surface`, with no shimmer when reduced motion is set.
- Empty library: a Card saying "No dramas yet" with a "New drama" primary.
- No filter matches: "No dramas match" plus a "Clear filters" ghost button.
- Error: `ErrorBanner` above the grid.

**API gaps (backend dependencies):**
- There is no cover-image route. `DramaDetail.has_cover_art` exists (`frontend/src/api/types.ts:62`), but grep over `api/routers` finds no cover route. The title tile stands in until one exists.
- `DramaSummary` has no per-drama line counts or stage. Card progress bars need either a batch progress endpoint or those fields on list items. Until then, show the status badge only.

### 3.2 Drama detail (`components/DramaDetailPanel.tsx`)

Evidence:
- Raw values Status "translated", Type "audio_drama" and a raw engine id (`DramaDetailPanel.tsx:63-68`; `drama-detail-desktop.png`).
- "Open workspace · Read" are underlined text links (`:87-88`) that always go to `stage: 'source'`.
- The panel renders below the list.

| Task | Current | Proposed |
|---|---|---|
| See details | desktop 1 + scroll; phone 1 + scroll | 1 ("Details" → `Sheet`: bottom sheet on phone, centred dialog on desktop) |
| Open workspace or Read from details | 1 (text link) | 1 (`ButtonLink` primary / secondary) |
| Delete | 2 + type DELETE | unchanged (rule 15), `.btn-danger` last, behind a divider |

Sheet contents, in order:
1. Title and original title.
2. A pill row with status, type and language.
3. Summary, clamped to 4 lines with a "More" ghost button.
4. A `dl` of credits with humanized values.
5. An actions row: **"Open workspace"** primary and "Read" secondary.
6. Separately: "Delete drama…" `.btn-danger .btn-sm`, or the PC-only note.

On phone the actions row sticks to the bottom of the sheet. Focus goes to the title on open and back to the "Details" button on close (Sheet already does this).

### 3.3 Workspace shell (`pages/workspace/WorkspaceShell.tsx`, `stages.ts`, `router.ts`)

Evidence:
- The back control is an underlined "Back to Library" (`WorkspaceShell.tsx:23`), and the status badge is raw (`:27`).
- The stage always defaults to the default stage (`stages.ts:23`, `router.ts:32`).
- On phone the stage tabs scroll sideways and cut "Export" to "Ex" (`ws-*-phone.png`).
- Stage content starts at about y=428 on a 390×844 phone.
- **`GET /api/workflow/dramas/{id}/progress` exists** (`api/routers/workflow_routes.py:16-21`, schema `api/schemas.py (before the package split):1846-1864`: `stage`, per-stage `state` done/current/pending/optional/blocked, `untranslated_count`, `flagged_count`) but **no frontend code calls it**.

| Task | Current | Proposed |
|---|---|---|
| Reach the stage with work | 1 extra tab click after opening | 0: a stage-less URL `#/drama/<id>` loads progress and `location.replace`s to `progress.stage` (rule 13). Library, Reader and detail links drop the hard-coded `'source'` (`DramaCards.tsx:27`, `DramaDetailPanel.tsx:87`, `Reader.tsx:330`) |
| See how far the drama has got | none | 0: a stepper on the stage tabs |

Header:
- Desktop, one row: "‹ Library" (`ButtonLink` ghost sm), the title (`--text-xl`), a toned `Badge status`, and on the right "Read" ghost sm.
- Phone, one row: a 44px "‹" icon ButtonLink with `aria-label="Back to Library"`, the title on one line with ellipsis, then the badge.

Stepper:
- Desktop: 5 tabs, each with a state marker. Done shows "✓" and the label. Current is bold with the accent underline. Pending is muted. Blocked is muted with a visually hidden "(blocked)".
- Counts appear where they exist: "Translate · 32 left", "Review · 12 flagged".
- Phone: a **5-column grid** at `--text-sm` with no sideways scroll. Counts are hidden on phone; the state marker stays.
- Keep `aria-current="page"`. Each state is also given as text for screen readers.

App header on phone (`App.tsx:49-64`): move "Report a problem" into the title row as a 44px icon button with a label. This saves about 56px. Target: stage content starts at y ≤ 300 on 390×844.

### 3.4 Source stage (`stages/SourceStage.tsx`, `TranscribeStage.tsx`, plus the Sections below)

Evidence:
- The primary sits between an "Upload" secondary and "Detect speakers only".
- "Still needed: the transcript text." points at a field below the fold (`TranscribeStage.tsx:280-287, 340-344`; `ws-source-desktop.png`).
- Expected, Min and Max speakers are always shown even though Min and Max only apply to "Detect speakers only" (`:329-337`, help text).
- Raw option labels `whisper_diff`, `qwen3_forced_align`, `qwen3_asr`, `audio_separator`, `demucs`, `tesseract`, `paddle` (`:360-363`).
- Checkboxes at `:266-271` and `:345-347`.

| Task | Current | Proposed |
|---|---|---|
| Transcribe with a file | 2 (Upload, Transcribe) | 2, unchanged: the upload/transcribe merge is guideline 3.3, not repeated here |
| Fix a "Still needed" blocker | scroll and find | 1 (the reason is a link that focuses the field) |
| Detect speakers only | 1 | 1 (ghost button inside the "Speakers" Section) |

Layout:
- **Card "Audio or video."**
  - Media state as badges ("Audio attached" ok / "No audio" neutral, plus "limit 2048 MB" as meta).
  - A segmented control "Upload file | From URL" (replaces the radios at `SourceStage.tsx:132-141`).
  - The file picker, and "Upload" secondary.
  - "Remove…" danger sm.
- **Card "Transcribe."**
  - The primary, with its reason (rule 22) directly under it.
  - Visible fields: Source language, Chinese script, Whisper model (humanized).
  - Toggle: "Detect speakers after transcribing".
  - Section "Speakers" (summary "auto" or "3 expected") holding Expected, Min and Max plus "Detect speakers only" ghost.
  - Section "Advanced", as today but with Toggles and humanized option labels.
- **Other Cards and Sections.** NovelPanel, "Edit details", Source mode, Auto-fill and Analyze stay Sections below, in that order (**(inferred)**: below the captured viewport).

Phone: the fields stack in one column. The primary and its reason stay directly under the card title.

### 3.5 Translate stage (`stages/TranslateStage.tsx`)

Evidence:
- **"Translate 0 lines" is enabled with 0 lines** (`:282-283`; `ws-translate-*.png`), which breaks rule 2.
- "Starting tier / Apply tier" takes the first row above Engine (`:246`).
- Six checkboxes (`:345-384`). Help text for the genre guidance and pronoun checkboxes is only in `title=` (`:357,361`).
- "Bulk batches 0 none pending" renders even when empty (screenshot).

| Task | Current | Proposed |
|---|---|---|
| Start a translation (lines exist) | 2 (tab, primary) | 1 with §3.3 landing |
| Understand why it can't run | none (it runs on 0) | 0: disabled, with "No lines yet · Go to Source" (ButtonLink) or "All 412 lines have English. Turn on Re-translate in Advanced." |
| Toggle Reflect or Bulk | 2 (Advanced, checkbox) | 2 (Advanced, Toggle) |

Layout:
- Card "Translate", with the tier select and "Apply" `.btn-sm` secondary in the Card `actions` slot (desktop) or first row (phone).
- Engine and Model on one row, then Style and Locale on one row.
- The primary, with "Estimate cost" ghost beside it.
- Section "Advanced": every boolean becomes a Toggle with Field help (no `title`). "Re-translate existing" is a Toggle, and its "I understand…" stays a checkbox (rule 19b).
- Hide "Bulk batches" when it has no rows (rule 8). Glossary, Characters and Novel reference stay Sections with count Badges.

### 3.6 Review stage (`stages/ReviewStage.tsx`, `review/*`): visual pass only, after the Review player work lands

The empty state is already good (`ws-review-*.png`: "Go to Source" primary plus "Add first line").

Tasks:
- The line toolbar (`LineRow.tsx:190-238`) uses `.btn-ghost .btn-sm`.
- The flag becomes `Badge tone="warn"` with its text (`:127-135`).
- "(not translated)" is shown muted and italic.
- `Player.tsx:158` "Loop" becomes a Toggle.
- The filter select becomes count chips ("All 412 · Flagged 12 · Untranslated 32"), per guideline 3.5 row 5.

The click counts don't change. Line rows with real data were **not seen**.

### 3.7 Dub stage (`stages/DubStage.tsx`)

Evidence:
- The option label reads "edge-tts (free, online, more natural) (online)" (`:97-100` appends "(online)" to a label that already says it; `ws-dub-desktop.png`).
- "Keep background music" is a checkbox (`:169-174`).

Tasks:
- Append "(online)" only when the label lacks it, or move it into a Badge next to the select.
- "Keep background music" becomes a Toggle. When it is disabled, the reason is visible, not only in help.
- A "GPU" Badge `info` in the Card header (`:88`).
- Hide "Voices and cloning" while there are 0 speakers **(inferred: it has nothing actionable then; check the body first)**.

Clicks: 1 (Generate dub), unchanged. Phone: the reason sits under the primary, which already works (screenshot).

### 3.8 Export stage (`stages/ExportStage.tsx`, `ExportSubtitles.tsx`, `ExportAss.tsx`)

Evidence:
- "Export" is enabled at "0 lines · 0 translated" (`ws-export-*.png`; `ExportSubtitles.tsx:92`).
- "ASS style" is always rendered even when SRT is chosen (`ExportStage.tsx:119`).
- The Advanced summary says "wrap off/off".
- Phone: Format takes a full row while Language and Export share the next one.

| Task | Current | Proposed |
|---|---|---|
| Export SRT | 2 | 2 (or 1 with §3.3 landing when the stage is Export) |
| Style an ASS file | 1 (always-visible fold) | 1 (the fold appears only when Format = ASS or a burned-in video export is open) |

Tasks:
- Disabled with reason: "No lines to export yet · Go to Source".
- Readiness as badges ("412 lines", "380 translated" ok/warn).
- Summary text "no wrap".
- Phone: Format and Language at 50/50, then **Export at full width** below.
- Checkboxes in `ExportSubtitles.tsx:114` and `ExportAss.tsx:103,107` become Toggles.

### 3.9 Translate page (`pages/Translate.tsx`): structural redesign on desktop

Evidence:
- "Engine details" is a fold listing "key configured: yes/no" for every engine (`:132-147`).
- Direction and language are two selects.
- History is always open with no limit (`:183-198`).
- The result appears below the form.

| Task | Current | Proposed |
|---|---|---|
| Reverse direction | 2 (select, option) | 1 ("⇄" swap button, `aria-label="Swap languages"`) |
| Check an engine's key | 1 (fold) | 0 (the engine option reads "(no key)"; one warning line for the chosen engine only) |
| Clear | select and delete by hand | 1 (Clear ghost) |

Layout:
- Desktop ≥1024: a toolbar row (Engine, Model, "Chinese ⇄ English", Open file), then **two columns**: source textarea on the left, result on the right (`--surface-2`, with Copy and Download in the result header).
- "Translate" is the primary under the source text, with "Clear" ghost beside it.
- History is a Card showing the last 5 with a "Show all" ghost button. Rows use humanized "Chinese → English · Claude".
- Phone: stacked. Toolbar fields go 2 per row. The result sits directly under the primary.
- Remember engine, model and languages (rule 12).

### 3.10 Sources (`pages/Sources.tsx`, `sources/*`)

Evidence:
- The summary "15 on" is cryptic (`sourcesFormat.ts:135`).
- "Search in: All 9 sources" and "Source settings: 15 of 15 on" read as if they contradict each other (`sources-desktop.png`). Search likely covers only sources that support search **(inferred)**.

Tasks:
- `page-head` "Sources" with meta "15 sources on · 9 searchable · 0 paused" and a paused Badge in warn.
- One Card with a segmented "Paste a link | Search by title". Only one input is visible, and its button is the Card's single primary. Today "Preview" and "Search" sit at equal weight.
- "Search in" stays a Section.
- "New chapters" is a Card, rendered when non-empty (already the case).
- "Source settings" stays a Section. It's PC-only and rare. Per-source checkboxes (`SourceSettings.tsx`) become Toggles. The health light becomes `Badge` ok/warn/bad with the `healthText` label.

Clicks: search 1 + Enter, and 1 more to switch mode. Phone: the button goes full width under its input (already the case). The results and series panel with data were **not seen**.

### 3.11 Reader (`pages/Reader.tsx`, `reader/reader.css`)

Evidence:
- **Phone has horizontal page overflow.** The title and "Aa" run off-screen and the panel's right edge is cut (`reader-phone.png`). The likely cause **(inferred)** is that `.reader` is `display:grid` with an `auto` column (`reader.css:2`). The nowrap title in the full-bleed header (`reader.css:71-83,93`) then sizes the track beyond 390px. Fix: `grid-template-columns: minmax(0, 1fr)`.
- The desktop breadcrumb is underlined (`Reader.tsx:354-358`).
- The empty state is a plain link (`:371-375`).
- The workspace link is hard-coded to `stage: 'source'` (`:330`).

Tasks:
- Desktop top bar: "‹ Library" ghost sm, the title (link to the stage-less workspace URL, not underlined), and "Aa" `.btn-secondary .btn-sm` with `aria-label="Reading settings"`.
- The text column is capped at 72ch and centred.
- Empty state: a Card with "No lines to read yet" and a **"Go to Source"** primary ButtonLink.
- The Words, Story and Notes Sections stay folded; they're secondary while reading.

### 3.12 Settings (`pages/Settings.tsx`, `settings/*`)

Evidence:
- Four checkboxes (`:51-61`; `settings-*.png`). **Done in this PR:** they are now Toggles in a `.setting-list` (`docs/design/screens/after/settings-*.png`).
- The key summary lists raw ids "claude, deepseek, … hf_token, ollama_url" (`:65`).
- A 3-line paragraph sits in the default view (`:67-71`).
- Non-secret endpoints show "Yes"/"No" under raw names (`:89-97`).
- Notifications and Browser extension are folds showing "Off".

| Task | Current | Proposed |
|---|---|---|
| Change a job option | 1 | 1 (Toggle) |
| See which keys are set | 1 (fold) | 0 |
| Set a key | 2 (fold, then that engine's form) | 1 ("Set key" on its row) + Save |
| Turn notifications on | 2 (fold, control) | 1 (Toggle in the Card header) |

Layout (`.page-narrow`):
- Card **"Jobs"**: a `.setting-list` of 4 Toggles with Field help.
- Card **"API keys"** (meta "2 of 10 set"): one row per engine with a humanized name, `Badge` "Set" ok or "Missing" neutral, and a "Set key"/"Replace" `.btn-sm` that expands that row's `SettingsKeyForm` in place. The .env explanation moves into the Card's help.
- Cards **"Notifications"** and **"Browser extension"**: the on/off Toggle in the Card `actions`, and the body shown only when on. The extension section is being edited by another builder, so apply this after it lands.

### 3.13 Diagnostics (`pages/Diagnostics.tsx`, `diagnostics/*`): structural redesign

Evidence:
- Every section is a fold (`Diagnostics.tsx:123-157`).
- The support report is a raw text blob behind 3 clicks (`SupportReportSection.tsx:30-39`: open fold, "Build report", "Copy"). The API returns plain `Key: value` text (`services/diagnostics_gaps_service.py:184-200`, `diagnostics.py:598-631`).

| Task | Current | Proposed |
|---|---|---|
| See overall health | 0 (summary line) | 0 (badge strip) |
| Copy support report | 3 | **1** ("Copy report" builds and copies) |
| See setup problems | 0–1 | 0 (Setup Card open) |
| Packages, Log, Speaker detection, Model cache, Job history, Bug reports | 1 each | 1 each (Sections) |

Layout:
- **`page-head`** with a badge strip: "1 setup problem" warn, "18 of 55 packages" neutral, "No jobs running" / "2 running" info.
- **Jobs**: a Card while jobs are running or failed (existing `JobsBlock` logic).
- **Card "Setup"**: rows from `setupRows` with name, value and `Badge` OK/Problem. "Check again" is a secondary sm in the header actions. "Model engines" stays a Section.
- **Card "Support report"**:
  - "Redacted; safe to share".
  - **"Copy report"** primary. Use `navigator.clipboard.write` with a `ClipboardItem` holding a promise, so Safari keeps the user gesture across the fetch; fall back to the existing select-all message.
  - "Download .txt" secondary (a client-side Blob).
  - A "Preview" Section showing the report as a `dl`. Lines matching `^([^:]+): (.*)$` become rows. Indented `  - name: version` lines become a nested list under "Model/engine versions". "Recent errors" lines render in `--font-mono`. The raw `<pre>` sits behind a "Plain text" ghost toggle.
  - Optional backend dependency: a structured `sections` field on `GET …/support-report` would remove the client-side parsing.
- Packages, Speaker detection, Model cache, Job history, Log and Bug reports stay Sections. **Danger zone** comes last, with its summary in `--bad`.

---

## 4. Rollout (each item is one agent-sized task)

0. **Kit** (this PR, done): tokens, `Toggle`, `Button`/`ButtonLink`, `Badge` and `labels.ts`, `Card`, `.setting-list`, applied to Settings' four job options. Not done here, optional as a later step: alias the legacy `button.primary` and `.badge` to the kit look, so older screens stop clashing before they are migrated.
1. **Chrome and bugs** (small): the phone header compaction (§3.3); the Reader phone overflow fix (§3.11); the `humanize` sweep (rule 20 sites plus `Translate.tsx` history, `Settings.tsx:65`, `DramaDetailPanel.tsx:63-68`, `WorkspaceShell.tsx:27`). Files: `App.tsx`, `index.css`, `reader/reader.css`, `components/labels.ts`, `sources/sourcesFormat.ts`, and the call sites.
2. **Rule-2 fixes**: disable with reason "Translate 0 lines" and "Export" at 0 lines. Files: `stages/TranslateStage.tsx`, `stages/ExportSubtitles.tsx`, `stages/ExportStage.tsx`.
3. **Workspace shell**: the progress client (`api/workflow.ts`, new), stage-less route and redirect, stepper, header. Files: `router.ts`, `WorkspaceShell.tsx`, `stages.ts`, plus link sites `DramaCards.tsx:27`, `DramaDetailPanel.tsx:87`, `Reader.tsx:330`. **Structural** (the tab look changes): take before/after screenshots.
4. **Library and detail** (after the concurrent Library and DramaDetailPanel work merges): §3.1–3.2. Files: `pages/Library.tsx`, `components/LibraryList.tsx`, `components/DramaCards.tsx`, `components/DramaDetailPanel.tsx`, `index.css`. **Structural**; split into 4a (header, New drama Sheet, Continue shelf, toolbar) and 4b (grid cards, List view, detail Sheet) if it runs large.
5. **Toggles and labels in the stages**: `TranslateStage.tsx`, `TranscribeStage.tsx`, `SourceStage.tsx` (segmented control), `DubStage.tsx`, `ExportSubtitles.tsx`, `ExportAss.tsx`, `GlossaryPanel.tsx`, `UrlDownload.tsx`. Localized.
6. **Settings** (§3.12): `pages/Settings.tsx`, `SettingsKeyForm.tsx`, `settings/NotificationsSection.tsx`; `ExtensionSection.tsx` after its builder finishes.
7. **Diagnostics** (§3.13): `pages/Diagnostics.tsx`, `diagnostics/SupportReportSection.tsx`, `diagnostics/SetupSection.tsx`, `LogSection.tsx` (`CopyBlock`). **Structural.**
8. **Translate page** (§3.9): `pages/Translate.tsx`, `TranslateFileControls.tsx`, `translate.css`. **Structural** on desktop.
9. **Sources** (§3.10): `pages/Sources.tsx`, `sources/UrlBox.tsx`, `sources/SearchPanel.tsx`, `sources/SourceSettings.tsx`.
10. **Review visual pass** (§3.6), after the Review player lands: `review/LineRow.tsx`, `review/LinesPanel.tsx`, `review/Player.tsx`, `review/review.css`.
11. **Docs**: fold §2.2 and §2.4 into `docs/react-ui-guidelines.md` (rules 4, 8 and new 16–22).

**Tests.** The agent counted 67 checkbox-role or `.check()`/`toBeChecked` uses across 19 files in `frontend/e2e/`; any of them whose control becomes a Toggle must switch to `getByRole('switch')`. `toBeChecked` works on `role=switch` with `aria-checked` in the pinned Playwright (the Settings spec in this PR uses it). Label-text selectors change where labels are humanized. Update them in the same task. `playwright.config.ts:28-39` already has a `phone` project (iPhone 13 user agent), so add `*.mobile.spec.ts` cases for tasks 1, 3, 4 and 11.

## 5. Acceptance checks (yes/no, per task)

- [ ] No horizontal page scroll at 360×800 and 390×844 on Library, every Workspace stage, Reader, Translate, Sources, Settings and Diagnostics (`document.documentElement.scrollWidth <= innerWidth`).
- [ ] Every tap target is ≥ 44×44px on the phone project, including `.btn-sm`, Toggle and the stepper tabs.
- [ ] No raw enum or id is visible: no `_` in any rendered status, type, language or engine label, and no lowercase status. Grep the task's files for `.replace(/_/g`.
- [ ] Each Card has ≤ 1 `.btn-primary` (rules 1 and 17), and each page has ≤ 1 primary outside Cards.
- [ ] Every disabled primary has a visible reason with a link or focus target (rules 2 and 22).
- [ ] Every boolean setting or option is a `role="switch"` with an accessible name. The only remaining checkboxes are list selection and acknowledgements (rule 19).
- [ ] "Open workspace", "Read", "Resume" and "Go to Source" are `a.btn`, and none are underlined (rule 18).
- [ ] No help text lives only in `title=` (rule 22).
- [ ] Opening a drama from Library lands on `progress.stage` (rule 13).
- [ ] Keyboard: Tab order follows the visual order (page-head action → toolbar → grid → tools). Focus is always visible. Sheet traps focus and returns it on close. Esc closes the Sheet and help popovers. Space and Enter flip a Toggle.
- [ ] Contrast is AA for text and ≥ 3:1 for Toggle thumb and focus ring in **both** themes.
- [ ] With `prefers-reduced-motion`, there are no transitions.
- [ ] Rules 1–15 still hold where not revised; every function reachable before is still reachable (list per task in the PR).

**Screenshots per structural task** (full page, not first viewport): 390×844 touch in dark and light, 360×800 dark, 1280×800 dark and light, plus 1024×768 for Library and Sources. Compare against `docs/design/screens/before/`, which holds dark, first-viewport shots only. Structural redesigns under the root `CLAUDE.md` screenshot rule: **Library (4), Workspace shell (3), Diagnostics (7), Translate page (8)**. The other tasks are localized.
