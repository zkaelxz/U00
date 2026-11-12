# Baihe Studio

A local app for transcribing, translating, reviewing and exporting English subtitles for Chinese, Japanese and Korean audio dramas, video dramas, novels, comics and streamer VODs, with a library to keep dozens of titles organised.

It runs on your own Windows PC: a Python (FastAPI) server with a React interface in your browser at `http://127.0.0.1:8600/`. Transcription runs locally with Whisper; translation uses the engine you choose, paid (Claude, Gemini, DeepSeek, OpenAI) or free and local (Ollama).

**Use it only on content you have the right to use:** files you own or were given, text you have licensed, or public pages and URLs you may download from. It does not unlock paid apps or streaming platforms.

New here? Read [Start here](docs/user-guide.md#start-here). To see how the code fits together, read [`docs/architecture-overview.md`](docs/architecture-overview.md).

## Features

Every feature below is in the code today. The [User guide](docs/user-guide.md) has the details of each.

### Library and organising
- **Library**: titles with cover art, a Continue shelf, filters, lists, a dashboard and bulk actions.
- **Series**: titles in one series share a glossary, style, characters and translation memory.
- **Translation profiles**: saved settings you pick when you start a title.
- **Discover**: find a title by name, add one from a listing page or by hand, or bulk-import tag and ranking pages.
- **Metadata lookup**: credits keep the original script with a romanized companion.
- **Backups**: database-only or full zip, an opt-in automatic backup, and a restorable Trash.
- **Storage view**: see what is taking up space, per title and for clips and the Trash.
- **Test copies and Keep original files**: try changes on a copy; keep your uploads as they were.
- **Watch folder**: drop audio or video into one folder and it is picked up for you.
- **Translation versions**: every run is saved with its engine and model, so you can compare versions and switch.
- **Merge titles and move chapters**: between comic or novel titles.

### Subtitles from audio and video
- **Make subtitles**: drop in an audio or video file and get an SRT in one screen.
- **Quick video translate**: paste a video link and one button downloads, transcribes, translates and exports it, with a "Watch once" option that deletes it afterwards.
- **Video links**: download with yt-dlp (queued, a part of a video only, long videos on request) or upload `.mp4`, `.mov`, `.mkv` or `.webm`.
- **Transcription**: Whisper (with optional Qwen3-ASR and Breeze-ASR-25 for Chinese), name priming, vocal separation, difficult-audio settings and coverage checks.
- **Speaker detection**: Nemotron-3 (default) or pyannote.
- **Hard-sub OCR**: read captions burned into a video (experimental).
- **OCR for page scans**: RapidOCR, PaddleOCR, manga-ocr or Tesseract.
- **Import existing subtitles**: bring in an SRT or similar file to translate or fix.
- **Timing tools**: tidy cue timing, reading-speed check, re-time, shot-change snapping and re-transcribing a single line.
- **Raw transcripts and Chinese script**: Simplified or Traditional handling.

### Translation
- **Engines**: Claude, Gemini, DeepSeek and OpenAI (paid), or Ollama (local and free, with hosted cloud models as an option).
- **Engine routing**: choose which engine does what, with per-person paid-engine limits.
- **Glossary**: per-term policies, exact find-and-replace, CSV, TSV and JSON import and export, shared glossary groups, and aliases that fix the transcript.
- **Auto-extract terms**: propose names and terms from a novel or a drama's own text, including a scan of one chapter or the whole novel.
- **Language packs**: built-in honorifics, address terms and VTuber lists for Chinese, Japanese and Korean.
- **Style presets, genre guidance and adaptive style**: register and pacing per title, learned from your edits.
- **Emotion-aware translation**: charged lines carry an emotional register into the prompt.
- **Context and checks**: lines before and after, a reply check, a glossary check, dialogue check, an optional source-match check and Retry for failed lines.
- **Translation memory**: approved lines reappear as suggestions in later episodes; TMX import and export.
- **Spending control**: cost per title, a monthly cap, past costs and a spend history.

### Review and fixing
- **Line editor**: flags, checks, history and undo, keyboard-first on desktop and a thumb bar on phones.
- **Review queue**: Riskiest first, Least confident, Next line to check, Mark checked and Mark episode reviewed.
- **AI checks**: flag lines for a second look, fix flagged lines, alternatives and a grammar breakdown for study.
- **Compare transcription**: hear lines again and compare recipes on your own audio.
- **Restructure**: add, delete, merge, split or re-segment lines, and restore an earlier version of the lines.
- **Episode summaries**: a running summary that feeds later translation.
- **Streamer VODs**: assign streams to a series to share glossary, style and characters.

### Novels and the Reader
- **Novels**: paste or upload `.txt` or `.epub`, with chapters detected (GBK, Big5 and Shift-JIS read correctly), and rename, move or re-translate a chapter.
- **Cast and speakers**: the glossary scan proposes a cast with pronouns, and the AI tags who speaks each line.
- **Reader**: raw and translated text side by side with pinyin, furigana, word segmentation and click-to-define.
- **Chapter list**: search and see each chapter's translated state.
- **Story tools**: character lookups, relationship maps, spoiler-safe recaps, a universe wiki and "Ask about the story", with a spoiler-free mode.
- **Vocabulary export**: words you looked up go to Anki as CSV or `.apkg`.
- **Follow along**: a phone screen that shows the lines on their clock while you listen elsewhere.
- **Web novel import**: via the optional lightnovel-crawler integration or a Sources adapter.

### Comics and manhua (Scanlate)
- **Scanlate**: detect bubbles, read the text, clean the art, translate and typeset each page.
- **Detector and cleanup**: an ML text and bubble detector with an OpenCV fallback, and LaMa-manga cleanup.
- **Comic editor**: move, resize, restyle and change the English of any text box, with undo.
- **Art text and sound effects**: optionally repaint large lettering drawn on the artwork.
- **Series glossary and characters**: each character's pronouns and voice notes go with every page.
- **Read-through first**: an option to read the whole chapter before translating it.
- **Comic reader**: one page at a time or double-page spreads, with the next pages translated ahead as you read.
- **Add a Comic**: from a link, images, a CBZ or a whole folder; export as ZIP or PDF.
- **Saved manga**: chapters saved as CBZ open in a built-in reader.

### Sources and the browser extension
- **Sources**: search or paste a link, import chapters into a title or save them as CBZ, and track series for new chapters (with optional auto-import).
- **Site adapters**: 24 built in, covering manhua, manga, web novels, video and audio drama sites, with a health light and a "Will this site work?" check.
- **Browser extension** (Chrome and Edge): translates the page you are reading, in place for comics and in a panel for text, and can capture a whole chapter into a title.
- **Extension title options**: Auto finds or creates the title, fills its name and source link, and files it in a series.
- **Baihe Grab (Android)**: the Android app can grab a comic chapter from a site and send it to Baihe.

### Live stream captions
- **Live**: caption a stream in near real time with Whisper and translate beside it (experimental).
- **Live extras**: read stream chat, automatic video delay, full screen, and a channel follow that checks hourly and can start captions for you.

### Voices, dubbing and audio
- **Narration**: Listen in the Reader turns a novel into audio.
- **Voice engines**: Qwen3-TTS, Chatterbox and OmniVoice, each installed in its own folder from Diagnostics.
- **Voice cloning**: clone a voice from a short clip, or design one from a description with OmniVoice.
- **Voice bank**: keep each speaker's voice print, reference clips and actor across series, and match new speakers to them.
- **Audiobook export**: an M4B with chapter markers.

### Export
- **Subtitle files**: SRT, VTT, ASS (with speaker colours) and LRC.
- **Video**: a soft-sub track, burned-in captions, or video with the dub audio.
- **Books**: EPUB and audiobook.
- **Comics**: ZIP or PDF of the typeset pages.
- **Jellyfin**: send subtitles to a Jellyfin library (optional, off by default).
- **Bulk export**: all translated titles as a zip, and a per-title export package with a manifest.

### Jobs, setup and tools
- **Job queue**: schedule work to start now or at a set time, with cancel, retry and per-stage progress.
- **Diagnostics**: checks your setup, installs packages, models and the GPU runtime, and shows ports and a redacted log.
- **Installer and updates**: a Windows installer, an optional background service, app-only updates, portable mode and a signed release check.
- **Notifications**: an in-app bell, and optional Discord or ntfy messages when a job finishes.
- **Command palette and shortcuts**: Ctrl+K search (it also finds lines), and a list of every key on `?`.
- **Phones**: pages fit small screens, install to the home screen and Share a link into Sources.
- **Benchmark Lab** (Developer Mode): golden sets, a side-by-side Model Arena and scores for translation, transcription and OCR.
- **Assistant** (Developer Mode): a read-only helper that answers questions about the app's code and logs.
- **Report a problem**: send a redacted report from inside the app.

### Other devices and safety
- **Household access**: optional sign-in with Google (email only) for other devices, off by default.
- **People and rights**: per-person permissions, monthly paid-engine limits and an Activity log.
- **PC profiles**: a PIN-protected Owner and a Guest that hides private titles.
- **Private by default**: your files and keys stay on your machine except for the direct call to the engine you chose; keys are never shown back.

### Coming soon (open draft pull requests, not yet merged)
- Open folder buttons on the simple title pages, with a copyable path for the Windows service.
- An option to letter comic balloons in the original lettering's colour.
- Scanlate progress counted page by page while the chapter is read first.
- Extension imports filed under their chapter, and a new title given an English name with one small AI call.

## Install

### Windows installer (recommended)

Download `BaiheStudio-Setup-<version>.exe` from the project's releases and run it. It installs for your user without admin rights, bundles Python and the interface, and keeps your library in a separate data folder that upgrades never touch. Start it from the Start menu entry **Baihe Studio**.

### From source (Windows)

You need Git. In PowerShell, from a folder you own:

```
git clone https://github.com/zkaelxz/U00 Baihe
cd Baihe
.\setup.bat                    # checks Python 3.12+, ffmpeg and Node.js 22 and installs what is missing (Python 3.13 if it has to)
.\start.bat --build-frontend   # first run: creates the environment and builds the interface (several minutes)
```

After that, double-click `start.bat`. Open a new PowerShell window after `setup.bat` installs anything, so Windows finds it. A git clone updates itself on each start; a downloaded zip does not.

### Manual install (any OS)

Python 3.12 or 3.13 (3.13 recommended) and `ffmpeg` with libass:

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-core.txt -c constraints.txt
python -m api                                      # then open http://127.0.0.1:8600/
```

Add `requirements-media.txt` for audio and video, and lines from `requirements-optional.txt` for the features you use (the Diagnostics page can install these for you). The interface must be built once with Node.js 22 (`cd frontend && npm ci && npm run build`) or unzipped from a release.

## First run

1. Open **Settings** and add a key for your translation engine, or point it at a local Ollama.
2. Open **Diagnostics** and install transcription if you want to transcribe audio. Models download the first time they are used.
3. Click **Make subtitles** and choose a file.

If something does not work, Diagnostics checks your setup and says what is missing.

## Documentation

- [User guide](docs/user-guide.md): every feature, install and start options (flags, portable mode, updates, uninstall), ports and troubleshooting.
- [Household access](docs/household-access.md): using the app from other devices.
- [Docs index](docs/README.md): architecture, database, jobs, engines, testing and release notes for contributors. Contributor rules: [`CLAUDE.md`](CLAUDE.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Licence

All rights reserved. See [`COPYRIGHT.md`](COPYRIGHT.md).
