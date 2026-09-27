# Windows installer/uninstaller architecture — design proposal

Roadmap Step 80. **Design only — nothing in this document has been
implemented.** No installer code, no packaging CI, and no change to
`start.bat`/`start.ps1` beyond what Step 79 already does. This is the
write-up an implementing session (or the user) reads before Step 80's
eventual follow-on build step starts.

Written against `baihe-subtitler` as it exists today (Step 79 not yet
merged — its Python-Store-stub fix is a separate, narrower patch to
`start.bat` and doesn't change anything below). Every claim here is
checked against this branch's real files, not assumed.

---

## 1. What's real today (the base this design builds on)

**Dependency tiers** (`requirements-core.txt`, `requirements-media.txt`,
`requirements-optional.txt`, Step 75):
- `requirements-core.txt` — streamlit, pandas, requests, beautifulsoup4,
  anthropic. Enough to launch the app and do text translation + subtitle
  export, no ffmpeg/GPU/ML needed.
- `requirements-media.txt` — faster-whisper, pydub, edge-tts, opencv-python,
  pillow. Audio/video: alignment, diarization, dubbing, subtitle burn-in.
  Needs ffmpeg on PATH.
- `requirements-optional.txt` — everything else, already grouped by
  feature in comments: alternative translation engines, yt-dlp downloads,
  pyannote diarization, Qwen3-ASR/aligner, voice cloning (F5-TTS +
  commented-out OmniVoice/Chatterbox/TADA, which can't share one
  environment), vocal separation, word-level forced alignment, offline TTS
  (Piper), OCR (pytesseract/PaddleOCR/manga-ocr), Reader segmentation
  (jieba/pypinyin/sudachipy/pykakasi/kiwipiepy), Scanlate ML
  detection+inpainting (torch/transformers/safetensors), PDF import, the
  erase/heal brush, export helpers (genanki/ebooklib), desktop
  notifications, Playwright, trafilatura, and mangaz.com's
  cryptography-dependent adapter.

**Diagnostics' existing dependency machinery** (`diagnostics.py`):
- `OPTIONAL_DEPENDENCIES` (line 60) tags every import by name → (import
  name, plain-English feature description, tier). Tiers used today:
  `"required"`, `"engine"`, `"feature"` (a fourth, `"dev"`, exists only in
  spirit for pytest — see the Step 83 note below).
- `INSTALLABLE_TIERS = ("feature", "engine")` (line 589) — only these two
  ever get a generic per-package Install button; `"required"` is assumed
  already installed (the app wouldn't be running otherwise) and `"dev"` has
  nothing to do with a running session.
- `parse_requirements_file()` (line 629) + a bulk-install action (Step 62)
  installs a whole tier's requirements file in one click, streaming real
  pip output (`stream_pip_install`, line 592) rather than swallowing
  errors.
- A throwaway-venv upgrade-safety check (Step 66, line ~1000 on):
  `_make_throwaway_venv()` creates an isolated venv that still sees the
  running environment's own site-packages via a `.pth` file, installs a
  candidate package version into it, and runs the real test suite there —
  never touching the real environment. Reports "safe" / "broken" /
  "conflict" / "incomplete", never a guess.
- **Known, pre-existing bug, not this step's to fix** (flagged by the
  secondary-review pass, tracked as provisional Step 83): `cv2`,
  `faster_whisper`, and `PIL` are tagged `"required"` in this dict even
  though none of the three ship in `requirements-core.txt` — only in
  `requirements-media.txt`. This design doesn't depend on that tagging
  being fixed, but a real installer's tier mapping (§3 below) should read
  from the same three `requirements-*.txt` files directly rather than
  trusting `OPTIONAL_DEPENDENCIES`'s tier field, so it isn't silently
  wrong if that bug isn't fixed first.

**Startup flow**: `start.bat`/`start.ps1` create a `venv/` next to the app,
install `requirements-core.txt` (checked by trying to import its packages,
not just checking `streamlit` — Step 53/63), run `check_setup.py` for a
plain-words ffmpeg/JS-runtime/CUDA check, then launch
`streamlit run app.py --server.headless true` and open a browser window
(Edge app-mode preferred, Chrome fallback, default browser last resort).
`app.py`'s own first two lines call `portable.activate_portable_mode()`
before any other import.

**Model/data storage**:
- `portable.py` — off by default. Model downloads (Whisper, pyannote,
  F5-TTS, audio-separator) go to each library's own OS-standard cache
  (`~/.cache/huggingface`, `~/.cache/audio-separator-models`, etc.) exactly
  as any other Python tool using them would. When a `PORTABLE` marker file
  sits next to `portable.py` (or `--portable`/`BAIHE_PORTABLE=1`), every
  model cache this app's own code can redirect (`HF_HOME`, `TORCH_HOME`,
  `BAIHE_AUDIO_SEP_MODEL_DIR`) gets pointed at `model_cache/<subdir>` next
  to the app instead, via `os.environ.setdefault` (never overriding a value
  the user already set). This must run before any module that imports
  `huggingface_hub`/`torch` at its own top level.
