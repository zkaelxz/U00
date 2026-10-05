# Technical notes & fix history

This is an engineering changelog: real bugs found during development,
how they were diagnosed, and how they were fixed — kept separate from
the main [README](../README.md) so that stays focused on using the app.
Nothing here is required reading to use Baihe Studio; it exists for
whoever is extending or debugging the codebase (including a future
session picking this project back up).

## Why translation used to stop when switching tabs

Every tab's content renders in the same script execution regardless of
which one is visually active -- `st.tabs()` is a client-side CSS toggle,
not a separate script per tab. So switching tabs by itself never stopped
anything.

The actual cause: Streamlit cancels whatever script run is currently in
flight whenever a new one starts, and translation ran as a blocking loop
directly inside the button-click handler. Any interaction anywhere --
including something in another tab's already-rendered content -- started
a new run and killed the old one mid-batch.

**Fixed by running translation in a real background thread**
(`background_jobs.py`), decoupled entirely from the script lifecycle.
Module-level state survives reruns because Python only imports a module
once per process, so a thread started during one script run keeps going
independent of whatever happens afterward -- switching tabs, using other
dramas, or just clicking around. The Translate button starts the job and
returns immediately; revisiting Workspace shows current progress via a
plain status dict, with a manual refresh button since Streamlit doesn't
poll on its own.

Verified directly, not just asserted: a job was started, then left
completely unpolled for a period longer than it needed to finish, then
checked -- and it had run to completion with nobody watching. That's the
actual property being fixed here, not just "did the function get called."

Two structural rules keep this safe: nothing inside the background
thread touches `st.*` (not session state, not widgets -- neither is
thread-safe to write from a background thread), and the thread works on
its own copy of the lines, saving through the database; the main script
reloads from there once the job is visible again rather than two threads
touching the same objects.

Single-process, in-memory job tracking -- correct for a local, one-user
app. Would need a real job queue for anything multi-user.

## GPU transcription failures ("cublas64_12.dll is not found")

A real bug in the GPU fallback itself, not a driver problem to fix on
your end -- though see the README's Troubleshooting section for what to
check if it keeps happening.

`faster-whisper` (via ctranslate2) defers ALL CUDA initialization until
the first actual transcription call. Constructing a `WhisperModel
(device="cuda")` object never touches the GPU at all; it just stores
config. The earlier GPU fallback wrapped that constructor -- which is
exactly why it always "succeeded" even with a broken CUDA install, and
why the real failure only surfaced later, uncaught, deep inside
`model.transcribe()`'s lazy generator, crashing the whole tab.

Fixed at the actual failure point: `transcribe_for_timing` now catches
a CUDA/cuBLAS-shaped error at the moment transcription itself runs, and
transparently retries on CPU -- with a warning explaining what happened,
since silently downgrading from GPU to CPU without saying so would be
confusing (CPU is meaningfully slower). Verified by reproducing the
exact reported traceback: a fake CUDA model that constructs successfully
but throws the identical `cublas64_12.dll` error partway through
transcription, confirming the fallback now returns real, correct
segments instead of crashing.

