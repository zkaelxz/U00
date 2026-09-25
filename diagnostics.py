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
    "adaptive_style.py", "line_tools.py", "emotion.py", "ui_theme.py", "page_fetch.py",
    "forced_align.py", "asr_backend.py", "asr_benchmark.py", "video_download.py",
]
EXPECTED_TABS_FILES = [
    "__init__.py", "settings_tab.py", "library_tab.py", "workspace_tab.py",
    "reader_tab.py", "scanlate_tab.py", "navigator_tab.py", "discover_tab.py",
    "diagnostics_tab.py",
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
    "elevenlabs": ("elevenlabs", "hosted voice cloning", "feature"),
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
    "playwright": ("playwright", "reading JavaScript-rendered sites (baihehub, Fanjiao)", "feature"),
    "audio-separator": ("audio_separator",
                        "background-music removal before transcription (Mel-Band RoFormer; "
                        "falls back to Demucs)", "feature"),
    "funasr": ("funasr", "audio emotion & sound tags (SenseVoice; model weights under the "
                         "FunASR Model Open Source License)", "feature"),
    "demucs": ("demucs", "background-music removal before transcription (fallback)", "feature"),
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


# ---------------------------------------------------------------------------
# Step 9b.2: model/engine version panel -- one row per AI model/engine
# actually wired into the app today (not the roadmap's full aspirational
# list; several named there, like OmniVoice or PaddleOCR-VL-For-Manga,
# aren't implemented yet and belong to later steps). No network call:
# this only reports what pip already knows is installed locally.
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
    {"name": "edge-tts", "kind": "package", "package": "edge-tts",
     "url": "https://github.com/rany2/edge-tts"},
    {"name": "ElevenLabs (hosted)", "kind": "package", "package": "elevenlabs",
     "url": "https://elevenlabs.io"},
]


def get_model_engine_versions(ollama_model: str = None) -> list:
    """[{"name", "version", "url"}, ...], one row per MODEL_ENGINE_REGISTRY
    entry plus the active Ollama tag if given. A "package" entry's version
    comes from importlib.metadata (no import of the package itself, so no
    heavy ML import-time cost just to check a version) -- "not installed"
    if it isn't present. A "repo" entry (a bare model checkpoint this
    app's own code names directly, not a pip-versioned package) shows its
    Hugging Face repo id(s) as its identifier instead of a version number.
    Makes no network call."""
    out = []
    for entry in MODEL_ENGINE_REGISTRY:
        if entry["kind"] == "repo":
            version = ", ".join(entry["repo_ids"])
        else:
            try:
                version = importlib.metadata.version(entry["package"])
            except importlib.metadata.PackageNotFoundError:
                version = "not installed"
        out.append({"name": entry["name"], "version": version, "url": entry["url"]})
    if ollama_model:
        out.append({"name": "Ollama (active tag)", "version": ollama_model,
                    "url": "https://ollama.com/library"})
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