- `db.py` — `LIBRARY_DIR = <app dir>/library`, with `DRAMAS_DIR`,
  `DB_PATH` (`library.db`), `BENCHMARK_DIR`, `VOICE_BANK_DIR` all derived
  from it. There's a `set_library_dir()` override, but nothing today calls
  it from a config file or installer-supplied path — `LIBRARY_DIR` is
  always relative to `db.py`'s own `__file__` unless a caller sets it in
  code. **This matters directly for §3 below**: today the library sits
  inside the app folder itself, which is fine for `start.bat`'s
  copy-the-folder model but is the wrong place once the app installs to
  `C:\Program Files\...` (a machine-wide, admin-writable, per-user-hostile
  location — see §5).

**Uninstall precedent** (`uninstall.bat`): removes exactly the desktop
shortcut, Start Menu entry, `venv/`, and `model_cache/` (if portable mode
was used) unconditionally, then separately prompts (default **No**) before
touching `library/`, and separately again (default **No**) before checking
user-level PATH for ffmpeg/Tesseract entries it might have added. Never
touches system-wide installs (ffmpeg, Ollama, CUDA driver) or Hugging
Face/PyTorch's own default (non-portable) cache — explicitly out of scope
because it can't know if something else on the machine still depends on
them.

---

## 2. Packaging approach: installer framework + bundled Python, vs. a compiled single executable

Two real, commonly used paths for a Python desktop app on Windows:

### Option A — Installer framework (Inno Setup / NSIS / WiX) + a bundled, pinned Python runtime

The installer (a real `.exe` built by Inno Setup, NSIS, or WiX) copies the
app's source/bytecode plus a **Python embeddable package** (the official
`python-3.x.y-embed-amd64.zip` redistributable, or a similarly pinned
standalone Python build) onto disk, then runs `pip install` against that
bundled interpreter at install time (or ships a pre-built `venv`/wheel
cache so no network call is needed for Basic tier). The running app is
still "real Python running real source," just with the interpreter
resolved from a fixed path the installer controls, never from `PATH` — so
the Windows-Store-alias bug class Step 79 fixes can't occur at all, because
nothing ever does `where python`.

- **Build size**: small base (embeddable Python is ~15-25MB; the app's own
  source is trivial). Total size scales with which tiers/components are
  selected — a Basic-tier installer can stay under 150-300MB; Recommended
  with GPU torch + pyannote weights predownloaded is multiple GB, same as
  today's real `pip install` footprint already is.
- **Update granularity**: excellent. Each install "component" (app files,
  a specific optional dependency, a specific downloaded model) can be its
  own file group in the installer script, updated independently — this is
  the natural fit for §5's "updates don't redownload unchanged models."
- **Startup time**: unaffected — it's the same interpreter starting the
  same `streamlit run app.py` process `start.bat` already does, just from a
  fixed path instead of a resolved-from-PATH one.
