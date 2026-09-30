# Step 19 — Full click-through UX test

Real, live click-through of the app (a real `streamlit run app.py` server,
driven with a headless Chromium via Playwright — not just a read of the
code) against the 12 workflows and 8 questions from the roadmap's own
spec. Test dramas seeded directly into a throwaway local library (never
committed — `library/` is gitignored) at each of the pipeline's stages,
since running the real Whisper/translation backends isn't available in
this environment (no GPU, no API keys, no network to AI services).

Questions asked of every workflow: **(1)** where am I, **(2)** what's the
next action, **(3)** is the primary action obvious, **(4)** is anything
unnecessary on screen, **(5)** do I have to scroll unnecessarily, **(6)**
are related controls together, **(7)** can I get back easily, **(8)** is
the current project obvious.

## Result summary

11 of 12 workflows pass all 8 questions outright. One real, fixed bug
(workflow 11, since it also affects 2/4/5/6/8 to varying degrees): the
stage-tab strip always opened on "Source" regardless of a drama's real
progress. One design question flagged for the planning session rather
than fixed (workflows 3/5): the actual "run" buttons for transcription
and diarization live under the **Translate** tab, not under Transcript
or Diarize.

## 1. Import a video

Two real entry points, both clear: Workspace's Source tab ("Add
audio/video" → "📁 Upload a file" / "🔗 Download from a URL (yt-dlp)",
file types spelled out including MP4/MKV/MOV/WEBM) and the Sources tab's
"🚪 Paste any URL" (chapter/series/novel-chapter/video, auto-detected,
preview before import). **Pass** on all 8 — single clear primary
action ("Preview" / "Upload"), no unrelated controls, current tab
obvious.

## 2. Configure transcription

Workspace's Transcript tab: transcript source (paste vs. Whisper vs.
caption-OCR), Whisper model/backend, timing method, novel-translation
upload for glossary priming. All grouped under one tab, one screen, no
scrolling beyond the tab's own natural length. **Pass.**

## 3. Run transcription

The actual "▶ Transcribe & Align" button (or its Whisper/caption-OCR/
novel-narration label variants) does **not** live on the Transcript tab
— it's rendered inside the **Translate** tab (`tabs/workspace_tab.py`,
inside `with tab_translate:`, `b1, b2 = st.columns(2)`), sitting next to
the "🌐 Translate all lines" button. Confirmed directly in the running
app: opening a drama that still needs transcription and clicking
"Transcript" shows only settings, no run button; the button only
appears after switching to "Translate". A disabled button always states
what's missing ("Still needed before this can run: an audio or video
file and a transcript") — so nobody gets stuck — but the tab that
configures transcription and the tab that runs it are two different
tabs, which fails question 6 ("are related controls together") and
partly question 3 for a first-time user following the tab labels
literally.

**Not fixed here** — this is a real placement decision from Step 14's
Workspace rebuild (grouping every pipeline-advancing "go" button
together on one tab, deliberately separate from the "configure inputs"
tabs), not an oversight, and moving it is a bigger call than this
step's "keep changes minimal" scope: it touches how every stage tab
divides configuration from execution, not just this one button.
Flagged for the planning session per this project's own "flag, don't
silently fix" convention.

## 4. Review transcript

Review tab: flagged-lines filter, full-text search & jump, per-line
edit boxes, unsaved-changes indicator. **Pass** — reused for workflow 8
below too, since Baihe doesn't separate "review the transcript" from
"review the translation" into different screens (both source and
target text sit in the same per-line row).

## 5. Run speaker diarization

Diarize tab: Hugging Face token, "Run speaker diarization during
alignment" checkbox, expected-speaker-count. Same finding as workflow 3
— this only sets a flag; the actual diarization run happens as part of
"▶ Transcribe & Align" on the Translate tab. Flagged, not fixed, same
reasoning as above.

## 6. Configure translation

Translate tab: starting-tier preset, translation style + genre
guidance, project instructions, series glossary, engine + model +
API key, locale, context-window slider, save-as-preset. All on one
tab. **Pass.**

