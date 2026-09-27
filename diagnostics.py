"""
diagnostics.py -- environment self-check: which optional dependencies
are actually installed, whether ffmpeg is on PATH, whether every
expected project file is present, and which API keys are configured.

Built directly in response to a real support issue during setup: a
missing file produced a cryptic ModuleNotFoundError that took several
back-and-forth messages to diagnose. This turns that into one glance.
"""

import getpass
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys

import diarize
import storage

# Every top-level .py file and tabs/*.py file expected to exist for the
# app to run. Kept as an explicit list (not auto-discovered) so a
# missing file shows up as "missing" rather than just not being checked.
EXPECTED_TOP_LEVEL_FILES = [
    "app.py", "common.py", "core.py", "db.py", "translate_engines.py",
    "diarize.py", "dub.py", "video_export.py", "ocr.py", "segment.py",
    "dictionary.py", "reader.py", "scanlate.py", "metadata_lookup.py",
    "known_sites.py", "title_library.py", "vocab_export.py",
    "qa.py", "bulk_import.py", "epub_io.py", "cli.py", "diagnostics.py",
    "export_package.py", "run_tests.py", "translation_guide.py",
    "story_context.py", "storage.py", "universe_wiki.py", "background_jobs.py",
    "adaptive_style.py", "line_tools.py", "debug_view.py", "emotion.py", "ui_theme.py", "page_fetch.py",
    "page_server.py",
    "forced_align.py", "asr_backend.py", "asr_benchmark.py", "video_download.py",
    "app_help.py",
    # Step 25d item 9: this list had drifted -- these were all real,
    # hard-imported modules missing from it, which meant the missing-file
    # health check below could no longer actually catch one of them going
    # missing.
    "applog.py", "audio_preprocess.py", "auto_qc.py", "benchmark.py",
    "bulk_translate.py", "check_setup.py", "hardsub_ocr.py", "live_translate.py",
    "navigator.py", "portable.py", "raw_transcript.py", "resegment.py",
    "sensevoice_tags.py", "subtitle_formats.py", "voice_id.py", "word_align.py",
    "translation_memory.py",
]
EXPECTED_TABS_FILES = [
    "__init__.py", "settings_tab.py", "library_tab.py", "workspace_tab.py",
    "reader_tab.py", "scanlate_tab.py", "discover_tab.py",
    "diagnostics_tab.py",
    # Step 25d item 9: same drift as EXPECTED_TOP_LEVEL_FILES above.
    "live_tab.py", "sources_tab.py", "translate_tab.py",
]

