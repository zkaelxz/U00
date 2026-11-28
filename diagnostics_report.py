"""
diagnostics_report.py -- which model engines are installed or reachable, and
the plain-text "copy diagnostics for support" report with its redaction.
"""

import contextlib
import contextvars
import getpass
import importlib.metadata
import ipaddress
import os
import platform
import re
from urllib.parse import urlsplit

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
    {"name": "Qwen3-ASR", "kind": "package", "package": "transformers", "min_version": "5.15",
     "url": "https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf",
     "help": "An alternative speech-to-text engine to Whisper, run by transformers 5.15 or "
             "newer. Models download from Hugging Face on first use: about 4.1 GB for 1.7B, "
             "1.6 GB for 0.6B and 1.8 GB for the forced aligner. Weights cached by the older "
             "qwen-asr package (Qwen/Qwen3-ASR-1.7B, ...) aren't reused, so the first run "
             "downloads them again."},
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
    {"name": "Nemotron-3-Diarization", "kind": "repo", "repo_ids": ["nvidia/Nemotron-3-Diarization"],
     "url": "https://huggingface.co/nvidia/Nemotron-3-Diarization",
     "help": "NVIDIA's speaker detection model (about 0.4 GB). When it is chosen in Settings but "
             "not downloaded, speaker detection falls back to pyannote."},
    {"name": "manga-ocr", "kind": "package", "package": "manga-ocr",
     "url": "https://github.com/kha-white/manga-ocr",
     "help": "Reads Japanese text out of manga page images (Scanlate's OCR step)."},
    {"name": "PaddleOCR", "kind": "package", "package": "paddleocr",
     "url": "https://github.com/PaddlePaddle/PaddleOCR",
     "help": "An alternate OCR backend for reading text out of manga/manhua page images."},
    {"name": "LaMa-manga", "kind": "repo", "repo_ids": ["mayocream/lama-manga"],
     "url": "https://huggingface.co/mayocream/lama-manga",
     "help": "The model Scanlate uses to erase the original text from a page (about 200 MB). "
             "Without it the text is erased with plain OpenCV, which can leave ghosts."},
    {"name": "audio-separator", "kind": "package", "package": "audio-separator",
     "url": "https://github.com/nomadkaraoke/python-audio-separator",
     "help": "Strips background music out of the audio track before transcription, so dialogue "
             "is easier to hear and transcribe."},
]


def get_model_engine_versions(ollama_model: str | None = None) -> list:
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
                # Still "installed" (the Update button fixes it), but the row
                # must not read as ready.
                if diagnostics.below_min_version(version, entry.get("min_version")):
                    version += f" (needs {entry['min_version']} or newer)"
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
                    "help": "The hosted Ollama model tag currently selected in Settings."})
    return out


# ---------------------------------------------------------------------------
# Proactive check for gated pyannote model access -- catches
# the exact real-world failure (a 403 on one gated model masking that the
# OTHER one is also gated, since load_pipeline() tries community-1 first
# and only surfaces 3.1's error) before it shows up as a runtime error on
# "Re-run speaker detection."
# ---------------------------------------------------------------------------

PYANNOTE_CHECK_TIMEOUT_S = 10  # a hung Hub must not pin the request thread


def check_pyannote_gated_access(hf_token: str | None = None, api=None) -> list:
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

