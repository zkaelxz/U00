# React UI guidelines: make it concise, like Streamlit

Status: design guidance plus a prioritised change list. Docs only; no code changed by this file.
Implementation status (2026-09-29): every screen in section 3 is implemented (Library #285 and #302, Source #299, Translate #297/#300, Review #298, Dub #295, Export #296, Settings and Diagnostics #301, shared Section/Field #293). Remaining gaps are backend-blocked (e.g. a pending-batch list endpoint).
Written 2026-09-29 after the user reviewed the React app and said: "I want the UI and functionality
to be more concise, like it was on Streamlit."

Method: read `tabs/workspace_tab.py` (Source through Export sections), `tabs/library_tab.py`,
`tabs/translate_tab.py`, `tabs/settings_tab.py`, `tabs/diagnostics_tab.py`, `app.py`, and every page and
stage under `frontend/src/pages/**`. Anything marked **(inferred)** was not read directly in code (for
example click counts, or how something looks at runtime).

## 1. Why the React pages feel long (diagnosis)

Read from the code, not guessed:

- **One panel per API endpoint.** Library renders 7 summary panels (Stats, Recently active, Series, Cost by
  drama, Reading history, Presets, Voice bank) before the list of dramas. Streamlit put one dashboard
  expander (4 metrics and a caption or two) on top, and moved the rest into collapsed expanders
  (Series, Cost breakdown, Reading history, Presets, Voice bank, Storage, Backup).
- **Nothing is collapsed.** `grep '<details'` over `frontend/src` finds nothing. Streamlit's Workspace
  alone uses roughly 45 `st.expander` / `st.popover` calls to hide options.
- **All options are visible at once.** The React Translate stage shows 10+ fields (engine, model, style
  preset, locale, style note, batch size, two context windows, cost cap, fallback list, force) as one
  fieldset. Streamlit shows engine + model, style preset, and the primary button first; context and batch
  sliders have defaults (context 6/3, batch 20, or 10/6/30 for novels).
- **Long labels, inline paragraphs.** Examples: "Keep the original background music (BGM-preserving; real
  audio has not been verified)", "Cost cap for this run in dollars (blank = none)", "Upload selected file
  and transcribe". Streamlit put the explanation in `help=` tooltips.
- **Several primary buttons per panel.** Transcribe has 3 buttons at equal weight; Streamlit has one
  primary button ("Transcribe & Align") that is disabled with a one-line "Still needed: ..." message.
- **No shortcuts, no remembered choices.** No `keydown` or `localStorage` anywhere in `frontend/src`.
  Streamlit has Ctrl+S (Save edits), Alt+Up/Down (prev/next flagged line), Alt+Space (play/pause), and
  remembers engine/model/style through Settings ("Defaults for new dramas").
- **Literal status dumps.** Export "Readiness" is a 6-item list; Dub summary, Translate counts, Media
  status are each a separate muted paragraph. Streamlit shows a project header/stepper and warns only when
  something is wrong.
- **Stage set differs.** Streamlit has 7 stage tabs (Source, Transcript, Diarize, Translate, Review, Dub,
  Export) and opens on the drama's current stage. React has 5 (Transcript and Diarize live inside Source)
  and always opens on `DEFAULT_STAGE`.

## 2. Rules

Apply to every new or reworked React page. A reviewer can check each with a yes or no.

1. **Primary action first, and only one.** Each panel has at most one filled/primary button; it is the
   first thing visible, above the options. Secondary actions (Estimate cost, Detect speakers only, Upload
   only) are plain or link buttons, or sit in the options area.