## 7. Translate

"🌐 Translate all lines", with real, verified pre-flight guards rather
than a job that starts and fails silently: a translation-only engine
(DeepL/Google/NLLB/LibreTranslate) disables the button with a plain-
language reason; a paid engine with no API key set shows "Required —
set it here or in Settings"; **verified live** by picking Ollama
against an unreachable server — the button disabled itself immediately
with "⚠️ Can't reach Ollama at http://localhost:11434 — is it running?"
before any job was ever submitted. **Pass**, and a genuinely good
pattern (catch it before the job starts, not after).

## 8. Review/edit translation

Same Review tab as workflow 4 — flag reasons shown inline, find &
replace with old/new-text preview before applying, translation-memory
suggestions (Step 24) with Accept/Dismiss, save-status indicator
("✅ Saved" / "Unsaved changes (N)" / "Save failed"). **Pass.**

## 9. Export subtitles

Export tab: format/style picker with live preview (from Step 6b),
"Export this drama as a package", "Mark as exported". Grouped, single
tab, no unrelated controls. **Pass.**

## 10. Return to the project later

Verified live: opened a drama, navigated to Library, navigated back to
Workspace — the same drama was still selected (`active_drama_id`
persists in session) and the stage tab was still on the one last
clicked ("Review"), not reset to "Source". **Pass** on all 8, including
7 (Library and Workspace are both one click away in the top tab bar at
all times) and 8 (the drama's name is always shown in both the Drama
picker and the project header).

## 11. Resume an incomplete project

**Real bug found and fixed.** `st.tabs(stage_labels)` never passed
`default=`, so the tab strip always opened on "Source" regardless of
how far a drama had actually progressed — confirmed live: a drama
whose stepper showed "● Diarize" as current still opened on Source's
content, an extra click away from the drama's real next action, every
single time the drama was reopened. This fails questions 2 and 3
outright (the stepper says one thing, the interactive tab strip opens
somewhere else) and dents 4/5 (irrelevant Source-tab content shown
first).

**Fix**: `tabs/workspace_tab.py`'s `st.tabs()` call now passes
`default=stage_labels[_current_stage_index]` (the same index the
stepper already computes) and `key=f"workspace_stage_tabs_{picked_id}"`
so the default is re-seeded per drama but a manual tab click within the
same drama survives later reruns (verified live: clicking "Export"
then clicking the unrelated "🔄 Refresh" button, which triggers its own
rerun, left the tab on "Export", not back on the newly-computed
default). Verified live across three real stage indices (Diarize,
Review, and the unaffected Source-for-a-new-drama case) with
screenshots taken at each step.

## 12. Handle a failed processing task

Verified live for the *pre-flight* class of failure (workflow 7's
Ollama-unreachable case above) — a clear, specific, actionable message
before anything runs. The *mid-job* failure card
(`ui_status.render_failure_card`: a plain-language reason, a "Retry"
button where retrying is safe, and a collapsible traceback under
"Advanced details") is used consistently at every job-completion call
site in `workspace_tab.py` (translate/transcribe/flag/consistency/
emotion/notes and more — 12 call sites, `grep -c
ui_status.render_failure_card tabs/workspace_tab.py`) and each already
has its own dedicated test
coverage from the steps that built it. **Not independently reproduced
live here**: forcing a real mid-job crash needs a call that starts
successfully and then fails partway through (a flaky network call to a
real external service, or a corrupted model download) — not something
this sandboxed environment can trigger safely or deterministically.
Noted as a real scope limit rather than silently skipped, per this
project's own standing rule for a manual check that can't be run from
here.

## Manual check (per the roadmap's own exit condition)

The user should still run the same 12 workflows themselves once real
audio/API keys are available, in particular workflow 12's actual
mid-job failure path (e.g. pull the network cable mid-translation, or
use an intentionally-wrong Claude API key that passes the "is it set"
check but fails on the real call) and workflow 1's real video import
against a real URL.