**A second thing this surfaced**: running the full test suite after the
fix (rather than trusting the fix's own standalone verification) found
5 unrelated pre-existing failures -- tests for `process_page` and
`bulk_render_pages` that were never updated when those functions' return
signatures changed a few rounds earlier while fixing the blank-bubble
render bug. The production code and its UI callers were updated
correctly at the time; the tests covering them were not, and nothing
caught it because that specific fix's own verification didn't happen to
touch those exact tests. Fixed, with two new test cases added for
coverage that was missing even before the staleness (a bubble with
blank text specifically, in both the single-page and bulk-render paths).

## Dark mode gaps

The dark toggle originally only styled a subset of what the app
actually uses -- checkboxes, radio buttons, sliders, file uploaders,
popovers, chat elements, and (probably the biggest source of "still
looks white") the success/info/warning/error alert boxes used
constantly throughout the app had no dark styling at all. Even the
dark-mode toggle switch itself wasn't styled for dark mode. Fixed by
inventorying every Streamlit element function actually called anywhere
in the codebase and checking each against the stylesheet, rather than
patching whatever happened to be visible.

**One thing that can't be fixed with more CSS**: `st.dataframe` and
`st.data_editor` render their actual cell contents to an HTML5
`<canvas>` (via glide-data-grid) rather than as DOM elements. CSS can
restyle the container and border, but not the individual cell
backgrounds or text colour drawn onto that canvas -- a genuine
Streamlit/glide-data-grid constraint, not something worth chasing
further with selectors that will never match. Tables used throughout
the app (glossary, bulk import review, library filtering) may still
show light cell backgrounds in dark mode for this reason.

## The HF token fix only covered one of two download paths

Whisper's model download was fixed to use an HF token, but the Scanlate
ML bubble detector calls Hugging Face directly through a separate code
path that never got the same fix -- same warning, different function,
easy to miss without checking every `hf_hub_download` call in the
codebase rather than just the one that prompted the original fix.
Diarization was already correct (it requires a token as a parameter,
so there was nothing to silently skip). Verified with tests covering
all three cases: token passed explicitly, token already present in the
environment, and no token at all.

## A full stress-testing pass, and what it found

After several rounds of fixes shipping the same bug shape, the review
approach itself needed to widen -- re-running existing checks wasn't
enough. This pass added new categories and, in the process of building
them, found a genuinely serious bug.

**New static checks, both now permanent and self-tested:**
- Duplicate widget keys (a silent Streamlit state collision -- one
  widget's changes overwrite another's, no error). None found.
- session_state keys written under one name and read under a slightly
  different one (a typo that makes a feature silently never work).
  None found -- the apparent mismatches were all false positives from
  `.pop()` calls the checker didn't originally track, confirmed by
  reading each site directly rather than trusting the heuristic.
- **A name used but never imported anywhere in the file** -- different
  from the earlier use-before-def checker, which catches a name defined
  too *late*; this catches one never defined at all. Building it found
  a live bug on the spot (see below), and is now `TestNoUndefinedNames`
  in `test_static_analysis.py`, checked against what `from common
  import *` actually provides by parsing `common.py`'s own AST.

**The serious one -- found by testing an interaction, not a unit:**
resetting the library while a background translation job was still
running. First reproduction looked contained (a stray error, harmless).
But following the "does this survive contact with reality" instinct
further surfaced the real hazard: after `reset_library()` deletes the
whole database file, SQLite has no memory left of previously-used IDs
-- AUTOINCREMENT protects against reuse *within* a database's lifetime,
but that protection is itself deleted along with the file. A new drama
created right after a reset can receive the exact id an orphaned
background thread is still writing to, silently landing stale content
(wrong translations, wrong source text) onto a drama the person just
created.

The cancellation flag for this had existed in `background_jobs.py`
since it was first built, but nothing ever actually checked it --
verified directly by grep before assuming a fix was needed. Now:
`translate_lines_with_engine` checks a `cancel_check_cb` between
batches and stops early rather than running to completion regardless,
and the reset button requests cancellation of every running job and
**waits (up to 10 seconds) for them to actually stop** before the
destructive reset proceeds -- refusing to reset rather than risk
corruption if a job won't stop in time. (Later replaced: reset now takes
the exclusive hold, so no new job can start, and then calls
`background_jobs.wait_for_job_threads`; see `docs/background-jobs.md`.) Verified by reproducing the
exact original scenario end to end, including the id-reuse case, and
confirming no contamination survives.

**Fixing that surfaced a second live bug immediately**: the fix itself
used `time.time()`/`time.sleep()` in a file where `time` was never
imported -- caught before shipping only because the new undefined-names
checker was built moments earlier and run against the fresh code.

## A crash class the test suite couldn't previously catch

`source_language` was read inside the "Raw novel" upload section and,
separately, inside the "Romanize credits" button handler -- both well
before the selectbox that actually defines it, further down the same
function. This compiles fine (misordered code always does) and every
existing test passed, because nothing exercised that specific render
path -- exactly the same shape as two earlier bugs this session
(`process_page`, `import_title_from_url`), where a `str_replace` edit
left code in the wrong position relative to something it depended on.

Fixed properly rather than patched at each crash site: `source_language`
is now defined once, immediately after the drama record loads -- the
earliest point it's actually available -- instead of far down in
"Content source" where two earlier sections had already tried to read it.

**Turned into a permanent test** (`test_static_analysis.py`) rather than
a one-off fix, since compiling and passing tests clearly isn't sufficient
protection against this bug shape. It walks each tab's render function in
execution order and flags any name read before it's assigned, when that
same name IS assigned later in the same function -- which is exactly
what a stray reordering produces. The checker has its own tests, since a
static analyzer that's wrong is worse than none: a false pass hides a
real bug, and a false fail trains people to ignore it.

## Finding the reset button

If "Reset everything" wasn't visible in Diagnostics: it used to sit
below an early `return` that only executed after clicking "Run
diagnostics" first -- an unrelated system check gating an unrelated
destructive action. It's now at the top of the tab, visible immediately
regardless of whether a diagnostics scan has ever been run.

## Resume, and a Streamlit limitation

That last part was itself a bug worth noting: the reader's drama picker
is a keyed selectbox, and a keyed widget's stored value wins over its
`index`. So Resume used to set a target the reader silently ignored,
opening whatever drama was last viewed instead.

## A note on fetching from sites (verification detail)

Several sites in this space -- baihehub and Fanjiao included -- build
their pages with JavaScript. A plain HTTP fetch of baihehub's novel
listing returns the navigation, the sort controls, the filter UI, and
the literal text `共 0 条数据` ("0 items"). None of the actual listings
are in the HTML; they arrive later, via script.

This was verified directly, not assumed. It matters because the earlier
implementation handed that empty shell to the extractor, which found
nothing and reported "couldn't extract metadata" -- indistinguishable
from a page that genuinely had no metadata. A silent failure. See the
README's "Fetching from JS-heavy sites" section for the three-layer fix
that's in place now.

## Migrating off components.html

`st.components.v1.html` and `st.components.v1.iframe` are both
deprecated in favour of `st.iframe`, which auto-detects whether it was
given raw HTML, a URL, or a file path. Both call sites in this project
(the interactive reader, and Discover's site-embed panel) were migrated,
and the now-unused `components` import was removed rather than left in
place as an invitation to use the deprecated API again.

## OCR bugs found by directly running real backends

Found by actually installing and running each OCR backend against
synthetic test images, not just reading the code:

- **Tesseract silently garbling short single-line CJK text.**
  `extract_text_tesseract()` passed no `config=` to
  `pytesseract.image_to_string()`, so Tesseract used its own default
  page-segmentation mode (PSM 3, "fully automatic page segmentation") --
  tuned for a whole scanned page, not a small pre-cropped region.
  Confirmed directly: PSM 3 can garble or truncate short single-line CJK
  text that PSM 6 ("single uniform block of text") reads correctly. This
  directly affects `hardsub_ocr.py`'s per-frame OCR, which crops to
  exactly that shape (a caption band, one line, large font). Fixed by
  making the PSM explicit (defaults to 6, overridable).
- **PaddleOCR backend built against a removed API.**
  `extract_text_paddle()` used PaddleOCR 2.x's call shape --
  `PaddleOCR(use_angle_cls=True)` then `.ocr(path, cls=True)` returning
  `[[(box, (text, confidence)), ...]]` per page. `pip install paddleocr`
  installs 3.x today, which removed all of that -- `use_angle_cls` is
  gone, and the old `.ocr(cls=True)` call raises `TypeError:
  PaddleOCR.predict() got an unexpected keyword argument 'cls'`
  immediately. Confirmed by actually installing paddleocr and running
  it. Fixed by switching to the 3.x `.predict()` call and its
  `rec_texts` result shape. Also found, on at least one tested CPU, a
  separate crash from PaddleOCR's default MKL-DNN inference backend
  (`NotImplementedError` from a oneDNN op) -- worked around by disabling
  MKL-DNN (`enable_mkldnn=False`), which costs some inference speed but
  avoids failing outright on affected machines.
- **Scanlate's auto-OCR returning nothing for round/bordered bubbles.**
  The bubble-detection step cropped a detected bubble's bounding box
  exactly as drawn, border included, before running OCR on it. Confirmed
  directly: OCRing a bubble crop with its outline still in frame can
  make Tesseract return an empty string instead of the real text,
  because the outline reads as one large enclosing shape it can't
  segment past -- the worst failure mode, since it leaves nothing for
  auto-translate to work with. Fixed with `inset_box_for_ocr()`, which
  trims a small margin off each side of the box before cropping for OCR
  (capped so a very large box only loses a fixed border, not a growing
  fraction of itself).
- **manga_ocr and the Tesseract simplified/traditional split**: both
  confirmed working as documented by actually installing and running
  them against synthetic Japanese/Chinese test images -- no fix needed.

## Per-line spoken language (`Line.lang`)

A title used to have exactly one spoken language (`dramas.source_language`),
which broke on clips that mix speakers: transcribing a Korean speaker as
Japanese produced Japanese text. Each line can now carry its own language.
Transcription detects it per span (Mixed languages); the live translate loop
reads it too (see the last bullet).

Contract:

- `core.Line.lang` / `lines.lang` (TEXT, nullable, no default): a lower-case
  code from `core.LINE_LANGUAGES` (`zh`, `ja`, `ko`, `en`). `None`/NULL means
  "the title's `source_language`", so every existing line keeps its meaning.
- `core.normalize_line_lang(value)` is the one validator for input: `None` or
  `""` gives `None`, a known code in any case gives it lower-cased, anything
  else raises `InvalidInputError`. Stored rows are read leniently: an unknown
  code (from an imported backup, say) loads as `None`.
- `lang` is in `core.LINE_FIELDS`, so `db.save_lines` (full sync, field-scoped
  `fields=("lang",)`, `orig`, `only_if_unchanged`) and the compare-and-set
  helpers handle it like any other column; `""` is stored as NULL.
- Undo snapshots and translation versions record it (`SAVED_MARK_FIELDS`);
  restoring one saved before the field existed keeps each line's current
  `lang`.
- Split, re-split and re-segment pieces keep the parent's `lang`. A merge
  keeps it only when every merged line has the same one, otherwise the line
  falls back to the title's language (nothing is flagged).
- API: line responses carry `lang` (null = title default). The line edit route
  accepts `lang` (`""` = title default) and `expected.lang`.
  `POST /api/lines/dramas/{drama_id}/set-language` takes `lang` plus exactly
  one of `line_ids` or `speaker`, writes only `lang`, and skips ids that are
  not lines of that drama.
- Review shows a language chip only on a line whose `lang` differs from the
  title's, so a single-language title looks the same as before.
- Translation (`translate_pipeline._translate_lines_with_engine`, shared by the
  app and `cli.py translate`): a line whose `lang` differs from the title's gets
  a `(spoken in Korean)` tag in the numbered prompt text and in all three Reflect
  passes (`context["line_languages"]`, `None` for a single-language batch, so
  those prompts are unchanged); `en` lines are copied to `en` without a model
  call; NLLB groups a batch by language.
- The other paths read it through the same helpers in `engine_backends/shared.py`
  (`tagged_line_languages`, `tagged_source_texts`, `is_english_line`):
  bulk translate tags each request's numbered lines and copies `en` lines at
  submission (bulk Reflect tags all three stages); the DeepSeek off-peak run and
  `cli.py translate` go through the shared translate loop; `try_line` (stronger
  engine), `retry_blocked_line` and the fix-flagged job tag the single line's
  context and answer an `en` line without a call; the line AI tools
  (`line_ai_service`) pass the line's own language to their prompts. Not yet
  covered: glossary terms per language.