# name -> (import name, feature it powers, required vs optional)
OPTIONAL_DEPENDENCIES = {
    "streamlit": ("streamlit", "the GUI itself", "required"),
    "pandas": ("pandas", "Library tab tables", "required"),
    "faster_whisper": ("faster_whisper", "audio alignment/timing", "required"),
    "cv2": ("cv2", "Scanlate bubble detection/inpainting", "required"),
    "anthropic": ("anthropic", "Claude translation engine", "engine"),
    "openai": ("openai", "DeepSeek translation engine", "engine"),
    "deepl": ("deepl", "DeepL translation engine", "engine"),
    "requests": ("requests", "Google/LibreTranslate/metadata lookup/navigator", "engine"),
    "bs4": ("bs4", "metadata lookup, navigator, bulk import", "feature"),
    "pyannote.audio": ("pyannote.audio", "speaker diarization", "feature"),
    "soundfile": ("soundfile", "speaker diarization, vocal separation chunking, word-level realignment", "feature"),
    "edge_tts": ("edge_tts", "free online dubbing", "feature"),
    "pydub": ("pydub", "dub/narration track mixing", "feature"),
    "f5_tts": ("f5_tts", "local voice cloning", "feature"),
    # Keys are the real pip names -- Diagnostics' Install button runs
    # `pip install <key>`. These three can't share one environment (see
    # requirements.txt), which the descriptions say before anyone clicks.
    "omnivoice": ("omnivoice", "local voice cloning + voice design (OmniVoice; can't share an "
                               "install with Chatterbox/TADA)", "feature"),
    "chatterbox-tts": ("chatterbox", "emotion-aware local voice (Chatterbox; adds a PerTh "
                                     "watermark; can't share an install with OmniVoice/TADA)",
                       "feature"),
    "hume-tada": ("tada", "long-narration local voice (TADA; model weights under the Llama 3.2 "
                          "Community License; can't share an install with OmniVoice/Chatterbox)",
                  "feature"),
    "pytesseract": ("pytesseract", "OCR (Tesseract backend)", "feature"),
    "PIL": ("PIL", "OCR, Scanlate rendering", "required"),
    "paddleocr": ("paddleocr", "OCR (PaddleOCR backend)", "feature"),
    "manga_ocr": ("manga_ocr", "OCR (Japanese manga backend)", "feature"),
    "piper": ("piper", "offline TTS", "feature"),
    "jieba": ("jieba", "Chinese word segmentation (Reader, meaning-based line re-segmentation)", "feature"),
    "pypinyin": ("pypinyin", "Chinese pinyin (Reader)", "feature"),
    "sudachipy": ("sudachipy", "Japanese word segmentation (Reader, meaning-based line re-segmentation)", "feature"),
    "pykakasi": ("pykakasi", "Japanese furigana (Reader)", "feature"),
    "kiwipiepy": ("kiwipiepy", "Korean word segmentation (Reader)", "feature"),
    "transformers": ("transformers", "local NLLB-200 translation engine, ML bubble detection "
                                     "(Scanlate), PaddleOCR-VL-For-Manga", "feature"),
    "torch": ("torch", "ML bubble detection/inpainting (Scanlate), PaddleOCR-VL-For-Manga, "
                        "word-level realignment, several TTS/ASR backends", "feature"),
    "safetensors": ("safetensors", "ML inpainting (Scanlate, LaMa-manga checkpoint)", "feature"),
    "huggingface_hub": ("huggingface_hub", "ML bubble detection/inpainting, voice cloning model downloads",
                        "feature"),
    "pypdf": ("pypdf", "Scanlate PDF import (splitting a PDF into pages)", "feature"),
    "streamlit_drawable_canvas": ("streamlit_drawable_canvas",
                                  "Scanlate manual erase/heal brush -- confirmed incompatible "
                                  "with this app's pinned streamlit>=1.49 as of this check "
                                  "(fails at setup, not just missing)", "feature"),
    "genanki": ("genanki", "Anki .apkg export (Reader vocab)", "feature"),
    "ebooklib": ("ebooklib", "EPUB import/export", "feature"),
    "plyer": ("plyer", "desktop notification when a background job finishes (Settings toggle, "
                       "off by default)", "feature"),
    "playwright": ("playwright", "reading JavaScript-rendered sites (baihehub, Fanjiao; the "
                                 "Sources tab's browser tier)", "feature"),
    "trafilatura": ("trafilatura", "Sources tab: pulling a novel chapter's main text out of a "
                                   "pasted URL (falls back to a simpler built-in extractor)",
                    "feature"),
    "audio-separator": ("audio_separator",
                        "background-music removal before transcription (Mel-Band RoFormer; "
                        "falls back to Demucs)", "feature"),
    "funasr": ("funasr", "audio emotion & sound tags (SenseVoice; model weights under the "
                         "FunASR Model Open Source License)", "feature"),
    "demucs": ("demucs", "background-music removal before transcription (fallback)", "feature"),
    "cryptography": ("cryptography", "mangaz.com adapter's session-scoped RSA+AES page "
                                     "decryption (Sources tab)", "feature"),
    "pytest": ("pytest", "running the test suite", "dev"),
}


def check_python_version():
    import sys
    v = sys.version_info
    return {"version": f"{v.major}.{v.minor}.{v.micro}", "ok": v.major == 3 and v.minor >= 9}


def check_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        return {"found": False, "path": None, "version": None}
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        version_line = result.stdout.splitlines()[0] if result.stdout else "unknown version"
        return {"found": True, "path": path, "version": version_line}
    except Exception:
        return {"found": True, "path": path, "version": "found but version check failed"}


# In yt-dlp's own preference order -- Deno is its default; the others
# also work when that's what's actually installed.
JS_RUNTIME_CANDIDATES = ["deno", "node", "bun", "quickjs"]


