# Baihe Audio Drama Subtitler — Library Edition

A local tool for translating, subtitling, and optionally AI-dubbing
Chinese audio dramas *and* novels at scale. Inspired by
[pyvideotrans](https://github.com/jianchang512/pyvideotrans)'s workflow
(ASR → translate → TTS dub / clone), scoped to audio dramas and their
source novels, with a persistent filterable library for managing
dozens of titles.

**Important:** this tool works on files you already have legal access
to (audio/video you've downloaded or been given, novel text you own or
have licensed). It does not scrape, download, or extract content from
paid apps or streaming platforms — point it at local files only.

## Features

- **Library**: filterable by title, author, studio, director, voice
  actor, status, source language (zh/ja/ko), and content type
  (audio drama, video drama, novel, manhwa, manga, manhua, ASMR).
- **Metadata auto-fill**: paste a link to a public listing page (a
  JJWXC book page, a Fanjiao show page, etc.) and the app extracts
  bibliographic metadata -- title, author, studio, cast, a short
  synopsis -- for review before saving. Only ever pulls cataloging
  info, never the actual chapters/episodes.
- **Site Navigator**: for sites in a language you don't read -- paste
  a URL and a goal, get the page's visible menu/labels translated plus
  step-by-step navigation guidance. Describes the site's own public
  interface only; doesn't log in, purchase, or fetch anything for you.
- **Known-site registry**: a curated list of well-known official
  platforms for baihe, Korean GL, and Japanese yuri content (audio
  drama, novel, comic) -- browsable in the Navigator tab and selectable
  as a starting point for metadata lookup, instead of typing URLs from
  memory.
- **Discover / known titles library**: a searchable catalog of known
  titles (title, author, tags, a short synopsis) -- separate from your
  working drama catalog. Search in any language, import from a listing
  page URL (baihehub.com or elsewhere), or add manually (e.g. for
  Japanese/Korean titles). One click imports a catalog entry into your
  actual working Library to start production on it.
- **Locale variants**: choose American/British/Australian English
  spelling and phrasing per drama.
- **Series glossaries**: assign dramas to a series to share a
  consistent name/term glossary across multiple seasons or books,
  automatically applied during translation.
- **Consistency checker**: flags the same term translated differently
  across a drama's lines -- for review, doesn't auto-fix.
- **Real subtitle merging**: combines consecutive short lines from the
  same speaker into one natural subtitle when they're close enough in
  time, rather than just flagging pacing issues.
- **In-app Q&A**: ask questions about a drama grounded in its own
  transcript/translation, right in the Reader tab.
- **Vocabulary export**: every word looked up in the Reader is saved
  and exportable as Anki-importable CSV or a proper `.apkg` deck.
- **Click-to-seek Reader audio**: embeds the current page's audio span
  with click-to-jump-to-timestamp on any line.
- **Two content modes per drama**:
  - **Audio drama**: you have the audio (or video — it'll pull the
    audio track) + a transcript. Aligns your transcript to real timing.
  - **Novel narration**: no audio exists yet. Paste the novel text and
    the app chunks it, tags who's speaking each line via the
    translation LLM, translates it, and can generate a full AI
    narration/dub from scratch.
- **Speaker diarization** (audio drama mode): distinguishes voices in
  the audio so lines can be grouped and named by character.
- **Multi-engine translation**: Claude, DeepSeek, DeepL, or Google.
- **AI dubbing with optional voice cloning**: free TTS (edge-tts) by
  default; attach a reference clip per character for real voice
  cloning (F5-TTS) instead. If you have an existing audio drama for a
  title, reference clips can be auto-extracted from it per character
  — useful for keeping a consistent voice between the audio-drama
  episodes and any novel-only chapters/side stories for the same title.
- **Source language**: Chinese, Japanese, or Korean — set per drama, drives both speech recognition and OCR.
- **Video input**: upload `.mp4`/`.mov`/`.mkv`/`.webm` directly — the
  audio track is extracted automatically for alignment/diarization,
  while the original video is kept for final export.
- **Full subtitled episode export**: burn subtitles permanently into
  the video (hardsub, plays everywhere) or add them as a toggleable
  track (softsub). Also supports swapping/mixing in an AI dub track
  as the video's new audio.
- **Dubbing pacing check**: flags translated lines that won't fit
  naturally in their time slot, with one-click LLM shortening.
- **Interactive Reader**: raw text with pinyin/furigana annotations
  side-by-side with the translation. Click any word for its reading
  and definition -- built for proofing translations and casual
  language learning, not just producing subtitles.
- **In-app playback**: watch/listen to the original, dub, or narration
  track directly in the Reader tab.
- **Scanlate (manga/comic typesetting)**: hybrid workflow -- auto-
  detect speech bubbles, auto-clean the original text, auto-translate
  and place text, then adjust position/size/font/text per bubble
  before final render. Free OpenCV-based detection by default.
- **CLI**: headless batch mode (`cli.py`) for unattended runs across
  your whole library.
- **Bulk export**: zip up subtitles + dub/narration tracks.

## Setup

You'll need Python 3.9+ and `ffmpeg` installed **with libass support**
(needed for burning subtitles into video). Most standard `ffmpeg`
builds already include it.

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows: download from ffmpeg.org and add to PATH
```

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` is grouped by feature — skip `pyannote.audio` if
you're not diarizing, skip `f5-tts` if you're not cloning voices, etc.

## Running it

**GUI:**
```bash
streamlit run app.py
```

**CLI (headless batch):**
```bash
python cli.py list
python cli.py align --whisper-size medium                    # audio-drama mode
python cli.py narrate-prep --engine claude --api-key $KEY     # novel-narration mode
python cli.py translate --status aligned --engine claude --api-key $KEY
python cli.py dub --status translated
python cli.py export-video --style hardsub --subs english     # full subtitled episodes
```
For CLI-driven audio-drama prep, place the transcript at
`library/dramas/<id>/transcript.txt` and audio/video as
`library/dramas/<id>/source.<ext>`. For novel-narration mode, place the
novel text at `library/dramas/<id>/novel_narration_source.txt` and set
`content_mode = 'novel_narration'` on that drama row (the GUI does all
of this automatically when you use it — manual placement is only for
adding dramas without ever opening the GUI).

## Translation engines

| Engine | Notes |
|---|---|
| `claude` | Best for tone/character voice. Supports novel reference + prompt caching. |
| `deepseek` | Cheap, strong on Chinese. Supports novel reference. |
| `gemini` | Close to DeepSeek on price (Flash-Lite tier), strong on Chinese/Japanese. Supports novel reference. Google's model lineup/pricing changes often — see `GEMINI_MODELS` in `translate_engines.py` if a run starts erroring. |
| `deepl` | Fast, natural phrasing, pure MT — no reference-novel awareness. |
| `google` | Broadest coverage, cheapest at scale, pure MT. |
| `ollama` | Runs locally via [Ollama](https://ollama.com). No per-token billing, but it uses your hardware — a usable model wants meaningful RAM/VRAM. Supports novel reference. Won't match Claude/DeepSeek on nuance. |
| `libretranslate` | Self-hosted [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) or [LTEngine](https://github.com/LibreTranslate/LTEngine). Pure MT, no reference-novel awareness. **See the cost note below — "open source" is not the same as "free to use".** |

Only `claude`, `deepseek`, `gemini`, and `ollama` (LLM-based) can do
speaker attribution for novel-narration mode — DeepL/Google will just tag
everything "Narrator".

### Context from recent lines

The "Context lines shown from before each batch" slider (Workspace, next
to the engine/locale settings) shows the LLM-based engines above how the
immediately preceding lines were already translated, not just the batch
currently being translated. Batches are otherwise translated in
isolation — a pronoun or someone referred to only by relation ("her",
"that guy") a few lines back has nothing to resolve against, and the
model has to guess fresh every batch instead of staying consistent with
what came right before it. Defaults to 6 lines; 0 turns it off. Doesn't
apply to the pure-MT engines (DeepL, Google, LibreTranslate) — they
translate one line at a time with no concept of surrounding context at
all.

### What each engine actually costs

Worth stating precisely, because "open source" and "free to use" are
different claims:

| Engine | Per-use cost | What you actually need |
|---|---|---|
| `claude` | Paid per token | API key from console.anthropic.com (separate from, and billed separately to, a Claude.ai subscription) |
| `deepseek` | Paid per token, far cheaper than Claude | API key |
| `gemini` | Paid per token, close to DeepSeek on the Flash-Lite tier | API key from aistudio.google.com |
| `deepl` | Paid above a limited free tier | API key |
| `google` | Paid per character | API key |
| `ollama` | No billing | Your own hardware — a model worth using wants real RAM/VRAM |
| `libretranslate` | No billing **if self-hosted** | Your own server. LibreTranslate wants ~8GB RAM and ~10GB disk for full language support. LTEngine's best model (gemma3-27b) wants roughly a 24GB-VRAM GPU; CPU-only runs, but slowly. |

**The hosted libretranslate.com API is a paid service** with pricing
tiers and requires a key — pointing this app at it is not free. The
AGPL-3.0 licence makes the *software* free, not that endpoint. And
self-hosting doesn't remove the cost so much as move it: you pay in
hardware, setup, and maintenance instead of per-token billing.

The genuinely no-cost path is `ollama` or self-hosted
`libretranslate`/LTEngine **on hardware you already own**. Everything
else bills you, one way or another.

## OCR (image-based chapter scans)

Some platforms serve chapters as images specifically to block copy/
paste. If that's what you're working with, upload the page images in
the novel-narration section's OCR expander instead of pasting text.

Default backend (Tesseract): `pip install pytesseract pillow`, plus
the Tesseract binary with the matching language pack:
- macOS: `brew install tesseract tesseract-lang`
- Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-jpn tesseract-ocr-kor`
- Windows: [installer](https://github.com/UB-Mannheim/tesseract/wiki) — select the languages you need during setup

Higher-accuracy alternative for Chinese specifically: PaddleOCR
(`pip install paddleocr paddlepaddle`) — heavier install, downloads its
own models on first use, but noticeably better on stylized fonts or
lower-quality scans.

For Japanese manga specifically: `pip install manga-ocr` — purpose-
built for speech-bubble/vertical text layouts (the same model family
koharu uses), noticeably better than Tesseract on stylized manga fonts.
Works best on single speech-bubble crops rather than whole pages.

**Always review OCR output before translating** — misrecognized
characters are common, especially on compressed screenshots.


## Reading captions already burned into a video (hardsub OCR, experimental)

For clips where the caption is what should be translated regardless of
what's actually spoken — a compilation, a variety show, a short with a
caption over background music — pick "The video already has captions
burned in" in the Workspace's transcript-source choice instead of Whisper
or a pasted transcript. Only shows up once a **video** file (not
audio-only) is attached, since it needs the actual picture, not just the
audio track.

It samples frames from the video, auto-finds the row band most likely to
contain the caption (looking for text-like edges that stay in the same
place while the rest of the picture keeps changing underneath), reads
each sampled frame with the same Tesseract/PaddleOCR backends the page-
scan OCR above uses, and collapses repeated frames of the same caption
into one timed line — the output drops into the same review table,
translate, and export pipeline as a Whisper transcript. No new
dependency beyond what's already required for Scanlate/page-scan OCR
(opencv-python, pytesseract + the Tesseract binary, or PaddleOCR) — you
do still need `ffmpeg` (already required generally, see Setup above).

Real limitations, not edge cases to eventually round out:
- It finds **one** caption band. A video with captions in two places at
  once (a stylized header AND a separate bottom caption, for instance)
  only gets whichever one scores higher — the other is missed entirely.
- It's a heuristic, not a trained detector — a static logo/watermark can
  occasionally outscore a genuinely low-contrast or unusually-placed
  caption.
- Runs in the background with a progress bar (same as Whisper
  transcription), but is slower than audio transcription for the same
  runtime — it's OCR-ing a frame every N seconds, not just decoding
  audio. Raise the sample interval (in the same section) if it's taking
  too long and you can tolerate missing very short-lived captions.


## Speaker diarization (audio-drama mode)

Needs `pip install pyannote.audio`, a free Hugging Face token
(https://huggingface.co/settings/tokens), and accepting the model
terms at https://huggingface.co/pyannote/speaker-diarization-3.1.
First run downloads the model. CPU works, GPU is faster.

## AI dubbing & voice cloning

**Default (free, online)**: edge-tts, picks from a fixed voice list,
one voice per character.

**Offline (free, no internet)**: Piper (`pip install piper-tts`) —
select "Offline / Piper" as the fallback engine before generating a
dub/narration track. Lower voice quality than edge-tts, but works with
zero API calls and no network once the voice models are downloaded —
useful for fully offline batch runs or avoiding any cloud dependency.

**Voice cloning (F5-TTS)**: `pip install f5-tts`. For each character,
provide:
- A reference audio clip (a clean few seconds of just that voice)
- The exact text spoken in that clip (needed to anchor the clone)

**Voice cloning (ElevenLabs, hosted alternative)**: `pip install
elevenlabs` + an ElevenLabs API key (paid, limited free tier). No GPU
or local model needed — you upload the same reference clip, the app
clones the voice via their API, and reuses the resulting voice ID for
all of that character's lines. Worth trying first if F5-TTS gives you
setup trouble, at the cost of being a paid cloud service instead of
free/local.

In the Workspace tab, use **"Auto-extract reference clips"** after
diarizing an audio drama to pull clean per-character clips
automatically. For novel-only dramas with no audio, you'd supply a
reference clip yourself.

⚠️ **Not verified end-to-end in this build** — the F5-TTS integration
(`dub.py: synthesize_line_cloned`) is written against its documented
API but I couldn't install/run it in the environment this was built
in (no network access there). Test on one short line before batch-
processing a whole drama, and check `f5_tts.api.F5TTS`'s current
signature against your installed version if it errors.

⚠️ **A note on cloning real people's voices**: voice actors' voices are
tied to their identity and performance. Cloning them — even for
personal, non-commercial fan translation — sits in a legally and
ethically gray area depending on jurisdiction and what you do with the
output. This is worth thinking through for your own use case,
especially if you'd ever share the dubbed files beyond personal use.

## Novel-narration line timing

Since there's no source audio to align to, line timings are set to the
actual duration of each generated TTS clip once you run narration
generation — download the `.srt` *after* generating the track, not
before, so timings match.

## Interactive Reader

A dedicated tab for reading raw + translated text side-by-side, built
for proofing and casual language learning, not just producing
subtitles/dubs.

- **Word segmentation**: jieba (Chinese), sudachipy (Japanese),
  kiwipiepy (Korean) -- all pure-Python, no external binaries needed.
- **Ruby annotations**: pinyin above Chinese words, furigana (hiragana
  readings) above Japanese kanji. Korean is skipped since Hangul is
  already phonetic.
- **Click-to-define**: click any word to see its reading + definition.
  Chinese uses a local CC-CEDICT lookup (auto-downloads once, then
  works fully offline). Japanese/Korean, and any Chinese word CC-CEDICT
  doesn't have, fall back to an LLM-generated definition using the
  surrounding text as context -- these need an API key entered in the
  Reader tab, batched once per page rather than per word.
- **Paginated**: large episodes/chapters are split into pages so the
  page doesn't need to segment/define thousands of words at once.

Install what you need: `pip install jieba pypinyin` for Chinese,
`pip install sudachipy sudachidict_core pykakasi` for Japanese,
`pip install kiwipiepy` for Korean.

- **Bulk import from listing pages**: extract many entries at once
  from a tag/category or ranking page (e.g. JJWXC's Baihe tag listing,
  Fanjiao's ranking page) -- title, author, tags, and whether an audio
  drama adaptation exists, never the actual content. Supports paginated
  listings (one URL per page). Always shows a review/edit table before
  committing anything to your library.

- **EPUB import/export**: import chapters directly from an .epub you
  own (novel-narration mode), or export finished translations as a
  proper .epub for any e-reader.
- **Scanlate cross-page context**: translation carries a rolling
  summary between pages, keeping character voice and plot threads
  consistent across a whole chapter instead of each page being
  translated in isolation.
- **Scanlate bulk render**: render every page with saved bubbles and
  download the whole chapter as one ZIP, instead of one page at a time.

## Failure isolation

A design goal: one thing breaking should break only that thing. Several
cascade risks were found and fixed by auditing for them directly.

**Tab isolation.** Streamlit runs every tab's render function on every
page load, so an unhandled exception anywhere took the *whole page*
down -- Library, Workspace and Reader all became unusable because
Scanlate raised. Each tab now renders inside a guard that shows the
error (with a traceback, so it stays diagnosable) inside that tab while
the rest keep working. Tab modules are also imported individually, so
one failing to import doesn't stop the others loading.

**Database connections.** If a statement raised between opening a
connection and closing it -- a foreign-key violation, a bad parameter
type -- the close was skipped and the connection leaked. Under WAL that
blocked every subsequent write with "database is locked", so a single
failed call poisoned the whole session until restart. Connections are
now tracked and reclaimed.

**Destructive writes.** `save_lines`, `save_bubbles` and
`save_line_history_snapshot` delete existing rows before re-inserting.
A failure partway through would destroy translation work that cost real
money. All three are now wrapped in explicit transactions with rollback
on error, verified by reproducing a mid-save failure and confirming the
previous data survives intact. (sqlite's implicit transaction happened
to cover this already, but as a side effect of connection lifecycle
rather than a stated guarantee -- worth making explicit.)

**Optional dependencies.** Heavy optional packages (Whisper, OpenCV,
TTS engines, OCR backends) are imported lazily inside the functions
that use them, never at module load, so a missing one disables its own
feature rather than preventing the app from starting.

**Per-item isolation.** Batch operations already isolate individual
failures: one drama failing in a CLI batch, one line failing to
synthesise during dubbing, one page failing to render in a bulk
scanlate job.

## A note on fetching from sites

Several sites in this space — **baihehub and Fanjiao included** — build
their pages with JavaScript. A plain HTTP fetch of baihehub's novel
listing returns the navigation, the sort controls, the filter UI, and
the literal text `共 0 条数据` ("0 items"). None of the actual listings
are in the HTML; they arrive later, via script.

This was verified directly, not assumed. It matters because the earlier
implementation handed that empty shell to the extractor, which found
nothing and reported "couldn't extract metadata" — indistinguishable
from a page that genuinely had no metadata. A silent failure.

`page_fetch.py` now handles this in three layers:

1. **Detection.** After any fetch, the response is checked for the
   signature of an unrendered shell (JS app container, many scripts,
   little text, empty-state strings). If it looks like a shell, you're
   told so explicitly, with the reasons.
2. **Rendering.** With `playwright` installed (`pip install playwright`
   then `playwright install chromium` — the second command is easy to
   miss), the page is re-fetched with a real browser engine so the
   JavaScript actually runs.
3. **Manual paste.** Always available, always works: open the page in
   your browser, select all, copy, paste into the app. No dependency,
   no rendering, no guessing.

The same three layers back metadata lookup, bulk import, and title
import. None of them will now quietly return nothing and leave you
wondering whether the page was empty or the fetch was broken.

## Embedding sites in the app

There's an embed panel in Discover, but be realistic about it: most
substantial sites (JJWXC, MissEvan, Lezhin, BookWalker, Naver, Kakao,
and others) send headers that forbid being placed in an iframe, as
clickjacking protection. For those, the panel renders blank — the site
refusing, not a bug. Known blockers are flagged before you try.

In practice the Navigator tab is the better tool for this: it translates
a page's menu labels and gives you step-by-step navigation guidance,
which you follow in a normal browser tab.

## Emotion-aware translation

The failure this prevents: a translation that's semantically correct
and tonally dead — sarcasm rendered as sincerity, suppressed anger read
as calm. In an audio drama that's especially costly, because the voice
actor's delivery already carries the emotion; a subtitle that
contradicts the performance breaks the scene.

Lines are tagged with an emotional register (14 of them, including the
ones most often flattened: sarcasm, dry humour, suppressed anger,
flirtation, evasion) plus an intensity. For timed audio, delivery cues
from the original — unusual pacing, long pauses — are passed as weak
supporting evidence. Tags are folded into the next translation run with
register-specific guidance, and only for lines that are actually
charged, so the instruction isn't diluted by a wall of "this line is
neutral". Emotion tags also map to TTS rate/pitch, so dubbed lines are
voiced with roughly the right energy.

## Manhua & webtoon handling

- **Bubble detection** — the key signal is that a speech bubble is an
  enclosed light region that *doesn't touch the page edge*, while the
  page background and gutters do. Filtering on that removes the
  background without needing to understand the artwork. It struggles
  with borderless bubbles, dark or inverted panels, very low-contrast
  scans, and text drawn straight onto art — in those cases it tells you
  which candidates it rejected and why, rather than just reporting
  nothing found.
- **Panel detection** — finds panels via gutter analysis, in manga
  reading order. Lets you translate only the panels you're looking at,
  and gives the translator panel-level context.
- **Long-strip webtoons** — a 10,000px vertical strip breaks
  page-oriented detection. Strips are sliced at whitespace gaps near a
  target height so cuts land between panels, with a small overlap so a
  bubble straddling a cut isn't lost.
- **Text region classification** — sorts regions into bubbles,
  narration boxes, signs, SFX, and thought bubbles. These want different
  treatment: bubbles get clean inpaint-and-replace, signs are part of
  the artwork and often better served by an overlay or margin note, and
  SFX lettering frequently shouldn't be touched at all.
- **Font style sampling** — estimates stroke weight and glyph
  irregularity to suggest a matching face, so handwritten lettering
  isn't replaced with the same bold sans as everything else.

All of these are heuristic and surfaced for review rather than applied
silently.

## If lines get merged, timing feels off, or some lines never got translated

Diagnosed directly from a real uploaded file, not guessed at.

**Merged lines / missing "thoughts"**: Whisper's voice-activity detector
defaults to merging any two stretches of speech separated by less than
2 seconds of silence into ONE segment -- keeping only the first
sentence as that line's text. For back-to-back dialogue or internal
monologue with short pauses, this routinely swallows several real
lines into one oversized block (one real case: a single "line" spanning
6.4 minutes). Workspace -> Recognition accuracy has a **speech-splitting
sensitivity** slider (default 2000ms) -- lower it to 500-1000ms if this
is happening. There's no universally correct value: too low starts
splitting mid-sentence on normal pauses instead.

**Finding where this happened**: Workspace -> Review & edit ->
**Check line coverage**, run after aligning, before translating. Flags
suspiciously long lines relative to their text length, large silent
gaps between lines, blank source text, and untranslated lines with
source text present -- so you can jump straight to problem spots
instead of scrubbing the whole file by ear.

**Transcribed but never translated, with no error shown**: this was a
real gap in the background-translation system. The completion warning
used to be ephemeral -- shown once, then discarded -- so restarting the
app or missing that one rerun lost the explanation entirely, leaving
only silently-blank lines with no trace of why. Failures are now
persisted to the database and shown as a standing banner in Workspace
until dismissed or the drama is re-translated cleanly. Click Translate
again; already-translated lines are skipped automatically, so this
doesn't re-cost anything already done.

## Cross-referencing the raw and translated novel

One upload now does both jobs instead of needing the same file twice:
the raw source-language novel (Workspace -> Content source -> "Raw
novel") feeds transcription priming AND, in "Build a glossary from this
novel," pairs automatically with the existing English reference
translation to extract terms as matched pairs -- capturing how each was
actually rendered rather than inventing new wording. Uploading it in
either place saves it for both uses.

## Claude model selection

Previously hardcoded to an older model generation with no way to change
it. Workspace -> Translation now has a model picker (Sonnet 5, Opus 4.8,
Haiku 4.5, or the previous Sonnet 4.6) when Claude is the selected
engine. Anthropic updates this lineup periodically -- if a model starts
erroring, check console.anthropic.com for what's currently available.

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
your end -- though see below for what to check if it keeps happening.

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

If you keep seeing this warning, the underlying cause is usually one
of: PyTorch/ctranslate2 installed without CUDA support (a CPU-only
wheel), or a CUDA toolkit version that doesn't match your driver.
Turning GPU off in Settings -> Performance avoids the warning entirely
if you'd rather not chase it down.

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

## Dark mode gaps, and one real limitation

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
corruption if a job won't stop in time. Verified by reproducing the
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

## Resetting for testing

Diagnostics -> Danger zone -> **Reset everything**. Deletes every drama,
translation, glossary, series, progress record, and file on disk, then
reinitializes an empty database. Two-step confirmation (a checkbox, then
typing RESET) since there's no undo. For removing one drama rather than
everything, use the delete button in Library or Workspace instead.

## Transcription accuracy

Whisper mishears proper nouns constantly in Chinese, and it does so
invisibly: a wrong guess is usually still a real word, so nothing looks
broken until the translation reads oddly. Four levers, in order of value:

1. **A real transcript.** Still the single biggest quality difference.
   Everything below is making the best of not having one.
2. **Prime it with names** (Workspace -> Recognition accuracy). Whisper
   conditions on an `initial_prompt`, so telling it which names to expect
   fixes much of the proper-noun problem. This is wired to your series
   glossary automatically -- build a glossary from the novel and it feeds
   straight back into transcription. Costs nothing.
3. **`large-v3` instead of `medium`.** Markedly better on names and
   homophones, free, ~3GB and slower -- but practical with GPU enabled.
4. **Wider beam search** (8-10). Considers more alternatives before
   committing. Costs time, not money.

None of these cost anything. GPU acceleration is what makes `large-v3`
usable rather than painful, and an 8GB card handles it comfortably.

## About database IDs

Drama IDs skip numbers after a deletion (1, 2, 4...) and this is
deliberate. Lines, characters, glossaries, progress and translation
versions all reference a drama by ID, so reusing a deleted one would let
leftover rows re-attach to the wrong work -- and any backup taken before
the deletion would restore into a conflicting record.

The Library table shows a tidy sequential **#** column for display
alongside the real `id`, which gives orderly numbering without putting
your data at risk.

## Metadata romanization

Credits are stored in the original script and gain a romanized companion
rather than being overwritten -- 一半山川 stays and displays as
"Yiban Shanchuan (一半山川)".

Personal names are romanized rather than translated, because a name is a
name and not a phrase to render. Studios and platforms use their
established English name where one exists (晋江文学城 is normally written
"JJWXC", not "Jinjiang Literature City") and are romanized otherwise.

## Resume, and a Streamlit limitation

The Continue shelf's **Resume** button sets your target drama and page,
then tells you to open the Read & Watch tab. It does not switch tabs for
you, because `st.tabs()` has no API for that -- Streamlit tab selection
is purely client-side and can't be driven from Python.

Rather than pretend otherwise, the button says plainly what it did and
what to click. When you do open the tab, the right drama is already
selected at your saved page, with a banner confirming it.

(That last part was itself a bug worth noting: the reader's drama picker
is a keyed selectbox, and a keyed widget's stored value wins over its
`index`. So Resume used to set a target the reader silently ignored,
opening whatever drama was last viewed instead.)

## Follow-along playback

With click-to-seek enabled in the Read & Watch tab, the line currently
being spoken is highlighted and scrolled into view as the audio plays.
There's a toggle to stop it auto-scrolling if you'd rather read ahead.

This runs entirely inside the reader panel, because Streamlit can't
observe an `<audio>` element's playback position from Python. It only
works with the reader's own embedded clip -- the separate "Watch /
listen" player above is a standard Streamlit component and can't be
hooked into the same way.

The embedded clip covers only the current page, so its `t=0` is that
page's first line; absolute line timestamps have that offset added back
before matching.

## Progress tracking

Reading progress is saved when you change pages, not continuously --
so the percentage reflects the furthest page you've opened rather than
your position within it. Playback position is tracked separately from
reading position, so listening and reading don't overwrite each other.

## Interface

**Dark mode** is a toggle at the top of the ⚙️ Settings sidebar and
applies to the whole app. The Reader keeps its own separate theme
(light/sepia/dark) for the reading surface, since reading preferences
and UI preferences aren't always the same.

A restrained design system in `ui_theme.py` and `.streamlit/config.toml`:
one accent colour carrying emphasis (when five things are highlighted,
nothing is), a fixed spacing scale for vertical rhythm, status shown as
colour-coded pills rather than prose, deliberate empty states that say
what to do next, and stage indicators for multi-step workflows so a
ten-step process shows the step you're on instead of all ten at once.

## Story memory & spoiler-free mode

**Spoiler-free mode** (⚙️ Settings, on by default) is a global setting
that scopes *every* AI feature to the page you've reached. Character
lookups, recaps, relationship maps, and the wiki all respect it. An
entry introduced at line 500 stays hidden while you're on line 100, so
asking "who is this again?" can never reveal a betrayal, a death, or a
hidden identity you haven't reached yet. Turn it off once you've
finished a story.

**Universe wiki** — an encyclopedia that grows as you read: characters,
places, sects, artifacts, concepts, and events. Entries accumulate
rather than resetting: re-running extraction after reading further
updates existing entries with new information, tracks aliases and title
changes, and records attributes like cultivation level, rank, and
equipment. Each entry stores how far into the story it was built from,
which is what makes the spoiler boundary work. Exportable as Markdown.

## Adaptive translation style

Every line you rewrite in the review table is recorded as a before/after
pair. Once enough accumulate (8 minimum), they can be analyzed for
consistent patterns — do you reliably tighten? prefer more literal
phrasing? keep more romanized terms than the model does? — and the
result becomes a style profile injected into future translation prompts.

Deliberately conservative: it only reports preferences visible across
several edits, states its own confidence, and always shows the profile
for review before applying. Local statistics (shortened / expanded /
rephrased counts, average word delta) are shown immediately with no API
call. Scoped per series where one is assigned, otherwise global.

## Line tools

Per-line operations in the Reader, for polishing rather than batch work:

- **Why this?** — what the source says literally, which choices were
  interpretive, and what didn't survive
- **Alternatives** — other valid renderings, each labeled with what it
  prioritizes and what it trades away
- **Grammar** — word-by-word breakdown with reading, meaning, and
  grammatical function
- **🔊 Pronounce** — hear a name or phrase in the *source* language
- **Improve this line** — targeted rewrite; applying it also feeds the
  adaptive style profile

## Reading experience

Configurable in ⚙️ Settings: text size, line spacing, content width,
font (system/serif/sans/mono), and three themes (light, sepia, dark).

## Performance

Optional GPU acceleration for Whisper transcription, diarization, and
local voice cloning. Falls back to CPU automatically if CUDA isn't
actually available, so enabling it on a machine without a GPU degrades
rather than breaks.

## Library experience

The app is meant to feel like a proper library, not a folder of files.

**Progress & resume**
- Reading percentage and last page tracked per drama, saved automatically as you page through the Reader
- Audio position stored separately from reading position, so listening and reading don't overwrite each other
- **Continue** shelf on the Library tab: cover art, progress bar, one-click resume to where you left off
- Reading history log, clearable

**Metadata**
- Cover art, genre, publication status (ongoing/completed/hiatus), chapter count
- Custom user-defined tags, filterable in the Library
- Private personal notes per drama, editable from Workspace or Reader
- Reading/listening time estimates (word count for text, real duration for timed audio)

**Translation versions**
- Every translation run is saved as a named version tagged with its engine and model
- Re-translate with a different model without losing the previous attempt
- Side-by-side diff view showing only the lines that actually differ
- Activate whichever version reads better; switching snapshots the current one first

**Story tools** (Reader tab, grounded strictly in the drama's own lines)
- *Who is this character?* — role, relationships, and speech notes, drawn from lines that actually mention them
- *Relationship map* — structured cast + relationships, rendered as a Mermaid diagram
- *Recap* — "previously..." summary of what you've read, spoiler-safe (never references past your current page)
- *Explain an idiom or reference* — on-demand explanation of a 成语, allusion, or cultural reference

**Storage management**
- Scan showing library size, what's reclaimable, a per-category breakdown, and the largest dramas
- Quality presets: *Archival* (keep everything), *Balanced* (drop intermediates), *Minimal* (sources and text only)
- Cleanup only ever removes regenerable artifacts — source audio/video, reference novels, voice-clone samples, and the database are never touched

## Finding titles

The Discover tab's title search looks at titles **you've saved locally** --
it is not a web search, and with an empty library it returns nothing.
Load the starter titles first, or add your own.

To find where a work actually exists, use **"Find a title on the official
platforms"**. Type a title in English or Chinese and it builds real
search links across every known platform, then hands you the links.

It deliberately does not search from inside the app and does not ask a
model to "find" titles. A model asked to recommend obscure works will
invent plausible-sounding ones, and a library full of titles that don't
exist is worse than a small accurate one. Generated links can't
fabricate anything -- you click through and see for yourself.

Links are site-scoped web searches rather than each platform's internal
search URL, because those schemes differ per site, change without
notice, and several platforms render results with JavaScript. A scoped
web search works everywhere and keeps working.

There's also a direct link to browse JJWXC's 百合 tag listing.

## Trying it for free first

Before spending anything, run a drama through with the `test_offline`
engine. It needs no API key, no network, and costs nothing -- it emits
obvious `[TEST]` placeholder text so it can never be mistaken for a real
translation. The point is to confirm the whole pipeline works on your
machine (align -> translate -> review -> merge -> export -> dub) before
a single token is billed.

Pick `test_offline` as the translation engine in the Workspace tab. The
API key field disappears; everything else behaves normally.

## If Hugging Face is unreachable

Whisper downloads its model from Hugging Face on first use. If that
fails with `getaddrinfo failed` or `LocalEntryNotFoundError`, it's a
network problem, not an audio one. In order of likelihood on Windows:

1. **Antivirus or firewall** blocking Python's network access -- allow
   `python.exe` and `streamlit.exe` explicitly.
2. **A VPN** that's connected but not routing.
3. **DNS**: `ipconfig /flushdns`, then set your adapter's DNS servers to
   `1.1.1.1` and `8.8.8.8`.
4. **A DNS blocker on your own network** (Pi-hole, AdGuard, some
   corporate filters). These return `0.0.0.0` for blocked domains rather
   than failing, which looks identical to a broken connection. Check
   with `nslookup huggingface.co` -- if the answer is `0.0.0.0` or `::`,
   that's it. Whitelist all of these, since the `cdn-lfs` hosts serve
   the actual model files and allowing only the first will fail
   mid-download:
   ```
   huggingface.co
   cdn-lfs.huggingface.co
   cdn-lfs-us-1.hf.co
   hf.co
   ```
   Then `ipconfig /flushdns`. The app detects this case specifically and
   names it rather than blaming your connection.
5. **Hugging Face blocked by your ISP or region** -- use a mirror:
   ```
   $env:HF_ENDPOINT="https://hf-mirror.com"     # PowerShell
   streamlit run app.py
   ```

Check which case you're in:
```
python -c "import socket; print(socket.gethostbyname('google.com'))"
python -c "import socket; print(socket.gethostbyname('huggingface.co'))"
```
If google resolves and huggingface doesn't, it's case 4.

Fully offline option: download a `faster-whisper` model on another
machine and point at the folder under **Settings -> Offline /
restricted networks**.

## Translation guide (style, terms, and notes)

The craft layer -- what separates a mechanical translation from a good
one. Configured per drama in the Workspace tab's Translation section.

**Style presets** change register and pacing guidance:
- *Audio drama* -- written to be spoken: contractions, breath-length
  sentences, nothing a voice actor would stumble over.
- *Novel* -- literary register, room for imagery and narrative rhythm.
- *Subtitles* -- instant comprehension at a glance, front-loaded meaning.
- *Manhua* -- concise bubble dialogue matching the art's emotional pitch.

**Term handling policies** decide what stays Chinese and what gets
translated, per term:

| Policy | Example |
|---|---|
| Keep as pinyin | 沈清疑 → Shen Qingyi |
| Hybrid | 云隐宗 → Yunyin Sect |
| Translate meaning | 听雨阁 → Listening Rain Pavilion |
| Keep + note | 道 → dao, flagged for a translation note |
| Contextual | 姐姐 → "jiejie" or "older sister" depending on use |

Terms are categorized (person name, clan/sect, title, honorific,
cultivation realm, place, artifact, technique, concept) and grouped by
policy in the prompt, so the model gets rules rather than a flat list.

**Auto-extract terms** scans your source text and proposes terms with a
category and policy for each, including flagging names whose characters
carry thematic meaning. Everything goes to a review table first -- these
are judgment calls.

**Build a glossary from the novel** (in the reference-novel section).
The novel is usually a better terminology source than the drama's
dialogue -- longer, and it introduces more names, sects and places.
Terms are sampled from across the whole text rather than the opening
chapters, so later introductions aren't missed.

If you supply *both* the original-language novel and an existing
English translation, terms are extracted as matched pairs -- capturing
how each was actually rendered rather than inventing new wording. That
keeps a drama you translate later consistent with the novel readers
already know.

Only terminology is extracted -- names, places, sects, titles, concepts.
No passages of the novel are stored; the output is a term list.

**Import an existing glossary** as CSV, TSV, or JSON. A plain
two-column term/translation sheet works; headers and extra columns
(category, policy, enforce_exact, notes) are picked up when present.
Unknown categories or policies are corrected to safe defaults and
reported rather than silently accepted. Glossaries export back to CSV
for backup, spreadsheet editing, or sharing with someone translating
the same series.

**Enforce exactly (🔒)** does a hard find-and-replace after translation,
correcting known wrong variants to the canonical form. The glossary
*asks* the model for consistency; this *guarantees* it, for names where
drift is unacceptable.

**Translation notes** flags what didn't survive the crossing -- 成语 and
set phrases, puns and homophones, meaningful names, literary allusions,
cultural specifics, and honorific nuance. Notes are stored per line,
editable, and exportable as a Markdown appendix.

**Genre guidance** (toggleable) covers baihe-specific concerns: pronoun
clarity, kinship terms used as intimate address rather than literal
family, and not softening or degendering romantic content.

## Testing

The project has a test suite (137 tests) covering the pure-logic
pieces -- line merging, timing alignment, retry/backoff behavior,
database CRUD, cost estimation, image processing, export packaging,
and undo history. Tests use an isolated temp database, so running
them never touches your real library.

```bash
pip install pytest      # if not already installed
python run_tests.py     # run everything
python run_tests.py -k history   # run a subset
```

Worth running after any change you make to the code, and useful for
confirming a fresh install is working before you start real work.

## Diagnostics ("Check my setup")

A dedicated 🩺 tab that reports, in one place:
- Python version and whether ffmpeg is on PATH
- Which optional dependencies are actually installed, grouped by what
  they power (core / translation engines / optional features)
- Whether every expected project file is present -- the usual cause of
  a cryptic `ModuleNotFoundError` after a partial download
- Which API keys are currently configured
- Whether the library directory is writable

Run this first whenever something isn't working.

## Undo / version history

Snapshots of a drama's lines are taken automatically before the two
operations that discard work irreversibly -- a **force re-translate**
and an **applied merge**. Restore any snapshot from the "Version
history / undo" expander in the Workspace tab; restoring takes its own
snapshot first, so you can undo an undo. Only the 10 most recent
snapshots per drama are kept.

## Per-drama export package

Separate from the whole-library backup: bundles one title's metadata,
all subtitle formats, original audio/video, dub/narration track, and
reference novel into a single zip -- for archiving a finished drama or
handing it off without exporting everything you own. Shows a manifest
of what was actually included, so a missing piece (e.g. no dub
generated yet) is visible rather than silently absent.

## Discover: known titles library

A searchable catalog of known titles -- separate from your working
drama catalog (`dramas`), so you can browse/discover before committing
to actually working on something.

**Getting started**: the library starts empty; click "Load starter
titles" for a handful of real, publicly-documented baihe audio dramas
to bootstrap it.

**Search**: works in any language against whatever's in your local
library. Searching baihehub.com specifically translates your query to
Chinese first (since it's a Chinese-language database), then attempts
an automatic search.

**On baihehub.com's search specifically**: its search page is a
client-rendered app, so its live JSON API couldn't be verified from
the environment this was built in -- `search_baihehub()` tries a
best-effort guess and may come back empty depending on their actual
endpoint. When that happens, you get a fallback link to baihehub's
human search page -- open it, copy URLs of anything interesting, and
use "Import a title from a URL" instead, which works regardless (same
extraction logic as the Workspace metadata auto-fill).

**Manual entry**: for Japanese/Korean titles, or anything without a
convenient listing page -- there's no baihehub equivalent wired up for
those languages yet, so add entries by hand as you come across them.
If you find a good JP/KR reference site later, the same
`import_title_from_url()` logic in `title_library.py` will work
against it too -- just point it at listing pages there instead.

**Importing**: one click turns a known_titles entry into an actual
drama record in your working Library, pre-filled with whatever
metadata was captured, ready for the normal align/translate/dub
pipeline.

## Scanlate (manga/comic typesetting)

Hybrid workflow: auto-detect speech bubbles → auto-clean the original
text → auto-translate and place text → review/adjust each bubble
before final render.

1. Upload page image(s) for a drama (any drama, doesn't need audio).
2. Click **Detect bubbles + auto-clean + auto-translate**. This uses:
   - `detect_bubbles_cv()` — a free, local OpenCV heuristic (looks for
     large light-colored regions with a clear border). Works well on
     clean scans with typical white bubbles; misses irregular or
     borderless bubbles.
   - OCR per bubble (manga-ocr for Japanese, Tesseract otherwise).
   - Translation via whichever engine you've selected.
3. Review the bubble table: adjust x/y/w/h, font size, translated
   text, or check "skip" for false positives. Add missed bubbles
   manually with the expander below the table.
4. Click **Render typeset page** to inpaint (erase original text) and
   draw the translated text into each bubble, then download the result.

**Known limitations:**
- Detection is a heuristic, not a trained model — it works best on
  clean scans with solid white bubbles and clear borders. Irregular
  bubble shapes, sound effects, or borderless text will need manual
  boxes.
- Inpainting uses OpenCV's built-in algorithm (no ML model) — works
  well on plain backgrounds, less well on bubbles with patterns/gradients.
- Text rendering is horizontal only — no vertical CJK layout (that's
  what koharu specializes in, not replicated here).
- For meaningfully better detection accuracy, `scanlate.py` has a
  `detect_bubbles_ml()` hook where a real trained detector (e.g.
  `ogkalu/comic-text-and-bubble-detector` on Hugging Face) can be
  wired in — not implemented here, ask if you want it built out.

## Known-site registry

`known_sites.py` holds a small curated list of well-known, official/
licensed platforms for this genre space -- the kind of information
found in library research guides ("where to legally read X"). No
aggregator/scanlation sites included. Currently covers:

- **Chinese baihe**: JJWXC (novel), Fanjiao (audio drama), MissEvan/
  Maoer FM (audio drama), Kuaikan Manhua, Bilibili Comics (manhua)
- **Korean GL**: Naver Webtoon/Series, Lezhin Comics, KakaoPage/Kakao
  Webtoon, Ridibooks, Bomtoon, Tappytoon (manhwa/novel)
- **Japanese yuri**: BookWalker, ComicWalker (manga/novel), DLsite
  (audio drama + manga -- their "DLsite Sound" section is a major
  source of independent yuri audio drama), Comic Yuri Hime (the
  flagship official yuri manga magazine), Fantia (audio drama)

Browsable in the Navigator tab (filterable by language/content type)
and selectable as a starting point in metadata lookup. This is just a
directory — you still need your own account/access on whichever
platform you use, same as everywhere else in this app.

## Code organization

`app.py` is a thin orchestrator; each tab's actual UI logic lives in
`tabs/*.py` (`library.py`, `workspace.py`, `reader.py`, `scanlate.py`,
`navigator.py`, `discover.py`, `settings.py`), with shared imports
centralized in `common.py`. If you're extending this yourself, that's
where to look.

## Performance & dashboard

- **Paginated review table** (Workspace): long dramas render one page
  of lines at a time instead of every line's widgets at once -- edits
  on one page never touch other pages (splice-tested).
- **SQLite WAL mode**: smoother concurrent reads/writes at library scale.
- **Library dashboard**: total dramas, lines translated, API calls
  logged, estimated spend, status/type breakdowns, and a "recently
  active" list, all at the top of the Library tab.
- **Bulk actions** (Library): select multiple dramas via checkboxes,
  set status or delete them together instead of one at a time.
- **Global search**: search text across every drama's lines from the
  Library tab, not just within one title.
- **Cost tracking**: real token usage captured from Claude/DeepSeek API
  responses, logged per drama, with a cost breakdown table in the
  dashboard. Estimates only -- pricing changes over time.
- **Default settings**: save your usual translation engine, English
  locale, and style notes in the Settings sidebar so new dramas start
  pre-filled instead of resetting every time.

## Reliability

- **Rate-limit-aware backoff**: API calls (translation and cloud TTS)
  distinguish rate-limit responses from genuine errors. A rate limit
  gets exponential backoff (2s, 4s, 8s... up to 5 attempts) since
  waiting actually helps; a real error (bad key, malformed request)
  fails fast after one quick retry instead of wasting a minute on
  something that will never succeed.
- **Crash-safe, resumable translation**: progress saves after every
  batch, not just at the end. Re-running translation on a drama skips
  lines that already have a translation by default -- only what
  actually failed gets retried. Use "force re-translate everything"
  when you deliberately want a full redo.
- **Isolated per-line dubbing failures**: one bad line's TTS/cloning
  failure leaves that line silent in the mix rather than losing every
  other line's already-generated audio. Already-generated clips are
  reused on a re-run instead of re-synthesized (and re-paid for).
- **Isolated per-drama batch failures (CLI)**: every CLI batch command
  processes each drama independently -- one drama's failure (corrupt
  file, API error) is logged and the run continues to the rest, with a
  summary at the end (N succeeded, M failed, listing which IDs to
  retry with `--id`). Set `BAIHE_CLI_DEBUG=1` for full tracebacks.
- **Isolated diarization failures**: if diarization fails (bad HF
  token, missing install), the alignment work already done is still
  saved -- you just don't get speaker labels until you fix and re-run it.
- **Shared Settings panel** (sidebar): enter each API key/endpoint
  once per session, reused as the default everywhere else, still
  overridable per-tab. Nothing here is written to disk.
- **Backup & restore** (Library tab): zips the whole library --
  database plus every drama's audio/video/dub files and reference
  clips -- for download, with a matching restore flow. Worth doing
  before any big batch run. Note: at very large libraries (100+ GB,
  e.g. many video dramas), the in-app zip buffers in memory -- for
  libraries that large, back up the `library/` folder directly with
  normal file tools (rsync, cloud sync) instead.

## About the reference novel & cost

Each drama's novel reference (for Claude/DeepSeek) is sent as a
prompt-cached block, scoped to that drama only — first batch pays full
price to load it, later batches (within ~5 min activity) read the
cache at a fraction of cost. A long break just re-warms the cache on
your next batch.

**Context limits:** a very long novel (200k+ Chinese characters) may
not fit alongside your dialogue batches. Trim to the relevant arc, or
compress into a glossary of names/relationships/key phrases instead.

## Scaling to 50–100+ dramas

- SQLite + per-drama folders handle this volume fine locally.
- Use Library tab filters to track progress (e.g. `status = not started`).
- Use `cli.py` for unattended batch runs.
- For cost/speed at real volume, consider the Anthropic **Batch API**
  (roughly half the per-token cost, async) — ask if you want the app
  extended to support that mode.




## Notes & tips

- First run will download the Whisper model (a few hundred MB to ~3GB
  depending on size chosen) — this only happens once.
- No GPU required, but a GPU will make transcription much faster. Edit
  `load_whisper_model()` in `app.py` to use `device="cuda"` and
  `compute_type="float16"` if you have one.
- The alignment is an approximation — it interpolates timing for any
  lines Whisper didn't clearly catch. Always skim the review table
  before exporting, especially around scene transitions or overlapping
  dialogue.
- Your API key and files never leave your machine except for the
  direct call to Anthropic's API for translation.

## Raw novel context for transcription

Separate from the reference translation used for translation quality:
upload the ORIGINAL-language novel (Workspace -> Content source ->
"Raw novel") and it feeds speech recognition, not translation.

Whisper's `initial_prompt` has a real, hard limit -- only roughly the
last ~224 tokens actually influence decoding, so the whole novel isn't
handed over (that would silently waste most of it). Instead: your
glossary's proper nouns go in first since they're the highest-value
part, then a bounded excerpt of real prose for phrasing and rhythm,
trimmed to fit. Accepts .txt, .md, or .epub.

## Resume after a crash

Confirmed end to end, not just asserted: a translation run that dies
partway through leaves every completed batch saved. Restarting only
re-sends whatever didn't finish -- verified with a batch that fails
consistently (not just once), then a fresh run afterward that needed
exactly one API call to finish the other nine lines rather than
resending anything already done.

## If your exported subtitles are blank

A `.srt` with real, correct timestamps but no text is not a rendering
bug -- `lines_to_srt` faithfully writes whatever's in each line's field,
and an untranslated line simply has nothing there yet. This is what it
looks like when English subtitles are exported for a drama that's been
aligned but not translated: 391 timed entries, every one empty.

Check the Library dashboard's "Lines translated X / Y" metric, or the
Workspace's line count next to your drama. If translation hasn't run,
press **Translate all lines** first -- or use the free `test_offline`
engine to confirm the whole export pipeline works before spending
anything on a real translation.

The export buttons now warn before this happens rather than after:
zero translated lines disables the English/bilingual downloads outright,
a partial translation shows how many lines will export blank, and the
same check runs before the CLI burns subtitles into a video.

## Migrating off components.html

`st.components.v1.html` and `st.components.v1.iframe` are both
deprecated in favour of `st.iframe`, which auto-detects whether it was
given raw HTML, a URL, or a file path. Both call sites in this project
(the interactive reader, and Discover's site-embed panel) were migrated,
and the now-unused `components` import was removed rather than left in
place as an invitation to use the deprecated API again.
