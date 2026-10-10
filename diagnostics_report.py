"""
diagnostics_report.py -- which model engines are installed or reachable, and
the plain-text "copy diagnostics for support" report with its redaction.
"""

import getpass
import importlib.metadata
import os
import re

import diagnostics
import diarize
import storage

# ---------------------------------------------------------------------------
# Model/engine version panel -- one row per AI model/engine
# actually wired into the app today (not the roadmap's full aspirational
# list; several named there aren't implemented yet and belong to later steps). No network call: this only
# reports what pip already knows is installed locally.
# ---------------------------------------------------------------------------

MODEL_ENGINE_REGISTRY = [
    {"name": "Whisper (faster-whisper)", "kind": "package", "package": "faster-whisper",
     "url": "https://github.com/SYSTRAN/faster-whisper",
     "help": "The default speech-to-text engine used to transcribe dialogue when you start a "
             "new drama."},
    {"name": "Qwen3-ASR", "kind": "package", "package": "qwen-asr",
     "url": "https://github.com/QwenLM/Qwen3-ASR",
     "help": "An alternative speech-to-text engine to Whisper, used for transcription when "
             "selected in Settings."},
    {"name": "SenseVoice (FunASR)", "kind": "package", "package": "funasr",
     "url": "https://github.com/modelscope/FunASR",
     "help": "An alternate transcription engine that also tags emotion and non-speech sounds "
             "(laughing, sighing, etc.) in the audio."},
    {"name": "pyannote.audio", "kind": "package", "package": "pyannote.audio",
     "url": "https://github.com/pyannote/pyannote-audio",
     "help": "Figures out who's speaking and when, so lines can be split and labeled by speaker "
             "(speaker diarization)."},
    {"name": "pyannote diarization model", "kind": "repo",
     "repo_ids": diarize.DIARIZATION_MODELS,
     "url": "https://huggingface.co/pyannote/speaker-diarization-community-1",
     "help": "The actual model weights pyannote.audio uses to tell speakers apart -- a gated "
             "Hugging Face download, not a pip package."},
    {"name": "manga-ocr", "kind": "package", "package": "manga-ocr",
     "url": "https://github.com/kha-white/manga-ocr",
     "help": "Reads Japanese text out of manga page images (Scanlate's OCR step)."},
    {"name": "PaddleOCR", "kind": "package", "package": "paddleocr",
     "url": "https://github.com/PaddlePaddle/PaddleOCR",
     "help": "An alternate OCR backend for reading text out of manga/manhua page images."},
    {"name": "audio-separator", "kind": "package", "package": "audio-separator",
     "url": "https://github.com/nomadkaraoke/python-audio-separator",
     "help": "Strips background music out of the audio track before transcription, so dialogue "
             "is easier to hear and transcribe."},
    {"name": "Demucs", "kind": "package", "package": "demucs",
     "url": "https://github.com/facebookresearch/demucs",
     "help": "A fallback background-music remover, used when audio-separator isn't installed."},
    {"name": "OmniVoice", "kind": "package", "package": "omnivoice",
     "url": "https://github.com/k2-fsa/OmniVoice",
     "help": "A local voice-cloning engine that can also design a new voice from a text "
             "description, not just clone an existing sample."},
]


