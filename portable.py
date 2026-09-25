"""
portable.py -- Step 10's "the whole app folder just works when copied
elsewhere" mode.

Off by default: model downloads (Whisper, pyannote, F5-TTS, the
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
not on every call -- app.py and cli.py both call this as their literal
first lines for exactly that reason. A module that imports one of them
at ITS OWN top level (audio_preprocess.py, torch-backed ASR/TTS/OCR
backends) must not be imported anywhere before this runs either.
"""
import os
import sys

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_MARKER_PATH = os.path.join(_APP_DIR, "PORTABLE")

# Everything this mode redirects lives under one folder, so "copy this
# whole app folder" really does carry every downloaded model with it.
MODEL_CACHE_DIR = os.path.join(_APP_DIR, "model_cache")

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


def activate_portable_mode() -> bool:
    """No-op, returning False, if portable mode isn't on. Otherwise
    creates MODEL_CACHE_DIR and points every redirect above at a
    subfolder of it -- via os.environ.setdefault, so a value the user
    already set explicitly (their own HF_HOME, say) is respected, never
    silently overridden. Returns True if portable mode is on, regardless
    of whether any given variable was already set."""
    if not is_portable():
        return False
    os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
    for env_var, subdir in _REDIRECTS.items():
        os.environ.setdefault(env_var, os.path.join(MODEL_CACHE_DIR, subdir))
    return True