def check_js_runtime():
    """Since late 2025, YouTube downloads through yt-dlp need one of
    these on PATH (its EJS system) or formats silently go missing --
    see video_download.py/live_translate.py's own js_runtimes option."""
    for name in JS_RUNTIME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return {"found": True, "name": name, "path": path}
    return {"found": False, "name": None, "path": None}


def check_cuda() -> dict:
    """Whether a GPU is actually usable, for start.bat's own "print
    anything missing in plain words" launcher check (Step 10) -- this is
    deliberately the minimal "is it there at all" answer, not the
    driver/CUDA-build version-mismatch detail Step 18 adds to the
    in-app GPU/VRAM display; that's a different, later check built for a
    different place (the Diagnostics tab, checked once you're already in
    the app), not a launcher-time one. Doesn't import torch at all if
    it isn't installed -- CPU-only is a fully supported, if slower, way
    to run this app, not something to warn about."""
    if not check_dependency("torch"):
        return {"torch_installed": False, "cuda_available": None}
    try:
        import torch
        return {"torch_installed": True, "cuda_available": bool(torch.cuda.is_available())}
    except Exception:
        # An installed-but-broken torch (a real, if rare, possibility --
        # e.g. a CUDA build with no matching driver at all) shouldn't
        # crash the launcher's own check; just report what's known.
        return {"torch_installed": True, "cuda_available": None}


def check_dependency(module_name: str) -> bool:
    """Checks importability without actually importing (avoids side
    effects and is faster for modules with heavy import-time work,
    like torch-backed packages)."""
    try:
        # dotted names (e.g. pyannote.audio) need the parent importable too
        parts = module_name.split(".")
        spec = importlib.util.find_spec(parts[0])
        if spec is None:
            return False
        if len(parts) > 1:
            spec = importlib.util.find_spec(module_name)
            return spec is not None
        return True
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def check_all_dependencies():
    results = {}
    for label, (import_name, feature, tier) in OPTIONAL_DEPENDENCIES.items():
        results[label] = {
            "installed": check_dependency(import_name),
            "powers": feature,
            "tier": tier,
        }
    return results


def check_file_completeness(project_root: str):
    missing_top_level = [f for f in EXPECTED_TOP_LEVEL_FILES
                          if not os.path.exists(os.path.join(project_root, f))]
    tabs_dir = os.path.join(project_root, "tabs")
    if not os.path.isdir(tabs_dir):
        missing_tabs = list(EXPECTED_TABS_FILES)
    else:
        missing_tabs = [f for f in EXPECTED_TABS_FILES
                         if not os.path.exists(os.path.join(tabs_dir, f))]
    return {
        "missing_top_level": missing_top_level,
        "missing_tabs": missing_tabs,
        "all_present": not missing_top_level and not missing_tabs,
    }


