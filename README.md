# Baihe Studio

A local app for transcribing, translating, reviewing and exporting English subtitles for Chinese, Japanese and Korean audio dramas, video dramas, novels, comics and streamer VODs, with a library to keep dozens of titles organised.

It runs on your own Windows PC: a Python (FastAPI) server with a React interface in your browser at `http://127.0.0.1:8600/`. Transcription runs locally with Whisper; translation uses the engine you choose, paid (Claude, Gemini, DeepSeek, OpenAI) or free and local (Ollama).

**Use it only on content you have the right to use:** files you own or were given, text you have licensed, or public pages and URLs you may download from. It does not unlock paid apps or streaming platforms.

## Features

- **Library**: titles with series, filters, lists, profiles, backups and a restorable Trash.
- **Make subtitles**: drop in an audio or video file and get an SRT in one screen.
- **Transcribe**: Whisper (optional Qwen3-ASR), speaker detection, OCR for page scans and burned-in captions.
- **Translate**: glossaries, style presets, per-series terms, cost tracking and an optional monthly spending cap.
- **Review**: line editor with flags, checks, history and undo, keyboard-first on desktop and a thumb bar on phones.
- **Export**: SRT, VTT, ASS and LRC, burned-in or soft-sub video, EPUB, audiobook and bulk zip.
- **More**: Quick translate, an interactive Reader (pinyin and furigana), comic translation (Scanlate), Sources for importing chapters, Live stream captions, local AI dubbing, a Job queue, and Benchmark Lab for comparing models.
- **Other devices**: optional household access with Google sign-in (off by default; the app listens on this PC only).

## Install

### Windows installer (recommended)

Download `BaiheStudio-Setup-<version>.exe` from the project's releases and run it. It installs for your user without admin rights, bundles Python and the interface, and keeps your library in a separate data folder that upgrades never touch. Start it from the Start menu entry **Baihe Studio**.

### From source (Windows)

You need Git. In PowerShell, from a folder you own:

```
git clone https://github.com/zkaelxz/U00 Baihe
cd Baihe
.\setup.bat                    # checks Python 3.12, ffmpeg and Node.js 22 and installs what is missing
.\start.bat --build-frontend   # first run: creates the environment and builds the interface (several minutes)
```

After that, double-click `start.bat`. Open a new PowerShell window after `setup.bat` installs anything, so Windows finds it. A git clone updates itself on each start; a downloaded zip does not.

### Manual install (any OS)

Python 3.10+ (3.12 recommended) and `ffmpeg` with libass:

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
- [Status](docs/STATUS.md): what works today and what is next.
- [Docs index](docs/README.md): architecture, database, jobs, engines, testing and release notes for contributors. Contributor rules: [`CLAUDE.md`](CLAUDE.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Licence

All rights reserved. See [`COPYRIGHT.md`](COPYRIGHT.md).
