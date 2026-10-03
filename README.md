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
- [Reliability & performance](#reliability--performance)
- [Troubleshooting](#troubleshooting)
- [Testing & diagnostics](#testing--diagnostics)
- [Code organization](#code-organization)
- [Notes & tips](#notes--tips)

## Project status and architecture

*Verified against the repository on 2026-09-29.*

- **Launcher.** `start.bat` starts `python -m api` on
  `http://127.0.0.1:8600/` (loopback only), which serves the prebuilt
  React app from `frontend/dist` (a release zip, see
  [`docs/RELEASE.md`](docs/RELEASE.md)).
- **Streamlit app (removed).** The old UI (`app.py`, `tabs/`) was deleted;
  `legacy/streamlit` and the `pre-streamlit-removal` tag hold the last version.
  The React app is the only UI.
- **FastAPI + React app (the app going forward).** An HTTP API (`api/`)
  and a React frontend (`frontend/`) over the *same* library, database and
  background jobs. Pages: Library, the per-drama Workspace
  stages, the standalone Translate page, Reader, Discover, Live, Sources,
  Comic (Scanlate), Benchmark Lab, Assistant, Diagnostics and Settings
  (`frontend/src/pages/`; status in `docs/STATUS.md`).
- **Layers.** `db.py` (plain `sqlite3`) and the domain modules at the
  repo root hold the logic; `services/` wraps them in UI-independent
  functions; `api/` exposes those as HTTP routes; `frontend/` is the
  React client and calls `/api`. `cli.py` uses the same underlying
  modules.
- **Not a production deployment story yet.** `python -m api` serves the
  built `frontend/dist` at `/` (`api/static_frontend.py`). It is
  loopback-only with no login, so other devices on your network can't
  reach it until authentication exists (`docs/remote-access-decision.md`).
- **Background services.** `python -m api` also starts the browser-extension
  bridge (`page_server.py`) and the scheduled chapter check
  (`api/background.py`).

Where to read more: [`FILE_ORGANIZATION.md`](FILE_ORGANIZATION.md) (file
map), [`docs/README.md`](docs/README.md) (docs index),
[`docs/STATUS.md`](docs/STATUS.md) (current status and what's next) and
[`docs/archive/`](docs/archive/) (migration history, old bug tracker).

## Features

- **Library**: filterable by title, author, studio, director, voice
  actor, status, source language (zh/ja/ko), and content type
  (audio drama, video drama, novel, manhwa, manga, manhua, ASMR,
  streamer VOD).
- **Metadata auto-fill**: paste a link to a public listing page (a
  JJWXC book page, a Fanjiao show page, etc.) and the app extracts
  bibliographic metadata -- title, author, studio, cast, a short
  synopsis -- for review before saving. Only ever pulls cataloging
  info, never the actual chapters/episodes.
- **Site navigation helper** (in Discover): for sites in a language you
  don't read -- paste a URL and a goal, get the page's visible menu/
  labels translated plus step-by-step navigation guidance. Describes
  the site's own public interface only; doesn't log in, purchase, or
  fetch anything for you.
- **Known-site registry**: a curated list of well-known official
  platforms for baihe, Korean GL, and Japanese yuri content (audio
  drama, novel, comic) -- browsable in Discover's site navigation
  helper section and selectable as a starting point for metadata
  lookup, instead of typing URLs from memory.
- **Discover / known titles library**: a searchable catalog of known
  titles (title, author, tags, a short synopsis) -- separate from your
  working drama catalog. Search in any language, import from a listing
  page URL (baihehub.com or elsewhere), or add manually (e.g. for
  Japanese/Korean titles). One click imports a catalog entry into your
  actual working Library to start production on it.
- **Locale variants**: choose American/British/Australian English
  spelling and phrasing per drama.
- **Series glossaries**: assign dramas to a series to share a
  consistent name/term glossary across multiple seasons, books, or a
  streamer's whole VOD archive.
- **Consistency checker**: flags the same term translated differently
  across a drama's lines -- for review, doesn't auto-fix.
- **Real subtitle merging**: combines consecutive short lines from the
  same speaker into one natural subtitle when they're close enough in
  time, rather than just flagging pacing issues.
- **In-app Q&A**: ask questions about a drama grounded in its own
  transcript/translation, right in the Reader.
- **Vocabulary export**: every word looked up in the Reader is saved
  and exportable as Anki-importable CSV or a proper `.apkg` deck.
- **Click-to-seek Reader audio**: embeds the current page's audio span
  with click-to-jump-to-timestamp on any line.
- **Three content modes per drama**:
  - **Audio drama**: you have the audio (or video — it'll pull the
    audio track) + a transcript. Aligns your transcript to real timing.
  - **Novel narration**: no audio exists yet. Paste the novel text and
    the app chunks it, tags who's speaking each line via the
    translation LLM, translates it, and can generate a full AI
    narration/dub from scratch.
  - **Streamer VOD**: link the original YouTube/stream URL and keep
    both the untranslated and translated stream name (see
    [Streamer VODs & archives](#streamer-vods--archives)).
- **Speaker diarization** (audio drama mode): distinguishes voices in
  the audio so lines can be grouped and named by character.
- **Multi-engine translation**: Claude, DeepSeek, Gemini, OpenAI, Ollama,
  or NLLB.
- **AI dubbing with optional voice cloning**: free TTS (edge-tts) by
  default; attach a reference clip per character for real voice
  cloning (F5-TTS) instead. If you have an existing audio drama for a
  title, reference clips can be auto-extracted from it per character
  — useful for keeping a consistent voice between the audio-drama
  episodes and any novel-only chapters/side stories for the same title.
- **Source language**: Chinese, Japanese, or Korean — set per drama, drives both speech recognition and OCR.
- **Video input**: upload `.mp4`/`.mov`/`.mkv`/`.webm` directly, or
  download from a URL via yt-dlp — the audio track is extracted
  automatically for alignment/diarization, while the original video is
  kept for final export.
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
  track directly in the Reader.
- **Scanlate (manga/comic typesetting)**: hybrid workflow -- auto-
  detect speech bubbles, auto-clean the original text, auto-translate
  and place text, then adjust position/size/font/text per bubble
  before final render. Free OpenCV-based detection by default.
- **Live (experimental)**: near-live translation of an ongoing stream,
  chunked and translated as it arrives rather than after the fact.
- **CLI**: headless batch mode (`cli.py`) for unattended runs across
  your whole library.
- **Bulk export**: zip up subtitles + dub/narration tracks.

## Installation

### Windows: one-click setup

If you're on Windows and don't want to type any commands, double-click
**`start.bat`** (or the "Baihe Subtitler" desktop shortcut, once you've
run `make_shortcut.bat` once to create it). It creates the virtual
environment and installs dependencies the first time, checks that
ffmpeg/a JS runtime/CUDA are set up and tells you plainly if any of them
aren't, then starts the app server (`python -m api`) on
`http://127.0.0.1:8600/` and, once `/api/health` answers, opens it in its
own window (Edge's app mode, falling back to Chrome or your default
browser). Running it again just reopens the window if the app's already
running.

**One-time extra step: the app's screens.** The React frontend ships as a
prebuilt zip, so you don't need Node.js. Download
`baihe-frontend-<version>.zip` from the project's GitHub Releases page and
extract it into the Baihe folder, so that `frontend\dist\index.html`
exists. If it's missing, `start.bat` stops and tells you this. How the zip
is made: [`docs/RELEASE.md`](docs/RELEASE.md). Developers with Node.js 22
can run `start.bat --build-frontend` instead.

`start.ps1` is a PowerShell equivalent (`-Portable`, `-PythonVersion`,
`-BuildFrontend`), and `start.bat` also accepts `--portable`,
`--server-only` (don't open a window), `--ci`, `--build-frontend` and
`--python-version 3.12` (or a `PYTHON_VERSION` marker file). It uses
`constraints.lock.txt` instead of `constraints.txt` if you've made one
with `make_lock.bat`. `uninstall.bat` removes the
shortcut and virtual environment, and asks separately (defaulting to
**no** each time) before it will touch your library or check your
user-level PATH for ffmpeg/Tesseract entries you may have added by hand
during setup. See "Portable mode" below if you want to run the whole
app from a USB stick or move it between
machines.

Everything below this also works the same way on macOS/Linux, or if you
just prefer the command line on Windows too.

### Prerequisites

Python 3.10+ (CI and the cloud test setup use 3.11; the Windows installer
ships 3.12) and `ffmpeg` **with
libass support** (needed for burning subtitles into video). Most
standard `ffmpeg` builds already include it. Node.js is only needed to
build the React frontend yourself (22 is what CI uses); the release zip
avoids that. Optional system
tools such as the Tesseract binary (for `pytesseract` OCR) are covered
where each feature is described.

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows: download from ffmpeg.org and add to PATH
```

### Install the app

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements-core.txt -c constraints.txt
```

(`requirements-core.txt` also installs FastAPI and uvicorn for the API.)
That's the minimum to launch the app and translate text — the same
starting point `start.bat` installs on Windows. Add only what you'll
actually use on top of it:

```bash
pip install -r requirements-media.txt -c constraints.txt      # audio/video: align, dub, burn subtitles
pip install -r requirements-optional.txt -c constraints.txt   # everything else, one feature at a time
```

`requirements-optional.txt` is grouped by feature and commented per
package — skip `pyannote.audio` if you're not diarizing, skip `f5-tts`
if you're not cloning voices, etc.; install just the lines you need
instead of the whole file. The same picking is available with no
typing at all from the Diagnostics page's own Install buttons, once the
app is running. If you'd rather install everything in one shot instead
of picking, `requirements.txt` is those three files combined (it also
pulls in `pytest`, via `requirements-optional.txt`).

**First run:** start the app, open **Settings** and add the API key for
the translation engine you'll use, or use a free local one (Ollama, NLLB).
Keys are read from a `.env` file next to `start.bat` (copy `.env.example`
to `.env`; it is excluded from version control) or from environment
variables. Models such as Whisper download on first use.

`-c constraints.txt` caps a handful of packages at major versions known
not to have broken this app (that's exactly how pyannote 4 broke
diarization before) — it doesn't install anything by itself. If you've
run `make_lock.bat` and have a `constraints.lock.txt`, use that instead
(`-c constraints.lock.txt`) to reproduce your own known-working setup
exactly.

### Portable mode

`library/` — every drama, translation, audio/video file and backup — was
always saved relative to this app's own folder, not some fixed OS
location, so copying the folder already carried your data with it. What
wasn't portable is the models Whisper/pyannote/F5-TTS/the audio
separator download on first use: by default those go to your OS's own
shared cache (`~/.cache/huggingface`, etc.) outside this folder, the
same as they would for any other tool using them.

**Portable mode** redirects those into a `model_cache/` folder inside
this one, so the whole app folder — library and downloaded models
together — can be copied to a USB stick or a different PC and just work
there. Turn it on either way:

- Run `start.bat --portable` (or `.\start.ps1 -Portable`), or
- Create an empty file named `PORTABLE` next to `start.bat` — the simplest
  way to make it "part of the folder" so a copy keeps the setting.

**The real limit, stated plainly**: this moves *the app and its data*,
not *the need for Python and system tools to already be present*. The
machine you copy it to still needs its own Python install (matching
[Prerequisites](#prerequisites) above) and `ffmpeg` on PATH — portable
mode doesn't bundle either. A genuinely no-install single-file build
would need to bundle a full Python interpreter plus every ML dependency
this app can use, multi-gigabytes either way, so that isn't what this
does.

### Access from other devices

By default the app is **loopback-only** (`127.0.0.1`): only the PC running it can open it, and the PC's own window (port 8600) is never exposed to the network. Household members can reach it from their own devices through remote access, which is opt-in: the installed background service plus Caddy (HTTPS on port 443) in front of a separate household listener with Google sign-in always on. Nothing opens a router port or a firewall rule for you. The step-by-step guide, including which ports are opened, is [`docs/household-access.md`](docs/household-access.md); the design is [`docs/remote-access-decision.md`](docs/remote-access-decision.md).
`start.bat --server-only` still runs the server without opening a window
(same effect as setting `BAIHE_SERVER_ONLY`).

## Usage

### Running the app

**GUI:** double-click `start.bat` (see Installation). Manual equivalent,
from the repo root inside the venv, once `frontend/dist` exists:
```bash
python -m api     # then open http://127.0.0.1:8600/
```

**CLI (headless batch):**
```bash
python cli.py list
python cli.py align --whisper-size medium                    # audio-drama mode
python cli.py align --id 3 --transcript script.txt            # or --transcript - for stdin
python cli.py narrate-prep --engine claude --api-key $KEY     # novel-narration mode
python cli.py translate --status aligned --engine claude --api-key $KEY
python cli.py dub --status translated
python cli.py export-video --subs english                     # full subtitled episodes (styled ASS, speaker colours; --style PRESET, --no-speaker-colors, --plain, --mode softsub)
```
For CLI-driven audio-drama prep, pass the transcript with `--transcript FILE`
(with `--id`; also accepted by `run`) or place it at
`library/dramas/<id>/transcript.txt`, and audio/video as
`library/dramas/<id>/source.<ext>`. For novel-narration mode, place the
novel text at `library/dramas/<id>/novel_narration_source.txt` and set
`content_mode = 'novel_narration'` on that drama row (the GUI does all
of this automatically when you use it — manual placement is only for
adding dramas without ever opening the GUI).

### Running the API and the React frontend

This is the developer setup (for normal use, `start.bat` does all of it);
it uses the same library as before (see [Project status and architecture](#project-status-and-architecture)
for what works). FastAPI/uvicorn come with `requirements-core.txt`; the
frontend needs Node.js (22 is what CI uses). Two terminals, from the
repo root:

```bash
BAIHE_API_ENV=development python -m api     # API on http://127.0.0.1:8600, docs at /api/docs
cd frontend && npm ci && npm run dev        # React on http://127.0.0.1:5173
```

- On Windows `cmd`, set the variable first (`set BAIHE_API_ENV=development`).
- The API reads `BAIHE_API_HOST` (default `127.0.0.1`), `BAIHE_API_PORT`
  (default `8600`), `BAIHE_API_ENV` (`development` or `production`,
  default `production`; development enables auto-reload and CORS for the
  Vite ports) and `BAIHE_API_CORS_ORIGINS` (see `api/api_config.py`).
- **Changing the port.** `BAIHE_API_PORT` is read from the environment
  (not `.env`), so `setx BAIHE_API_PORT 8601` and restart Baihe. The host is
  always `127.0.0.1`. `start.bat`, `start.ps1` and the installed launcher
  honour it (window, health check, `--stop`); the Vite dev proxy needs
  `BAIHE_API_URL=http://127.0.0.1:8601` to match. The household listener's
  port is separate (`BAIHE_API_HOUSEHOLD_PORT`); never forward either port
  through the router. The default (8600) is defined once, as
  `DEFAULT_PORT` in `api/api_config.py`; change it there. The files that
  can't import it (`start.bat`, `start.ps1`, `frontend/vite.config.ts`,
  `frontend/src/report/capture.test.ts`, the two Windows workflows, this
  README and `CLAUDE.md`) carry a literal, and `tests/test_installer_runtime.py`
  fails if one of them drifts. Baihe also refuses its own ports as ntfy, SearXNG and
  Jellyfin targets.
- **The Windows boot service's port.** With the service installed, its
  stored port is the one that counts, and the installed launcher uses it
  too. Open **"Baihe Studio service"** in the Start menu: it shows every
  port Baihe uses and changes the service's port (and turns remote access
  on or off). **Changing or deleting `BAIHE_API_PORT` does not change the
  service's port**, and neither does running Setup again while it is set
  (only a fresh install takes it; an update keeps the stored port). Without
  the service, removing it just returns to 8600.
- **Service commands** (what the menu runs; `service.py --help` prints this
  list). In an administrator prompt (`status` needs none), run
  `"%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" COMMAND`
  with COMMAND one of:
  `status` (both services, every port, remote access, the firewall rule);
  `set-port N` (move the service to port N, put back if it doesn't answer);
  `enable-remote [--household-port N]` (household access through Caddy);
  `disable-remote`; `stop`; `install [--port N]` (Setup's step);
  `uninstall` (the uninstaller's step). Details:
  `docs/windows-installer-design.md` section 11, "Commands".
- The React app calls the relative path `/api`; the Vite dev server
  (5173) and `npm run preview` (4173) proxy it to
  `http://127.0.0.1:8600`, or to `BAIHE_API_URL` if you set that.
- `npm run build` produces `frontend/dist`, and `python -m api` serves it
  at `/` when it exists (see below); `npm run preview` also works.
- Loopback-only and no login. Keep `BAIHE_API_HOST` at `127.0.0.1` until
  authentication exists (`start.bat` forces it); see
  [`docs/remote-access-decision.md`](docs/remote-access-decision.md).

#### React app in one command

`start.bat` is the one-command flow: one process, one port, no Vite/npm at
runtime (see Installation). The manual equivalent, from the repo root:

```bash
cd frontend && npm ci && npm run build && cd ..   # once, or unzip the release zip
python -m api                                     # then open http://127.0.0.1:8600/
```

Set `BAIHE_API_SERVE_FRONTEND=0` to run API-only.

### Resetting for testing

Diagnostics -> Danger zone -> **Reset everything**. Deletes every drama,
translation, glossary, series, progress record, and file on disk, then
reinitializes an empty database. Two-step confirmation (a checkbox, then
typing RESET) since there's no undo. For removing one drama rather than
everything, use the delete button in Library or Workspace instead.

## Translation

### Translation engines

| Engine | Notes |
|---|---|
| `claude` | Best for tone/character voice. Supports novel reference + prompt caching. |
| `deepseek` | Cheap, strong on Chinese. Supports novel reference. |
| `gemini` | Close to DeepSeek on price (Flash-Lite tier), strong on Chinese/Japanese. Supports novel reference. Google's model lineup/pricing changes often — see `GEMINI_MODELS` in `translate_engines.py` if a run starts erroring. |
| `openai` | OpenAI GPT models (default `gpt-5-mini`) over the Chat Completions API, set with `BAIHE_OPENAI_KEY`. Pay per token. Supports novel reference. Newer GPT-5-and-later models appear in the picker after Diagnostics > Model check with "offer provider models" on, and are costed at a high ceiling ($5 in / $40 out per 1M tokens) because their real price is unknown; see `OPENAI_MODELS` in `translate_engines.py` for the built-in list and prices. |
| `ollama` | Runs locally via [Ollama](https://ollama.com). No per-token billing, but it uses your hardware — a usable model wants meaningful RAM/VRAM. Supports novel reference. Won't match Claude/DeepSeek on nuance. |
| `nllb` | Fully local via Meta's [NLLB-200](https://github.com/facebookresearch/fairseq/tree/nllb) (`transformers` + `sentencepiece`). Genuinely free and fully offline — no API key, ever, unlike every paid engine above. Pure MT with no instruction-following, so noticeably rougher on idiom/tone than Claude/DeepSeek/Gemini. Downloads a model (2.4–5.2GB depending on size picked) on first use, then never touches the network again. |

Only `claude`, `deepseek`, `gemini`, `openai`, and `ollama` (LLM-based) can do
speaker attribution for novel-narration mode — the pure-MT engines will just tag
everything "Narrator".

**Picking one**: Claude gives the best tone/character-voice results and
is the most expensive; DeepSeek is the cheapest engine that still does
context-aware, glossary-aware translation, and is a strong default for
Chinese; Gemini sits close to DeepSeek on price with strong
Chinese/Japanese quality, worth trying as a middle ground. Gemini is a
**translation** engine only in this app — transcription still runs
through Whisper (or the optional Qwen3-ASR backend), not Gemini.

#### Context from recent lines

The "Context lines shown from before each batch" slider (Workspace, next
to the engine/locale settings) shows the LLM-based engines above how the
immediately preceding lines were already translated, not just the batch
currently being translated. Batches are otherwise translated in
isolation — a pronoun or someone referred to only by relation ("her",
"that guy") a few lines back has nothing to resolve against, and the
model has to guess fresh every batch instead of staying consistent with
what came right before it. Defaults to 6 lines; 0 turns it off. Doesn't
apply to the pure-MT engines (NLLB) — it
translates one line at a time with no concept of surrounding context at
all.

For a **streamer with multiple VODs**, this same context mechanism is
what keeps translation consistent within one video. Consistency
*across* separate VODs from the same streamer comes from a different
piece: assigning every one of their streams to the same **series** (see
[Streamer VODs & archives](#streamer-vods--archives)) so they share one
glossary, one style profile, and one set of named characters, instead of
each upload starting from a blank slate.

#### What each engine actually costs

Worth stating precisely, because "open source" and "free to use" are
different claims:

| Engine | Per-use cost | What you actually need |
|---|---|---|
| `claude` | Paid per token | API key from console.anthropic.com (separate from, and billed separately to, a Claude.ai subscription) |
| `deepseek` | Paid per token, far cheaper than Claude | API key |
| `gemini` | Paid per token, close to DeepSeek on the Flash-Lite tier | API key from aistudio.google.com |
| `openai` | Paid per token | API key from platform.openai.com |
| `ollama` | No billing | Your own hardware — a model worth using wants real RAM/VRAM |
| `nllb` | No billing, ever | Nothing beyond `pip install transformers sentencepiece` and disk space for the model (2.4GB for the 600M size, 5.2GB for 1.3B). Runs on CPU, just slower than with a GPU. |

The genuinely no-cost path is `ollama` or `nllb` **on hardware you already
own**. Everything else bills you, one way or another.

### Claude model selection

Workspace -> Translation has a model picker (Sonnet 5, Opus 4.8, Haiku
4.5, or the previous Sonnet 4.6) when Claude is the selected engine.
Anthropic updates this lineup periodically -- if a model starts
erroring, check console.anthropic.com for what's currently available.

### Translation guide (style, terms, and notes)

The craft layer -- what separates a mechanical translation from a good
one. Configured per drama in the Workspace's Translation section.

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

**Auto-extract terms** scans your source text — a novel, or a drama's
own transcript — and proposes terms with a category and policy for
each, including flagging names whose characters carry thematic meaning.
Everything goes to a review table first -- these are judgment calls.
For content with no separate source novel (an audio drama, a streamer's
VOD), this is how a glossary gets built at all: run it against the
drama's own transcript, and the terms it finds join the series glossary
so the next episode/VOD in that series starts with them already known,
instead of the glossary only ever coming from a novel.

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

### Adaptive translation style

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

### Review queue (flag lines that need a second look)

For a multi-hour transcript, reading every line to catch the handful with
real problems doesn't scale. "🔍 Find lines to flag" (Workspace → 7.
Review & edit → Review queue) asks the translation engine to review its
own already-translated lines and flag only the ones worth a second look:
a possible mistranslation, an unresolved pronoun/reference, an uncertain
name, or slang/idiom that may not have translated cleanly. Most lines get
no flag at all — over-flagging defeats the point, since you can't tell a
real issue from noise in a long list of them.

Runs in the background with a progress bar (same as Transcribe/Emotion),
persists to the database (so it survives closing the app, not just this
session), and adds a "Show flagged lines only" toggle above the review
table to jump straight to what needs attention instead of paging through
everything. A flag clears automatically once you actually edit that
line's translation — no separate "mark reviewed" click on top of the fix
itself — or dismiss it directly if it turns out fine as written.

Deliberately scoped to what's assessable from text alone. "Speaker
uncertain" and "audio unclear"/"overlapping speech" would need real
diarization-confidence or audio evidence this codebase doesn't expose
yet — worth adding later if diarization confidence scores become
available, not guessed at now.

### Emotion-aware translation

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

### Streamer VODs & archives

For a streamer, "Title (English)" and "Title (Chinese)" double as the
stream's translated and untranslated name — no separate fields needed.
There's also a **Source URL** field (Workspace's "Edit details"
section) to keep the original YouTube/stream link on the drama record,
auto-filled when the audio/video is downloaded via a URL.

A **series** isn't only for a book's numbered volumes — assign every
stream from the same streamer to one series (Workspace → 5. Translation
→ Series glossary & term handling → Series) and its glossary, style
profile, *and* named characters all persist across every stream, not
just one.

Named characters are the piece that's genuinely new: a per-drama
`characters` row is keyed to that ONE drama's own diarization labels
(`SPEAKER_00`, `SPEAKER_01`, …), which aren't stable across separate
recordings — `SPEAKER_00` in one stream isn't necessarily the same person
as `SPEAKER_00` in the next. So "Su Shan" needs to exist as her own
series-level record, independent of any single drama's speaker labels.
Once she's added (in the same "Series glossary" section, or the moment
you type a new name in section 6 and check "Remember this as a known
character"), every later stream in that series shows her in a dropdown in
section 6 ("Name your characters") — pick her instead of retyping and
re-spelling her name each time. Renaming her once (same section) updates
every drama she's linked to; nothing needs a per-drama edit for a name
correction.

Deliberately NOT automatic: typing a name into section 6 does not, by
itself, add it to the series. That's an opt-in checkbox, not a background
behavior — auto-saving every typed name (including a mid-typo one) would
clutter a streamer's cast list with one-off junk. The dropdown-and-pick
step for an ALREADY-known character is the hands-off part; deciding a
NEW person is worth remembering permanently is the one moment that stays
a deliberate choice.

For building a glossary without a source novel (the normal case for a
streamer), see **Auto-extract terms** above — it works directly from the
drama's own transcript.

### Cross-referencing the raw and translated novel

One upload now does both jobs instead of needing the same file twice:
the raw source-language novel (Workspace -> Content source -> "Raw
novel") feeds transcription priming AND, in "Build a glossary from this
novel," pairs automatically with the existing English reference
translation to extract terms as matched pairs -- capturing how each was
actually rendered rather than inventing new wording. Uploading it in
either place saves it for both uses.

## Transcription & OCR

### Transcription accuracy

Whisper mishears proper nouns constantly in Chinese, and it does so
invisibly: a wrong guess is usually still a real word, so nothing looks
broken until the translation reads oddly. Four levers, in order of value:

1. **A real transcript.** Still the single biggest quality difference.
   Everything below is making the best of not having one.
2. **Prime it with names** (Workspace -> Recognition accuracy). Whisper
   conditions on an `initial_prompt`, so telling it which names to expect
   fixes much of the proper-noun problem. This is wired to your series
   glossary automatically -- build a glossary from the novel (or from the
   transcript itself) and it feeds straight back into transcription.
   Costs nothing.
3. **`large-v3` instead of `medium`.** Markedly better on names and
   homophones, free, ~3GB and slower -- but practical with GPU enabled.
4. **Wider beam search** (8-10). Considers more alternatives before
   committing. Costs time, not money.

None of these cost anything. GPU acceleration is what makes `large-v3`
usable rather than painful, and an 8GB card handles it comfortably.

### Raw novel context for transcription

Separate from the reference translation used for translation quality:
upload the ORIGINAL-language novel (Workspace -> Content source ->
"Raw novel") and it feeds speech recognition, not translation.

Whisper's `initial_prompt` has a real, hard limit -- only roughly the
last ~224 tokens actually influence decoding, so the whole novel isn't
handed over (that would silently waste most of it). Instead: your
glossary's proper nouns go in first since they're the highest-value
part, then a bounded excerpt of real prose for phrasing and rhythm,
trimmed to fit. Accepts .txt, .md, or .epub.

### OCR (image-based chapter scans)

Some platforms serve chapters as images specifically to block copy/
paste. If that's what you're working with, upload the page images in
the novel-narration section's OCR section instead of pasting text.

Default backend (Tesseract): `pip install pytesseract pillow`, plus
the Tesseract binary with the matching language pack:
- macOS: `brew install tesseract tesseract-lang`
- Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-jpn tesseract-ocr-kor`
- Windows: [installer](https://github.com/UB-Mannheim/tesseract/wiki) — select the languages you need during setup

**If OCR still fails with "tesseract is not installed or it's not in your
PATH" after installing it**: on Windows, the installer doesn't always add
itself to PATH. Rather than editing a system PATH variable by hand, set
**Settings -> OCR -> Tesseract binary path** to the full path of
`tesseract.exe` (typically `C:\Program Files\Tesseract-OCR\tesseract.exe`,
or wherever you chose during setup) — leave it blank once OCR works, it's
only needed for this one situation.

Higher-accuracy alternative for Chinese specifically: PaddleOCR
(`pip install paddleocr paddlepaddle`) — heavier install, downloads its
own models on first use, but noticeably better on stylized fonts or
lower-quality scans. `pip install paddleocr` today installs the 3.x
release line, which this app's OCR code already targets.

For Japanese manga specifically: `pip install manga-ocr` — purpose-
built for speech-bubble/vertical text layouts (the same model family
koharu uses), noticeably better than Tesseract on stylized manga fonts.
Works best on single speech-bubble crops rather than whole pages.

**Always review OCR output before translating** — misrecognized
characters are common, especially on compressed screenshots.

### Reading captions already burned into a video (hardsub OCR, experimental)

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
do still need `ffmpeg` (already required generally, see Installation
above).

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

### Simplified vs Traditional Chinese (Taiwan, Hong Kong sources)

For `source_language = zh`, a "Chinese script" toggle appears (Workspace →
2. Content source) with two options: Simplified (Mainland) and Traditional
(Taiwan, Hong Kong). What it actually affects:

- **Whisper transcription and translation (Claude/DeepSeek/Gemini/etc.):
  unaffected either way.** Whisper's `zh` language code doesn't distinguish
  dialects, and every LLM translation engine reads Simplified or
  Traditional input equally well -- Taiwanese Mandarin speech transcribes
  and translates the same as Mainland Mandarin.
- **OCR (page-scan novels, hardsub captions): this toggle picks Tesseract's
  `chi_sim` vs `chi_tra` language pack.** Running Simplified OCR against
  Traditional text (or vice versa) recognizes badly -- the two scripts
  only share some characters. Install the matching Tesseract language
  pack (`tesseract-ocr-chi-tra` on Ubuntu; the Windows/macOS installers'
  language picker already covers this).
- **Reader word segmentation**: jieba's dictionary is Simplified-only.
  Traditional mode converts to Simplified with OpenCC (`pip install
  opencc-python-reimplemented`) just to find word boundaries, then slices
  the *original* Traditional text using those boundaries -- so the
  segmented words you actually see are always in the source script, not
  silently converted.

Defaults to Simplified; only matters at all when source_language is `zh`.

### Speaker diarization (audio-drama mode)

Needs `pip install pyannote.audio soundfile`, a free Hugging Face token
(https://huggingface.co/settings/tokens), and accepting the model
terms at https://huggingface.co/pyannote/speaker-diarization-3.1.
First run downloads the model. CPU works, GPU is faster.

### If lines get merged, timing feels off, or some lines never got translated

Diagnosed directly from a real uploaded file, not guessed at.

**Merged lines / missing "thoughts"**: Whisper's voice-activity detector
defaults to merging any two stretches of speech separated by less than
2 seconds of silence into ONE segment -- keeping only the first
sentence as that line's text. For back-to-back dialogue or internal
monologue with short pauses, this routinely swallows several real
lines into one oversized block (one real case: a single "line" spanning
6.4 minutes). Workspace -> Recognition accuracy has a **speech-splitting
sensitivity** slider (default 300ms, range 300-3000ms) that already
starts a new line at almost any real pause, so this specific problem is
largely solved out of the box. If a title still shows it -- pauses
shorter than 300ms, or genuinely continuous speech with no pause at all
-- see the VAD-sensitivity and word-level re-split fixes below instead
of lowering this slider further (300ms is already its floor). Raise it
(1000ms+) only for the opposite problem: a drama with long natural
pauses where lines are splitting mid-thought because it's too
sensitive.

**Quiet dialogue missing, or noise/music producing phantom lines**:
Workspace -> Recognition accuracy also has a **speech detection
sensitivity** slider (Silero VAD's own threshold -- faster-whisper's
built-in VAD IS Silero, not a separate technology, this just exposes its
sensitivity knob). Lower it (0.3-0.4) if quiet/distant dialogue is being
cut as silence; raise it (0.6-0.7) if a noisy or music-heavy source is
producing lines from non-speech.

**A music bed under the dialogue is confusing Whisper**: the same
section has a **"Remove background music before transcribing"**
checkbox. It runs a real source-separation model (not a generic noise
filter) over the whole file first and transcribes only its vocals: a
Mel-Band RoFormer vocal model via
[audio-separator](https://github.com/nomadkaraoke/python-audio-separator)
(`pip install audio-separator`, preferred -- cleaner vocals), falling back
to [Demucs](https://github.com/facebookresearch/demucs) (`pip install
demucs`). Adds a full extra pass over the audio (roughly as long as
transcription itself) -- skip it for already-clean dialogue, since
there's nothing for it to separate out.

**A merged line is still one oversized block after all the above**:
Workspace -> Recognition accuracy has a **"Split long merged lines
using word-level alignment"** checkbox, marked experimental. Instead of
trusting Whisper's own segment cuts (which only know where a whole
merged span starts and ends, not where the real pauses are inside it),
it re-aligns that line's own text against its own audio via Meta's
[MMS](https://github.com/facebookresearch/fairseq/tree/main/examples/mms)
forced-alignment model to find its actual internal pauses, then splits
it back into multiple correctly-timed lines at those pauses. This can
only re-time text Whisper already transcribed -- it can't recover
content Whisper genuinely missed. Off by default and not verified
against real speech in development (a comparable aligner has a known,
documented failure on some Japanese text) -- a missing dependency or an
alignment problem on a specific line never costs the transcript itself,
it just leaves that line's original timing in place. Needs
`pip install torchaudio uroman` (first use downloads a ~1.1GB model).

**Finding where this happened**: Workspace -> Review & edit ->
**Check line coverage**, run after aligning, before translating. Flags
suspiciously long lines relative to their text length, large silent
gaps between lines, blank source text, and untranslated lines with
source text present -- so you can jump straight to problem spots
instead of scrubbing the whole file by ear.

**Transcribed but never translated, with no error shown**: this was a
real gap in the background-translation system, fixed by persisting
failures to the database and showing them as a standing banner in
Workspace until dismissed or the drama is re-translated cleanly. Click
Translate again; already-translated lines are skipped automatically, so
this doesn't re-cost anything already done.

### Live (experimental)

For translating a stream as it happens rather than after the fact: the
🔴 The Live page pulls a running stream, cuts it into short chunks (10-60s,
your choice) as they arrive, transcribes and translates each chunk in
the background, and shows a growing feed of original + translated lines
you refresh manually.

This is a genuinely different, looser tradeoff than the rest of the
app, not just a faster version of it:
- **Latency is at least one chunk's length.** A 20s chunk means a line
  said at t=0 doesn't appear until roughly t=20-40 (transcribe +
  translate both have to finish first). Shorter chunks lower the delay
  but give Whisper less context per cut, so a sentence split across a
  chunk boundary can transcribe worse than the same audio in one piece.
  There's no setting that removes this tradeoff, only where you sit on it.
- **Lower accuracy than the normal pipeline on purpose.** Each chunk is
  transcribed on its own, with only a few seconds of the previous
  chunk's audio re-heard at its start ("Chunk overlap", default 3s) and
  the previous chunk's text as a hint -- none of the glossary/proper-
  noun priming the rest of the app uses for names. The re-heard part is
  removed by an exact text match before it's shown; if the two
  transcriptions of it disagree, a line straddling the boundary can
  still repeat a word or two.
- **Doesn't save into your Library yet.** The feed is ephemeral for
  this first version; copy anything worth keeping before you stop it.
- **A resolved stream URL can expire** after a few hours on some
  platforms -- if new lines stop appearing on a long-running stream,
  stop and start again to re-resolve a fresh one.
- Needs `yt-dlp` (already required for URL downloads elsewhere) and
  `ffmpeg` (already a hard requirement of this project) -- no new
  dependency.
- Built and verified against a real, continuous ffmpeg capture-and-
  segment pipeline; **not tested against an actual live YouTube/Twitch
  broadcast**, since that needs a real stream running at test time.
  Sanity-check the first couple of lines after starting before relying
  on it for a whole stream.

### Novel-narration line timing

Since there's no source audio to align to, line timings are set to the
actual duration of each generated TTS clip once you run narration
generation — download the `.srt` *after* generating the track, not
before, so timings match.

**EPUB import/export**: in novel-narration mode's content source, you
can import chapters directly from an `.epub` you own instead of pasting
text or using OCR (needs `ebooklib`/`beautifulsoup4`). Export subtitles
offers the reverse for a finished novel-narration drama: download the
translation as a proper `.epub` for any e-reader, alongside the
audiobook export below.

## Dubbing & voice cloning

**Default (free, online)**: edge-tts, picks from a fixed voice list,
one voice per character.

**Offline (free, no internet)**: Piper (`pip install piper-tts`) —
select "Offline / Piper" as the fallback engine before generating a
dub/narration track. Lower voice quality than edge-tts, but works with
zero API calls and no network once the voice models are downloaded —
useful for fully offline batch runs or avoiding any cloud dependency.

**Fitting a dubbed line to its original timing**: when a dubbed clip
is longer than the original line's time slot, it's sped up (pitch kept,
via ffmpeg) by at most 1.4×; a shorter one is slowed toward its slot by
at most 0.85×. Past the speed-up limit the line is left to run over
rather than sound chipmunked — shorten it first with the pacing check in
Review & edit. Both limits are adjustable in section 8 (and via
`--max-speedup`/`--max-slowdown` on `cli.py dub`). After generating,
section 8 shows each line as 🟢 fits, 🟡 sped up (hover for the factor),
or 🔴 still runs over. Editing a line's text (or changing its voice)
and generating again re-voices just that line; unchanged lines reuse
their existing clips. Novel narration has no timing to fit, so none of
this applies there.

**Voice cloning (F5-TTS)**: `pip install f5-tts`. For each character,
provide:
- A reference audio clip (a clean few seconds of just that voice)
- The exact text spoken in that clip (needed to anchor the clone)

**More local voice engines, picked per character** (Workspace section 6,
"Voice engine"):
- **OmniVoice** (`pip install omnivoice`, Apache-2.0): clones from a
  3-10 second clip. It also does **voice design**: type a description
  like `female, low pitch, british accent` under "Or describe a voice"
  and a character with no clip still gets its own voice.
- **GPT-SoVITS** (MIT): clones from a 3-10 second clip. It isn't a pip
  package. Download it from its GitHub page, run `python api_v2.py` in
  its folder, and set the server URL in Settings if it isn't
  `http://127.0.0.1:9880`.
- **Chatterbox** (`pip install chatterbox-tts`, MIT): voices each line
  with the emotion detected for it, so angry lines sound different from
  calm ones. Run emotion detection first. Works with or without a clip.
  Its audio carries Resemble AI's imperceptible PerTh watermark.
- **TADA** (`pip install hume-tada`): built to stay on-script over long,
  unattended runs. Its code is MIT, but the model weights are under
  Meta's Llama 3.2 Community License. Accept that license on Hugging
  Face and run `huggingface-cli login` before first use.

⚠️ OmniVoice, Chatterbox and TADA pin conflicting `transformers`/`torch`
versions, so pip can't install any two of them into the same
environment. Install the one you want, or give each its own venv.

Characters set up before these existed keep using F5-TTS.

**Narration generation**: several consecutive lines from the same
speaker (within one paragraph) are voiced in one TTS call, which gives
more natural cross-sentence delivery. Each line is still its own
subtitle cue. With edge-tts, clips are generated a few at a time in
parallel. Local engines always run one clip at a time.

**Audiobook export**: after generating a novel narration, **🎧 Generate
audiobook (.m4b)** in Export subtitles builds an M4B with chapter
markers. Chapters come from the novel's own headings (第一章 / Chapter 1),
or one per paragraph when it has none. From the CLI, use
`python cli.py dub --id N --m4b`.

In the Workspace, use **"Auto-extract reference clips"** after
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

## Reader & Library

### Interactive Reader

A dedicated page for reading raw + translated text side-by-side, built
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
  Reader, batched once per page rather than per word.
- **Paginated**: large episodes/chapters are split into pages so the
  page doesn't need to segment/define thousands of words at once.

Install what you need: `pip install jieba pypinyin` for Chinese,
`pip install sudachipy sudachidict_core pykakasi` for Japanese,
`pip install kiwipiepy` for Korean.

**Follow-along playback**: with click-to-seek enabled, the line
currently being spoken is highlighted and scrolled into view as the
audio plays, with a toggle to stop auto-scrolling. This only works with
the reader's own embedded clip (a separate
`<audio>` element's playback position can't be observed from Python), and the embedded
clip covers only the current page, so its `t=0` is that page's first
line — absolute line timestamps have that offset added back before
matching.

**Progress tracking**: reading progress saves when you change pages,
not continuously, so the percentage reflects the furthest page you've
opened rather than your position within it. Playback position is
tracked separately from reading position, so listening and reading
don't overwrite each other.

### Reading experience

Configurable in ⚙️ Settings: text size, line spacing, content width,
font (system/serif/sans/mono), and three themes (light, sepia, dark).

### Interface

**Theme** is a menu in the app header (including a Sepia theme) and
applies to the whole app. The Reader keeps its own separate theme
(light/sepia/dark) for the reading surface, since reading preferences
and UI preferences aren't always the same. Tables (glossary, bulk
import review, library filtering) may still show light cell backgrounds
in dark mode — a data-grid rendering constraint of the old UI;
see `docs/technical-notes.md` for why.

A restrained design system:
one accent colour carrying emphasis (when five things are highlighted,
nothing is), a fixed spacing scale for vertical rhythm, status shown as
colour-coded pills rather than prose, deliberate empty states that say
what to do next, and stage indicators for multi-step workflows so a
ten-step process shows the step you're on instead of all ten at once.

### Story memory & spoiler-free mode

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

### Line tools

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
- **Re-transcribe this line** — re-runs Whisper on just that line's own
  audio window, for a single mistranscribed line without redoing the
  whole file

**Ask about this drama**: a chat box, grounded only in the lines
you've loaded so far, for open-ended questions about the story — needs
an API key entered on this page, same as the tools above.

**Vocabulary export**: see the Features section above (Anki-importable
CSV or a proper `.apkg` deck) — available from this page for whatever
words you've looked up or queued while reading.

### Library experience

The app is meant to feel like a proper library, not a folder of files.

**Progress & resume**
- Reading percentage and last page tracked per drama, saved automatically as you page through the Reader
- Audio position stored separately from reading position, so listening and reading don't overwrite each other
- **Continue** shelf on the Library page: cover art, progress bar, one-click resume to where you left off. Resume opens the Reader or Workspace at your saved place
- Reading history log, clearable

**Metadata**
- Cover art, genre, publication status (ongoing/completed/hiatus), chapter count
- Custom user-defined tags, filterable in the Library
- Private personal notes per drama, editable from Workspace or Reader
- Reading/listening time estimates (word count for text, real duration for timed audio)

**Dashboard**: totals across the whole library — translated-line count, API call count, estimated spend, prompt-cache-hit rate, status/media-type breakdowns, and a per-drama cost table.

**Series**: dramas that share a glossary or character list (2+ per series) get a consolidated view with a one-click jump into Workspace.

**Search & bulk actions**: a global search box across every drama's original and translated lines (not just titles), plus a filterable table with a checkbox column for bulk status changes, bulk delete (behind a confirm checkbox), and bulk translate — which skips any drama missing an API key, lines, or already running, and reports the skip count.

**Export all as .zip**: bundles subtitle files (and dub tracks, where generated) for every translated/dubbed/exported drama in one download.

**Backup & restore**: a database-only snapshot, or a full zip of the database plus media (streamed to disk rather than held in memory, so it scales to a large library). Restoring validates the zip before touching anything and needs a confirm checkbox. Signed-in browser sessions from the Sources page aren't included — you'll need to sign back in after a restore.

**Presets**: saved Workspace configurations (engine, style, locale, pronoun default) can be renamed or deleted from the Library page.

**Translation versions**
- Every translation run is saved as a named version tagged with its engine and model
- Re-translate with a different model without losing the previous attempt
- Side-by-side diff view showing only the lines that actually differ
- Activate whichever version reads better; switching snapshots the current one first

**Story tools** (Reader, grounded strictly in the drama's own lines)
- *Who is this character?* — role, relationships, and speech notes, drawn from lines that actually mention them
- *Relationship map* — structured cast + relationships, rendered as a Mermaid diagram
- *Recap* — "previously..." summary of what you've read, spoiler-safe (never references past your current page)
- *Explain an idiom or reference* — on-demand explanation of a 成语, allusion, or cultural reference

**Storage management**
- Scan showing library size, what's reclaimable, a per-category breakdown, and the largest dramas
- Quality presets: *Archival* (keep everything), *Balanced* (drop intermediates), *Minimal* (sources and text only)
- Cleanup only ever removes regenerable artifacts — source audio/video, reference novels, voice-clone samples, and the database are never touched

**Undo / version history**: snapshots of a drama's lines are taken
automatically before the two operations that discard work irreversibly
-- a **force re-translate** and an **applied merge**. Restore any
snapshot from the "Version history / undo" section in the Workspace
tab; restoring takes its own snapshot first, so you can undo an undo.
Only the 10 most recent snapshots per drama are kept.

**Per-drama export package**: separate from the whole-library backup —
bundles one title's metadata, all subtitle formats, original
audio/video, dub/narration track, and reference novel into a single
zip, for archiving a finished drama or handing it off without exporting
everything you own. Shows a manifest of what was actually included, so
a missing piece (e.g. no dub generated yet) is visible rather than
silently absent.

### Finding titles

The Discover page's title search looks at titles **you've saved locally** --
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

### Discover: known titles library

A searchable catalog of known titles -- separate from your working
drama catalog (`dramas`), so you can browse/discover before committing
to actually working on something.

**Getting started**: the library starts empty; click "Load starter
titles" for a handful of real, publicly-documented baihe audio dramas
to bootstrap it.

**Search**: works in any language against whatever's in your local
library. Searching baihehub.com specifically translates your query to
Chinese first (since it's a Chinese-language database), then attempts
an automatic search. Its search page is a client-rendered app, so
`search_baihehub()` is a best-effort guess and may come back empty --
when that happens, you get a fallback link to baihehub's human search
page instead.

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

**Bulk import from a tag/ranking listing page**: paste one or more
listing-page URLs (e.g. JJWXC's Baihe tag listing, Fanjiao's ranking
page) to extract many entries at once -- title, author, tags, and
whether an audio drama adaptation exists, never the actual content.
Supports paginated listings (one URL per page). If a site needs JS
rendering to show its listing and Playwright isn't installed, a
"paste the page text yourself" fallback appears instead of failing
outright. Always shows a review/edit table before committing anything
to your library.

### Known-site registry

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

Browsable in Discover's site navigation helper section (filterable by
language/content type) and selectable as a starting point in metadata
lookup. This is just a directory — you still need your own account/
access on whichever platform you use, same as everywhere else in this
app.

### Manhua & webtoon handling

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

### Scanlate (manga/comic typesetting)

Hybrid workflow: auto-detect speech bubbles → auto-clean the original
text → auto-translate and place text → review/adjust each bubble
before final render.

1. Upload page image(s) or a PDF for a drama (any drama, doesn't need
   audio) — a checkbox can slice tall webtoon strips into pages first.
2. Pick a **bubble detection** backend and an **OCR** backend, or leave
   both on Auto. Click **Detect bubbles + auto-clean + auto-translate**
   (or expand **Batch** to run the same pipeline over a whole chapter,
   carrying translation context forward). This uses:
   - **Detection** — `detect_bubbles_cv()`, a free, local OpenCV
     heuristic (looks for large light-colored regions with a clear
     border; works well on clean scans with typical white bubbles,
     misses irregular or borderless ones), or `detect_bubbles_ml()`, a
     trained detector (`ogkalu/comic-text-and-bubble-detector` on
     Hugging Face; needs `transformers`+`huggingface_hub`, downloads
     the model on first use) for meaningfully better accuracy.
   - **OCR** — five backends: manga_ocr and two PaddleOCR variants
     (best for Japanese), Tesseract, or Auto (picks by language).
   - Translation via whichever engine you've selected.
3. Review the bubble table: adjust x/y/w/h, font size, region type
   (bubble/SFX/sign/etc.), source/translated text, or check "skip" for
   false positives or bubbles you don't want auto-translated (SFX is
   excluded by default). Add missed bubbles manually — drawing a box
   OCRs it on demand.
4. Click **Render typeset page** to inpaint (erase original text) and
   draw the translated text into each bubble, then download the
   result — or use **Bulk render** to render a whole chapter to a
   ZIP/PDF at once.

**Also available:**
- **Custom fonts** — upload `.ttf`/`.otf` files per style category for
  rendering.
- **Bulk find & replace** — across every saved bubble in a drama, with
  a preview before applying.
- **Export detected font styles** — as JSON, for reuse.

**Known limitations:**
- The default CV detector works best on clean scans with solid white
  bubbles and clear borders. Irregular bubble shapes, sound effects, or
  borderless text will need manual boxes or the ML detector.
- Inpainting uses OpenCV's built-in algorithm (no ML model) — works
  well on plain backgrounds, less well on bubbles with patterns/gradients.
- Text rendering is horizontal only — no vertical CJK layout (that's
  what koharu specializes in, not replicated here).
- Auto-OCR crops slightly inside a detected bubble's own border before
  reading it (a round or thick-bordered bubble can otherwise make OCR
  return nothing at all) — always double check the OCR'd source text
  in the review table before translating.

## Metadata & site tools

### Translate the page you're reading (browser extension)

A Chrome/Edge extension that sends the comic page you're looking at into
Baihe and draws the translation over it in place, with a toggle to hide
the overlays and click-to-see-the-original.

It complements the Sources page rather than replacing it: the adapters do
bulk import and chapter tracking, this is "translate what I'm looking at
right now." It also reaches pages an adapter structurally can't — ones
delivered as `blob:` objects that only exist inside the tab, ones a site's
own reader unscrambles, ones behind a signed-in session — because your own
browser has already done that work, so nothing has to be circumvented.
And it works on sites with no adapter at all, which is most of them.

Turn it on in **Settings -> Browser extension**, then load the
`extension/` folder unpacked (`chrome://extensions` -> Developer mode ->
Load unpacked) and paste the token Settings shows you. It talks only to
`127.0.0.1`, and every request needs that token.

Full setup, limits and security notes: [`docs/browser-extension.md`](docs/browser-extension.md).

### Metadata romanization

Credits are stored in the original script and gain a romanized companion
rather than being overwritten -- 一半山川 stays and displays as
"Yiban Shanchuan (一半山川)".

Personal names are romanized rather than translated, because a name is a
name and not a phrase to render. Studios and platforms use their
established English name where one exists (晋江文学城 is normally written
"JJWXC", not "Jinjiang Literature City") and are romanized otherwise.

### About database IDs

Drama IDs skip numbers after a deletion (1, 2, 4...) and this is
deliberate. Lines, characters, glossaries, progress and translation
versions all reference a drama by ID, so reusing a deleted one would let
leftover rows re-attach to the wrong work -- and any backup taken before
the deletion would restore into a conflicting record.

The Library pagele shows a tidy sequential **#** column for display
alongside the real `id`, which gives orderly numbering without putting
your data at risk.

### Fetching from JS-heavy sites

Several sites in this space — baihehub and Fanjiao included — build
their pages with JavaScript, so a plain HTTP fetch returns an empty
shell (navigation, filters, "0 items") with none of the actual listing
in the HTML. `page_fetch.py` handles this in three layers:

1. **Detection.** After any fetch, the response is checked for the
   signature of an unrendered shell. If it looks like one, you're told
   so explicitly, with the reasons — instead of a generic "couldn't
   extract metadata" that looks identical to a page with genuinely no
   metadata.
2. **Rendering.** With `playwright` installed (`pip install playwright`
   then `playwright install chromium` — the second command is easy to
   miss), the page is re-fetched with a real browser engine so the
   JavaScript actually runs.
   If Playwright's own browser is missing (for example after a Playwright
   upgrade), an installed Google Chrome or Microsoft Edge is used instead.
   To pick a specific browser, set the environment variable
   `BAIHE_BROWSER_PATH` to its program file (a system variable, not
   `.env`). Diagnostics > Setup shows whether one was found.
3. **Manual paste.** Always available, always works: open the page in
   your browser, select all, copy, paste into the app. No dependency,
   no rendering, no guessing.

The same three layers back metadata lookup, bulk import, and title
import.

### Embedding sites in the app

There's an embed panel in Discover, but be realistic about it: most
substantial sites (JJWXC, MissEvan, Lezhin, BookWalker, Naver, Kakao,
and others) send headers that forbid being placed in an iframe, as
clickjacking protection. For those, the panel renders blank — the site
refusing, not a bug. Known blockers are flagged before you try.

In practice Discover's site navigation helper section is the better
tool for this: it translates a page's menu labels and gives you
step-by-step navigation guidance, which you follow in a normal browser
tab.

## Reliability & performance

### Failure isolation

A design goal: one thing breaking should break only that thing.

**Page isolation.** Each React page renders inside an error boundary,
so one page failing shows its error there while the rest of the app
keeps working.

**Database connections.** Connections are tracked and reclaimed even
when a statement raises between opening and closing one, so a single
failed call can't leave the database locked for the rest of the session.

**Destructive writes.** `save_lines` (which updates lines in place by
id and deletes only lines missing from the list), `save_bubbles` and
`save_line_history_snapshot` (which delete existing rows before
re-inserting) run inside explicit transactions with rollback on error,
so a failure partway through can't destroy translation work that cost
real money.

**Optional dependencies.** Heavy optional packages (Whisper, OpenCV,
TTS engines, OCR backends) are imported lazily inside the functions
that use them, never at module load, so a missing one disables its own
feature rather than preventing the app from starting.

**Per-item isolation.** Batch operations already isolate individual
failures: one drama failing in a CLI batch, one line failing to
synthesise during dubbing, one page failing to render in a bulk
scanlate job.

### Reliability

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
  when you deliberately want a full redo. Confirmed end to end: a run
  that dies partway through leaves every completed batch saved, and
  restarting only re-sends whatever didn't finish.
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
- **Settings page**: enter each API key/endpoint once, reused as the
  default everywhere else. Keys are never shown back by the API.
- **Backup & restore** (Library page): zips the whole library --
  database plus every drama's audio/video/dub files and reference
  clips -- for download, with a matching restore flow. Worth doing
  before any big batch run. The zip is streamed to disk, so it scales
  to a large library.

### Performance & dashboard

- **Paginated review table** (Workspace): long dramas render one page
  of lines at a time instead of every line's widgets at once -- edits
  on one page never touch other pages (splice-tested).
- **SQLite WAL mode**: smoother concurrent reads/writes at library scale.
- **Library dashboard**: total dramas, lines translated, API calls
  logged, estimated spend, status/type breakdowns, and a "recently
  active" list, all at the top of the Library page.
- **Bulk actions** (Library): select multiple dramas via checkboxes,
  set status or delete them together instead of one at a time.
- **Global search**: search text across every drama's lines from the
  Library page, not just within one title.
- **Cost tracking**: real token usage captured from Claude/DeepSeek API
  responses, logged per drama, with a cost breakdown table in the
  dashboard. Estimates only -- pricing changes over time.
- **Default settings**: save your usual translation engine, English
  locale, and style notes in Settings so new dramas start
  pre-filled instead of resetting every time.
- **GPU acceleration**: optional, for Whisper transcription,
  diarization, and local voice cloning. Falls back to CPU automatically
  if CUDA isn't actually available, so enabling it on a machine without
  a GPU degrades rather than breaks.

### About the reference novel & cost

Each drama's novel reference (for Claude/DeepSeek) is sent as a
prompt-cached block, scoped to that drama only — first batch pays full
price to load it, later batches (within ~5 min activity) read the
cache at a fraction of cost. A long break just re-warms the cache on
your next batch.

**Context limits:** a very long novel (200k+ Chinese characters) may
not fit alongside your dialogue batches. Trim to the relevant arc, or
compress into a glossary of names/relationships/key phrases instead.

### Scaling to 50–100+ dramas

- SQLite + per-drama folders handle this volume fine locally.
- Use Library page filters to track progress (e.g. `status = not started`).
- Use `cli.py` for unattended batch runs.
- For cost/speed at real volume, consider the Anthropic **Batch API**
  (roughly half the per-token cost, async) — ask if you want the app
  extended to support that mode.

## Troubleshooting

### If Hugging Face is unreachable

Whisper downloads its model from Hugging Face on first use. If that
fails with `getaddrinfo failed` or `LocalEntryNotFoundError`, it's a
network problem, not an audio one. In order of likelihood on Windows:

1. **Antivirus or firewall** blocking Python's network access -- allow
   `python.exe` explicitly.
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
   python -m api
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

### GPU transcription failures ("cublas64_12.dll is not found")

If you keep seeing this warning (the app already retries on CPU
automatically when it happens), the underlying cause is usually one
of: PyTorch/ctranslate2 installed without CUDA support (a CPU-only
wheel), or a CUDA toolkit version that doesn't match your driver.
Turning GPU off in Settings -> Performance avoids the warning entirely
if you'd rather not chase it down. See `docs/technical-notes.md` for
what was actually wrong in the fallback code that made this surface as
a crash instead of a quiet retry.

### GPU PyTorch (NVIDIA)

`pip install torch` from PyPI gives a CPU-only build on Windows, and
installing torch, torchvision or torchaudio one at a time can leave them
built for different torch versions ("torchvision 0.29.0 requires
torch==2.14.0, but you have torch 2.11.0+cu128"). In the React app,
**Diagnostics -> Packages -> GPU PyTorch** shows your GPU, driver and the
installed torch/torchvision/torchaudio, and **Set up GPU PyTorch** (on
the PC only) installs the matched set the app is tested with: torch
2.11.0, torchvision 0.26.0 and torchaudio 2.11.0 from
`https://download.pytorch.org/whl/cu128` (NVIDIA driver 570.65 or newer
on Windows, 570.26 on Linux), then imports it in a fresh Python to check
that CUDA works. The by-hand equivalent, from the venv:

```
python -m pip install --no-cache-dir --force-reinstall --no-deps torch==2.11.0+cu128 torchvision==0.26.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128 -c constraints.txt
python -m pip install --no-cache-dir torch==2.11.0+cu128 torchvision==0.26.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128 -c constraints.txt
```

After that, every Install/Update in Diagnostics pins the installed
torch family, so a voice or ASR package that wants a different torch is
refused with a plain message instead of replacing your CUDA build. The
versions live in `diagnostics.TORCH_VARIANTS`, with their sources.

### If your exported subtitles are blank

A `.srt` with real, correct timestamps but no text is not a rendering
bug -- `lines_to_srt` faithfully writes whatever's in each line's field,
and an untranslated line simply has nothing there yet. This is what it
looks like when English subtitles are exported for a drama that's been
aligned but not translated: 391 timed entries, every one empty.

Check the Library dashboard's "Lines translated X / Y" metric, or the
Workspace's line count next to your drama. If translation hasn't run,
press **Translate all lines** first (Ollama and NLLB are free if you
would rather not spend anything).

The export buttons now warn before this happens rather than after:
zero translated lines disables the English/bilingual downloads outright,
a partial translation shows how many lines will export blank, and the
same check runs before the CLI burns subtitles into a video.

## Testing & diagnostics

### Testing

The project has a substantial test suite covering the pure-logic
pieces -- line merging, timing alignment, retry/backoff behavior,
database CRUD, cost estimation, image processing, export packaging,
and undo history -- plus real (non-mocked) tests against actual OCR
backends where a system dependency (Tesseract) is available. Tests use
an isolated temp database, so running them never touches your real
library.

```bash
pip install -r requirements-core.txt pytest -c constraints.txt   # minimum plus the test runner; add requirements-media.txt / requirements-optional.txt for those features
python run_tests.py     # run everything (a wrapper around pytest, config in pytest.ini)
python run_tests.py -k history   # run a subset
```

Tests are fully mocked: no GPU, models, API keys or network
needed.

Frontend checks, from `frontend/` after `npm ci`: `npm run lint`,
`npm test` (vitest), `npm run build` (typecheck + bundle) and
`npm run e2e` (Playwright against a seeded throwaway library; needs a
browser from `npx playwright install chromium`, or set
`PLAYWRIGHT_CHROMIUM_PATH` to an existing Chromium executable, and
Python with `requirements-core.txt` installed). The e2e run uses ports
8611 and 4174, so it can run beside a normal dev stack.

Worth running after any change you make to the code, and useful for
confirming a fresh install is working before you start real work.

### Diagnostics ("Check my setup")

A Diagnostics page that reports, in one place:
- Python version and whether ffmpeg is on PATH
- Which optional dependencies are actually installed, grouped by what
  they power (core / translation engines / optional features), with an
  inline install button for anything missing
- Whether every expected project file is present -- the usual cause of
  a cryptic `ModuleNotFoundError` after a partial download
- Which API keys are currently configured
- Whether the library directory is writable

None of the above appears until you click "🔍 Run diagnostics" -- the
tab is empty on load.

The same page also holds:
- **Running jobs** -- a live view of every background job across the
  whole app (translate, transcribe, dub, diarize, Live capture, etc.)
  with progress bars and a per-job cancel button.
- **Downloaded model cache** -- lists Hugging Face cache entries
  (Whisper, pyannote, TTS models) with size and a delete button per
  revision.
- **Model & engine versions** -- installed versions per engine/model,
  plus a one-click GPU PyTorch install if it detects an NVIDIA GPU with
  only CPU PyTorch installed.
- **pyannote gated model access** -- an on-demand check (only makes a
  network call when you click it) that your Hugging Face token can
  actually access the diarization model, separate from just having a
  token set.
- **App Assistant** -- a chat box (needs its own API key) that answers
  "where is X" questions about the app itself, with a button to copy a
  bundled Q&A + diagnostics report for a developer.
- **Copy diagnostics for support** -- a redacted, one-click copyable
  report (strips API keys, paths, and username).
- **Log** -- the last 50 lines of the app's own log file.

Run this first whenever something isn't working. The same page holds
the "Reset everything" danger-zone button described in
[Resetting for testing](#usage).

### Accuracy benchmark

Also in the Diagnostics page, above the danger zone: catches a
pipeline "improvement" that actually makes things worse. `run_tests.py`
proves the code does what it's supposed to against mocked libraries --
it can't tell you whether a VAD-threshold change, a new translation
engine, or an OCR backend swap actually helps on YOUR real content,
since no real audio/image/text ships with the project.

Register a case once per content type you actually use -- a short audio
clip (speech recognition), a novel excerpt (translation), a manhua/
scanned page (OCR) -- optionally pasting in the text you already KNOW is
correct. Then, any time you change a setting and want to know if it
actually helped, hit "Run all cases" and compare: a case with a
reference gets a 0.0-100% similarity score (character-level, via the
same difflib approach `align_transcript_to_timing()` already uses
elsewhere in this app -- a proxy for accuracy, not a proper WER/BLEU
score, so treat it as directional rather than exact); a case with no
reference still gets timed and checked for errors as a smoke test. A
score that drops more than 5 points since the previous run on the same
case is flagged as a regression right there in the case's own row, not
buried in a separate report you have to remember to check.

Cases and their run history live in the same database as everything
else in this app, so they persist across sessions and are easy to keep
around long-term as your reference set.

## Code organization

`services/`, `api/` and `frontend/`
hold the API/React layers described under
[Project status and architecture](#project-status-and-architecture);
`FILE_ORGANIZATION.md` has the full file map. If you're extending this yourself, that's
where to look. `docs/technical-notes.md` has a detailed log of bugs
found and fixed during development, for anyone debugging or extending
the codebase further.

## Notes & tips

- First run will download the Whisper model (a few hundred MB to ~3GB
  depending on size chosen) — this only happens once.
- No GPU required, but a GPU will make transcription much faster (see
  Settings -> Performance).
- The alignment is an approximation — it interpolates timing for any
  lines Whisper didn't clearly catch. Always skim the review table
  before exporting, especially around scene transitions or overlapping
  dialogue.
- Your API key and files never leave your machine except for the
  direct call to your chosen translation/TTS provider's API.
