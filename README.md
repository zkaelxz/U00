# Baihe Audio Drama Subtitler — Library Edition

A local tool for translating, subtitling, and optionally AI-dubbing
Chinese/Japanese/Korean audio dramas, novels, comics, and streamer VODs
at scale. Inspired by
[pyvideotrans](https://github.com/jianchang512/pyvideotrans)'s workflow
(ASR → translate → TTS dub / clone), scoped to this genre space, with a
persistent filterable library for managing dozens of titles.

It is a Python app with a FastAPI server and a React frontend, started with
`start.bat` (or `python -m api`), plus a headless CLI (`cli.py`). The older
Streamlit UI has been removed (`legacy/streamlit` and the
`pre-streamlit-removal` tag hold the last version).

**Important:** this tool works on files you already have legal access
to (audio/video you've downloaded or been given, novel text you own or
have licensed). It does not scrape, download, or extract content from
paid apps or streaming platforms — point it at local files, or a public
URL you have the right to download from, only.

## What it does

- **Library** of titles, filterable by title, author, studio, director, voice actor, status, source language (zh/ja/ko) and content type (audio drama, video drama, novel, manhwa, manga, manhua, ASMR, streamer VOD); series share glossaries, styles and characters.
- **Transcription** with Whisper (optional Qwen3-ASR), speaker diarization, OCR for page scans and burned-in captions, and **Live** near-live stream translation.
- **Multi-engine translation**: Claude, DeepSeek, Gemini, OpenAI or Ollama, with glossaries, term policies, style presets, emotion tags, a review queue and a consistency checker.
- **Review and polish**: line merging, pacing checks with one-click LLM shortening, translation versions, undo, locale variants (American/British/Australian English).
- **AI dubbing** with the local OmniVoice voice engine (voice cloning and voice design); audiobook (.m4b) export.
- **Export**: SRT/VTT/ASS, burned-in (hardsub) or toggleable (softsub) video, dub track mixing, EPUB, bulk zip.
- **Interactive Reader** with pinyin/furigana, click-to-define, in-app Q&A, in-app playback and Anki vocabulary export.
- **Scanlate** (manga/comic typesetting): detect bubbles, clean, translate, place text, adjust, render.
- **Sources and Discover**: site adapters for importing chapters, metadata auto-fill from public listing pages, a known-titles catalog, and a site navigation helper.
- **Video input**: upload `.mp4`/`.mov`/`.mkv`/`.webm` or download from a URL via yt-dlp.
- **CLI** (`cli.py`): headless batch runs across your library.

How each feature works, with options and limits: [`docs/user-guide.md`](docs/user-guide.md).

## Install and start

### Windows installer

Run `BaiheStudio-Setup-<version>.exe` (built by the "Windows Installer" workflow; see [`docs/RELEASE.md`](docs/RELEASE.md)). It installs per user with no admin rights, bundles Python and the frontend, keeps your library in a separate data folder that upgrades never touch, and by default also offers a Windows service that starts the app at boot. Details: [`docs/windows-installer-design.md`](docs/windows-installer-design.md). The Start menu entry "Baihe Studio" opens the app at `http://127.0.0.1:8600/`; "Stop Baihe Studio" stops it.

### From a source checkout

Double-click **`start.bat`**. On first run it creates the virtual environment and installs dependencies, checks ffmpeg, a JS runtime and CUDA and says plainly what is missing, starts `python -m api` on `http://127.0.0.1:8600/` (loopback only), and opens it in its own window (Edge app mode, else Chrome, else your default browser). Running it again just reopens the window. `make_shortcut.bat` makes a desktop shortcut; `uninstall.bat` removes the shortcut and virtual environment and asks separately (default no) before touching your library or your user PATH entries for ffmpeg/Tesseract.

- **The app's screens.** A source checkout needs `frontend\dist\index.html`. Download `baihe-frontend-<version>.zip` from the project's GitHub Releases and extract it into the Baihe folder, or with Node.js 22 run `start.bat --build-frontend` (how the zip is made: [`docs/RELEASE.md`](docs/RELEASE.md)). If it is missing, `start.bat` stops and says so.
- **Flags.** `--portable`, `--server-only` (don't open a window), `--ci`, `--no-update`, `--build-frontend`, and `--python-version 3.12` (or a `PYTHON_VERSION` marker file). `start.ps1` is the PowerShell equivalent (`-Portable`, `-PythonVersion`, `-BuildFrontend`; it does not update). `constraints.lock.txt` (from `make_lock.bat`) is used instead of `constraints.txt` when present.
- **Automatic updates.** A git clone on the `baihe-subtitler` branch is fast-forwarded to `origin/baihe-subtitler` before launch; it never discards or overwrites your changes. It skips the update and starts what is on disk when the copy is on another branch, has edited tracked files, or Baihe is already running (restart to pick up an update). Turn it off for one run with `--no-update`, or for good with an empty file named `NOUPDATE` next to `start.bat`. `--ci` and `--server-only` never update.

### Prerequisites and manual install

Python 3.10+ (CI and the installer use 3.12) and `ffmpeg` with libass (for burning subtitles; most builds have it). Node.js 22 is only needed to build the frontend yourself.

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements-core.txt -c constraints.txt
```

That is the minimum to launch and translate text. Add what you use:

```bash
pip install -r requirements-media.txt -c constraints.txt      # audio/video: align, dub, burn subtitles
pip install -r requirements-optional.txt -c constraints.txt   # per feature; install only the lines you need
```

`requirements.txt` is those three files combined. The Diagnostics page's Install buttons do the same picking without typing. `-c constraints.txt` caps packages at major versions known not to break the app (it installs nothing itself). Then `python -m api` and open `http://127.0.0.1:8600/`.

### First run

Open **Settings** and add the API key for your translation engine, or use a free local one (Ollama). Keys are read from `.env` next to `start.bat` (copy `.env.example`; it is not versioned) or from environment variables. Models such as Whisper download on first use. If something isn't working, run the **Diagnostics** page first. To change the port (default 8600), see [`docs/user-guide.md`](docs/user-guide.md#developer-setup-ports-and-the-windows-service).

### Portable mode

`library/` is saved inside the app folder, so copying the folder carries your data. Portable mode also redirects the model caches (Whisper, pyannote, OmniVoice, the audio separator) into a `model_cache/` folder there. Turn it on with `start.bat --portable` or an empty file named `PORTABLE` next to `start.bat`. The other PC still needs its own Python and `ffmpeg`.

### Access from other devices

The app is **loopback-only** (`127.0.0.1`) by default. Household members can reach it through opt-in remote access: the installed background service plus Caddy (HTTPS) in front of a separate household listener with Google sign-in always on. Nothing opens a router port or firewall rule for you. Guide, including ports: [`docs/household-access.md`](docs/household-access.md); design: [`docs/remote-access-decision.md`](docs/remote-access-decision.md).

## Docs

- [`docs/user-guide.md`](docs/user-guide.md): CLI, dev setup, translation, transcription, dubbing, Reader, Library, troubleshooting, Diagnostics, Benchmark Lab.
- [`docs/README.md`](docs/README.md): the docs index. [`docs/STATUS.md`](docs/STATUS.md): current status and what's next. [`FILE_ORGANIZATION.md`](FILE_ORGANIZATION.md): file map. [`docs/archive/`](docs/archive/): history.
- [`docs/technical-notes.md`](docs/technical-notes.md): bugs found and fixed. [`CLAUDE.md`](CLAUDE.md): repo layout, tests and rules for contributors.