2. **Explain a disabled primary button.** One line under it: "Still needed: an audio or video file."
   Never a silently disabled button (Streamlit's `can_prep` message is the model).
3. **Sensible defaults, pre-filled.** Every field arrives filled from the drama, the Settings defaults, or
   the API's `defaults` object. A user who changes nothing can press the primary button. Blank means
   "default", never "required".
4. **Advanced options collapsed.** Use `<details>` (one shared `Section` component) for anything a
   first-time run does not need. The summary shows the current values in one line
   (e.g. "Advanced: batch 20, context 6/3, no cost cap") so people see the state without opening it.
5. **One line per setting.** Label left (or above on phones), control right, help as tooltip. No setting
   uses two lines except a textarea. Group related controls in a row (Max speed-up and Max slow-down on
   one row, as Streamlit's `sp1, sp2` columns do).
6. **Short labels, help in a tooltip.** Labels are 1-4 words plus a unit. Everything else goes in a
   `title`/`(?)` help control or inside `<details>`. No inline explanatory paragraphs in the default
   view. Error and blocking messages are the exception and stay inline.
7. **Numbers and units in labels.** "Batch (lines)", "Cost cap ($)", "Start (s)", "Wrap at (chars)",
   "Speed-up (x)". Put the range in the input (`min`/`max`/placeholder), not in the label.
8. **No empty panels.** If a list or table has no rows, render nothing. Panels for Series, Presets, Voice
   bank, Reading history, Notes, TM suggestions, Versions, Line history appear only when non-empty, or
   live inside one collapsed "More" section.
9. **Summaries are one line.** Counts and status go in a single header line ("412 lines, 380 translated,
   12 flagged"), not a list or a paragraph per figure. Show a warning only when something is wrong.
10. **Consistent spacing and controls.** One `Field` component (label + control + optional help) and one
    `Section` component (title, optional summary, optional collapsed body). Same button sizes, number
    input width and gap. No page-local inline `style={{...}}` layouts (Settings has one now).
11. **Keyboard shortcuts where Streamlit had them.** Ctrl/Cmd+S saves the current line edit; Alt+Up /
    Alt+Down jump to the previous or next flagged line; Alt+Space plays or pauses (once media playback
    exists). Enter submits single-field forms (search, new drama). Show the shortcut in the tooltip.
12. **Remember last-used choices.** Persist per-viewer conveniences (last engine/model/style/locale, last
    export format and language, last dub engine, last Library filters, open/closed `Advanced`) in
    `localStorage` inside try/catch, falling back to API defaults. Server-owned facts (drama fields) still
    go through the API.
13. **Stage opens where the work is.** Opening a drama lands on its current stage (Streamlit's Step 19
    behaviour), not always Source.
14. **Few clicks on the common path.** Targets **(inferred, not measured)**: start a translation in 1 click
    from a drama that has transcript lines; export SRT in 2 clicks (Export stage, Export); create a drama
    with 1 field + Enter (title), everything else defaulted.
15. **Do not hide risk to gain brevity.** Destructive actions (force re-translate, delete, reset) keep
    their explicit confirm, but compact (one checkbox on the same line, or a confirm popover), not a
    paragraph. Cost estimates and cap warnings still show before a paid run.

## 3. Per-screen audit and changes

Priority: **P1** = most of the "too long" feeling, small changes, do first; **P2** = clear improvement;
**P3** = polish. Each row is meant to be one agent-sized task; "Files" is the likely edit set. The shared
building blocks (section 4) should land first.

### 3.1 Library

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| `Dashboard` expander (open): 4 metrics (Total dramas, Lines translated, API calls logged, Estimated spend), status and type captions, "Continue reading" cards. Recently active, Cost breakdown, Series, Reading history, Presets, Voice bank, Storage, Backup are all collapsed expanders. | 7 always-open panels (Stats, Recently active, Series, Cost by drama, Reading history, Presets, Voice bank) above everything, often with empty lists. 7 API calls on load. | Replace with one line "N dramas - X/Y lines translated - $Z spent". Put Recently active, Series, Cost by drama, Reading history, Presets, Voice bank in one collapsed `More` section, each rendered only if non-empty. Keep the API calls (lazy-load on open is P3). | P1 | `pages/Library.tsx` (Summaries) |
| "All dramas" expander (open): filter row (Search title/summary, Studio, Author, Voice actor, Status, Language, Type), quick-filter pills, tag multiselect, then a table. Search across all lines is its own expander. | Page order: summaries, Search lines, New drama form, drama list, detail panel. The list (the main content) is fourth. | Order: drama list first with one search box, plus status/language/type selects; "Search lines" becomes a toggle beside the search box; detail opens under the selected row. Which filters `LibraryList.tsx` supports today was not fully read **(inferred)**; check first. | P1 | `pages/Library.tsx`, `components/LibraryList.tsx` |
| New drama: choose "New drama" in the Workspace picker; metadata fields, with two optional expanders (Auto-fill from a listing page, Analyze a media file). | Always-open "New drama" panel: 2 title inputs, 2 selects, button; no Enter-to-create shortcut beyond form submit. | Collapse to a `+ New drama` button that expands one row: title, language (last used), media type inside `Advanced`. Auto-fill and Analyze now have an API (Slice 37) and are live on the React Source stage (PR #304); do not duplicate them here. | P2 | `pages/Library.tsx` (CreateForm), `pages/libraryForm.ts` |
| Actions per drama sit in the expander below the table. | `DramaDetailPanel.tsx` (113 lines) not fully read; **(inferred)** it mirrors the endpoint set. | Audit against rules 5, 6, 8; hide empty sections. | P3 | `components/DramaDetailPanel.tsx` |

### 3.2 Workspace shell

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| Drama picker plus Refresh; project header with stepper; 7 stage tabs; tab defaults to the drama's current stage (`_current_stage_index`). | Header with Back link, title, status badge; 5 stage links; always opens on the default stage. `App.tsx` shows a permanent muted paragraph, "Preview of the new React frontend. The Workspace stages still live in the Streamlit app." | Remove the stale paragraph (or make it a one-line dismissible banner). Open a drama on the stage implied by `drama.status`; the mapping must be ported from `workspace_tab.py` near line 1577 (**inferred**, read it first). The roadmap notes "no exposed stage index": if the API cannot supply it, mark blocked and use `drama.status` only. | P1 | `App.tsx`, `pages/workspace/WorkspaceShell.tsx`, `router.ts` |

### 3.3 Workspace: Source and Transcribe

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| Source tab: source language select, Chinese script radio (zh only), "What are you working from?" radio (3 options), import method radio, one uploader or text area. Optional imports are expanders: OCR, EPUB, "Build a glossary from this novel", raw novel. | `SourceStage` stacks 3 panels: Media (upload, status line, own Upload button), Transcribe (config form + run form + 3 buttons), Novel. | Merge Media and Transcribe into one "Transcribe" panel: file picker, primary `Transcribe` (uploads then transcribes when a file is chosen), one-line media status ("Audio attached"). Remove "Upload selected file and transcribe" as a separate button; keep plain `Upload` only as a secondary action. | P1 | `stages/SourceStage.tsx`, `stages/TranscribeStage.tsx` |
| Visible: "Speech recognition model" select (default from drama, `large-v3` hint in help), Fast mode and Groq checkboxes, "Run speaker diarization" checkbox + "Expected speakers". Expander "Recognition accuracy (free)": beam size, min silence ms, VAD threshold, initial prompt, vocal separation, re-align; nested "Auto-tune". One primary `Transcribe & Align`. | `ConfigForm` ("Transcribe options" fieldset with its own "Save options" button) plus "Run options" fieldset: language, script, transcript text, Initial prompt, Expected speakers, "Detect speakers after transcribing", 3 equal-weight buttons. | Visible: Whisper model, "Detect speakers" checkbox, primary button. Into `Advanced`: beam size, min silence (ms), VAD threshold, initial prompt, separation backend, realign, ASR backend, alignment method, hardsub OCR fields. Auto-save `ConfigForm` fields on run instead of a separate "Save options" click. `Detect speakers only` becomes a link button in the diarization row. | P1 | `stages/TranscribeStage.tsx` |
| Source language and script save to the drama on change, no button. | Language and script are per-run fields inside Transcribe. | Show as a one-line drama chip ("Chinese, Simplified") with an edit popover that saves on change. Needs a drama update endpoint (**inferred** to exist via Library detail; verify). | P2 | `stages/TranscribeStage.tsx`, `components/DramaDetailPanel.tsx` |
| Disabled button: "Still needed before this can run: ...". Uncached-model note appears only when relevant. | "Whisper model will be downloaded on first use" paragraph always; no blocker text. | Add the one-line blocker; move the model-download note into the model select's help, or show it only when uncached. | P2 | `stages/TranscribeStage.tsx` |
| Novel text and glossary-from-novel are shown for `novel_narration`, optional otherwise. | `NovelPanel` always rendered. | Render only when `drama.content_mode === 'novel_narration'`, else inside `More` (`content_mode` is already used by `ExportStage`). | P2 | `stages/NovelPanel.tsx`, `stages/SourceStage.tsx` |

### 3.4 Workspace: Translate stage

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| Style preset (with expander "What this style asks the translator for"), a few checkboxes, instructions text area, expander "Series glossary & term handling"; then Translation engine + model; style note; "English variant"; save preset; 3 sliders (context before 0-20 default 6, context after 0-20 default 3, lines per request 5-40 default 20; novels 10/6/30); primary `Translate all lines` gated by key/Ollama/monthly-cap warnings. | One `Translate run` panel: counts paragraph, a 10-field fieldset (engine, model, style preset, locale, style note textarea, batch 1-200, context before 0-100, context ahead 0-100, cost cap, fallback list with Add/Remove, force + confirm), then `Estimate cost` and `Start translation` at equal weight. Glossary and Characters panels always open below. | Visible: Engine + Model on one row, Style preset, Locale, then `Translate N lines` primary (N = `untranslated_count`) with `Estimate` beside it as a secondary link. `Advanced`: style note, batch (lines), context before (lines), context ahead (lines), cost cap ($), fallback engines, "Re-translate existing" with a compact confirm. Optionally narrow UI ranges to Streamlit's (0-20, 0-20, 5-40) while the API still accepts 0-100 and 1-200 (product choice). | P1 | `stages/TranslateStage.tsx`, `translateForm.ts` |
| Glossary and characters are collapsed expanders. | `GlossaryPanel` (231 lines) and `CharactersPanel` (96 lines) always open. | Wrap each in a collapsed `Section` with a count in the summary ("Glossary (14 terms)", "Characters (3)"). | P1 | `stages/TranslateStage.tsx`, `stages/GlossaryPanel.tsx`, `stages/CharactersPanel.tsx` |
| Counts and month spend appear only as warnings when a cap would be hit. | Always-visible paragraph "X of Y lines have no English yet. Spend this month: $A of $B." | Keep the untranslated count in the button label; show spend only inside the estimate or as a warning near the cap. | P2 | `stages/TranslateStage.tsx` |
| Engine, model, style, locale defaults come from Settings (`settings_default_engine`, model, style note). | Engine `Default (...)`, model `Engine default`; style and locale from the API. Every engine option reads "(key configured/not configured)". | Remember last-used engine/model/style per browser (rule 12). Show "(no key)" only where a key is missing. | P2 | `stages/TranslateStage.tsx` |
| Progress bar and message under the button. | `JobPanel` with heading "Job". | Inline progress line under the primary button while running; nothing when idle. | P3 | `stages/JobPanel.tsx` |

### 3.5 Workspace: Review

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| One row per line: start/end, speaker, source and English text areas, play button, SFX checkbox; one bulk `Save edits (this page)` (Ctrl+S); Alt+Up/Down flagged navigation. Toolbar popovers: Checks, AI refinement, Restructure lines; expanders: Find & replace, Search transcript, Review queue, Version history. | 4 stacked panels: AI review jobs, Lines, Find and replace, Records. Each line has an edit mode with Start, End, Speaker, Source, English and Save/Cancel; "Add note" opens a 3-field form. | Show the line as read-only text; click the English text to edit; Enter or Ctrl+S saves, Esc cancels. Start/End/Speaker/Source/Note behind an "Edit details" toggle. Add Alt+Up/Down for flagged navigation. | P1 | `review/LineRow.tsx`, `review/LinesPanel.tsx`, `review/review.css` |
| Filter and search share one area; Find & replace is an expander. | Filter select, search box, Search and Clear buttons; Find and replace is an open panel. | Fold Find and replace into the search bar as a `Replace...` toggle. Debounced search-as-you-type; keep Enter. | P2 | `review/LinesPanel.tsx`, `review/FindReplacePanel.tsx` |
| Checks, AI refinement, Restructure in three popovers; Bulk jobs only when pending. | `ReviewJobsPanel` shows one button per job kind, always. | One `AI review` menu or `Advanced` section listing job kinds; progress inline. | P2 | `review/ReviewJobsPanel.tsx` |
| Notes, translation memory, versions, history are per-feature expanders, collapsed. | `RecordsPanel` with 4 sub-headings (Notes, TM suggestions, Versions, Line history); whether empty ones render was not checked **(inferred)**. | One collapsed `Records` section with counts ("Notes 2, Versions 1"); hide empty subsections. | P2 | `review/RecordsPanel.tsx` |
| Count strip "N in this view - X flagged - Y untranslated". | Same as a muted paragraph plus a `Show` select. | Make the counts the filter chips ("All 412 / Flagged 12 / Untranslated 32"), replacing the select. | P3 | `review/LinesPanel.tsx` |

### 3.6 Workspace: Dub

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| Narration language radio (novel only), fallback TTS engine radio, Max speed-up (1.0-2.0) and Max slow-down (0.5-1.0) side by side, one primary `Generate dub track` / `Generate narration track`; download shown after the run. Pacing check is a collapsed expander with counts in its title. | Summary paragraph, Voice engine select, narration language or the two number inputs, BGM checkbox with a long label and a paragraph, blocker text, button, and a permanent "Downloading the finished dub track is not available yet." Pacing table is a separate open panel. | Visible: Voice engine + `Generate dub`. `Advanced`: Max speed-up (x) and Max slow-down (x) on one row; BGM checkbox "Keep background music" with the caveat as a tooltip. Show the "not available yet" note only after a run completes, and delete it when a download endpoint exists. Pacing table becomes a collapsed section whose summary is the count line. | P1 | `stages/DubStage.tsx`, `stages/NarrationPanel.tsx` |
| GPU note lives in help text. | "Uses the GPU" appended to the summary. | Badge only; when a track exists, summary is just "Dub track ready". | P3 | `stages/DubStage.tsx` |

### 3.7 Workspace: Export

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| Expanders, all collapsed: `Export subtitles` (format radio, base name, notes options, split long lines, "Subtitle style" used by .ass and burned-in video), `Export full subtitled episode`, `Vertical/shorts export`, `Export this drama as a package`. `Mark as exported` at the bottom. | Readiness panel (6 counts), then 5 open panels: Subtitle file (SRT/VTT), ASS subtitle file (about 14 fields), Flag lines for review, EPUB (novel), media jobs. | One `Export` panel: Format (SRT, VTT, ASS) + Language (English, Source, Both) + primary `Export`. Choosing ASS reveals a collapsed `Style`. Notes, wrap and base name in `Advanced`. Readiness becomes a one-line summary plus warnings only (untranslated, overlaps, test-mode output); "Flag lines" becomes a link in the warning. Burned-in and vertical video under a collapsed `More export`. | P1 | `stages/ExportStage.tsx`, `ExportSubtitles.tsx`, `ExportAss.tsx`, `ExportFlags.tsx`, `ExportMedia.tsx` |
| Choices persist across reruns through widget keys. | ASS form re-initialised from `default_preset` on every visit. | Remember last format, language and ASS preset (rule 12). | P2 | `stages/ExportStage.tsx` |
| ASS style controls live in one "Subtitle style" block. | About 14 controls always visible (Text, Preset, Font, colours, wrap at, per-speaker colours, 3 checkboxes). | Only Preset visible; Font, colours, wrap (chars), per-speaker colours inside `Style`. | P2 | `stages/ExportAss.tsx` |

### 3.8 Translate (standalone) page

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| Direction radio, source/target selects, engine select (label includes tier text), model select, key input if missing, upload + 250px text area, primary `Translate` and `Clear`, two-column result, History in expanders. | Engine, Model and 2 language selects; a list "key configured yes/no" for every engine; text area; submit; Result; History heading. | Drop the per-engine key list (show "no key" in the label or one warning for the chosen engine). Engine and languages on one row. Add `Clear`. History collapsed with a count. Remember last engine and languages. | P2 | `pages/Translate.tsx` |

### 3.9 Settings

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| Sidebar: dark mode toggle; "Common" with collapsed expanders (Reading experience, OCR, Defaults for new dramas, API keys & endpoints); "Advanced" (Offline networks, Performance, Spending, Downloads); Manage profiles. | One `Job options` checkbox list (`TOGGLES`) and an "API keys configured" Yes/No list plus a paragraph saying key entry is not available yet. | Group the checkboxes under headings matching Streamlit's (Performance, Spending, Notifications; actual keys in `api/settings.ts`, **inferred**), one line each with tooltip help. Replace the key list with one chip row ("Configured: Claude, Gemini. Missing: DeepSeek"); move the explanatory paragraph into a tooltip. Adding monthly cap, default engine/model/style fields is blocked on a settings write API (Slice 24). | P2 | `pages/Settings.tsx`, `api/settings.ts` |
| "Defaults for new dramas" feed later defaults. | None. | Blocked on API; `localStorage` (rule 12) covers most of it meanwhile. | P3 | API work |

### 3.10 Diagnostics

| Streamlit today | React today | Concrete change | Pri | Files |
|---|---|---|---|---|
| `Check my setup` (open) with one primary `Run diagnostics`; Running jobs and Model & engine versions open; job history, bug bundles, source access, model cache, pyannote, App Assistant, copy report, log, benchmark, danger zone collapsed. Dependencies grouped by tier with installed/total counts. | 3 open panels: Optional packages (full list), System, Jobs table. | Optional packages: one line "22/25 installed" plus only the missing ones, full list collapsed. System: collapsed unless something is wrong. Jobs: visible while running or failed, otherwise a collapsed `History`. Add `Copy report`. | P2 | `pages/Diagnostics.tsx` |

## 4. Shared building blocks (do first)

| Task | Change | Pri | Files |
|---|---|---|---|
| `Section` component | `<details>`-based: `title`, optional one-line `summary` (current values), `defaultOpen`, open state remembered in `localStorage` (try/catch). Styles in `index.css`. Returns `null` when told the content is empty. | P1 | new `components/Section.tsx`, `index.css` |
| `Field` component | Label + control + optional `help` tooltip + unit suffix; replaces ad hoc `<label>` wrappers and `.form-grid`. | P1 | new `components/Field.tsx`, `index.css` |
| `usePersistedState` hook | Typed, namespaced `localStorage` read/write with try/catch; used for last-used engine/model/format. | P2 | new `hooks/usePersistedState.ts` |
| Shortcut hook | `useShortcut('mod+s', handler)`; mod is Ctrl or Cmd; ignores IME composition; helper for tooltip text. | P2 | new `hooks/useShortcut.ts` |

## 5. Suggested order of work

1. `Section` and `Field` (section 4), one task.
2. Library summaries (3.1 row 1) and the stale `App.tsx` banner (3.2).
3. Translate stage (3.4 rows 1-2), Transcribe (3.3 rows 1-2), Export (3.7 row 1), Dub (3.6 row 1).
4. Review line row (3.5 row 1) with Ctrl+S and Alt+Up/Down.
5. Remaining P1 (Library order), then P2, then P3.

Whole-layout rewrites follow the root `CLAUDE.md` screenshot rule; most rows here are localized. Many
tests select by label text, so shortening a label also means updating those tests in the same task.

## 6. Limits and open questions

- Some Streamlit behaviour has no API yet (OCR, EPUB import, playback, dub download; Auto-fill and Analyze
  are live on the React Source stage, and per-line "Improve translation"/"Why this?" now have an API,
  `/api/line-ai` from Slice 49, with UI pending). These guidelines do not ask
  React to add UI for endpoints that do not exist.
- The user asked for "concise like Streamlit". Streamlit is concise mainly because it hides options in
  expanders, not because it has fewer options. These guidelines keep every existing React function
  reachable and only reorder and collapse. If the user wants fewer functions (not just fewer visible
  controls), that is a product decision to confirm per feature.
- Streamlit's slider ranges (0-20, 5-40) are narrower than the API's (0-100, 1-200). Narrowing is a
  choice, not a requirement.
- Defaults held in Streamlit session state (`settings_*`) are not exposed by the settings API, so until
  they are, React can only remember per-browser choices.
- Click counts and visual density were not measured in a browser; the screenshots in the lead's scratchpad
  cover only Library and Translate.