def check_engine_reachable(engine_name: str, api_key: str | None = None, model: str | None = None,
                           base_url: str | None = None) -> dict:
    """{"engine", "ok", "error"} -- does a real, minimal translate call
    (zh -> en, this app's own best-supported direction per
    standalone_direction_support's docstring) and reports whether it
    succeeded. This DOES reach the network/a local server and DOES spend
    real quota on a paid engine -- one short line, not a batch -- so call
    it only from an explicit "Test" action or right before starting a
    job, never on every page load. Any error message is passed through
    shared.redact_secrets first, matching every other place
    in this app that shows an engine error."""
    from engine_backends import shared, engine_registry, standalone
    try:
        engine = engine_registry.get_engine(engine_name, api_key, model, base_url=base_url)
    except Exception as e:
        details = getattr(e, "details", None)
        if isinstance(details, dict) and details.get("reason") == "missing_package":
            # The exception's own message is the generic "See Diagnostics", which
            # is no help on the Diagnostics page itself.
            return {"engine": engine_name, "ok": False,
                    "error": f"The Python package {details['package']} is not installed."}
        return {"engine": engine_name, "ok": False,
                "error": shared.redact_secrets(str(e))}
    try:
        result = standalone.standalone_translate("你好", engine, "zh", "en")
        if not (result or "").strip():
            return {"engine": engine_name, "ok": False,
                    "error": "Reached the engine, but it returned an empty translation."}
        return {"engine": engine_name, "ok": True, "error": None}
    except Exception as e:
        return {"engine": engine_name, "ok": False,
                "error": shared.redact_secrets(str(e))}


# ---------------------------------------------------------------------------
# "Copy diagnostics for support" -- the existing key/token
# redaction (shared.redact_secrets) plus stripping local file
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
_PATH_LAST = (rf'((?:{_PATH_WORD}(?: {_PATH_WORD}){{0,4}} '
              rf'{_PATH_CHARS}+\.[A-Za-z0-9]{{1,5}}(?![A-Za-z0-9])|{_PATH_CHARS}+))')
PATH_PATTERN = re.compile(
    rf'(?:[A-Za-z]:)?[\\/]+(?:{_PATH_CHARS}+(?: {_PATH_CHARS}+)*[\\/]+)+' + _PATH_LAST)
# A path redact_for_support already cut to its last part (the support report).
_CUT_PATH = re.compile(r'\.\.\./' + _PATH_LAST)

# ANSI escape sequences (CSI: colours, cursor moves), e.g. yt-dlp's
# "\x1b[0;31mERROR:\x1b[0m" -- unreadable noise in a report or log view.
_ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


_URL_PATTERN = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
# Credential-shaped parameters of any length, wherever they sit (a query
# string, a header dump, JSON): token=, sig=, X-Amz-Signature=,
# X-Goog-Signature=, Key-Pair-Id=, Policy=, api_key=, refresh_token=,
# password=, ... redact_secrets only knows key formats, not these names. The
# name set follows sources/ai_extract._SENSITIVE_PARAM, without its need for
# a preceding ?/&. Group 1 is the name and "=".
CREDENTIAL_PARAM_PATTERN = re.compile(
    r"(?i)(?<![\w.\-])((?=x-amz-[\w.\-]|[\w.\-]*?(?:token|sig|auth|session|secret|credential|passw|key|jwt|"
    r"ticket|hmac|policy))[\w.\-]+=)[^&#\s\"'<>\\;,]*")

# A name counts only between non-alphanumerics: matching inside a word would
# turn a short name like "li" into "[USER]brary", while "_", "-", "@" and "."
# still end one ("bob@bob-laptop", "bob_notes.txt").
_SEGMENT_BEFORE = r"(?<![^\W_])"
_SEGMENT_AFTER = r"(?![^\W_])"
# A placeholder the path pattern can't match (no slash), so URLs survive it.
_URL_SLOT = "\x00URL{}\x00"


# Set inside reusing_account_names(): the names read once for a whole list.
_reused_names: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar(
    "reused_account_names", default=None)


@contextlib.contextmanager
def reusing_account_names():
    """Every redact_for_support inside the block uses one reading of the
    account names, for a caller redacting many rows at once (the job list).
    Scoped, not cached for the process: the names come from the environment
    and getpass, which may change between calls."""
    token = _reused_names.set(_account_names())
    try:
        yield
    finally:
        _reused_names.reset(token)