- **Large optional ML dependencies**: handled cleanly — pip already knows
  how to install torch/pyannote/transformers against this bundled
  interpreter exactly as it does in today's dev venv; nothing about the
  packaging format constrains this.
- **GPU/CUDA passthrough**: no additional complication versus what the app
  already does — CUDA drivers stay a system-level install (already true
  per `uninstall.bat`'s own scope), and `torch`'s CUDA wheel installs into
  the bundled interpreter the same way it would into any venv.
- **Cost**: an installer script to write and maintain (Inno Setup's
  scripting language, or WiX's XML, or NSIS's own script language); one
  more build artifact type in the release process.

### Option B — Fully compiled single executable (PyInstaller / Nuitka)

Freezes the interpreter and the app's own code (and, in practice, a large
slice of its dependency tree) into one `.exe` or a `.exe` + support-files
folder, with no Python source or separate interpreter visible at all.

- **Build size**: much larger per-build up front — freezing even
  `requirements-core.txt` alone typically produces a base bundle in the
  tens of MB to ~100MB+ before any optional ML dependency is added; adding
  torch/transformers/pyannote to a *frozen* build routinely produces
  multi-GB single artifacts, and every optional-dependency permutation
  either needs its own separate frozen build or the freezer has to bundle
  every optional dependency it might ever need, defeating the point of
  tiers.
  This is the most contested trade-off Basic  Standard packaging is
  designed to avoid.
- **Update granularity**: poor by default. PyInstaller/Nuitka produce one
  monolithic bundle; there's no first-class notion of "just update the
  model files" or "just update this one optional component" without
  significant custom bootstrapping on top (effectively rebuilding
  Option A's component model by hand, inside a format that isn't designed
  for it).
- **Startup time**: PyInstaller in particular has a real, well-documented
  cold-start penalty (unpacking to a temp dir on `--onefile` builds) that
  doesn't exist for a normal `python -m streamlit run` process. Nuitka
  (compiles to C) avoids most of that penalty but has a much heavier build
  step and less mature support for some of this app's heavier optional
  packages (pyannote, funasr, several TTS backends) than plain pip/venv
  does.
- **Large optional ML dependencies**: the worst fit of the two options.
  Freezers work by static analysis of imports; a codebase this large, with
  this many optional, dynamically-imported ML backends (Scanlate's ML
  detector, four alternate TTS engines, several OCR backends, several ASR
  backends — many already conditionally imported specifically so they
  don't have to all coexist, per `requirements-optional.txt`'s own
  comments about OmniVoice/Chatterbox/TADA not sharing an environment)
  is exactly the shape that trips up static-import freezing the hardest,
  and doesn't map onto "toggle this component on/off" the way separate
  `pip install` targets already do today.
- **GPU/CUDA passthrough**: no inherent advantage or penalty versus
  Option A — CUDA drivers are still a system install either way — but
  torch's own CUDA wheels are large and freezing them in adds directly to
  the single-executable size problem above, whereas Option A only pulls
  them in for whichever tier/component actually needs them.

### Recommendation: Option A