def get_model_engine_versions(ollama_model: str = None) -> list:
    """[{"name", "version", "url", "installed", "package", "help"}, ...],
    one row per MODEL_ENGINE_REGISTRY entry plus the active Ollama tag if
    given. A "package" entry's version comes from importlib.metadata (no
    import of the package itself, so no heavy ML import-time cost just to
    check a version) -- "not installed" if it isn't present. A "repo"
    entry (a bare model checkpoint this app's own code names directly, not
    a pip-versioned package) shows its Hugging Face repo id(s) as its
    identifier instead of a version number and has no real "not
    installed" state of its own, so it counts as installed. "installed" is a real boolean computed here
    from the actual check, not a string match against "not installed" in
    whatever renders it (that match would silently break
    if this literal ever changed). Makes no network call. "package" is the real pip/importlib.metadata distribution name for a
    "package" kind entry, None otherwise -- the exact string a caller
    should pass to the install route for that
    row's own Install button, straight from the registry rather than
    re-derived by matching against OPTIONAL_DEPENDENCIES's own keys (those
    use import-style names -- "faster_whisper", "manga_ocr" -- that don't
    all match the real pip names here, and some registry packages have no
    OPTIONAL_DEPENDENCIES entry at all).
    "help" is a short plain-English description of what the row is and
    which app feature uses it, for a "?" affordance in the UI."""
    out = []
    for entry in MODEL_ENGINE_REGISTRY:
        if entry["kind"] == "repo":
            version = ", ".join(entry["repo_ids"])
            installed = True
        else:
            try:
                version = importlib.metadata.version(entry["package"])
                installed = True
            except importlib.metadata.PackageNotFoundError:
                version = "not installed"
                installed = False
        out.append({"name": entry["name"], "version": version, "url": entry["url"],
                    "installed": installed, "package": entry.get("package"),
                    "help": entry.get("help", "")})
    if ollama_model:
        out.append({"name": "Ollama (active tag)", "version": ollama_model,
                    "url": "https://ollama.com/library", "installed": True,
                    "package": None,
                    "help": "The local Ollama model tag currently selected in Settings for "
                            "free local translation."})
    return out


# ---------------------------------------------------------------------------
# Proactive check for gated pyannote model access -- catches
# the exact real-world failure (a 403 on one gated model masking that the
# OTHER one is also gated, since load_pipeline() tries community-1 first
# and only surfaces 3.1's error) before it shows up as a runtime error on
# "Re-run speaker detection."
# ---------------------------------------------------------------------------

PYANNOTE_CHECK_TIMEOUT_S = 10  # a hung Hub must not pin the request thread


def check_pyannote_gated_access(hf_token: str = None, api=None) -> list:
    """[{"model", "accessible", "error"}, ...] for every entry in
    diarize.DIARIZATION_MODELS. This DOES reach the network (a lightweight
    HfApi().model_info() call per model) -- unlike everything else in this
    module, so call it only from an explicit button, never on every
    Diagnostics page load. [] if huggingface_hub isn't installed.
    api: injected HfApi-shaped object for tests; a real HfApi() otherwise."""
    if api is None:
        try:
            from huggingface_hub import HfApi
        except ImportError:
            return []
        api = HfApi()
    results = []
    for model in diarize.DIARIZATION_MODELS:
        try:
            api.model_info(model, token=hf_token or None, timeout=PYANNOTE_CHECK_TIMEOUT_S)
            results.append({"model": model, "accessible": True, "error": None})
        except Exception as e:
            results.append({"model": model, "accessible": False, "error": str(e)})
    return results


# ---------------------------------------------------------------------------
# Pre-flight a translation engine's credentials/reachability
# before a batch job starts, rather than discovering a dead API key or
# an unreachable local server only after committing lines to a job.
# ---------------------------------------------------------------------------

def check_engine_reachable(engine_name: str, api_key: str = None, model: str = None,
                           base_url: str = None) -> dict:
    """{"engine", "ok", "error"} -- does a real, minimal translate call
    (zh -> en, this app's own best-supported direction per
    standalone_direction_support's docstring) and reports whether it
    succeeded. This DOES reach the network/a local server and DOES spend
    real quota on a paid engine -- one short line, not a batch -- so call
    it only from an explicit "Test" action or right before starting a
    job, never on every page load. Any error message is passed through
    translate_engines.redact_secrets first, matching every other place
    in this app that shows an engine error."""
    import translate_engines
    try:
        engine = translate_engines.get_engine(engine_name, api_key, model, base_url=base_url)
    except Exception as e:
        return {"engine": engine_name, "ok": False,
                "error": translate_engines.redact_secrets(str(e))}
    try:
        result = translate_engines.standalone_translate("你好", engine, "zh", "en")
        if not (result or "").strip():
            return {"engine": engine_name, "ok": False,
                    "error": "Reached the engine, but it returned an empty translation."}
        return {"engine": engine_name, "ok": True, "error": None}
    except Exception as e:
        return {"engine": engine_name, "ok": False,
                "error": translate_engines.redact_secrets(str(e))}