def _account_names() -> set:
    """The OS login name plus the profile folder names, which can differ
    from it (a renamed account keeps its old C:\\Users\\<name> folder)."""
    reused = _reused_names.get()
    if reused is not None:
        return reused
    names = set()
    try:
        names.add(getpass.getuser())
    except Exception:
        pass  # no login name (a service account with no USER/USERNAME set)
    for home in (os.path.expanduser("~"), os.environ.get("USERPROFILE", "")):
        names.add(re.split(r"[\\/]", (home or "").rstrip("\\/"))[-1])
    return {n for n in names if n and n not in (".", "~")}


def redact_for_support(text) -> str:
    """Same secret redaction the rest of the app already uses for stored
    errors (shared.redact_secrets), plus: signed-link query
    parameters, every URL cut to scheme, host and path, the OS username
    and profile folder name replaced with [USER], and every absolute
    filesystem path (POSIX or Windows) collapsed to just its last path
    segment prefixed with ".../" -- enough to stay readable without
    exposing the folder structure (or a username embedded in it)
    underneath. ANSI colour codes are stripped first."""
    from engine_backends import shared
    text = _ANSI_PATTERN.sub("", "" if text is None else str(text))
    # NUL marks the URL placeholders below, so one in the input could forge
    # one; callers run this inside except blocks, so it must never raise.
    text = text.replace("\x00", "")
    urls = []

    def _park(m):
        # Before redact_secrets: its "[REDACTED]" would end the URL match early
        # and leave the rest of the query string behind.
        url = shared.display_url(m.group(0))
        urls.append(shared.redact_secrets(url) or "[URL]")
        return _URL_SLOT.format(len(urls) - 1)

    text = _URL_PATTERN.sub(_park, text)
    text = shared.redact_secrets(text)
    text = CREDENTIAL_PARAM_PATTERN.sub(r"\1[REDACTED]", text)
    text = PATH_PATTERN.sub(lambda m: ".../" + m.group(1), text)
    text = re.sub("\x00URL(\\d{1,9})\x00",
                  lambda m: urls[int(m.group(1))] if int(m.group(1)) < len(urls) else "[URL]",
                  text)
    for name in sorted(_account_names(), key=len, reverse=True):
        text = re.sub(_SEGMENT_BEFORE + re.escape(name) + _SEGMENT_AFTER, "[USER]", text,
                      flags=re.IGNORECASE)
    return text


# Only for the Send logs bundle, which carries whole log files (third-party
# warnings included) rather than the error lines the rest of Diagnostics shows:
# those can hold sign-in traffic (cookie headers, a password typed into a
# source's sign-in form, a session JWT), account emails, title names in source
# links and addresses on the home network. Kept out of redact_for_support so
# job messages and install output shown elsewhere don't change.
#
# Any scheme, and anything up to whitespace or a quote: a "]" or ")" inside a
# query must not end the match and leave the rest of the query behind.
_BUNDLE_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,15}://[^\s\"'<>`]+")
_LAN_NAME = re.compile(r"(?i)^(?:localhost|.+\.(?:local|lan|home|internal|ts\.net|home\.arpa|localdomain|fritz\.box))$")
# A request path written without its scheme and host, as urllib3 does in
# "Max retries exceeded with url: /book/<slug>?page=2": the path is a title's
# slug as often as not, and the query its search words.
_BARE_REQUEST_PATH = re.compile(r"(?i)(\burl:\s*)[^\s\"'<>`]+|(?<![\w/.:~])/[^\s\"'<>`/]+(?:/[^\s\"'<>`/]*)*\?[^\s\"'<>`]*")
# Any query string left over ("login?password=...&next=..."): its values are what was asked for.
_BARE_QUERY = re.compile(r"\?[\w.%\[\]-]+=[^\s\"'<>`]*")
# host='nas' in a connection-pool error: a name with no dot is a PC on the network.
_DOTLESS_HOST_ARG = re.compile(r"(?i)(\bhost=['\"]?)([a-z0-9-]+)(?=['\"]?[,)\s])")
# The same private hosts written without a scheme ("172.20.9.5:11434", "nas.local").
_LAN_IN_TEXT = re.compile(
    r"(?i)(?<![\w.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
    r"|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|lan|home|internal|ts\.net|home\.arpa|localdomain|fritz\.box))(?![\w.])")