def check_library_writable(library_dir: str):
    try:
        os.makedirs(library_dir, exist_ok=True)
        test_file = os.path.join(library_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Step 9b.2: Hugging Face model-cache visibility & cleanup.
#
# Whisper/pyannote/Qwen3-ASR/ForcedAligner/F5-TTS weights live in
# huggingface_hub's own cache (~/.cache/huggingface by default), entirely
# separate from storage.py's own accounting of this app's `library/`
# folder -- across several backends this can reach tens of GB with no
# visibility from inside the app.
# ---------------------------------------------------------------------------

def scan_hf_cache(cache_dir: str = None) -> list:
    """[{"repo_id", "repo_type", "revision", "size_bytes"}, ...] for every
    cached model revision, largest first. [] if huggingface_hub isn't
    installed or there's no cache yet -- never raises, since this runs on
    every Diagnostics load and a missing/corrupt cache shouldn't break
    the rest of the page."""
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return []
    try:
        info = scan_cache_dir(cache_dir) if cache_dir else scan_cache_dir()
    except Exception:
        return []
    entries = [
        {"repo_id": repo.repo_id, "repo_type": repo.repo_type,
         "revision": rev.commit_hash, "size_bytes": rev.size_on_disk}
        for repo in info.repos for rev in repo.revisions
    ]
    return sorted(entries, key=lambda e: -e["size_bytes"])


def delete_hf_cache_revision(revision: str, cache_dir: str = None) -> bool:
    """Deletes one cached revision by its commit hash (a thin wrapper
    over huggingface_hub's own delete strategy, which handles the
    blob/symlink bookkeeping). False, not raised, if huggingface_hub
    isn't installed or the delete fails for any reason."""
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return False
    try:
        info = scan_cache_dir(cache_dir) if cache_dir else scan_cache_dir()
        strategy = info.delete_revisions(revision)
        if strategy.expected_freed_size <= 0:
            # huggingface_hub treats an unknown revision as a silent
            # no-op (a logged warning, nothing raised) -- checked here so
            # a caller can tell "deleted" from "nothing matched" instead
            # of reporting success either way.
            return False
        strategy.execute()
        return True
    except Exception:
        return False


def scan_piper_voices(voices_dir: str = None) -> list:
    """[{"voice", "size_bytes"}, ...] for every downloaded Piper voice
    model, largest first. Step 25d item 14: this panel only ever scanned
    the Hugging Face model cache above -- Piper voices (Step 25c item 1's
    offline-voice picker) download to `library/piper_voices` instead, so
    they were invisible here and to whatever cleanup/disk-usage view
    relies on this. [] if the directory doesn't exist yet -- never
    raises, same reasoning as scan_hf_cache above."""
    if voices_dir is None:
        import dub
        voices_dir = dub.piper_voices_dir()
    try:
        if not os.path.isdir(voices_dir):
            return []
        entries = []
        for fname in os.listdir(voices_dir):
            if not fname.endswith(".onnx"):
                continue
            onnx_path = os.path.join(voices_dir, fname)
            size = os.path.getsize(onnx_path)
            json_path = onnx_path + ".json"
            if os.path.exists(json_path):
                size += os.path.getsize(json_path)
            entries.append({"voice": fname[:-len(".onnx")], "size_bytes": size})
        return sorted(entries, key=lambda e: -e["size_bytes"])
    except OSError:
        return []


def delete_piper_voice(voice: str, voices_dir: str = None) -> bool:
    """Deletes one downloaded Piper voice's .onnx + .onnx.json. False,
    not raised, if neither file exists or the delete fails for any
    reason (permissions, a file already gone)."""
    if voices_dir is None:
        import dub
        voices_dir = dub.piper_voices_dir()
    onnx_path = os.path.join(voices_dir, f"{voice}.onnx")
    json_path = onnx_path + ".json"
    try:
        deleted = False
        for path in (onnx_path, json_path):
            if os.path.exists(path):
                os.remove(path)
                deleted = True
        return deleted
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Step 9b.2: model/engine version panel -- one row per AI model/engine
# actually wired into the app today (not the roadmap's full aspirational
# list; several named there, like PaddleOCR-VL-For-Manga, aren't
# implemented yet and belong to later steps). No network call: this only
# reports what pip already knows is installed locally.
# ---------------------------------------------------------------------------

MODEL_ENGINE_REGISTRY = [
    {"name": "Whisper (faster-whisper)", "kind": "package", "package": "faster-whisper",
     "url": "https://github.com/SYSTRAN/faster-whisper"},
    {"name": "Qwen3-ASR", "kind": "package", "package": "qwen-asr",
     "url": "https://github.com/QwenLM/Qwen3-ASR"},
    {"name": "SenseVoice (FunASR)", "kind": "package", "package": "funasr",
     "url": "https://github.com/modelscope/FunASR"},
    {"name": "pyannote.audio", "kind": "package", "package": "pyannote.audio",
     "url": "https://github.com/pyannote/pyannote-audio"},
    {"name": "pyannote diarization model", "kind": "repo",
     "repo_ids": diarize.DIARIZATION_MODELS,
     "url": "https://huggingface.co/pyannote/speaker-diarization-community-1"},
    {"name": "manga-ocr", "kind": "package", "package": "manga-ocr",
     "url": "https://github.com/kha-white/manga-ocr"},
    {"name": "PaddleOCR", "kind": "package", "package": "paddleocr",
     "url": "https://github.com/PaddlePaddle/PaddleOCR"},
    {"name": "audio-separator", "kind": "package", "package": "audio-separator",
     "url": "https://github.com/nomadkaraoke/python-audio-separator"},
    {"name": "Demucs", "kind": "package", "package": "demucs",
     "url": "https://github.com/facebookresearch/demucs"},
    {"name": "F5-TTS", "kind": "package", "package": "f5-tts",
     "url": "https://github.com/SWivid/F5-TTS"},
    {"name": "OmniVoice", "kind": "package", "package": "omnivoice",
     "url": "https://github.com/k2-fsa/OmniVoice"},
    {"name": "GPT-SoVITS", "kind": "service",
     "note": "separate local server (not pip-installed)",
     "url": "https://github.com/RVC-Boss/GPT-SoVITS"},
    {"name": "Chatterbox", "kind": "package", "package": "chatterbox-tts",
     "url": "https://github.com/resemble-ai/chatterbox"},
    {"name": "TADA", "kind": "package", "package": "hume-tada",
     "url": "https://github.com/HumeAI/tada"},
    {"name": "edge-tts", "kind": "package", "package": "edge-tts",
     "url": "https://github.com/rany2/edge-tts"},
]


def get_model_engine_versions(ollama_model: str = None) -> list:
    """[{"name", "version", "url", "installed"}, ...], one row per
    MODEL_ENGINE_REGISTRY entry plus the active Ollama tag if given. A
    "package" entry's version comes from importlib.metadata (no import of
    the package itself, so no heavy ML import-time cost just to check a
    version) -- "not installed" if it isn't present. A "repo" entry (a bare
    model checkpoint this app's own code names directly, not a
    pip-versioned package) shows its Hugging Face repo id(s) as its
    identifier instead of a version number; a "service" entry (an engine
    running as its own separate server) shows its note. Neither a "repo"
    nor a "service" entry has a real "not installed" state of its own, so
    both count as installed. "installed" is a real boolean computed here
    from the actual check, not a string match against "not installed" in
    whatever renders it (Step 18 item 2 -- that match would silently break
    if this literal ever changed). Makes no network call."""
    out = []
    for entry in MODEL_ENGINE_REGISTRY:
        if entry["kind"] == "repo":
            version = ", ".join(entry["repo_ids"])
            installed = True
        elif entry["kind"] == "service":
            version = entry["note"]
            installed = True
        else:
            try:
                version = importlib.metadata.version(entry["package"])
                installed = True
            except importlib.metadata.PackageNotFoundError:
                version = "not installed"
                installed = False
        out.append({"name": entry["name"], "version": version, "url": entry["url"],
                    "installed": installed})
    if ollama_model:
        out.append({"name": "Ollama (active tag)", "version": ollama_model,
                    "url": "https://ollama.com/library", "installed": True})
    return out


# ---------------------------------------------------------------------------
# Step 9b.2: proactive check for gated pyannote model access -- catches
# the exact real-world failure (a 403 on one gated model masking that the
# OTHER one is also gated, since load_pipeline() tries community-1 first
# and only surfaces 3.1's error) before it shows up as a runtime error on
# "Re-run speaker detection."
# ---------------------------------------------------------------------------

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
            api.model_info(model, token=hf_token or None)
            results.append({"model": model, "accessible": True, "error": None})
        except Exception as e:
            results.append({"model": model, "accessible": False, "error": str(e)})
    return results


# ---------------------------------------------------------------------------
# Step 9b.2: "Copy diagnostics for support" -- the existing key/token
# redaction (translate_engines.redact_secrets) plus stripping local file
# paths and the OS username, since a raw library path or a home directory
# can leak the machine's username into a support conversation.
# ---------------------------------------------------------------------------

_PATH_PATTERN = re.compile(
    r'(?:[A-Za-z]:)?[\\/](?:[^\s\\/:*?"<>|]+[\\/])+([^\s\\/:*?"<>|]+)')


def redact_for_support(text: str) -> str:
    """Same secret redaction the rest of the app already uses for stored
    errors (translate_engines.redact_secrets), plus: the current OS
    username replaced with [USER], and every absolute filesystem path
    (POSIX or Windows) collapsed to just its last path segment prefixed
    with ".../" -- enough to stay readable without exposing the folder
    structure (or a username embedded in it) underneath."""
    import translate_engines
    text = translate_engines.redact_secrets(text or "")
    username = getpass.getuser()
    if username:
        text = re.sub(re.escape(username), "[USER]", text, flags=re.IGNORECASE)
    text = _PATH_PATTERN.sub(lambda m: ".../" + m.group(1), text)
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
            (files.get("missing_top_level") or []) + (files.get("missing_tabs") or [])))
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
    -- pass whatever's currently in session state, since diagnostics.py
    itself has no access to Streamlit session state."""
    return {
        "python": check_python_version(),
        "ffmpeg": check_ffmpeg(),
        "js_runtime": check_js_runtime(),
        "dependencies": check_all_dependencies(),
        "files": check_file_completeness(project_root),
        "library_writable": check_library_writable(library_dir),
        "api_keys": api_keys_set,
    }


# ---------------------------------------------------------------------------
# Step 18c: in-app "Install" buttons for optional dependencies, run against
# the CURRENTLY RUNNING interpreter (sys.executable) -- when this app was
# launched via start.bat/portable.py's own venv activation, that's already
# the venv's own python, never a bare system `pip`.
# ---------------------------------------------------------------------------

# Only these two tiers ever get a generic Install button -- "required" is
# already installed by definition (the app wouldn't be running otherwise)
# and "dev" (pytest) has nothing to do with a running app session.
INSTALLABLE_TIERS = ("feature", "engine")


def stream_pip_install(pip_args: list, python_executable: str = None):
    """Yields {"line": str} for each line of combined stdout/stderr as
    `<python> -m pip install <pip_args>` runs, then a final
    {"done": True, "ok": bool, "returncode": int}. Never swallows a
    failed install into a generic message -- the real pip error text is
    exactly what's yielded, for the caller to show in full (confirmed
    live during this session: a genuine `audio-separator` build failure
    on a real machine is exactly the case this must not hide)."""
    python_executable = python_executable or sys.executable
    cmd = [python_executable, "-m", "pip", "install"] + list(pip_args)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        yield {"line": line.rstrip("\n")}
    returncode = proc.wait()
    yield {"done": True, "ok": returncode == 0, "returncode": returncode}


def stream_pip_uninstall(pip_args: list, python_executable: str = None):
    """Same shape as stream_pip_install, for `<python> -m pip uninstall -y`."""
    python_executable = python_executable or sys.executable
    cmd = [python_executable, "-m", "pip", "uninstall", "-y"] + list(pip_args)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        yield {"line": line.rstrip("\n")}
    returncode = proc.wait()
    yield {"done": True, "ok": returncode == 0, "returncode": returncode}


# ---------------------------------------------------------------------------
# Step 27: "is this dependency outdated?" + an Upgrade action. Like
# check_pyannote_gated_access above, this reaches the network (PyPI's own
# public JSON API, a plain unauthenticated GET) -- so it must only ever run
# from an explicit button click, never automatically on page load, and the
# caller (tabs/diagnostics_tab.py) caches the result in session state
# rather than re-querying on every rerun.
# ---------------------------------------------------------------------------

def get_installed_version(pip_name: str):
    """The installed version of `pip_name` via importlib.metadata, or None
    if no distribution is registered under that exact name. That covers
    both "genuinely not installed" and the handful of OPTIONAL_DEPENDENCIES
    entries whose dict key isn't the distribution name importlib.metadata
    actually knows it by (e.g. "cv2"/"PIL" above are really distributed as
    "opencv-python"/"pillow") -- either way, None means "can't determine",
    never a wrong version."""
    try:
        return importlib.metadata.version(pip_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def get_latest_pypi_version(pip_name: str, timeout: float = 10.0):
    """The latest version PyPI's public JSON API reports for `pip_name`, or
    None on any failure (network error, non-200 for a name PyPI doesn't
    recognize, unexpected JSON shape) -- never raises, since one
    dependency's lookup failing shouldn't break the whole check. Makes a
    real network call every time it's called; callers gate this behind an
    explicit button and cache the result (see check_dependency_versions)."""
    import requests
    try:
        resp = requests.get(f"https://pypi.org/pypi/{pip_name}/json", timeout=timeout)
        if resp.status_code != 200:
            return None
        return (resp.json().get("info") or {}).get("version") or None
    except Exception:
        return None


def _version_sort_key(version: str):
    """A best-effort, dependency-free ordering key for version strings, so
    "1.10.0" correctly compares as newer than "1.9.0" (a plain string
    compare gets that backwards). Splits on "." and "-" and reads the
    leading digits of each segment; a segment with no leading digits (a
    pre-release tag like "rc1") reads as 0 for that position rather than
    failing the comparison outright. Not full PEP 440 semantics -- good
    enough to flag "this is genuinely a newer release" without adding a
    dependency on the optional `packaging` library just for this."""
    key = []
    for segment in re.split(r"[.\-]", version):
        match = re.match(r"^(\d+)", segment)
        key.append(int(match.group(1)) if match else 0)
    return key


def check_dependency_versions(deps: dict, timeout: float = 10.0) -> dict:
    """For every dependency in `deps` (as returned by
    check_all_dependencies()) that's actually installed, looks up its
    latest PyPI version and compares it to the installed one. Returns
    {name: {"installed_version", "latest_version", "outdated"}} --
    "outdated" is None (not guessed) when either version couldn't be
    determined, True/False otherwise. Makes one real PyPI request per
    installed dependency -- call this only from an explicit button click,
    never automatically."""
    results = {}
    for name, info in deps.items():
        if not info.get("installed"):
            continue
        installed_version = get_installed_version(name)
        latest_version = get_latest_pypi_version(name, timeout=timeout)
        outdated = None
        if installed_version and latest_version:
            outdated = _version_sort_key(installed_version) < _version_sort_key(latest_version)
        results[name] = {
            "installed_version": installed_version,
            "latest_version": latest_version,
            "outdated": outdated,
        }
    return results


def upgrade_pip_args(pip_name: str, project_root: str = None) -> list:
    """pip args for `python -m pip install --upgrade <pip_name>`, adding
    constraints.txt's existing version caps (pyannote.audio<5,
    transformers<6, torch<3, streamlit<2, faster-whisper<2, ...) via pip's
    own `-c` flag whenever the file exists -- the same mechanism
    stream_gpu_torch_reinstall already uses for torch/torchaudio,
    generalized here since an Upgrade click can just as easily target any
    of constraints.txt's other pinned packages (e.g. transformers, which
    OmniVoice/Chatterbox/TADA each need a specific range of -- see
    OPTIONAL_DEPENDENCIES above). A constraint for a package not named in
    the file is a no-op, so passing it unconditionally is always safe.
    Note: this does NOT stop someone from upgrading OmniVoice, Chatterbox
    and TADA into the same environment despite them documented above as
    unable to share one -- no code anywhere enforces that today (the
    existing Install button doesn't either, it's caption-text-only), so
    Upgrade deliberately matches that existing behavior rather than
    inventing a new guard for just this one action."""
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    constraints_path = os.path.join(project_root, "constraints.txt")
    args = ["--upgrade", pip_name]
    if os.path.exists(constraints_path):
        args += ["-c", constraints_path]
    return args


def get_gpu_status() -> dict:
    """A live GPU/VRAM readout for Diagnostics' routine view (Step 18 item
    3) -- {"available": bool, "name", "vram_used_gb", "vram_total_gb",
    "torch_cuda_version", "message"}. "available" is False, with a plain
    "message" (never an exception), for every case that isn't a real,
    torch-visible CUDA device: torch not installed, torch installed but
    can't see a GPU with no NVIDIA GPU on the machine, and torch installed
    but CPU-only despite a real NVIDIA GPU being present (the same
    footgun gpu_torch_mismatch() already detects, worded here as a plain
    status message rather than a warning+action). "torch_cuda_version"
    (torch.version.cuda -- what torch was built against, distinct from
    whether a GPU is actually available right now) is included whenever
    torch is installed, even when no GPU is available, since it's useful
    context either way. Never imports torch if it isn't installed."""
    if not check_dependency("torch"):
        return {"available": False, "message": "torch isn't installed -- GPU info unavailable."}
    try:
        import torch
    except Exception:
        return {"available": False, "message": "GPU info unavailable."}
    torch_cuda_version = getattr(torch.version, "cuda", None)
    try:
        cuda_available = bool(torch.cuda.is_available())
    except Exception:
        cuda_available = False
    if not cuda_available:
        if shutil.which("nvidia-smi"):
            message = ("A real NVIDIA GPU is on this machine, but the installed PyTorch build "
                       "is CPU-only -- reinstall following pytorch.org's own selector for your "
                       "driver (or use the Install GPU PyTorch button below).")
        else:
            message = "GPU info unavailable -- no CUDA-capable GPU detected."
        return {"available": False, "torch_cuda_version": torch_cuda_version, "message": message}
    try:
        device_index = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(device_index)
        return {
            "available": True,
            "name": props.name,
            "vram_used_gb": torch.cuda.memory_allocated(device_index) / (1024 ** 3),
            "vram_total_gb": props.total_memory / (1024 ** 3),
            "torch_cuda_version": torch_cuda_version,
        }
    except Exception:
        return {"available": False, "torch_cuda_version": torch_cuda_version,
                "message": "GPU info unavailable."}