# ---------------------------------------------------------------------------
# "Copy diagnostics for support" -- the existing key/token
# redaction (translate_engines.redact_secrets) plus stripping local file
# paths and the OS username, since a raw library path or a home directory
# can leak the machine's username into a support conversation.
# ---------------------------------------------------------------------------

# Middle segments may contain single spaces ("My Documents") so folder-name
# fragments aren't left behind; they can't start or end with one, which keeps
# a path from swallowing the prose around it. The filename may too, but only
# when it ends in a short extension ("my file name.wav"): without that anchor
# there is no telling where the name stops and the sentence resumes. Words
# before the final one can't themselves end in an extension or a comma, so
# "b.wav because ... see c.txt" stops at b.wav, and the word cap bounds how
# far a spaced name can reach.
_PATH_CHARS = r'[^\s\\/:*?"<>|]'
_PATH_WORD = (rf'(?!{_PATH_CHARS}*(?:\.[A-Za-z0-9]{{1,5}}|[,;])(?:\s|$))'
              rf'{_PATH_CHARS}+')
PATH_PATTERN = re.compile(
    rf'(?:[A-Za-z]:)?[\\/]+(?:{_PATH_CHARS}+(?: {_PATH_CHARS}+)*[\\/]+)+'
    rf'((?:{_PATH_WORD}(?: {_PATH_WORD}){{0,4}} '
    rf'{_PATH_CHARS}+\.[A-Za-z0-9]{{1,5}}(?![A-Za-z0-9])|{_PATH_CHARS}+))')

# ANSI escape sequences (CSI: colours, cursor moves), e.g. yt-dlp's
# "\x1b[0;31mERROR:\x1b[0m" -- unreadable noise in a report or log view.
_ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


_URL_PATTERN = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
# Signed-link parameters that also turn up outside a URL (a header dump, a
# query string logged on its own); redact_secrets doesn't know these names.
_SIGNED_PARAM_PATTERN = re.compile(
    r"(?<![\w-])(X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|sig|token|access_token)"
    r"=[^\s&\"'<>]+", re.IGNORECASE)
# A name counts only as a whole path segment or word: matching inside words
# would turn a short name like "li" into "[USER]brary".
_SEGMENT_BEFORE = r"(?<![^\s\\/\"'=:(\[])"
_SEGMENT_AFTER = r"(?![^\s\\/\"'.,;:)\]])"
# A placeholder the path pattern can't match (no slash), so URLs survive it.
_URL_SLOT = "\x00URL{}\x00"


def _account_names() -> set:
    """The OS login name plus the profile folder names, which can differ
    from it (a renamed account keeps its old C:\\Users\\<name> folder)."""
    names = set()
    try:
        names.add(getpass.getuser())
    except Exception:
        pass  # no login name (a service account with no USER/USERNAME set)
    for home in (os.path.expanduser("~"), os.environ.get("USERPROFILE", "")):
        names.add(re.split(r"[\\/]", (home or "").rstrip("\\/"))[-1])
    return {n for n in names if n and n not in (".", "~")}


