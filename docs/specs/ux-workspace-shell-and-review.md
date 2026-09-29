# UX spec: Workspace shell and Review editor (Phase 2)

Status: proposal from a read-only `ux-designer` pass, 2026-09-29. Direction from the user: "a pro editor app that is still minimal and stylistic". Both specs are **structural redesigns** under the root `CLAUDE.md` screenshot rule (before/after screenshots required).
Seen as images: `scratchpad/shots/phone/workspace-review-phone-light.png`, `shots2/desktop/workspace-review-top-desktop-light.png` (40 lines), `shots2/desktop/workspace-review-flagged-desktop-dark.png`, `shots2/desktop/workspace-translate-desktop-light.png`, `shots2/phone/workspace-review-{top,edit}-phone-light.png` (structure only), `shots2/metrics.json`. Only reasoned about: phone dark shell, 360px, tablet, reduced motion, running-job/playback/restructure states.

**Decisions (user, 2026-09-29):** (1) the API version badge moves into Diagnostics and the header shows it only when the API is down: yes. (2) single-line delete uses the two-step "Confirm delete" button (re-segment and restore keep the typed word). (3) stage progress and counts wait for the progress endpoint: no interim mapping from `drama.status`; build `GET /api/workflow/dramas/{id}/progress` first, then the stage bar.

## Evidence (code, 2026-09-29)

- Library nav stays active inside a drama (`App.tsx:32`); the API badge is always in the header (`App.tsx:13-28,56`; asserted by `e2e/library.spec.ts:7`); Diagnostics does not show `api_version`.
- A drama always opens on Source: `router.ts:21` fills `DEFAULT_STAGE`; `DramaDetailPanel.tsx:81` hard-codes `stage: 'source'` (`router.test.ts:21` asserts it).
- Stage links are plain text, no status/counts (`WorkspaceShell.tsx:30-40`); on phone the first content starts at about y=390 of 844.
- Review is four stacked panels (`ReviewStage.tsx:18-21`); filter/search not sticky (`LinesPanel.tsx:81-109`); pager bottom only (`:117-123`); shortcut help is a `title` tooltip only (`:67-74`, fails rule 11); Alt+Up/Down only moves between flagged rows on the page (`:43-58`); save closes the editor with no advance (`LineRow.tsx:55-60`); meta row 0.8rem (`review.css:10`); about 176px per line on phone.
- No React code calls `/api/restructure/*`, `/coverage`, `/pacing-flags`, `/provenance` or `/original-text`.
- "Translate 0 lines" is enabled (`TranslateStage.tsx:81,139-141`).
- No "set a flag" endpoint (`LinesPatchRequest`, `api/schemas.py:1007-1017`, has no flag field; only `dismiss-flag`). It accepts `sfx`, which the React `LinePatch` omits (`types/review.ts:44-52`).
- Restructure writes need `expected_line_ids` for the whole drama (`schemas.py:1402-1406`, `restructure_service.py:93-94`); Review loads 40-line pages.
- Job ids are `<kind>_<dramaId>` across services. `compute_workspace_stage_index` (`services/workflow_service.py:10-56`) returns 0-6 on the 7-tab scale; no endpoint yet.
- `FILE_ORGANIZATION.md:344-349` is stale for `frontend/src/components/` (lists only LibraryList and DramaDetailPanel).

## Spec 1: Workspace shell

**Goal:** always know which drama I'm in, how far along it is, what to do next and whether something is running, from any stage, on any screen. **Happy path:** open a drama from Library and land on the stage where the work is; press that stage's one primary action; watch the job pill and follow the "Next" cue when it finishes.

