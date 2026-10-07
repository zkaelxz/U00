"""
portable.py -- "the whole app folder just works when copied
elsewhere" mode.

Off by default: model downloads (Whisper, pyannote, OmniVoice, the
audio-separator backend, ...) go to their libraries' own OS-standard
cache locations (~/.cache/huggingface, ~/.cache/audio-separator-models),
exactly as they would for any other Python tool using them -- unchanged
from before this step. db.py's own library/ folder was already relative
to the app's own directory (LIBRARY_DIR, set from __file__), so that part
of "portable" was already true; what wasn't is every model cache this
app's own code doesn't itself manage but still triggers downloads for.

On (see is_portable() for how it's turned on): every model cache this
app's own code points at is redirected inside a model_cache/ folder next
to this file instead, so the whole app folder -- library and downloaded
models included -- can be copied to a USB stick or a new machine and
just works there, AS LONG AS Python and the system tools this app
already needs (ffmpeg, a JS runtime, optionally CUDA/Ollama) are already
present on that machine too. This moves the app and its data, not the
need for those to already be installed -- see the README's own
"Portable mode" section for the real limit stated plainly, the same
caution the roadmap itself calls for.

Must be activated (activate_portable_mode()) before any other module in
this app is imported: huggingface_hub and torch each read their own
cache-location environment variable once, at their own first import,
not on every call -- api/__main__.py and cli.py both call this before
their other imports for exactly that reason. A module that imports one of
them at ITS OWN top level (audio_preprocess.py, torch-backed ASR/TTS/OCR
backends) must not be imported anywhere before this runs either.

Installed copies (the Windows installer) keep their data out of
the program folder: an INSTALLED marker file next to this one says so,
and its first line names the per-user data folder (default
%LOCALAPPDATA%\\Baihe Studio). data_dir() is where db.py puts library/,
settings_service.py puts .env, and -- for an installed copy --
activate_portable_mode() points the model caches (model_cache/). A
source checkout has no marker, so data_dir() stays this folder, exactly
as before. BAIHE_DATA_DIR overrides both.
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
_MARKER_PATH = os.path.join(APP_DIR, "PORTABLE")
_INSTALLED_MARKER_PATH = os.path.join(APP_DIR, "INSTALLED")
DATA_DIR_ENV = "BAIHE_DATA_DIR"
INSTALLED_DATA_DIR_NAME = "Baihe Studio"

# Everything this mode redirects lives under one folder, so "copy this
# whole app folder" really does carry every downloaded model with it.
MODEL_CACHE_DIR = os.path.join(APP_DIR, "model_cache")

# {env var this app's own code (or a library it calls) reads for a model
# cache location: the subfolder under MODEL_CACHE_DIR it gets redirected
# to}. HF_HOME is huggingface_hub's own (used by faster-whisper, pyannote,
# transformers-backed models); TORCH_HOME is torch.hub's; the last one is
# this app's own env var, read by audio_preprocess.py, for a cache
# location with no standard env var of its own.
_REDIRECTS = {
    "HF_HOME": "huggingface",
    "TORCH_HOME": "torch",
    "BAIHE_AUDIO_SEP_MODEL_DIR": "audio-separator-models",
}


def is_portable() -> bool:
    """True if any of: a PORTABLE marker file sits next to this one (what
    a portable copy of the app folder would carry with it -- see the
    README), --portable was passed on the command line, or
    BAIHE_PORTABLE=1 is set in the environment (what start.bat's own
    portable launch path sets, so the marker file doesn't have to be
    created by hand for that route)."""
    return (os.path.exists(_MARKER_PATH) or "--portable" in sys.argv
            or os.environ.get("BAIHE_PORTABLE", "").strip().lower() in ("1", "true", "yes"))


def is_installed() -> bool:
    """True only for a copy with the INSTALLED marker the Windows
    installer's install step writes. Nothing else counts (no guessing from
    the folder layout), so a source checkout is never taken for an install.
    An install whose marker is missing (an interrupted upgrade) is refused
    by installer/launcher.py until Setup is run again, so it never keeps its
    library in the program folder either."""
    return os.path.isfile(_INSTALLED_MARKER_PATH)


def default_installed_data_dir() -> str:
    """%LOCALAPPDATA%\\Baihe Studio: per-user, writable without admin
    rights, and separate from the program files an update replaces."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, INSTALLED_DATA_DIR_NAME)


def _marker_data_dir() -> str:
    """The data folder named on the INSTALLED marker's first non-blank,
    non-comment line, or "" if it names none (or can't be read). Only an
    absolute path counts, so a stray relative line can't point the
    library somewhere unexpected."""
    try:
        with open(_INSTALLED_MARKER_PATH, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                return line if os.path.isabs(line) else ""
    except OSError:
        pass
    return ""


def data_dir() -> str:
    """Where this copy keeps its user data (library/, .env and, when
    installed, model_cache/): BAIHE_DATA_DIR if set; else, for an
    installed copy, the folder its INSTALLED marker names (or the
    per-user default); else this app folder, as it always was."""
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        return os.path.abspath(override)
    if is_installed():
        return _marker_data_dir() or default_installed_data_dir()
    return APP_DIR


def activate_portable_mode() -> bool:
    """No-op, returning False, if neither portable mode is on nor this is
    an installed copy. Otherwise creates the model cache folder
    (MODEL_CACHE_DIR in portable mode, `<data_dir()>/model_cache` for an
    installed copy, so models land with the user's data rather than in
    scattered per-library caches the uninstaller can't find) and points
    every redirect above at a subfolder of it -- via
    os.environ.setdefault, so a value the user already set explicitly
    (their own HF_HOME, say) is respected, never silently overridden.
    Returns True if a redirect mode is on, regardless of whether any
    given variable was already set."""
    if is_portable():
        cache_dir = MODEL_CACHE_DIR
    elif is_installed():
        cache_dir = os.path.join(data_dir(), "model_cache")
    else:
        return False
    os.makedirs(cache_dir, exist_ok=True)
    for env_var, subdir in _REDIRECTS.items():
        os.environ.setdefault(env_var, os.path.join(cache_dir, subdir))
    return True