def redact_for_support(text: str) -> str:
    """Same secret redaction the rest of the app already uses for stored
    errors (translate_engines.redact_secrets), plus: signed-link query
    parameters, every URL cut to scheme, host and path, the OS username
    and profile folder name replaced with [USER], and every absolute
    filesystem path (POSIX or Windows) collapsed to just its last path
    segment prefixed with ".../" -- enough to stay readable without
    exposing the folder structure (or a username embedded in it)
    underneath. ANSI colour codes are stripped first."""
    import translate_engines
    text = _ANSI_PATTERN.sub("", text or "")
    urls = []

    def _park(m):
        # Before redact_secrets: its "[REDACTED]" would end the URL match early
        # and leave the rest of the query string behind.
        url = translate_engines.display_url(m.group(0))
        urls.append(translate_engines.redact_secrets(url) or "[URL]")
        return _URL_SLOT.format(len(urls) - 1)

    text = _URL_PATTERN.sub(_park, text)
    text = translate_engines.redact_secrets(text)
    text = _SIGNED_PARAM_PATTERN.sub(lambda m: m.group(1) + "=[REDACTED]", text)
    text = PATH_PATTERN.sub(lambda m: ".../" + m.group(1), text)
    text = re.sub("\x00URL(\\d+)\x00", lambda m: urls[int(m.group(1))], text)
    for name in sorted(_account_names(), key=len, reverse=True):
        text = re.sub(_SEGMENT_BEFORE + re.escape(name) + _SEGMENT_AFTER, "[USER]", text,
                      flags=re.IGNORECASE)
    return text


def format_diagnostics_report(results: dict, hf_cache: list = None,
                              model_versions: list = None) -> str:
    """Plain-text "copy diagnostics for support" report -- built from the
    same `results` dict the Diagnostics page already renders, so nothing
    here re-checks anything run_full_diagnostics doesn't already check.
    Pass through redact_for_support() before showing/copying it."""
    lines = []
    py = results.get("python") or {}
    lines.append(f"Python: {py.get('version', '?')}")
    ff = results.get("ffmpeg") or {}
    lines.append("ffmpeg: " + ("found" if ff.get("found") else "MISSING")
                 + (f" ({ff['version']})" if ff.get("version") else ""))
    js = results.get("js_runtime") or {}
    lines.append(f"JS runtime: {js.get('name') or 'MISSING'}")
    lines.append(f"Library writable: {results.get('library_writable')}")
    api_keys = results.get("api_keys") or {}
    set_keys = [k for k, v in api_keys.items() if v]
    lines.append("API keys set: " + (", ".join(set_keys) if set_keys else "none"))
    deps = results.get("dependencies") or {}
    missing = sorted(k for k, v in deps.items() if not v.get("installed"))
    lines.append("Missing dependencies: " + (", ".join(missing) if missing else "none"))
    files = results.get("files") or {}
    if not files.get("all_present", True):
        lines.append("Missing files: " + ", ".join(
            (files.get("missing_top_level") or [])))
    if hf_cache is not None:
        total = sum(e["size_bytes"] for e in hf_cache)
        lines.append(f"Hugging Face cache: {len(hf_cache)} revision(s), "
                     f"{storage.format_bytes(total)} total")
    if model_versions is not None:
        lines.append("Model/engine versions:")
        for m in model_versions:
            lines.append(f"  - {m['name']}: {m['version']}")
    return "\n".join(lines)


def run_full_diagnostics(project_root: str, library_dir: str, api_keys_set: dict):
    """api_keys_set: dict like {"claude": bool, "deepseek": bool, ...}
    -- pass whatever keys are currently set."""
    # Called through the module (not imported by name) so tests that patch
    # diagnostics.check_* still reach the report.
    return {
        "python": diagnostics.check_python_version(),
        "ffmpeg": diagnostics.check_ffmpeg(),
        "js_runtime": diagnostics.check_js_runtime(),
        "dependencies": diagnostics.check_all_dependencies(),
        "files": diagnostics.check_file_completeness(project_root),
        "library_writable": diagnostics.check_library_writable(library_dir),
        "api_keys": api_keys_set,
    }