def gpu_torch_mismatch() -> bool:
    """True only when a real NVIDIA GPU is on this machine (nvidia-smi on
    PATH) but the installed torch build can't see it -- the exact
    CPU-only-wheel footgun Step 18 item 7 traces to a bare `pip install
    torch` always resolving to PyPI's default (non-CUDA) wheel. A
    minimal, self-contained version of the same nvidia-smi-on-PATH
    detection Step 18 item 3's fuller GPU/VRAM display will also use --
    that display doesn't exist yet, but this button (item 6) needs the
    same signal regardless of which of the two lands first."""
    if not shutil.which("nvidia-smi"):
        return False
    cuda = check_cuda()
    return bool(cuda["torch_installed"]) and cuda["cuda_available"] is False


# cu128, not cu124 -- confirmed directly against download.pytorch.org that
# cu124's index only publishes wheels through cp313, nothing for cp314,
# while cu128 already carries real Windows cp314 CUDA wheels (matches the
# open pytorch/pytorch#169929 report of exactly this gap). Picked by the
# running interpreter's own Python version below, not hardcoded to a
# single value for every version, since CUDA-driver compatibility and
# Python-ABI wheel availability vary independently.
GPU_TORCH_CUDA_INDEX_BY_PYVER = {(3, 14): "cu128"}
GPU_TORCH_CUDA_INDEX_DEFAULT = "cu128"


