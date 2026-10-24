# User guide: setup

Part of the [user guide](user-guide.md).

## Install details

The README has the short version. This section has every install and start option for a source checkout; the Windows installer needs none of it.

### Set up a new Windows PC from source, step by step

1. **Open PowerShell as a normal user** (not "Run as administrator"). Don't open it from the root of a drive or from `C:\WINDOWS\System32`: Windows starts the window in the wrong folder. Go to a folder you own first, for example `cd $HOME` or `cd L:\`.
2. **Get the code.** Install [Git](https://git-scm.com/download/win) if `git --version` fails, then clone it. A clone matters: only a clone updates itself (see Automatic updates below); a downloaded zip never does.
   ```
   git clone https://github.com/zkaelxz/U00 Baihe
   cd Baihe
   ```
   The project is on the `baihe-subtitler` branch.
3. **Run the setup check.** In PowerShell a program in the current folder needs `.\` in front of its name:
   ```
   .\setup.bat
   ```
   It checks for Git, Python 3.12, ffmpeg (with the subtitles filter) and, only if the app's screens still have to be built, Node.js 22 or newer (it warns if your Node is older than 22.23.4, 24.22.1 or 26.12.1, which have a security fix), and installs what is missing with `winget` (it may ask for permission; the script needs no admin rights). It only reports the NVIDIA driver; install that yourself from <https://www.nvidia.com/drivers> if you want GPU features.
4. **Open a new window after any install.** Windows doesn't find newly installed programs in the window that installed them. Close it, open a new PowerShell, `cd` back into the Baihe folder and run `.\setup.bat` again. It is safe to run twice: anything `winget` already lists is reported as "already installed" and skipped. Stop when it ends with **Ready** or with "Everything is installed. Open a NEW window so PATH updates, then run start.bat."
5. **Build the screens and start.** The first run creates the Python environment, installs dependencies, builds the app's screens and checks ffmpeg and CUDA. It takes several minutes:
   ```
   .\start.bat --build-frontend
   ```
   After that, double-click **`start.bat`** (or run `.\start.bat`). The app opens in its own window at `http://127.0.0.1:8600/`.
6. **First run.** Add your API keys in Settings, or copy your `.env` next to `start.bat`. A `.env` saved as UTF-16 (Notepad's "Unicode", PowerShell 5's `>`) is read too, and Settings saves it back as UTF-8. A UTF-32 `.env` (PowerShell's `-Encoding UTF32`) is not read; save it as UTF-8, and saving a key in Settings leaves its existing bytes alone. To bring over your titles, copy the old PC's `library` folder into the Baihe folder before the first start. Models such as Whisper download the first time they are used.

Using Command Prompt (`cmd`) instead of PowerShell? The same commands work without the `.\`.

### Starting an existing checkout

Double-click **`start.bat`**. On first run it creates the virtual environment and installs dependencies, checks ffmpeg, a JS runtime and CUDA and says plainly what is missing, starts `python -m api` on `http://127.0.0.1:8600/` (loopback only), and opens it in its own window (Edge app mode, else Chrome, else your default browser). Running it again just reopens the window. If the Python the `venv` folder was made from is upgraded or uninstalled, `start.bat` says so; delete `venv` and run it again (optional packages such as torch then need installing again). `make_shortcut.bat` makes a desktop shortcut; `uninstall.bat` removes the shortcut and virtual environment, asks (default no) before touching your user PATH entries for ffmpeg/Tesseract, and deletes your library (backups included) only if you type `DELETE` in capitals; anything else keeps it. It ends by listing, never deleting, what it leaves behind (Hugging Face, torch, pip and Ollama caches, winget-installed Python/Git/ffmpeg/Node), and prints the real library location if `BAIHE_DATA_DIR` is set.

- **`setup.bat`.** Optional and never run by `start.bat`; it only points to it when something is missing. Run it again at any time to re-check the prerequisites.
- **The app's screens.** A source checkout needs `frontend\dist\index.html`. With Node.js 22 run `start.bat --build-frontend`. It prints the same warning for an unpatched Node. If it is missing, `start.bat` stops and says so.
- **Flags.** `--portable`, `--server-only` (don't open a window), `--ci`, `--no-update`, `--build-frontend`, and `--python-version 3.12` (or a `PYTHON_VERSION` marker file).

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

### Portable mode

`library/` is saved inside the app folder, so copying the folder carries your data. Portable mode also redirects the model caches (Whisper, pyannote, OmniVoice, the audio separator) into a `model_cache/` folder there. Turn it on with `start.bat --portable` or an empty file named `PORTABLE` next to `start.bat`. The other PC still needs its own Python and `ffmpeg`.

### ffmpeg and updates

- **ffmpeg** (with libass, for burning subtitles):
```bash
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Ubuntu/Debian
# Windows: download from ffmpeg.org and add to PATH
```
- **Automatic updates (source checkouts).** Before launch, `start.bat` fast-forwards a clone on `baihe-subtitler` to `origin/baihe-subtitler`. It never discards, stashes or overwrites your changes and leaves untracked and ignored files (`library\`, `.env`, `venv\`, `frontend\dist`) alone. When it updates it prints `Updated to <commit>: N new commits` and the latest subjects. If `requirements-core.txt` or the constraints files changed it reinstalls dependencies in the same launch, and tries again on the next start if that install fails or its window is closed; if the screens' source changed it rebuilds `frontend\dist` when `npm` exists, otherwise it says to run `start.bat --build-frontend`. It does nothing without a `.git` folder, without `git` on PATH, for an installed copy, or when GitHub can't be reached. A copy with local commits that aren't on origin is not updated (the update wouldn't sit on top of them).

## Developer setup, ports and the Windows service

```bash
BAIHE_API_ENV=development python -m api     # API on http://127.0.0.1:8600, docs at /api/docs
cd frontend && npm ci && npm run dev        # React on http://127.0.0.1:5173
```
On Windows `cmd`, run `set BAIHE_API_ENV=development` first. The API reads `BAIHE_API_HOST` (default `127.0.0.1`; keep it there), `BAIHE_API_PORT` (default `8600`), `BAIHE_API_ENV` (`development` enables reload and CORS for the Vite ports, default `production`) and `BAIHE_API_CORS_ORIGINS` (see `api/api_config.py`). The Vite dev server proxies `/api` to `http://127.0.0.1:8600`, or to `BAIHE_API_URL` if set. `npm run build` produces `frontend/dist`, which `python -m api` serves at `/`.

- **Changing the port.** `BAIHE_API_PORT` is read from the environment, not `.env`: `setx BAIHE_API_PORT 8601` and restart. `start.bat` and the installed launcher follow it; the Vite proxy needs a matching `BAIHE_API_URL`. The default is defined once as `DEFAULT_PORT` in `api/api_config.py`; the files that can't import it (`start.bat`, `frontend/vite.config.ts`, `frontend/src/report/capture.test.ts`, the two Windows workflows, the root `README.md` and `CLAUDE.md`) carry a literal, and `tests/test_installer_runtime.py` fails if one drifts. The household listener has its own port (`BAIHE_API_HOUSEHOLD_PORT`); never forward either port through the router. Baihe also refuses its own ports as ntfy, SearXNG and Jellyfin targets.
- **The Windows boot service's port.** With the service installed, its stored port wins; changing `BAIHE_API_PORT` or running Setup again does not change it (only a fresh install takes it). Use Start menu > "Baihe Studio service" (shows every port, changes the port, turns remote access on or off).
- **Service commands** (what the menu runs; `service.py --help` lists them): from an administrator prompt, run `"%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" COMMAND` with `status` (no admin needed), `set-port N`, `enable-remote [--household-port N]`, `disable-remote`, `stop`, `install [--port N]` or `uninstall`. Details: `docs/windows-installer-design.md` section 11.
- **If Setup or the uninstaller stops partway.** The uninstaller removes the boot service only after you confirm; answering No keeps everything. With two installs on one PC, uninstalling the one that doesn't run the service leaves the service alone. A Setup that is refused or cancelled before its install step removes the empty data folder it made, so the next run limits that folder to your account again. If Setup can't put the service's old files back, the service stays stopped until you run Setup again.

## Resetting for testing

Diagnostics > System > **Reset library** deletes every drama, translation, glossary, series, progress record and file, and also every account, sign-in session and the audit log, then reinitializes an empty database. You confirm by typing RESET; there is no undo. To remove one drama, use its delete button.