**Structure.** A. App header: brand and main nav; inside a drama no nav item gets `aria-current` (the breadcrumb carries location); the API badge renders only when `Connecting…` or `API unreachable`; "API v0.1 · production" moves to Diagnostics → System (keep `data-testid="api-status"` there; repoint `e2e/library.spec.ts:7`). B. Workspace bar (new `WorkspaceBar`, sticky): breadcrumb "Library" / one-line drama title / status chip / job pill (only when a job for this drama runs). C. Stage bar (restyled `nav.stage-tabs`, sticky with B): five stage links, each with a state glyph and a one-line count. D. Next-step cue: one text link under the stage bar, shown only when viewing a stage that is not the pipeline-current one (a link, not a button: the stage's own primary stays the only primary).

Desktop (≥ 900px):
```
Baihe Studio      Library  Translate  Settings  Diagnostics                (no badge)
Library / Grandmaster of Demonic Cultivation  [translated]       (● Translating 42%)
 ✓ Source      ✓ Translate      ● Review         Dub          Export
   Audio         26/40 done       4 flagged       optional     not ready
 Next: Review 4 flagged lines →          (only when viewing another stage)
```
Phone (360-430px):
```
Baihe                                         [Menu ▾]   (not sticky; 44px targets)
‹ Grandmaster of Demonic Cul…  [translated] (● 42%)      (sticky, 48px)
 Source  Translate  Review  Dub  Export                  (sticky, 5 equal cells ≥44px)
   ✓        ✓       4⚑     –     –
Next: Review 4 flagged →
```
- Phone: main nav collapses into a `<details>` "Menu" at ≤ 480px; "‹" is a 44×44 link named "Back to Library"; title one line with ellipsis; B+C sticky height ≤ 112px phone, ≤ 96px desktop; stage cells never scroll sideways (count line is glyph plus number). Tablet 600-900px: desktop layout with counts on the second line.
- **Stage status.** Map `compute_workspace_stage_index`: 0-2 → source, 3 → translate, 4 → review, 6 → export (5/Dub is never current; Dub shows "optional" unless a track exists). Interim until the endpoint exists (decision 3): `drama.status` not started → source, aligned → translate, translated → review, dubbed/exported → export; counts from `GET /api/review/dramas/{id}/lines?page=1&page_size=1&only=all` (`total`, `flagged_count`, `untranslated_count` are whole-drama); `has_audio` from `DramaDetail`.

| Stage | Count line (desktop / phone) | Done when |
|---|---|---|
| Source | "Audio" / "No media" / "40 lines" | index past 0-2 |
| Translate | "26/40 done" / "14" | untranslated = 0 and lines > 0 |
| Review | "4 flagged" / "4⚑"; "No flags" | index ≥ 6 |
| Dub | "optional" / "Track ready" | track exists |
| Export | "not ready" / "ready" / "exported" | status = exported |

Glyphs: done "✓" plus hidden "done"; current accent dot plus hidden "next step"; the viewed stage keeps the underline and `aria-current="page"`. Never colour alone.
- **Landing (rule 13):** `#/drama/3` resolves after load to `#/drama/3/<current>` with `location.replace`; an explicit stage is always honoured; "open drama" links drop `stage`; `router.ts` makes `stage` optional; while loading render the bar skeleton and no stage (no Source flash).
- **Job pill:** poll `GET /api/jobs` every 3s while mounted and visible; keep `queued`/`running` items whose job id's suffix after the last `_` equals the drama id exactly (interim; a `drama_id` on `JobRecord` is an API gap). Pill "● Translating 42%" (no % when progress is null) is a ≥44px button opening a popover with the existing JobPanel content and "Cancel job"; multiple jobs "● 2 jobs"; on finish shows "✓ Done"/"Failed" for 5s and refetches drama and counts. Stage-local JobPanels stay. Add `runningJobs` to `StageContextValue` so stages can disable with a reason.
- **Primaries never enabled with nothing to do (rules 1, 2):** e.g. Translate "Translate 0 lines" (enabled) → "Translate" (disabled) plus "Still needed: nothing to translate — all 40 lines have English. To redo them, use Re-translate existing under Advanced."
- **States:** loading (title placeholder "Drama #3", stage labels without counts); error (ErrorBanner under the bar, stage bar still usable); empty drama (Source current "No media", others "–", each stage's empty state links back, e.g. "No lines yet. Transcribe on Source first."); running job (pill; stage primaries that would 409 disabled with "Still needed: the translate job to finish."); API unreachable (header badge; pill hidden).
- **Copy:** header badge → hidden unless down; "Back to Library" → breadcrumb "Library /" (desktop) or "‹" (phone); stage "Review" → "Review" + "4 flagged"; new cues "Next: Translate 14 lines →", "Next: Review 4 flagged lines →", "Next: Export subtitles →", "Next: Transcribe the audio →", "Next: Add an audio or video file →"; job popover title "Running on this drama".
- **Keyboard/a11y:** new first focusable "Skip to stage content"; tab order skip → nav → breadcrumb → pill → stages → cue → content; Alt+1…5 switch stage (not in text fields or IME composition); stage bar stays a `<nav>` of links; Esc closes the pill popover; `scroll-padding-top: var(--bar-h)`; verify `--muted` on `--surface` ≥ 4.5:1 in both themes; no pulse under reduced motion.
- **Files:** `App.tsx`, `router.ts`(+test), `WorkspaceShell.tsx`, `StageContext.ts`, `stages.ts`, new `WorkspaceBar.tsx`, `StageBar.tsx`, `JobPill.tsx`, `useWorkspaceProgress.ts`, pure `stageProgress.ts` (unit-tested), `DramaDetailPanel.tsx:81`, `Diagnostics.tsx`, `TranslateStage.tsx`, `index.css` (first width breakpoints `max-width: 480px` and `min-width: 900px`; new tokens `--touch: 44px`, `--bar-h`, `--z-sticky`, `--row-active`), e2e `library.spec.ts`, `workspace.spec.ts`, `FILE_ORGANIZATION.md`.
- **API gaps:** `GET /api/workflow/dramas/{id}/progress` (`{stage_index, stage, line_count, untranslated_count, flagged_count, has_audio, has_dub_track, exported}`); `drama_id` on `JobRecord` or `GET /api/jobs?drama_id=`; dub-track existence (check `/api/artifacts/dramas/{id}/{kind}/info`).
- **Acceptance (yes/no):** no main-nav `aria-current` inside a drama and the breadcrumb shows it; badge absent when healthy, "API unreachable" when stopped, version in Diagnostics; `#/drama/<id>` lands on the mapped stage with no Source flash and Back returns to Library in one step; explicit `/dub` stays on Dub; every stage shows a count line and state by glyph plus text; "Next:" is a link, hidden on the current stage, at most one filled primary per panel; a running job shows the pill on every stage with Cancel in 2 taps and another drama's job does not; Translate disabled with "Still needed:" at 0 untranslated; at 360 and 390 no horizontal scroll, all five stages visible, every bar control ≥ 44px, sticky bar ≤ 112px; tab order as specified with visible focus in both themes; switching dramas remounts with no stale counts. Screenshots before/after: phone 390×844 touch light and dark, phone 360×740 light, desktop 1440×900 light and dark; states: empty drama, drama with lines, running translate job, Diagnostics version line. Rules 1, 2, 9, 10, 13, 14, 15.
- **Preserve:** every stage URL, `drama-title` testid, error banner, loading state, drama-keyed remount, `StageContext` fields, all four main nav destinations, API reachability feedback.

## Spec 2: Review as a pro subtitle editor

**Goal:** move line by line, fix translations fast by keyboard (or thumb), hear each line and fix segmentation without leaving Review. **Happy path:** land on Review with the first flagged or untranslated line active; Enter to edit, type, Enter to save and the next line opens; Alt+↓ to the next flagged line and repeat.

**Model:** one **active** row (roving `tabIndex`; arrows and J/K move it) separate from edit mode. Active row: `--row-active` background plus a 3px `--accent` left rule; the flagged `--bad` rule stays on the outer side. Initial active line: first flagged, else first untranslated, else line 1.

Desktop (≥ 900px):
```
[workspace bar + stage bar — sticky]
┌ Review toolbar (sticky) ─────────────────────────────────────────────────────┐
│ [All 40] [Flagged 4] [Untranslated 14]  [🔍 Search source or translation ] × │
│ Replace…   ‹ 1/3 ›  Go to #[   ]   Keys ?                                    │
├ Player strip (only if media) ────────────────────────────────────────────────┤
│ ▶ 0:12.00 / 24:10  ▮▮▮▮▯▯▮▮▯▮▮▮ ← page line segments, playhead │ Loop line □ │
└──────────────────────────────────────────────────────────────────────────────┘
 #5  0:12.00–0:14.60  Lan Wangji   ⚑ uncertain_translation · Awkward phrasing   [⋯]
 第5句：我从没想过…                     │ Line 5: I never thought the mountain…
 ┄┄ active row only: ▶ Play · Edit details · AI ▾ · Split · Merge ↓ · Dismiss flag
 ‹ 1/3 ›  (bottom pager kept)
 ▸ AI review (5)   ▸ Structure   ▸ Records (n)     (collapsed Sections)
```
- Video source at ≥ 1200px: sticky 16:9 pane on the right (max 40% width), list on the left; "Hide video" remembered. Meta row moves to `--font-sm`, times `tabular-nums`; only the flag note truncates.

Phone (360-430px):
```
‹ Grandmaster…  [translated]           (sticky)
Source Translate Review Dub Export     (sticky)
[All 40][⚑ 4][Untr. 14]  [🔍]  ‹1/3›   (sticky toolbar; search expands full width)
 #5 0:12.00  Lan Wangji  ⚑                          [⋯]
 第5句：我从没想过今晚的山…
 Line 5: I never thought the mountain would…
[▶ Play #5] [‹ Prev] [Next ›] [Edit]   ← sticky bottom action bar (56px, safe-area), active line only
```
- Target about 96px per line (today about 176px). Tap a row to make it active; tap the active row's translation to edit. Edit mode bottom bar `[Cancel] [Save] [Save & next]` (Save & next primary); textarea `enterkeyhint="next"`. "⋯" opens a bottom sheet (`<dialog>`) with 48px rows: Play line, Edit details, Improve translation (AI), Why this? (AI), Split line…, Merge with next…, Add line after…, Delete line…, Dismiss flag, Add note. Audio player is a compact strip in the toolbar; the timeline strip is hidden below 480px; video collapsible above the list, collapsed by default. Inputs: search `type="search"`, Go to # `inputmode="numeric"`, Start/End `inputmode="decimal"`. All controls ≥ 44×44.
- **Toolbar:** filter chips (radio group with counts) replace the `Show` select; search-as-you-type (300ms debounce), "×" clears, results mode "12 matches (max 200)"; "Replace…" expands the existing FindReplacePanel inline; pager top and bottom ("‹ 1/3 ›", `page-label` testid on the top one); Go to # (only once `idx` is verified as the 1-based contiguous position, else it waits for the line-index endpoint); "Keys ?" opens a shortcut sheet (replaces the `title` hint; coordinate with the mobile pass's tap hint so there is one control).
- **Player (needs slice 52):** native `<audio>`/`<video>`, `preload="metadata"`, src = the Range endpoint; render nothing without audio (rule 8). Play line seeks to start, pauses at end; Loop line; "Follow playback" off by default. Desktop timeline strip (P2): 20px bar of this page's segments plus playhead, `aria-hidden`; no waveform (needs a peaks endpoint). Errors: "Couldn't play the audio. Check the file on Source."
- **Structure ops** (restructure API; all send `expected_line_ids`; interim: page through `?page_size=200&only=all` at confirm time; correct fix: the line-index endpoint): refused while a job runs (pre-disabled with the reason); on a line-mismatch 409 "Lines changed since this page loaded. Reload and try again."; on success refetch, keep the new/merged line active, status toast "Split #12 into #12–#13. Undo in Records → Line history."

| Op | Entry | UI | Confirm |
|---|---|---|---|
| Split | "Split…" or Alt+Enter while editing | caret sets `at_char`/`en_at_char`; live preview; "Split at (s)" prefilled | explicit **Split line** button |
| Merge | "Merge with next…" (M); Shift+↓ extends (≤ 50) | preview of joined text | button "Merge #5–#7" |
| Add | "Add line after…" (A); "Add first line" in the empty state | Start/End prefilled from the gap, Speaker, Source, Translation | none |
| Delete | "Delete line…" (Shift+Delete) | button becomes "Confirm delete #12" (danger), 5s timeout | two-step (decision 2) |
| Re-segment | Structure → "Preview re-segmentation" (read-only) → summary "40 → 46 lines; 6 change; 3 translated, 1 flagged, 0 notes would be split" | "Use AI"; engine/model under Advanced | type `resegment`; runs as a job |
| Restore | Records → Line history → "Restore…" | snapshot label/time; "Your current lines are saved as a snapshot first." | type `restore` |

Verify that `restructure_routes.py:71` and `review_records_routes.py:33` return the same snapshots before wiring Restore onto the Records list.
- **Per-line AI:** keep `LineAi` behaviour (nothing saved until "Use this", stale guard, 503 message with Settings link). Placement: active-row toolbar (desktop), sheet (phone). Disabled with a reason when there is no English ("Improve (needs a translation first)"; today hidden, `LineRow.tsx:142`). Keys I and W.
- **Keyboard** (single letters only in the list, never in inputs, no Ctrl/Meta, not while composing; every shortcut has a visible control and is in the sheet): ↓/J ↑/K next/previous line (crosses page boundaries); Alt+↓/↑ next/previous flagged (kept; at the page's last flagged line a status line offers "Next page ›"); ]/[ page; Enter or E edit; Enter while editing = save and edit next (stay on failure); Ctrl/Cmd+S save and stay; Shift+Enter newline; Esc cancel/close; D edit details; Space play/stop line (media only); Alt+Space play/pause; L loop; Alt+Enter split at cursor; M merge; A add; Shift+Delete delete; F dismiss flag (setting a flag has no endpoint); I/W AI; / search; ? shortcuts. Moving away from a dirty draft saves first; if the save fails, the move is cancelled (never silently discard).
- **States:** loading (toolbar without counts, 6 static skeleton rows); empty drama ("No lines yet. Transcribe on Source first." plus "Add first line"); empty filters ("No flagged lines. Nice.", "Every line has English.", "No lines match “term”." each with an "All lines" link); running job (AI review JobPanel and the shell pill; structure ops disabled with "A job is running on this drama. Structure edits wait until it finishes."; line text edits stay allowed, compare-and-set); errors (list ErrorBanner under the toolbar; existing per-row banners and 409 Reload); done (structure toast; "Saved" text after save).
- **Copy:** Show select → chips "All 40" · "Flagged 4" · "Untranslated 14" (phone "⚑ 4", "Untr. 14" with full aria-labels); keep a visually hidden `line-counts` summary for `review-stage.spec.ts:54` or update the test; "Search"/"Clear search" → search-as-you-type and "×"; "?" tooltip → "Keys ?" sheet "Keyboard shortcuts"; pager "Previous/Next/Page 1 of 3" → "‹"/"›" with aria labels, `page-label` keeps "Page 1 of 3" as accessible text; "Save line" → "Save" + "Save & next"; hint → "Enter save & next · Shift+Enter new line · Esc cancel"; "AI ▾" → "Improve translation" / "Why this?".
- **Sections below the list:** AI review (existing); new collapsed **Structure** section ("Re-segment · restore", only when there are lines); Records (adds Restore…). Find and replace moves into the toolbar with the same logic.
- **Files:** `LinesPanel.tsx`, `LineRow.tsx`, `review.css`, `ReviewStage.tsx`, `FindReplacePanel.tsx`, `RecordsPanel.tsx`, `LineAi.tsx`, `reviewLogic.ts` (page-for-idx, caret-to-split, dirty check; unit-tested); new `ReviewToolbar.tsx`, `LineActionsSheet.tsx`, `SplitDialog.tsx`, `MergeConfirm.tsx`, `AddLineForm.tsx`, `StructureSection.tsx`, `Player.tsx`, `TimelineStrip.tsx` (after slice 52), `ShortcutSheet.tsx`, `components/TypedConfirm.tsx`, `components/Sheet.tsx`, `hooks/useShortcut.ts`, `hooks/usePersistedState.ts`; new `api/restructure.ts` and `types/restructure.ts`; a media URL helper; `LinePatch` gains `sfx?`; e2e `review-stage.spec.ts`; `FILE_ORGANIZATION.md`.
- **API gaps:** `GET /api/review/dramas/{id}/line-index` → `{line_ids, flagged_ids, untranslated_ids}` (for `expected_line_ids`, cross-page flag navigation, exact Go to #); `POST …/lines/{id}/flag {flag, flag_note}`; slice 52 media stream; waveform peaks (not requested); `drama_id` on `JobRecord`. Coverage, pacing, provenance and original-text endpoints exist but stay out of scope (backlog: a "What happened here?" item in the ⋯ sheet).
- **Acceptance (yes/no):** toolbar still visible after scrolling 30 lines on phone and desktop; pager above and below; ↓/J moves the active line with a visible ring in both themes and crosses pages; Enter saves and opens the next editor, Ctrl/Cmd+S saves and stays, a failed save keeps the draft; dirty drafts never discarded by navigation; Alt+↑/↓ work with a cue at the page's last flag; with media, Space plays exactly start→end and Loop repeats, without media no Play controls; split/merge/add/delete reachable by keyboard and by touch and each sends `expected_line_ids`, delete two-step, re-segment and restore typed; structure ops disabled with the reason during a job; at 360 and 390 no horizontal scroll, every control ≥ 44×44 (Review "small" count 110 → 0, inline prose links excepted), bottom bar clear of the safe area, about ≤ 100px per line; no hover-only affordance; every existing Review function still reachable (filter, search, clear, paging, counts, click-to-edit, Edit details, Add note, Dismiss flag, AI improve/explain with guards, 409 Reload, AI review's 5 jobs and Cancel, find/replace preview/apply with the stale list, Records note delete, TM accept, versions, history); at most one filled primary per panel/sheet; Section/Field reused; no inline `style={{}}`; focus visible in both themes; no animation under reduced motion; chip and meta text ≥ 4.5:1. Screenshots before/after: phone 390×844 touch light and dark (top, mid-scroll, editing, sheet open, split dialog, flagged filter, empty drama), phone 360×740 light (top), desktop 1440×900 light and dark (top, editing with Save & next, re-segment typed confirm, player strip once slice 52 lands). Rules 1, 2, 4, 5, 6, 8, 9, 10, 11, 12, 14, 15.
- **Preserve:** all `data-testid`s (`line-*`, `line-en`, `line-flag`, `line-conflict`, `line-ai-panel/result/suggestion/explanation/unavailable`, `line-counts` or an updated test, `page-label`, `fr-matches/result/stale`, `notes-list`, `tm-list`, `versions-list`, `history-list`, `job-panel`); compare-and-set patch semantics; identity by permanent line `id`, never position (matters for split/merge and AI results); the `reloads` refetch after any write or job; Section storage keys `review.ai`, `review.records`, `review.findreplace`.

**Coordination:** the mobile pass (#337, merged) added 44px targets and a tap hint for Review shortcuts; this spec replaces the hint with "Keys ?" and adds `--touch`. One writer should own `review.css` and `index.css` at a time.
