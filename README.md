# Baihe Audio Drama Subtitler — Library Edition

A local tool for translating, subtitling, and optionally AI-dubbing
Chinese/Japanese/Korean audio dramas, novels, comics, and streamer VODs
at scale. Inspired by
[pyvideotrans](https://github.com/jianchang512/pyvideotrans)'s workflow
(ASR → translate → TTS dub / clone), scoped to this genre space, with a
persistent filterable library for managing dozens of titles.

It is a Python app with a FastAPI server and React frontend, started with
`start.bat` (or `python -m api`), plus a headless CLI (`cli.py`). The
older Streamlit UI has been removed; see
[Project status and architecture](#project-status-and-architecture).

**Important:** this tool works on files you already have legal access
to (audio/video you've downloaded or been given, novel text you own or
have licensed). It does not scrape, download, or extract content from
paid apps or streaming platforms — point it at local files, or a public
URL you have the right to download from, only.

## Table of contents

- [Project status and architecture](#project-status-and-architecture)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Translation](#translation)
- [Transcription & OCR](#transcription--ocr)
- [Dubbing & voice cloning](#dubbing--voice-cloning)
- [Reader & Library](#reader--library)
- [Metadata & site tools](#metadata--site-tools)
- [Reliability](#reliability)
- [Troubleshooting](#troubleshooting)
- [Testing & diagnostics](#testing--diagnostics)
- [Code organization](#code-organization)
- [Notes & tips](#notes--tips)

## Project status and architecture

*Verified against the repository on 2026-10-04.*

- **Launcher.** `start.bat` starts `python -m api` on
  `http://127.0.0.1:8600/` (loopback only), which serves the prebuilt
  React app from `frontend/dist` (a release zip, see
  [`docs/RELEASE.md`](docs/RELEASE.md)).
- **Streamlit app (removed).** The old UI (`app.py`, `tabs/`) was deleted;
  `legacy/streamlit` and the `pre-streamlit-removal` tag hold the last version.
  The React app is the only UI.
- **FastAPI + React app.** An HTTP API (`api/`)
  and a React frontend (`frontend/`) over the *same* library, database and
  background jobs. Pages: Library (and Library tools), the per-drama Workspace
  stages, the standalone Translate page, Reader, Discover, Live, Sources,
  Comic (Scanlate), Saved manga, Benchmark Lab, Assistant, Diagnostics, Settings
  and Admin (`frontend/src/pages/`; status in `docs/STATUS.md`).
- **Layers.** `db.py` (plain `sqlite3`) and the domain modules at the
  repo root hold the logic; `services/` wraps them in UI-independent
  functions; `api/` exposes those as HTTP routes; `frontend/` is the
  React client and calls `/api`. `cli.py` uses the same underlying
  modules.
- **Access.** `python -m api` serves the
  built `frontend/dist` at `/` (`api/static_frontend.py`). The PC's own port is
  loopback-only with no login. Other household devices can use a separate
  listener with Google sign-in, which is opt-in (see "Access from other
  devices" below and `docs/remote-access-decision.md`).
- **Background services.** `python -m api` also starts the scheduled chapter
  check and other schedulers (`api/background.py`), and the browser-extension
  bridge (`page_server.py`) when the extension setting is on.

Where to read more: [`FILE_ORGANIZATION.md`](FILE_ORGANIZATION.md) (file
map), [`docs/README.md`](docs/README.md) (docs index),
[`docs/STATUS.md`](docs/STATUS.md) (current status and what's next) and
[`docs/archive/`](docs/archive/) (migration history, old bug tracker).

## Features

- **Library** of titles, filterable by title, author, studio, director, voice actor, status, source language (zh/ja/ko) and content type (audio drama, video drama, novel, manhwa, manga, manhua, ASMR, streamer VOD); series share glossaries, styles and characters.
- **Three content modes**: audio drama (your audio or video plus a transcript, aligned to real timing), novel narration (paste the text; the app chunks it, tags speakers with the LLM, translates, and can generate a narration/dub) and streamer VOD (see [Streamer VODs and series](#streamer-vods-and-series)).
- **Transcription** with Whisper (optional Qwen3-ASR), **speaker diarization**, OCR for page scans and burned-in captions, and **Live** near-live stream translation.
- **Multi-engine translation**: Claude, DeepSeek, Gemini, OpenAI, Ollama or NLLB, with glossaries, term policies, style presets, emotion tags, a review queue and a consistency checker.
- **Review and polish**: line merging, pacing checks with one-click LLM shortening, translation versions, undo, locale variants (American/British/Australian English).
- **AI dubbing** with free TTS (edge-tts) and optional voice cloning; audiobook (.m4b) export.
- **Export**: SRT/VTT/ASS, burned-in (hardsub) or toggleable (softsub) video, dub track mixing, EPUB, bulk zip.
- **Interactive Reader** with pinyin/furigana, click-to-define, in-app Q&A, in-app playback and Anki vocabulary export.
- **Scanlate** (manga/comic typesetting): detect bubbles, clean, translate, place text, adjust, render.
- **Sources and Discover**: site adapters for importing chapters, metadata auto-fill from public listing pages, a known-titles catalog, and a site navigation helper.
- **Video input**: upload `.mp4`/`.mov`/`.mkv`/`.webm` or download from a URL via yt-dlp; the audio is extracted for alignment and the video kept for export.
- **CLI** (`cli.py`): headless batch runs across your library.

## Installation

### Windows

Run the Setup installer (`BaiheStudio-Setup-<version>.exe`, built by the "Windows Installer" workflow; see `docs/RELEASE.md` and `docs/windows-installer-design.md`), or from a source checkout double-click **`start.bat`**. `start.bat` creates the virtual environment and installs dependencies on first run, checks ffmpeg, a JS runtime and CUDA and says plainly what is missing, starts `python -m api` on `http://127.0.0.1:8600/`, and opens it in its own window (Edge app mode, else Chrome, else your default browser). Running it again just reopens the window. `make_shortcut.bat` makes a desktop shortcut.

**The app's screens.** A source checkout needs `frontend\dist\index.html`. Download `baihe-frontend-<version>.zip` from the project's GitHub Releases and extract it into the Baihe folder (how it is made: [`docs/RELEASE.md`](docs/RELEASE.md)), or with Node.js 22 run `start.bat --build-frontend`. If it is missing, `start.bat` stops and says so.

`start.bat` also accepts `--portable`, `--server-only` (don't open a window), `--ci`, and `--python-version 3.12` (or a `PYTHON_VERSION` marker file); `start.ps1` is the PowerShell equivalent (`-Portable`, `-PythonVersion`, `-BuildFrontend`). It uses `constraints.lock.txt` instead of `constraints.txt` if you made one with `make_lock.bat`. `uninstall.bat` removes the shortcut and virtual environment and asks separately (default no) before touching your library or your user PATH entries for ffmpeg/Tesseract.

### Prerequisites

Python 3.10+ (CI uses 3.11; the installer ships 3.12) and `ffmpeg` with libass (for burning subtitles; most builds have it). Node.js 22 is only needed to build the frontend yourself. Other tools (Tesseract and so on) are covered where each feature is described.

```bash
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Ubuntu/Debian
# Windows: download from ffmpeg.org and add to PATH
```

### Install the app

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements-core.txt -c constraints.txt
```

That is the minimum to launch and translate text (it includes FastAPI and uvicorn). Add what you use:

```bash
pip install -r requirements-media.txt -c constraints.txt      # audio/video: align, dub, burn subtitles
pip install -r requirements-optional.txt -c constraints.txt   # per feature; install only the lines you need
```

`requirements.txt` is those three files combined. The Diagnostics page's Install buttons do the same picking without typing. `-c constraints.txt` caps packages at major versions known not to break the app (it installs nothing itself); use `constraints.lock.txt` instead to reproduce your own working setup.

**First run:** open **Settings** and add the API key for your translation engine, or use a free local one (Ollama, NLLB). Keys are read from `.env` next to `start.bat` (copy `.env.example`; it is not versioned) or from environment variables. Models such as Whisper download on first use.

### Portable mode

`library/` is saved inside the app folder, so copying the folder carries your data. Portable mode also redirects the model caches (Whisper, pyannote, F5-TTS, the audio separator) into a `model_cache/` folder there, so the whole folder works from a USB stick or another PC. Turn it on with `start.bat --portable` (`.\start.ps1 -Portable`) or an empty file named `PORTABLE` next to `start.bat`. The other PC still needs its own Python and `ffmpeg`.

### Access from other devices

The app is **loopback-only** (`127.0.0.1`) by default; the PC's own window (port 8600) is never exposed. Household members can reach it through opt-in remote access: the installed background service plus Caddy (HTTPS on 443) in front of a separate household listener with Google sign-in always on. Nothing opens a router port or firewall rule for you. Guide, including ports: [`docs/household-access.md`](docs/household-access.md); design: [`docs/remote-access-decision.md`](docs/remote-access-decision.md).

## Usage

### Running the app

Double-click `start.bat`, or from the repo root inside the venv (once `frontend/dist` exists):
```bash
python -m api     # then open http://127.0.0.1:8600/
```
Set `BAIHE_API_SERVE_FRONTEND=0` to run API-only.

**CLI (headless batch)** (`python cli.py --help` lists every command and option):
```bash
python cli.py list
python cli.py align --whisper-size medium                    # audio-drama mode
python cli.py align --id 3 --transcript script.txt            # or --transcript - for stdin
python cli.py narrate-prep --engine claude --api-key $KEY     # novel-narration mode
python cli.py translate --status aligned --engine claude --api-key $KEY
python cli.py dub --status translated
python cli.py export-video --subs english
python cli.py transcribe --id 3 --language zh --whisper-size large-v3 --diarize   # same service as the app; options are saved on the title
python cli.py qc --id 3                                       # Auto QC: flags number/name/banned-term slips, no engine
python cli.py glossary list --id 3                            # also add / remove / import FILE / export (the series glossary)
```
Without `--transcript`, put the transcript at `library/dramas/<id>/transcript.txt` and the media at `library/dramas/<id>/source.<ext>`. For novel narration, put the text at `library/dramas/<id>/novel_narration_source.txt` and set `content_mode = 'novel_narration'` on the drama row (the app does all this for you).

### Developer setup

```bash
BAIHE_API_ENV=development python -m api     # API on http://127.0.0.1:8600, docs at /api/docs
cd frontend && npm ci && npm run dev        # React on http://127.0.0.1:5173
```
On Windows `cmd`, run `set BAIHE_API_ENV=development` first. The API reads `BAIHE_API_HOST` (default `127.0.0.1`; keep it there), `BAIHE_API_PORT` (default `8600`), `BAIHE_API_ENV` (`development` enables reload and CORS for the Vite ports, default `production`) and `BAIHE_API_CORS_ORIGINS` (see `api/api_config.py`). The Vite dev server proxies `/api` to `http://127.0.0.1:8600`, or to `BAIHE_API_URL` if set. `npm run build` produces `frontend/dist`, which `python -m api` serves at `/`.

- **Changing the port.** `BAIHE_API_PORT` is read from the environment, not `.env`: `setx BAIHE_API_PORT 8601` and restart. `start.bat`, `start.ps1` and the installed launcher follow it; the Vite proxy needs a matching `BAIHE_API_URL`. The default is defined once as `DEFAULT_PORT` in `api/api_config.py`; the files that can't import it (`start.bat`, `start.ps1`, `frontend/vite.config.ts`, `frontend/src/report/capture.test.ts`, the two Windows workflows, this README and `CLAUDE.md`) carry a literal, and `tests/test_installer_runtime.py` fails if one drifts. The household listener has its own port (`BAIHE_API_HOUSEHOLD_PORT`); never forward either port through the router. Baihe also refuses its own ports as ntfy, SearXNG and Jellyfin targets.
- **The Windows boot service's port.** With the service installed, its stored port wins; changing `BAIHE_API_PORT` or running Setup again does not change it (only a fresh install takes it). Use Start menu > "Baihe Studio service" (shows every port, changes the port, turns remote access on or off).
- **Service commands** (what the menu runs; `service.py --help` lists them): from an administrator prompt, run `"%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" COMMAND` with `status` (no admin needed), `set-port N`, `enable-remote [--household-port N]`, `disable-remote`, `stop`, `install [--port N]` or `uninstall`. Details: `docs/windows-installer-design.md` section 11.

### Resetting for testing

Diagnostics > Danger zone > **Reset everything** deletes every drama, translation, glossary, series, progress record and file, then reinitializes an empty database. Two-step confirmation (a checkbox, then typing RESET); there is no undo. To remove one drama, use its delete button.

## Translation

### Translation engines

| Engine | Cost | Notes |
|---|---|---|
| `claude` | Paid per token (API key from console.anthropic.com, billed separately from a Claude.ai subscription) | Best tone and character voice; novel reference with prompt caching. Model picker in Workspace > Translation (Sonnet 5, Opus 4.8, Haiku 4.5, Sonnet 4.6); see `CLAUDE_MODELS` in `translate_engines.py`. |
| `deepseek` | Paid, far cheaper than Claude | Strong on Chinese; a good default for context-aware, glossary-aware work. |
| `gemini` | Paid, close to DeepSeek (Flash-Lite tier); key from aistudio.google.com | Strong on Chinese/Japanese. Translation only: transcription still uses Whisper. Lineup changes often; see `GEMINI_MODELS`. |
| `openai` | Paid per token; `BAIHE_OPENAI_KEY` | GPT models (default `gpt-5-mini`) over Chat Completions. Newer GPT-5+ models appear in the picker after Diagnostics > Model health with "offer provider models" on, costed at a high ceiling ($5 in / $40 out per 1M tokens) because their real price is unknown; see `OPENAI_MODELS`. |
| `ollama` | Free, uses your hardware | Local via [Ollama](https://ollama.com); a usable model wants real RAM/VRAM; rougher on nuance. |
| `nllb` | Free, fully offline | Meta's [NLLB-200](https://github.com/facebookresearch/fairseq/tree/nllb) (`pip install transformers sentencepiece`); downloads 2.4GB (600M) or 5.2GB (1.3B) once. Pure machine translation: rougher on idiom and tone, and no speaker attribution. |

Claude, DeepSeek, Gemini, OpenAI and Ollama can tag speakers for novel-narration mode and take the novel reference; pure-MT engines tag everything "Narrator".

**Context from recent lines.** The "Context lines shown from before each batch" slider (default 6, or 10 for novels; 0 turns it off) shows LLM engines how the preceding lines were already translated, so pronouns and relation-only references stay consistent across batches. It does not apply to NLLB. Consistency across separate VODs of one streamer comes from assigning them to the same series.

### Translation guide (style, terms, and notes)

Configured per drama in the Workspace's Translation section.

- **Style presets** change register and pacing: audio drama (spoken, contractions), novel (literary), subtitles (instant comprehension), manhua (concise bubble dialogue).
- **Term policies**, per term: keep as pinyin (沈清疑 → Shen Qingyi), hybrid (云隐宗 → Yunyin Sect), translate meaning (听雨阁 → Listening Rain Pavilion), keep + note, or contextual (姐姐 → "jiejie" or "older sister"). Terms are categorized (name, sect, title, honorific, place, artifact, concept...) and grouped by policy in the prompt.
- **Auto-extract terms** proposes terms with a category and policy from a novel or a drama's own transcript; everything goes to a review table first. Given both the original novel and an English translation, it extracts matched pairs. Only terms are stored, never passages. Terms join the series glossary.
- **Import / export glossaries** as CSV, TSV or JSON (a two-column term/translation sheet works; category, policy, enforce_exact and notes columns are picked up when present).
- **Enforce exactly (🔒)** does a hard find-and-replace after translation for names where drift is unacceptable.
- **Translation notes** flag what didn't survive the crossing (set phrases, puns, allusions, honorifics); they are stored per line and export as a Markdown appendix.
- **Genre guidance** (toggleable): pronoun clarity, kinship terms as intimate address, no softening of romantic content.

### Adaptive translation style

Lines you rewrite in Review are recorded as before/after pairs. After 8 or more, they can be analysed into a style profile that is shown for review before it is injected into future translation prompts. Scoped per series when one is assigned, otherwise global.

### Review queue

Review > "Find lines to flag" asks the translation engine to flag only the already-translated lines worth a second look (possible mistranslation, unresolved pronoun, uncertain name, idiom). Flags are saved, can be filtered with "Show flagged lines only", and clear when you edit the line or dismiss the flag.

### Emotion-aware translation

Lines are tagged with an emotional register (14, including sarcasm, dry humour, suppressed anger) plus an intensity, and the tags are folded into the next translation run for charged lines only. Tags also map to TTS rate and pitch for dubbing.

### Streamer VODs and series

For a streamer, "Title (English)" and "Title (Chinese)" are the translated and untranslated stream name, and the **Source URL** field keeps the original link (auto-filled for URL downloads). Assign every stream to one **series** to share its glossary, style profile and named characters. Speaker labels (`SPEAKER_00`) are not stable across recordings, so a named character lives at series level: add it from the Characters section, then pick it from the dropdown in later streams. Typing a name does not add it to the series unless you tick "Remember in this series".

### Raw novel

One upload of the original-language novel (Workspace > Source > Raw novel) feeds transcription priming and, with an English reference translation, matched-pair glossary extraction.

## Transcription & OCR

### Transcription accuracy

Whisper often mishears proper nouns in Chinese without it showing. In order of value:
1. **A real transcript.**
2. **Prime it with names.** The series glossary feeds Whisper's `initial_prompt` automatically (only about the last 224 tokens influence decoding, so the glossary's nouns go first, then a bounded excerpt of raw novel prose; `.txt`, `.md` or `.epub`).
3. **`large-v3` instead of `medium`**: slightly more accurate on Korean and clean Chinese in our tests, about twice as slow and ~3GB. The default (`large-v3-turbo`) is the same on CPU and GPU; see "Which Whisper model to pick" in `docs/asr-experiments.md`.
4. **Wider beam search** (8-10): costs time only.

Other Transcribe options (Workspace > Transcribe):
- **Speech-splitting sensitivity** (default 300 ms, range 100-3000 ms; lower values split at shorter pauses and can cut mid-sentence) and **speech detection sensitivity** (the Silero VAD threshold): the first controls how short a pause starts a new line, the second helps with quiet dialogue or noise producing phantom lines.
- **Remove background music before transcribing**: vocal separation with `audio-separator` (preferred) or Demucs. Adds a full extra pass; skip it unless the background is music alone (in the benchmark it hurt with noise and did nothing on clean audio).
- **Split long merged lines using word-level alignment** (experimental, off by default): re-aligns a line against its own audio with Meta's MMS aligner (`pip install torchaudio uroman`, ~1.1GB model on first use). It only re-times text Whisper already produced.
- **Review > Check line coverage** (run before translating) flags overlong lines, large gaps, blank source text and untranslated lines.
- A translation that failed in the background shows as a banner in the Workspace until dismissed; press Translate again and already-translated lines are skipped.

### OCR (image-based chapter scans)

Upload page images in the novel-narration OCR section instead of pasting text. Always review OCR output before translating.
- **Tesseract** (default): `pip install pytesseract pillow` plus the Tesseract binary with language packs (macOS `brew install tesseract tesseract-lang`; Ubuntu `sudo apt install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-jpn tesseract-ocr-kor`; Windows [installer](https://github.com/UB-Mannheim/tesseract/wiki)). If Windows can't find it, set Settings > OCR > Tesseract binary path to `tesseract.exe`.
- **PaddleOCR** (`pip install paddleocr paddlepaddle`): heavier, better on stylized Chinese fonts.
- **manga-ocr** (`pip install manga-ocr`): Japanese speech bubbles; best on single-bubble crops.

### Hardsub OCR (captions burned into a video; experimental)

Choose "The video already has captions burned in" as the transcript source (needs a video file). It samples frames, finds the caption row band, OCRs it with the same backends and collapses repeats into timed lines. Limits: one caption band only, a static logo can outscore a faint caption, and it is slower than audio transcription (raise the sample interval to speed it up).

### Simplified vs Traditional Chinese

For `zh` sources, the "Chinese script" toggle (default Simplified) affects only OCR (Tesseract `chi_sim` vs `chi_tra`; install the matching language pack) and Reader word segmentation (Traditional is converted with OpenCC to find word boundaries, then the original text is sliced). Whisper and translation are unaffected.

### Speaker diarization

Needs `pip install pyannote.audio soundfile`, a free Hugging Face token, and accepting the model terms at https://huggingface.co/pyannote/speaker-diarization-community-1. First run downloads the model. CPU works, GPU is faster.

### Live (experimental)

The Live page pulls a running stream, cuts it into short chunks (your choice of length), transcribes and translates each in the background, and shows a growing feed. Needs `yt-dlp` and `ffmpeg`.
- Latency is at least one chunk; shorter chunks lower it but give Whisper less context.
- Accuracy is lower than the normal pipeline: each chunk is transcribed alone with a few seconds of overlap and no glossary priming.
- The feed is not saved to the Library; copy what you want before you stop.
- A resolved stream URL can expire after a few hours; stop and start again.
- Tested against a continuous ffmpeg capture, not a real live broadcast.

### Novel-narration line timing and EPUB

With no source audio, line timings come from each generated TTS clip, so download the `.srt` after generating the track. Novel-narration mode can import chapters from an `.epub` you own (needs `ebooklib`, `beautifulsoup4`) and export the translation as an `.epub`.

## Dubbing & voice cloning

- **edge-tts** (default, free, online): a fixed voice list, one voice per character.
- **Piper** (`pip install piper-tts`): offline; choose "Offline / Piper" as the fallback engine.
- **F5-TTS** voice cloning (`pip install f5-tts`): per character, a clean reference clip and its exact text. "Auto-extract reference clips" pulls clips from a diarized audio drama. Not verified end to end in development: test on one short line first.
- **Other engines, picked per character**: OmniVoice (`pip install omnivoice`; clones from a 3-10s clip or designs a voice from a description), GPT-SoVITS (not a pip package: run its `api_v2.py`; default server `http://127.0.0.1:9880`, changeable in Settings), Chatterbox (`pip install chatterbox-tts`; emotion-aware, carries a PerTh watermark; run emotion detection first), TADA (`pip install hume-tada`; weights under Meta's Llama 3.2 licence, accept it on Hugging Face and run `huggingface-cli login`). OmniVoice, Chatterbox and TADA pin conflicting `transformers`/`torch` versions, so install only one per environment.
- **Fitting to timing**: a dubbed clip longer than its slot is sped up at most 1.4x (pitch kept), a shorter one slowed at most 0.85x; past the limit the line runs over, so shorten it with the pacing check. Both limits are adjustable (`--max-speedup` / `--max-slowdown` on `cli.py dub`). Editing a line and generating again re-voices only that line.
- **Narration**: consecutive lines from the same speaker in a paragraph are voiced in one call; each stays its own cue.
- **Audiobook export**: "Generate audiobook (.m4b)" in Export builds an M4B with chapter markers from the novel's headings (or one per paragraph); CLI: `python cli.py dub --id N --m4b`.

Cloning real people's voices, even for personal fan translation, is a legal and ethical gray area; think it through before sharing dubbed files.

## Reader & Library

### Interactive Reader

Raw and translated text side by side, for proofing and language learning.
- **Word segmentation**: jieba (Chinese), sudachipy (Japanese), kiwipiepy (Korean). Install what you need: `pip install jieba pypinyin` / `pip install sudachipy sudachidict_core pykakasi` / `pip install kiwipiepy`.
- **Ruby annotations**: pinyin over Chinese, furigana over Japanese kanji (Korean is skipped).
- **Click-to-define**: Chinese uses a local CC-CEDICT lookup (downloaded once, then offline). Japanese, Korean and unknown Chinese words use an LLM definition and need an API key.
- **Pagination** keeps big chapters light. Follow-along playback highlights the spoken line using the Reader's own embedded clip. Reading progress saves when you change pages; playback position is stored separately.
- **Settings**: text size, line spacing, width, font and a Reader theme (light, sepia, dark), separate from the app theme in the header.
- **Spoiler-free mode** (Settings, on by default) limits every AI feature (character lookups, recaps, relationship maps, the wiki) to the page you have reached.
- **Universe wiki**: an encyclopedia built as you read (characters, places, sects, artifacts, events), updated on re-extraction and exportable as Markdown.
- **Story tools**: who is this character, relationship map (Mermaid), spoiler-safe recap, explain an idiom or reference; **Ask about this drama** answers from the lines loaded so far. These need an API key.
- **Line tools**: Why this?, Alternatives, Grammar, Pronounce, Improve this line (feeds the adaptive style profile) and Re-transcribe this line (re-runs Whisper on that line's audio only).
- **Vocabulary export**: looked-up words export as Anki-importable CSV or a `.apkg` deck.

### Library

- **Continue shelf** with cover art and progress; reading history (clearable).
- **Metadata**: cover, genre, publication status, chapter count, custom tags, private notes, reading/listening time estimates.
- **Dashboard**: totals, translated lines, API calls, estimated spend, cache-hit rate, per-drama costs.
- **Series**: dramas sharing a glossary or characters get a consolidated view.
- **Search and bulk actions**: a global search over every drama's original and translated lines; bulk status, delete (typed confirm) and translate (skips dramas without a key, lines, or already running).
- **Library tools** (page): export all as a zip, per-drama export package (with a manifest of what was included), backup and restore, presets, voice bank, storage scan with quality presets (Archival, Balanced, Minimal; only regenerable files are removed), and a disk usage view with a Trash folder.
- **Backup and restore**: a database-only snapshot or a full zip with media, streamed to disk; restore validates the zip first and needs a typed confirm. Signed-in browser sessions from Sources are not included. Automatic backups are opt-in (Settings). See `docs/runbook.md` section 3.
- **Translation versions**: every translation run is saved with its engine and model; compare versions side by side and activate one (the current one is snapshotted first).
- **Undo**: line snapshots are taken before a force re-translate and an applied merge; restore from "Version history / undo" in the Workspace (the 10 most recent are kept).
- **Drama IDs skip numbers after a deletion, on purpose.** Lines, glossaries and versions reference a drama by id, so reusing one could re-attach stale rows or conflict with an older backup. The Library shows a separate sequential **#** column.

### Discover and titles

- Title search covers titles you've saved locally (empty until you "Load starter titles" or add your own); it is not a web search. "Find a title on the official platforms" builds site-scoped search links across known platforms. It deliberately never asks a model to suggest titles, since a model invents plausible ones.
- Baihehub search translates your query to Chinese first and is best effort; if it comes back empty you get a link to its own search page.
- Add a title by URL (a listing page), by hand, or by **bulk import** of one or more tag/ranking listing pages (title, author, tags, audio-drama flag; never content). A review table always comes before saving, with a "paste the page text" fallback for JS-only sites. "Import to library" turns a known title into a drama.
- **Known sites** (`known_sites.py`): a curated directory of official platforms for Chinese baihe, Korean GL and Japanese yuri, browsable in Discover's navigation helper. No aggregator or scanlation sites.
- An embed panel exists in Discover, but most big sites forbid iframes and render blank; the navigation helper is usually the better tool.

### Manhua and Scanlate

Detection is heuristic and surfaced for review. A speech bubble is an enclosed light region that doesn't touch the page edge; panels are found by gutter analysis in manga reading order; long webtoon strips are sliced at whitespace gaps with a small overlap; regions are classified (bubble, narration, sign, SFX, thought) and font style is sampled.

1. Upload page images or a PDF to any drama (optionally slice tall strips).
2. Pick a bubble detection backend (free OpenCV heuristic, or the ML detector `ogkalu/comic-text-and-bubble-detector`, which needs `transformers` and `huggingface_hub` and downloads on first use) and an OCR backend (manga_ocr, PaddleOCR, Tesseract, or Auto). Run detect + clean + translate, or Batch for a whole chapter.
3. Review the bubble table: adjust boxes, font size, region type, text, or "skip"; add missed bubbles by drawing a box.
4. Render the typeset page, or bulk render a chapter to ZIP/PDF.

Also: custom fonts per style, bulk find and replace with preview, font-style JSON export.
Limits: the CV detector misses borderless or irregular bubbles; inpainting is OpenCV's (weak on patterned backgrounds); text is horizontal only; OCR crops slightly inside the bubble border, so check the source text.

## Metadata & site tools

### Browser extension

A Chrome/Edge extension that sends the page you're reading into Baihe and draws the translation over it. It also reaches pages an adapter can't (`blob:` images, site-unscrambled readers, signed-in pages) because your own browser already did that work. Turn it on in Settings > Browser extension, load `extension/` unpacked (`chrome://extensions` > Developer mode > Load unpacked) and paste the token. It talks only to `127.0.0.1`. Details: [`docs/browser-extension.md`](docs/browser-extension.md).

### Metadata romanization

Credits keep the original script and gain a romanized companion: 一半山川 displays as "Yiban Shanchuan (一半山川)". Personal names are romanized; studios and platforms use their established English name where one exists (晋江文学城 as "JJWXC").

### Fetching from JS-heavy sites

Some sites (baihehub, Fanjiao) build pages with JavaScript, so a plain fetch returns an empty shell. `page_fetch.py` handles it in three layers: it detects an unrendered shell and says so; with `playwright` installed (`pip install playwright`, then `playwright install chromium`) it re-fetches with a real browser (falling back to an installed Chrome or Edge, or the program named by the `BAIHE_BROWSER_PATH` system environment variable; Diagnostics > Setup shows whether one was found); and manual paste (copy the page text into the app) always works.

## Reliability

- One failure breaks only that thing: each React page has an error boundary, database connections are reclaimed after errors, destructive writes (`save_lines`, `save_bubbles`, history snapshots) run in transactions with rollback, and optional packages are imported lazily so a missing one disables only its feature.
- Batch work isolates failures: a CLI batch continues past a failing drama and prints which ids to retry with `--id` (`BAIHE_CLI_DEBUG=1` for tracebacks); one failed TTS line leaves only that line silent; one failed page doesn't stop a bulk scanlate job; diarization failure keeps the alignment already saved.
- API calls back off exponentially on rate limits (up to 5 attempts) and fail fast on real errors.
- Translation saves after every batch and skips already-translated lines on re-run; use "force re-translate everything" for a full redo.
- Settings holds each API key once; keys are never shown back by the API.
- SQLite runs in WAL mode; the review table is paginated; real token usage is logged per drama (estimates only, since prices change).
- GPU acceleration is optional (Whisper, diarization, local cloning) and falls back to CPU if CUDA isn't available.
- The novel reference is sent as a prompt-cached block per drama. A very long novel (200k+ Chinese characters) may not fit; trim to the relevant arc or compress it into a glossary.
- For 50-100+ dramas: filter the Library by status and use `cli.py` for unattended batches.

## Troubleshooting

### If Hugging Face is unreachable

If Whisper's first download fails with `getaddrinfo failed` or `LocalEntryNotFoundError`, it is a network problem. Likely causes on Windows, in order:
1. Antivirus or firewall blocking `python.exe`: allow it.
2. A VPN that is connected but not routing.
3. DNS: `ipconfig /flushdns`, then try `1.1.1.1` and `8.8.8.8`.
4. A DNS blocker (Pi-hole, AdGuard, corporate filter) answering `0.0.0.0`. Check with `nslookup huggingface.co`; if it answers `0.0.0.0` or `::`, allow `huggingface.co`, `cdn-lfs.huggingface.co`, `cdn-lfs-us-1.hf.co` and `hf.co` (the `cdn-lfs` hosts serve the model files), then flush DNS. The app names this case itself.
5. Blocked by your ISP or region: set a mirror, e.g. `$env:HF_ENDPOINT="https://hf-mirror.com"` (PowerShell) before `python -m api`.

Fully offline: download a `faster-whisper` model elsewhere and point Settings > Offline / restricted networks at the folder.

### GPU transcription failures ("cublas64_12.dll is not found")

The app already retries on CPU. The cause is usually a CPU-only PyTorch/ctranslate2 wheel or a CUDA version that doesn't match the driver. Turn GPU off in Settings > Performance to silence it.

### GPU PyTorch (NVIDIA)

`pip install torch` gives a CPU-only build on Windows, and installing torch, torchvision and torchaudio separately can leave mismatched versions. Diagnostics > Packages > GPU PyTorch shows your GPU, driver and installed torch family, and "Set up GPU PyTorch" (PC only) installs the matched CUDA build the app is tested with, then checks that CUDA works. Afterwards every Install/Update pins the installed torch family, so a package wanting a different torch is refused instead of replacing your CUDA build.

### If your exported subtitles are blank

A `.srt` with correct timestamps but no text means the lines aren't translated yet (an aligned but untranslated drama exports every entry empty). Check "Lines translated X / Y" and press Translate first (Ollama and NLLB are free). The export buttons warn before this: zero translated lines disables the English/bilingual downloads, and a partial translation shows how many lines will export blank.

## Testing & diagnostics

### Testing

The test suite is fully mocked (no GPU, models, API keys or network) and uses an isolated temp database, so it never touches your library.

```bash
pip install -r requirements-core.txt pytest -c constraints.txt
python run_tests.py              # everything (a wrapper around pytest)
python run_tests.py -k history   # a subset
```

Frontend checks, from `frontend/` after `npm ci`: `npm run lint`, `npm test` (vitest), `npm run build` (typecheck + bundle) and `npm run e2e` (Playwright against a seeded throwaway library; uses ports 8611 and 4174; install a browser with `npx playwright install chromium` or set `PLAYWRIGHT_CHROMIUM_PATH`).

### Diagnostics ("Check my setup")

The Diagnostics page (under the header's cogwheel) reports, in one place:
- **Setup** -- Python version, whether ffmpeg is on PATH, GPU and PyTorch
  state, the Deno install, which API keys are configured, whether the library
  folder is writable, and the pyannote gated-model check (an on-demand check
  that your Hugging Face token can actually access the diarization model,
  separate from just having a token set), plus one list of the models in use.
- **Jobs** -- a live view of every background job across the whole app
  (translate, transcribe, dub, diarize, Live capture, etc.) with progress, a
  per-job cancel button and delete for finished jobs.
- **Model health** -- every model the app is set up to use, flagged when
  it is retired, deprecated or no longer listed by its provider (the provider
  check runs only when you press its button).
- **Packages** -- installed versions, install-by-task presets, per-package
  Install / Update (PC only), a "Test first" upgrade check and the GPU PyTorch
  set-up.
- **Ports**, **Job history** and **Log** (recent redacted lines of the app's own
  log, 50/100/200).
- **Danger zone** -- the "Reset everything" button described in
  [Resetting for testing](#resetting-for-testing).
- A link to the Benchmark Lab, described below.

The redacted support report (strips API keys, paths and user names) is in the
header's "Report a problem" dialog ("Copy a report for a bug"). The App
Assistant (Developer Mode, PC only) is its own page.

Run Diagnostics first whenever something isn't working.

### Benchmark Lab

Diagnostics > Benchmark Lab checks whether a model, engine or setting change
actually helps on your own content. No test data ships with the app: you
supply it.

- **Golden sets.** Cases are translation pairs (source text plus a reference
  translation) in a named set with a tier: public (a set you import, e.g. a
  FLORES-200 slice), application (your own corrected translations) or
  regression (a line you fixed by hand and kept). Import pasted JSONL
  (`{"source": ..., "reference": ...}` per line) or TSV (`source<TAB>reference`),
  up to 500 cases; add one case by hand; or keep a corrected line from Review
  as a regression case. Nothing is downloaded.
- **Run.** Pick a stage (translation, transcription or OCR), a set, and one to
  four engine/model configs; two or more run the same cases side by side
  (Model Arena). Press "Estimate cost" first: paid engines stop at the monthly cap.
- **Scores.** Translation uses chrF via `sacrebleu` when installed, else a text similarity ratio; transcription and OCR use
  1 - CER (1 - WER for space-delimited languages), via `jiwer` when installed,
  else a built-in scorer. Each result records its metric and scorer.
- **Results.** Every run is saved (engine, model, score, latency, cost, peak VRAM,
  per-case pass/fail) under "Recent runs"; tick runs and press "Compare".
  A scheduled re-evaluation of candidate models against your production model
  is on the same page.

## Code organization

`FILE_ORGANIZATION.md` has the full file map and `docs/technical-notes.md` a log of bugs found and fixed.

## Notes & tips

- The first run downloads the Whisper model (a few hundred MB to ~3GB, once).
- No GPU is required, but a GPU makes transcription much faster (Settings > Performance).
- Alignment is an approximation that interpolates timing for lines Whisper didn't clearly catch; skim the review table before exporting.
- Your API keys and files never leave your machine except for the direct call to your chosen translation/TTS provider.