def gpu_torch_cuda_index() -> str:
    v = sys.version_info
    return GPU_TORCH_CUDA_INDEX_BY_PYVER.get((v.major, v.minor), GPU_TORCH_CUDA_INDEX_DEFAULT)


def stream_gpu_torch_reinstall(python_executable: str = None, project_root: str = None):
    """Uninstalls the CPU-only torch/torchaudio, then reinstalls both from
    PyTorch's own CUDA index for the running interpreter's Python version.
    Reuses constraints.txt's existing torch<3/torchaudio<3 caps via pip's
    own `-c` flag (rather than duplicating those version numbers here) so
    this reinstall can't drift outside the range the rest of the app
    already assumes. Yields the same {"line": ...}/{"done": ...} items as
    stream_pip_install, across both subprocess calls in sequence -- only
    the LAST item has "done", so a caller can tell the whole sequence
    (uninstall + install) apart from either step finishing early."""
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    constraints_path = os.path.join(project_root, "constraints.txt")

    for item in stream_pip_uninstall(["torch", "torchaudio"], python_executable):
        if not item.get("done"):
            yield item

    index_url = f"https://download.pytorch.org/whl/{gpu_torch_cuda_index()}"
    install_args = ["torch", "torchaudio", "--index-url", index_url]
    if os.path.exists(constraints_path):
        install_args += ["-c", constraints_path]
    yield from stream_pip_install(install_args, python_executable)


def stream_dependency_install(name: str, python_executable: str = None,
                              project_root: str = None):
    """Same shape as stream_pip_install, for Diagnostics' generic
    per-dependency "Install" button (Step 18c). Routes `torch` specifically
    through the same GPU-aware CUDA-index reinstall stream_gpu_torch_reinstall
    already uses for the dedicated "Install GPU PyTorch" action, whenever a
    real NVIDIA GPU is present -- a bare `pip install torch` always resolves
    to the CPU-only PyPI wheel (Step 18 item 7's install-time footgun), and
    the generic Install button would otherwise reproduce that exact gap
    through a second path. Every other dependency, and torch on a
    non-NVIDIA machine, installs exactly as stream_pip_install always did."""
    if name == "torch" and shutil.which("nvidia-smi"):
        yield from stream_gpu_torch_reinstall(python_executable, project_root)
    else:
        yield from stream_pip_install([name], python_executable)