_BUNDLE_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?i)((?:set-)?cookie[\"']?\s*[:=]\s*)[^\r\n]*"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(passw(?:or)?d[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}&]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9_\-.=+/]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), "[REDACTED]"),
]


def _non_global_ip(host: str) -> bool:
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _url_host_only(m) -> str:
    try:
        parts = urlsplit(m.group(0))
        host = parts.hostname or ""
    except ValueError:
        return "[URL]"
    if not host:
        return "[URL]"
    if "." not in host or _LAN_NAME.match(host) or _non_global_ip(host):
        return "[LAN]"
    return f"{parts.scheme.lower()}://{host}"


def _terms_pattern(terms, min_length: int):
    """One case-insensitive alternation, longest first so a title that
    contains another is replaced whole. An ASCII letter or digit at either
    end must not touch another one, so a short title doesn't eat the middle
    of a word."""
    words = sorted({t.strip() for t in terms if t and len(t.strip()) >= min_length},
                   key=len, reverse=True)
    if not words:
        return None
    parts = [(r"(?<![A-Za-z0-9])" if w[0].isascii() and w[0].isalnum() else "")
             + re.escape(w)
             + (r"(?![A-Za-z0-9])" if w[-1].isascii() and w[-1].isalnum() else "")
             for w in words]
    return re.compile("|".join(parts), re.IGNORECASE)


def _path_extension(name: str) -> str:
    m = re.search(r"\.[A-Za-z0-9]{1,5}$", name)
    return m.group(0) if m else ""


def bundle_redactor(titles=(), secrets=(), hosts=()):
    """The Send logs scrubber: every URL cut to scheme and host (a private
    host to [LAN]), the exact configured secret values, the library's titles
    ([TITLE]), the configured endpoints' hosts ([LAN]), emails, cookie
    headers, passwords, bearer tokens and JWTs, every path down to [PATH] and
    its extension, then redact_for_support. URLs go first, while nothing has
    put a placeholder in one yet; emails before redact_for_support, so an
    email's user name is gone before account names become [USER] inside it."""
    secret_re = _terms_pattern(secrets, 8)
    title_re = _terms_pattern(titles, 2)
    pc_re = _terms_pattern([platform.node()], 2)
    host_re = _terms_pattern(hosts, 2)

    def redact(text) -> str:
        text = _ANSI_PATTERN.sub("", "" if text is None else str(text))
        text = _BUNDLE_URL.sub(_url_host_only, text)
        text = _BARE_REQUEST_PATH.sub(lambda m: (m.group(1) or "") + "[PATH]", text)
        text = _BARE_QUERY.sub("?[QUERY]", text)
        text = _DOTLESS_HOST_ARG.sub(lambda m: m.group(1) + "[LAN]", text)
        if pc_re:
            text = pc_re.sub("[PC]", text)
        if secret_re:
            text = secret_re.sub("[REDACTED]", text)
        if title_re:
            text = title_re.sub("[TITLE]", text)
        if host_re:
            text = host_re.sub("[LAN]", text)
        text = _LAN_IN_TEXT.sub("[LAN]", text)
        for pattern, repl in _BUNDLE_PATTERNS:
            text = pattern.sub(repl, text)
        # redact_for_support keeps a path's last part, which here is a media
        # file or a title's folder; the extension alone still tells what failed.
        text = PATH_PATTERN.sub(lambda m: "[PATH]" + _path_extension(m.group(1)), text)
        text = _CUT_PATH.sub(lambda m: "[PATH]" + _path_extension(m.group(1)), text)
        return redact_for_support(text)
    return redact


def format_diagnostics_report(results: dict, hf_cache: list | None = None,
                              model_versions: list | None = None) -> str:
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