Baihe's actual dependency shape — large, sometimes mutually-conflicting
optional ML components (§1), an existing venv-based dev workflow
(`start.bat`, this design's own §1), and already-built tiered-install
infrastructure (`requirements-core/media/optional.txt`, Diagnostics'
per-tier bulk-install buttons, the throwaway-venv upgrade-safety check) —
all point the same direction: an installer framework with a bundled,
pinned Python runtime, not a compiled single executable. It's the option
that:
- reuses the existing tiered requirements files and Diagnostics'
  install/upgrade machinery almost as-is (§3, §6);
- supports independent updates per category (§5) without inventing a
  parallel component system a freezer doesn't have;
- has no realistic single-executable story for a codebase whose optional
  dependency count and mutual exclusivity (OmniVoice vs. Chatterbox vs.
  TADA) is this large;
- is immune to the exact Windows-Store-alias bug class Step 79 exists to
  patch, the same guarantee Option B would also give, so that's not a
  deciding factor between them.

Inno Setup specifically (over NSIS/WiX) is the pragmatic default recommendation
within Option A: free, widely used for exactly this "installer +
Program Files + Add/Remove Programs entry" shape, scriptable components
map cleanly onto §3's three tiers, and it has straightforward support for
running a post-install `pip install` step against a bundled interpreter.
WiX gives more enterprise-grade MSI compliance the project doesn't
currently need; NSIS is comparable to Inno Setup in capability but with a
less readable scripting language. This is a preference to revisit at
actual build time, not a hard architectural dependency — nothing in §3-§6
requires Inno Setup specifically over NSIS or WiX.

---

## 3. Three-tier component breakdown, mapped onto existing infrastructure

Maps directly onto `requirements-core/media/optional.txt` (Step 75) and
Diagnostics' existing tier vocabulary (`OPTIONAL_DEPENDENCIES`'s
`required`/`engine`/`feature` tags, `INSTALLABLE_TIERS`) — **not a second
grouping**. The installer's tier selector is a UI in front of the same
three requirements files already checked into the repo:

- **🟢 Basic** — app files + bundled Python + `requirements-core.txt`
  only. Text translation, subtitle export, no ffmpeg/GPU/ML. This is
  already exactly what `requirements-core.txt`'s own header comment
  describes ("Minimum to launch the app and do text translation +
  subtitle export").
- **🔵 Recommended** — Basic + `requirements-media.txt` (alignment,
  diarization from `pyannote.audio`, dubbing, subtitle burn-in — note
  `pyannote.audio` itself is technically in `requirements-optional.txt`
  today under "speaker diarization," so Recommended's real definition is
  "core + media + the diarization/OCR/transcription-adjacent subset of
  optional that most users actually want," matched against what Step 75's
  own tiering already treats as the common path) + GPU-enabled `torch`
  (CUDA wheel where a supported GPU is detected, CPU wheel otherwise) +
  a default OCR backend (pytesseract) + a default local-segmentation set
  (jieba/pypinyin for Chinese, matching the app's own CJK-first framing).
  **This tier's exact membership is this design's one open call, deferred
  to the implementing step**: the honest options are (a) Recommended =
  core + media only, letting Custom cover every "feature"/"engine"-tier
  optional component, or (b) Recommended = core + media + a curated
  subset of optional (diarization, one OCR backend, one segmentation set
  per major source language) chosen to match what most users installing
  "the whole audio-drama-to-dub pipeline" actually want. Both are
  legitimate; picking between them needs the same kind of real-usage
  judgment call Step 75's own tiering already made once, not a fresh
  invention here.
- **🟣 Custom** — every optional component from `requirements-optional.txt`
  individually toggleable, sourced directly from `OPTIONAL_DEPENDENCIES`'s
  own per-package feature description (already written in plain English —
  e.g. "GPU acceleration — uses your NVIDIA GPU to significantly
  accelerate supported AI workloads; without it, supported workloads run
  on CPU but may be substantially slower" is exactly the tone
  `OPTIONAL_DEPENDENCIES`'s existing feature-description strings already
  use, just surfaced in an installer checkbox list instead of a Diagnostics
  table row). Mutually-exclusive groups (OmniVoice / Chatterbox-tts /
  TADA — `requirements-optional.txt`'s own comment already states these
  "can't all share one environment") render as a radio group, not
  independent checkboxes, so the installer enforces the same constraint
  the comment currently only documents.

Each tier/component maps to one or more pip package specs already listed
in the three requirements files — the installer's job is selecting which
specs to pass to a post-install `pip install` step against the bundled
interpreter, not maintaining a separate dependency list.

---

## 4. Data-category separation for install / update / uninstall

Four categories, kept genuinely independent so an update or uninstall can
touch one without touching the others:

| Category | What it is | Where it lives | Install/update behavior | Uninstall behavior |
|---|---|---|---|---|
| **App files** | Python source, `tabs/*.py`, `.streamlit/config.toml`, the bundled interpreter itself | Installed location (see §5's own open question on where that should be) | Replaced wholesale on every app update — small, fast | Always removed (this is "the app"), same as today's `venv`/shortcut cleanup |
| **Optional components** (pip packages) | Whatever the chosen tier/Custom selection installed — torch, pyannote, OCR backends, TTS backends, etc. | Inside the bundled interpreter's own `site-packages` | Only reinstalled/upgraded when that specific component's pinned version changes; untouched otherwise | Prompted per-component category (mirrors `uninstall.bat`'s existing separate-prompt pattern), not lumped in with app files |
| **Downloaded models** | Whisper/pyannote/F5-TTS/audio-separator weights, whatever `portable.py`'s redirect map already governs | `model_cache/` (portable-mode path, already real) or the OS default HF/torch cache, depending on whether the installer turns portable mode on by default (see §5) | Never redownloaded on an app-files-only update; each model updates independently only when that specific model's own version changes | Separate opt-in prompt, default **No** — same discipline as `uninstall.bat`'s existing library-deletion default |
| **User data** | `library/` (dramas, translations, audio/video, the SQLite database, backups), Settings' saved config | Wherever `db.py`'s `LIBRARY_DIR` resolves to (see §5) | Never touched by an app-files or optional-component update | Separate opt-in prompt, default **No** — this is exactly `uninstall.bat`'s existing `DELETE_LIBRARY` prompt, carried over unchanged in spirit |

This is a direct extension of `uninstall.bat`'s already-working pattern
(app files always removed; library only removed on explicit separate
opt-in, default no) — not a new design. The only genuinely new piece is
splitting "app files" and "optional components" into two categories
instead of one, so a components-only update (e.g. bumping `torch` for a
new CUDA release) doesn't have to reship or re-verify the whole app.

---

## 5. What needs to change later to support this (not built now)

Concrete list for the follow-on implementation step:

1. **`db.py`'s `LIBRARY_DIR` needs to stop being hardcoded relative to
   `__file__`.** Installing to `C:\Program Files\Baihe Subtitler\` means
   the app directory is (by Windows convention, and often by actual ACLs)
   not meant to hold per-user, frequently-written data — a
   multi-GB-and-growing SQLite database and media library sitting under
   `Program Files` is the wrong place both by convention and, on a
   locked-down machine, potentially unwritable without admin rights. The
   installer path should default `LIBRARY_DIR` to somewhere under
   `%LOCALAPPDATA%\Baihe Subtitler\` (or let the installer set an
   environment variable / config value `db.py` reads at startup, using
   `set_library_dir()`, which already exists but is currently never
   called from any config source). This is the single most important
   pre-existing assumption this design breaks, and needs deciding before
   an installer is actually built — not a detail to discover mid-build.
2. **A config layer that distinguishes "installed by the installer" from
   "run from a dev checkout."** Today `start.bat` always assumes the app
   directory is both the code location and (via `db.py`'s default) the
   data location. An installed copy needs a small startup check (a marker
   file or an installer-written config, similar in spirit to the existing
   `PORTABLE` marker file pattern) so `app.py`/`db.py` know to resolve
   `LIBRARY_DIR` and `model_cache`'s equivalent from the installed,
   per-user location instead of next to the code.
3. **Portable mode's redirect map (`portable.py`) becomes the installed
   app's default model-storage mechanism, not just an opt-in.** An
   installed app's models shouldn't go to each library's own scattered
   default cache (`~/.cache/huggingface`, etc.) — they should land
   somewhere the uninstaller and the update mechanism can find as one
   unit, which is exactly what `portable.py`'s `MODEL_CACHE_DIR` redirect
   already does. The installer likely turns portable-style model
   redirection on by default (pointed at the per-user data location from
   item 1, not literally next to the app files in `Program Files`), reusing
   the existing `_REDIRECTS` dict rather than inventing a new one.
4. **The installer's post-install step needs to shell out to the bundled
   interpreter's own `pip`**, running effectively the same three-tier
   install `start.bat` already does today (`pip install -r
   requirements-core.txt [-c constraints.lock.txt]`, then
   media/optional per the chosen tier), just from the installer's own
   script instead of a batch file. `check_setup.py`'s plain-words
   ffmpeg/JS-runtime/CUDA check should run as part of this step too, so
   a missing system tool is caught during install, not on first launch.
5. **Model storage and update handling need a manifest**: which model
   files/weights belong to which optional component, and what version
   each one is currently at, so an update can compare "what's already
   downloaded and current" against "what the new installer package
   offers" and skip anything unchanged (§4's "updates preserve data by
   design" requirement). Nothing like this exists today — `portable.py`
   redirects *where* models land, but nothing currently tracks *which*
   model version is sitting there. This is new bookkeeping, not a reuse
   of an existing mechanism.
6. **Diagnostics' `OPTIONAL_DEPENDENCIES`/bulk-install/throwaway-venv
   machinery stays exactly as it is for the installed app** — an
   installed user still benefits from Diagnostics' existing per-package
   Install button and Step 66's upgrade-safety check for anything they
   add or change after initial install; nothing about the installer
   replaces that in-app machinery, it only replaces the very first
   bootstrap (`start.bat`'s job) with a proper installer for end users.
7. **`start.bat`/`start.ps1` and the dev `venv/` workflow are explicitly
   unchanged** — they remain the contributor path, addressed to
   `python`-on-PATH exactly as today (plus Step 79's stub-detection fix,
   separately), with no dependency on anything in this document. Two
   audiences, two entry points, sharing the same `requirements-*.txt`
   files and the same `app.py`/`diagnostics.py` runtime code.
8. **Uninstaller registration**: a real Windows uninstaller entry under
   Settings → Apps → Installed apps (an Inno Setup `[UninstallRun]`/
   registry-uninstall-key mechanism, not a bare `.bat` file the user has
   to find and run manually as today). Its actual deletion logic is a
   near-verbatim port of `uninstall.bat`'s existing category/prompt
   structure (§4's table), not a rewrite of the policy.

---

## 6. Distinct from Docker/containerization (M8-F)

This design is about a **native Windows installer/uninstaller** — a
different packaging axis from the separately-deferred Docker/
containerization question tracked in the roadmap's own §3 as M8-F.
Docker would isolate CUDA/PyTorch dependency conflicts inside a container,
but GPU passthrough into Docker containers on Windows (the actual target
platform for this app, per Step 10) is itself a known, separately-tracked
source of friction — a different trade-off from anything in this
document. M8-F stays deferred, unchanged by this design; this document's
findings shouldn't be read as a substitute for, or a merge into, that
separate question.

---

## 7. Confirmation against existing infrastructure (exit condition)

- **Reuses, doesn't replace, Step 75's tiered requirements** — §3's three
  installer tiers map directly onto `requirements-core/media/optional.txt`
  ; the installer's job is a UI in front of `pip install -r
  <existing file>`, not a new dependency list.
- **Reuses, doesn't replace, Step 62's bulk-install UI** — Diagnostics'
  in-app Install buttons and `INSTALLABLE_TIERS` keep working exactly as
  they do today for anything a user adds or changes after initial
  install; the installer only replaces the very first bootstrap.
- **Reuses, doesn't replace, Step 66's throwaway-venv pattern** — nothing
  about the installer needs its own upgrade-safety logic; an installed
  app still has Diagnostics' existing "will this upgrade break the app?"
  check available for post-install package changes.
- **Extends, doesn't invent, `uninstall.bat`'s data-preservation
  default** — §4's category table and §5 item 8 are a direct port of the
  existing app-files-always / library-opt-in-default-no pattern, split one
  category further (app files vs. optional components) than today's
  script does.

**No installer code, no packaging CI changes, and no `start.bat`/
`start.ps1` changes exist as part of this step** — implementation is a
later, separate step once this design is reviewed and the app's own
dependency/model layout (specifically §5 item 1's `LIBRARY_DIR` question)
has stabilized further.
