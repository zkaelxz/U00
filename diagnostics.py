"""
diagnostics.py -- environment self-check: which optional dependencies
are actually installed, whether ffmpeg is on PATH, whether every
expected project file is present, and which API keys are configured.

Built directly in response to a real support issue during setup: a
missing file produced a cryptic ModuleNotFoundError that took several
back-and-forth messages to diagnose. This turns that into one glance.
"""

import getpass
import json
import importlib.metadata
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading

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
    "translation_memory.py", "action_tiers.py", "media_inspect.py",
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
    "faster_whisper": ("faster_whisper", "audio alignment/timing", "feature"),
    "cv2": ("cv2", "Scanlate bubble detection/inpainting", "feature"),
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
    # requirements-optional.txt), which the descriptions say before anyone clicks.
    "omnivoice": ("omnivoice", "local voice cloning + voice design (OmniVoice; can't share an "
                               "install with Chatterbox/TADA)", "feature"),
    "chatterbox-tts": ("chatterbox", "emotion-aware local voice (Chatterbox; adds a PerTh "
                                     "watermark; can't share an install with OmniVoice/TADA)",
                       "feature"),
    "hume-tada": ("tada", "long-narration local voice (TADA; model weights under the Llama 3.2 "
                          "Community License; can't share an install with OmniVoice/Chatterbox)",
                  "feature"),
    "pytesseract": ("pytesseract", "OCR (Tesseract backend)", "feature"),
    "PIL": ("PIL", "OCR, Scanlate rendering, cover art upload", "feature"),
    "paddleocr": ("paddleocr", "OCR (PaddleOCR backend)", "feature"),
    "manga_ocr": ("manga_ocr", "OCR (Japanese manga backend)", "feature"),
    "piper-tts": ("piper", "offline TTS", "feature"),
    "jieba": ("jieba", "Chinese word segmentation (Reader, meaning-based line re-segmentation)", "feature"),
    "pypinyin": ("pypinyin", "Chinese pinyin (Reader)", "feature"),
    "sudachipy": ("sudachipy", "Japanese word segmentation (Reader, meaning-based line re-segmentation)", "feature"),
    "pykakasi": ("pykakasi", "Japanese furigana (Reader)", "feature"),
    "kiwipiepy": ("kiwipiepy", "Korean word segmentation (Reader)", "feature"),
    "transformers": ("transformers", "local NLLB-200 translation engine, ML bubble detection "
                                     "(Scanlate), PaddleOCR-VL-For-Manga", "feature"),
    "torch": ("torch", "ML bubble detection/inpainting (Scanlate), PaddleOCR-VL-For-Manga, "
                        "word-level realignment, several TTS/ASR backends", "feature"),
    "torchaudio": ("torchaudio", "word-level realignment (MMS forced alignment, experimental)",
                   "feature"),
    "uroman": ("uroman", "word-level realignment (romanizing non-Latin text for MMS)", "feature"),
    "sentencepiece": ("sentencepiece", "local NLLB-200 translation engine (tokenizer)", "feature"),
    "yt-dlp": ("yt_dlp", "downloading video from YouTube and other sites, live translation, "
                         "Bilibili source adapter", "feature"),
    "opencc-python-reimplemented": ("opencc", "Traditional Chinese segmentation (Reader; "
                                              "converts to Simplified for jieba)", "feature"),
    "sudachidict_core": ("sudachidict_core", "Japanese word segmentation dictionary (sudachipy)",
                         "feature"),
    "safetensors": ("safetensors", "ML inpainting (Scanlate, LaMa-manga checkpoint)", "feature"),
    "huggingface_hub": ("huggingface_hub", "ML bubble detection/inpainting, voice cloning model downloads",
                        "feature"),
    "pypdf": ("pypdf", "Scanlate PDF import (splitting a PDF into pages)", "feature"),
    "streamlit_drawable_canvas": ("streamlit_drawable_canvas",
                                  "Scanlate manual erase/heal brush -- confirmed incompatible "
                                  "with this app's pinned streamlit>=1.56 as of this check "
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
    # Step 104: not on PyPI (installs from github.com/OpenMOSS/MOSS-Transcribe-Diarize)
    # and needs transformers>=5.6, which qwen-asr's transformers==4.57.6 pin rules out.
    "moss-transcribe-diarize": ("moss_transcribe_diarize",
                                "experimental one-pass transcription + speaker labels "
                                "(MOSS-Transcribe-Diarize; Settings > Transcription experiments; "
                                "can't share an install with Qwen3-ASR)", "experimental"),
    "cryptography": ("cryptography", "mangaz.com adapter's session-scoped RSA+AES page "
                                     "decryption (Sources tab); Google sign-in token checks",
                     "feature"),
    "authlib": ("authlib", "Google sign-in for household access (BAIHE_API_AUTH=on)", "feature"),
    "fastapi": ("fastapi", "the HTTP API the React frontend talks to (python -m api)",
                "required"),
    "uvicorn": ("uvicorn", "serves the HTTP API (python -m api)", "required"),
    "python-multipart": ("multipart", "file uploads through the HTTP API (media upload, "
                                      "novel attach)", "required"),
    "numpy": ("numpy", "keeping background music in a dub, Scanlate, hard-subtitle OCR",
              "feature"),
    "pytest": ("pytest", "running the test suite", "dev"),
    "httpx": ("httpx", "Google sign-in's HTTP client (with authlib); also the HTTP API's "
                       "tests (FastAPI TestClient)", "feature"),
}


# ---------------------------------------------------------------------------
# Install names, sizes, sources and task presets (Diagnostics "Packages").
# OPTIONAL_DEPENDENCIES's keys are what the API and UI call a package; most
# are also the pip distribution name (pip treats "_", "-" and "." alike),
# but a few are import names that don't exist on PyPI under that name
# ("cv2" made `pip install cv2` fail with "No matching distribution").
# ---------------------------------------------------------------------------

# key -> the real PyPI distribution to `pip install`.
PIP_DIST_NAMES = {
    "cv2": "opencv-python",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
}


def pip_install_name(name: str) -> str:
    """The distribution name pip should install for an OPTIONAL_DEPENDENCIES
    key or a MODEL_ENGINE_REGISTRY package."""
    return PIP_DIST_NAMES.get(name, name)


def canonical_dist(name: str) -> str:
    """PEP 503 normalized name: lowercase, runs of "-", "_", "." -> "-"."""
    return re.sub(r"[-_.]+", "-", name).lower()


# Approximate download size in MB of each distribution's own wheel plus its
# small dependencies (canonical dist name -> MB). A static estimate for the
# Packages list, never a network lookup; labelled "approx." wherever shown.
# Packages in PULLS_TORCH also pull PyTorch when it isn't installed yet,
# which is not counted here (torch is its own row).
APPROX_DOWNLOAD_MB = {
    "faster-whisper": 80, "opencv-python": 45, "anthropic": 2, "openai": 2, "deepl": 1,
    "requests": 1, "beautifulsoup4": 1, "pyannote-audio": 20, "soundfile": 2,
    "edge-tts": 1, "pydub": 1, "f5-tts": 60, "omnivoice": 60, "chatterbox-tts": 60,
    "hume-tada": 60, "pytesseract": 1, "pillow": 5, "paddleocr": 600, "manga-ocr": 20,
    "piper-tts": 30, "jieba": 20, "pypinyin": 1, "sudachipy": 5, "pykakasi": 3,
    "kiwipiepy": 90, "transformers": 20, "torch": 2500, "torchaudio": 10, "uroman": 1,
    "sentencepiece": 2, "yt-dlp": 3, "opencc-python-reimplemented": 1,
    "sudachidict-core": 70, "safetensors": 1, "huggingface-hub": 1, "pypdf": 1,
    "streamlit-drawable-canvas": 5, "genanki": 1, "ebooklib": 1, "plyer": 1,
    "playwright": 40, "trafilatura": 5, "audio-separator": 30, "funasr": 5, "demucs": 1,
    "cryptography": 4, "authlib": 1, "numpy": 15, "httpx": 1, "qwen-asr": 30,
}
PULLS_TORCH = {"pyannote-audio", "f5-tts", "omnivoice", "chatterbox-tts", "hume-tada",
               "manga-ocr", "audio-separator", "funasr", "demucs", "qwen-asr", "torchaudio"}


def approx_download_mb(name: str):
    """Approximate download in MB for a package key, or None if unknown."""
    return APPROX_DOWNLOAD_MB.get(canonical_dist(pip_install_name(name)))


def pypi_url(name: str):
    """https://pypi.org/project/<dist>/ for a package key, or None when the
    distribution name isn't a plain PEP 508 name (never builds a URL from
    anything else)."""
    dist = pip_install_name(name)
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", dist):
        return None
    return f"https://pypi.org/project/{canonical_dist(dist)}/"


# Packages that aren't on PyPI: Diagnostics links to their real source
# instead of a PyPI page someone else could register (canonical dist -> URL).
NON_PYPI_SOURCES = {
    "moss-transcribe-diarize": "https://github.com/OpenMOSS/MOSS-Transcribe-Diarize",
}


def package_source_url(name: str):
    """Where Diagnostics links a package: its real repository for a non-PyPI
    one, nothing for any other "experimental" entry, else pypi_url()."""
    dist = canonical_dist(pip_install_name(name))
    if dist in NON_PYPI_SOURCES:
        return NON_PYPI_SOURCES[dist]
    dep = OPTIONAL_DEPENDENCIES.get(name)
    if dep and dep[2] == "experimental":
        return None
    return pypi_url(name)


# Packages the generic Install button must not offer, with the reason shown
# instead (dist canonical name -> reason).
NOT_OFFERED_FOR_INSTALL = {
    "moss-transcribe-diarize": "not offered: it isn't on PyPI. It installs from its GitHub "
                               "repository (OpenMOSS/MOSS-Transcribe-Diarize) into this app's "
                               "environment, and it upgrades Transformers to 5.x, which stops "
                               "Qwen3-ASR and Qwen3 forced alignment working.",
    "streamlit-drawable-canvas": "not offered: it fails to set up with this app's pinned "
                                 "Streamlit, and the Scanlate brush that uses it is deferred "
                                 "until Scanlate moves to the new interface.",
}

# Exact pins a package declares on another one the app shares, for a
# "this would downgrade X" warning before installing (package -> {dep: pin}).
KNOWN_EXACT_PINS = {
    "qwen-asr": {"transformers": "4.57.6"},
}


def install_downgrade_warning(name: str):
    """None, or a plain-English warning when installing `name` would move an
    already-installed shared package to an older pinned version (e.g.
    qwen-asr pins transformers==4.57.6 while 5.x is installed). Read-only:
    checks the installed version only."""
    pins = KNOWN_EXACT_PINS.get(canonical_dist(pip_install_name(name)))
    if not pins:
        return None
    for dep, pin in pins.items():
        have = get_installed_version(dep)
        if have and _version_sort_key(have) > _version_sort_key(pin):
            return (f"installing this would downgrade {dep} from {have} to {pin}, which "
                    f"other features (NLLB translation, Scanlate, voice engines) use -- "
                    f"they may stop working until {dep} is upgraded again.")
    return None


# Install presets: what the user wants to do -> the packages it needs (by
# OPTIONAL_DEPENDENCIES key, or MODEL_ENGINE_REGISTRY package when it has
# no key). Derived from the "feature" descriptions above. Each package is
# "required" for its task (the task doesn't work without it) unless listed
# under "recommended" (better results, or one of several interchangeable
# engines) or "optional" (a heavier or niche extra). "Install for this
# task" installs required and recommended ones; optional ones are offered
# one by one.
INSTALL_TASKS = [
    {"id": "transcribe", "group": "Audio", "label": "Transcribe speech (Whisper)",
     "help": "Turn a drama's audio into timed lines.",
     "packages": ["faster_whisper", "soundfile", "numpy"]},
    {"id": "music_removal", "group": "Audio", "label": "Remove background music",
     "help": "Clean the audio before transcribing so dialogue is easier to hear.",
     "packages": ["demucs", "audio-separator", "torch", "soundfile", "numpy"],
     "recommended": ["audio-separator"]},
    {"id": "speakers", "group": "Audio", "label": "Speaker detection",
     "help": "Split and label lines by who is speaking (needs a Hugging Face token).",
     "packages": ["pyannote.audio", "soundfile", "torch"]},
    {"id": "alt_asr", "group": "Audio", "label": "Qwen3-ASR / SenseVoice transcription",
     "help": "Alternative transcription engines; SenseVoice also tags emotion and sounds.",
     "packages": ["qwen-asr", "funasr", "torch"],
     "recommended": ["qwen-asr", "funasr"]},
    {"id": "word_timing", "group": "Audio", "label": "Word-level timing",
     "help": "Re-align lines to individual words (experimental).",
     "packages": ["torch", "torchaudio", "uroman", "soundfile"]},
    {"id": "tts_online", "group": "Dubbing", "label": "Dubbing: free online voice (edge-tts)",
     "help": "Microsoft-hosted voices; needs internet, no GPU.",
     "packages": ["edge_tts", "pydub", "numpy"],
     "recommended": ["numpy"]},
    {"id": "tts_piper", "group": "Dubbing", "label": "Dubbing: offline voice (Piper)",
     "help": "Small offline voices, no cloning.",
     "packages": ["piper-tts", "pydub"]},
    {"id": "tts_f5", "group": "Dubbing", "label": "Voice cloning: F5-TTS",
     "help": "Clone a character's voice locally.",
     "packages": ["f5_tts", "torch", "pydub", "huggingface_hub"]},
    {"id": "tts_omnivoice", "group": "Dubbing", "label": "Voice cloning: OmniVoice",
     "help": "Clone or design a voice locally. Can't share an install with Chatterbox/TADA.",
     "packages": ["omnivoice", "torch", "pydub", "huggingface_hub"]},
    {"id": "tts_chatterbox", "group": "Dubbing", "label": "Voice cloning: Chatterbox",
     "help": "Emotion-aware local voice. Can't share an install with OmniVoice/TADA.",
     "packages": ["chatterbox-tts", "torch", "pydub", "huggingface_hub"]},
    {"id": "tts_tada", "group": "Dubbing", "label": "Long narration: TADA",
     "help": "Local voice for novel narration. Can't share an install with OmniVoice/Chatterbox.",
     "packages": ["hume-tada", "torch", "pydub", "huggingface_hub"]},
    {"id": "hardsub_ocr", "group": "Video", "label": "Read burned-in captions (OCR)",
     "help": "Pull hard-coded subtitles out of video frames.",
     "packages": ["cv2", "numpy", "PIL", "pytesseract", "paddleocr"],
     "recommended": ["pytesseract"],
     "optional": ["paddleocr"]},
    {"id": "url_import", "group": "Video", "label": "Import from a URL",
     "help": "Download video from YouTube, Bilibili and other sites.",
     "packages": ["yt-dlp"]},
    {"id": "reader_zh", "group": "Novels & reader", "label": "Chinese reader tools",
     "help": "Word splitting, pinyin and Traditional Chinese support.",
     "packages": ["jieba", "pypinyin", "opencc-python-reimplemented"],
     "recommended": ["pypinyin"],
     "optional": ["opencc-python-reimplemented"]},
    {"id": "reader_ja", "group": "Novels & reader", "label": "Japanese reader tools",
     "help": "Word splitting and furigana.",
     "packages": ["sudachipy", "sudachidict_core", "pykakasi"],
     "recommended": ["pykakasi"]},
    {"id": "reader_ko", "group": "Novels & reader", "label": "Korean reader tools",
     "help": "Word splitting.", "packages": ["kiwipiepy"]},
    {"id": "books", "group": "Novels & reader", "label": "EPUB and Anki export",
     "help": "Import/export EPUB books and export vocab to Anki.",
     "packages": ["ebooklib", "genanki"],
     "recommended": ["ebooklib", "genanki"]},
    {"id": "web_sources", "group": "Novels & reader", "label": "Novel sources from websites",
     "help": "Read chapters from pasted URLs and JavaScript-heavy sites.",
     "packages": ["bs4", "trafilatura", "playwright", "cryptography"],
     "recommended": ["trafilatura"],
     "optional": ["playwright", "cryptography"]},
    {"id": "scanlate", "group": "Scanlate", "label": "Scanlate (manga/manhua pages)",
     "help": "Bubble detection, Japanese OCR, inpainting and PDF import.",
     "packages": ["cv2", "PIL", "numpy", "manga_ocr", "pypdf", "transformers", "torch",
                  "safetensors", "huggingface_hub", "streamlit_drawable_canvas"],
     "recommended": ["manga_ocr", "pypdf", "transformers", "torch", "safetensors",
                     "huggingface_hub"],
     "optional": ["streamlit_drawable_canvas"]},
    {"id": "nllb", "group": "Translation", "label": "Free local translation (NLLB-200)",
     "help": "Translate offline on this PC.",
     "packages": ["transformers", "sentencepiece", "torch"]},
    {"id": "paid_engines", "group": "Translation", "label": "Claude, DeepSeek and DeepL",
     "help": "Client libraries for the paid translation engines (keys go in Settings).",
     "packages": ["anthropic", "openai", "deepl"],
     "recommended": ["anthropic", "openai", "deepl"]},
    {"id": "sign_in", "group": "App", "label": "Google sign-in for household access",
     "help": "Needed only when BAIHE_API_AUTH=on.",
     "packages": ["authlib", "httpx", "cryptography"]},
    {"id": "notifications", "group": "App", "label": "Desktop notifications",
     "help": "A notification when a background job finishes.",
     "packages": ["plyer"]},
]


def check_python_version():
    import sys
    v = sys.version_info
    return {"version": f"{v.major}.{v.minor}.{v.micro}", "ok": v.major == 3 and v.minor >= 9}


def check_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        return {"found": False, "path": None, "version": None, "libass": None}
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        version_line = result.stdout.splitlines()[0] if result.stdout else "unknown version"
        # Burned-in (hardsub) export and the styled preview need ffmpeg built
        # with libass; `-version` prints the build's configure flags.
        libass = "--enable-libass" in result.stdout if result.stdout else None
        return {"found": True, "path": path, "version": version_line, "libass": libass}
    except Exception:
        return {"found": True, "path": path, "version": "found but version check failed",
                "libass": None}


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


def model_folder(kind: str) -> str:
    """The other model download folders, outside the Hugging Face cache:
    "torch" is torch.hub's checkpoints folder under TORCH_HOME (demucs
    weights; resolved like torch.hub.get_dir(), without importing torch),
    "audio_separator" the Mel-Band RoFormer model folder
    (BAIHE_AUDIO_SEP_MODEL_DIR). Both follow portable mode's redirects."""
    if kind == "torch":
        home = os.environ.get("TORCH_HOME") or os.path.join(
            os.environ.get("XDG_CACHE_HOME") or os.path.join("~", ".cache"), "torch")
        return os.path.join(os.path.expanduser(home), "hub", "checkpoints")
    if kind == "audio_separator":
        import audio_preprocess
        return audio_preprocess._MODEL_DIR
    raise ValueError(f"Unknown model folder {kind!r}")


MODEL_FOLDERS = ("torch", "audio_separator")


def scan_model_folder(kind: str, folder: str = None) -> list:
    """[{"name", "size_bytes"}, ...] for every file or folder directly in
    one of model_folder()'s folders, largest first (symlinks are skipped).
    [] if it doesn't exist yet -- never raises, like scan_hf_cache."""
    folder = folder or model_folder(kind)
    try:
        entries = []
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.islink(path):
                continue
            if os.path.isdir(path):
                size = sum(os.path.getsize(os.path.join(root, f))
                           for root, _dirs, files in os.walk(path) for f in files
                           if not os.path.islink(os.path.join(root, f)))
            else:
                size = os.path.getsize(path)
            entries.append({"name": name, "size_bytes": size})
        return sorted(entries, key=lambda e: -e["size_bytes"])
    except OSError:
        return []


def delete_model_folder_entry(kind: str, name: str, folder: str = None) -> bool:
    """Deletes one entry scan_model_folder lists: a plain name directly in
    that folder, never a path or a symlink. False, not raised, if it isn't
    there or the delete fails."""
    folder = folder or model_folder(kind)
    if (not isinstance(name, str) or name in ("", ".", "..")
            or os.path.basename(name) != name or "\\" in name or "\x00" in name):
        return False
    path = os.path.join(folder, name)
    try:
        if os.path.islink(path) or not os.path.lexists(path):
            return False
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True
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
    {"name": "F5-TTS", "kind": "package", "package": "f5-tts",
     "url": "https://github.com/SWivid/F5-TTS",
     "help": "A local text-to-speech engine that can clone a character's voice for dubbing or "
             "novel narration."},
    {"name": "OmniVoice", "kind": "package", "package": "omnivoice",
     "url": "https://github.com/k2-fsa/OmniVoice",
     "help": "A local voice-cloning engine that can also design a new voice from a text "
             "description, not just clone an existing sample."},
    {"name": "GPT-SoVITS", "kind": "service",
     "note": "separate local server (not pip-installed)",
     "url": "https://github.com/RVC-Boss/GPT-SoVITS",
     "help": "A separate local voice-cloning server you run yourself -- the app talks to it over "
             "its own local API rather than installing it as a package."},
    {"name": "Chatterbox", "kind": "package", "package": "chatterbox-tts",
     "url": "https://github.com/resemble-ai/chatterbox",
     "help": "A local voice-cloning engine that can vary emotional delivery; adds an inaudible "
             "watermark to its output."},
    {"name": "TADA", "kind": "package", "package": "hume-tada",
     "url": "https://github.com/HumeAI/tada",
     "help": "A local voice engine tuned for long narration (e.g. novel narration) rather than "
             "short dubbed lines."},
    {"name": "edge-tts", "kind": "package", "package": "edge-tts",
     "url": "https://github.com/rany2/edge-tts",
     "help": "A free, online (Microsoft-hosted) text-to-speech engine used for dubbing when no "
             "local voice-cloning engine is set up."},
]


def get_model_engine_versions(ollama_model: str = None) -> list:
    """[{"name", "version", "url", "installed", "package", "help"}, ...],
    one row per MODEL_ENGINE_REGISTRY entry plus the active Ollama tag if
    given. A "package" entry's version comes from importlib.metadata (no
    import of the package itself, so no heavy ML import-time cost just to
    check a version) -- "not installed" if it isn't present. A "repo"
    entry (a bare model checkpoint this app's own code names directly, not
    a pip-versioned package) shows its Hugging Face repo id(s) as its
    identifier instead of a version number; a "service" entry (an engine
    running as its own separate server) shows its note. Neither a "repo"
    nor a "service" entry has a real "not installed" state of its own, so
    both count as installed. "installed" is a real boolean computed here
    from the actual check, not a string match against "not installed" in
    whatever renders it (Step 18 item 2 -- that match would silently break
    if this literal ever changed). Makes no network call. "package" (Step
    47) is the real pip/importlib.metadata distribution name for a
    "package" kind entry, None otherwise -- the exact string a caller
    should pass to stream_dependency_install/stream_pip_install for that
    row's own Install button, straight from the registry rather than
    re-derived by matching against OPTIONAL_DEPENDENCIES's own keys (those
    use import-style names -- "faster_whisper", "manga_ocr" -- that don't
    all match the real pip names here, and some registry packages, like
    Qwen3-ASR's "qwen-asr", have no OPTIONAL_DEPENDENCIES entry at all).
    "help" is a short plain-English description of what the row is and
    which app feature uses it, for a "?" affordance in the UI."""
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
# Step 9b.2: proactive check for gated pyannote model access -- catches
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
# Step 97: pre-flight a translation engine's credentials/reachability
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


def doctor_report(engines: list) -> list:
    """Runs check_engine_reachable for a list of
    {"engine", "api_key"?, "model"?, "base_url"?} dicts -- the CLI
    `doctor` command's own batch form, and reusable by any future UI
    button that wants to check several configured engines at once."""
    results = []
    for spec in engines:
        results.append(check_engine_reachable(
            spec["engine"], spec.get("api_key"), spec.get("model"), spec.get("base_url")))
    return results


# ---------------------------------------------------------------------------
# Step 9b.2: "Copy diagnostics for support" -- the existing key/token
# redaction (translate_engines.redact_secrets) plus stripping local file
# paths and the OS username, since a raw library path or a home directory
# can leak the machine's username into a support conversation.
# ---------------------------------------------------------------------------

_PATH_PATTERN = re.compile(
    r'(?:[A-Za-z]:)?[\\/](?:[^\s\\/:*?"<>|]+[\\/])+([^\s\\/:*?"<>|]+)')

# ANSI escape sequences (CSI: colours, cursor moves), e.g. yt-dlp's
# "\x1b[0;31mERROR:\x1b[0m" -- unreadable noise in a report or log view.
_ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def redact_for_support(text: str) -> str:
    """Same secret redaction the rest of the app already uses for stored
    errors (translate_engines.redact_secrets), plus: the current OS
    username replaced with [USER], and every absolute filesystem path
    (POSIX or Windows) collapsed to just its last path segment prefixed
    with ".../" -- enough to stay readable without exposing the folder
    structure (or a username embedded in it) underneath. ANSI colour
    codes are stripped first."""
    import translate_engines
    text = _ANSI_PATTERN.sub("", text or "")
    text = translate_engines.redact_secrets(text)
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
# "experimental" (Step 104's MOSS) is listed but never installed from here:
# it isn't on PyPI.
INSTALLABLE_TIERS = ("feature", "engine")

# Added to every install: pip's wheel cache can be unwritable or locked on
# Windows (antivirus, another Python process), which fails the whole install
# with "[Errno 13] Permission denied: ...\\pip\\cache\\wheels\\...", and the
# "new release of pip" notice only clutters the output shown to the user.
PIP_INSTALL_FLAGS = ("--no-cache-dir", "--disable-pip-version-check")

PIP_CACHE_PERMISSION_HINT = (
    "pip couldn't write to its download cache. Close other Python windows (and the "
    "Baihe launcher if it's open twice), pause antivirus scanning of the pip folder, "
    "or delete %LOCALAPPDATA%\\pip\\cache, then try again.")


def pip_cache_permission_hint(lines) -> str:
    """PIP_CACHE_PERMISSION_HINT when pip's output shows a permission error
    inside its own cache folder, else None."""
    for line in lines:
        low = (line or "").lower().replace("/", "\\")
        if "permission denied" in low and ("pip\\cache" in low or "cache\\pip" in low):
            return PIP_CACHE_PERMISSION_HINT
    return None


def stream_pip_install(pip_args: list, python_executable: str = None):
    """Yields {"line": str} for each line of combined stdout/stderr as
    `<python> -m pip install <pip_args>` runs, then a final
    {"done": True, "ok": bool, "returncode": int}. Never swallows a
    failed install into a generic message -- the real pip error text is
    exactly what's yielded, for the caller to show in full (confirmed
    live during this session: a genuine `audio-separator` build failure
    on a real machine is exactly the case this must not hide)."""
    python_executable = python_executable or sys.executable
    cmd = [python_executable, "-m", "pip", "install", *PIP_INSTALL_FLAGS] + list(pip_args)
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
# Step 62: install a whole requirements tier, and a real Deno install
# action -- both real subprocess actions triggered only from an explicit
# button click, matching stream_pip_install's own "never swallow the real
# error" discipline.
# ---------------------------------------------------------------------------

def parse_requirements_file(path: str) -> list:
    """Package specifiers from a requirements.txt-style file: comments
    (a leading `#`, or trailing after a real spec) and blank lines
    skipped, everything else returned in file order. A line commented out
    entirely (e.g. one of two TTS engines whose dependencies conflict --
    see requirements-optional.txt's own note) is correctly never
    installed, the same as a plain `pip install -r` would skip it."""
    if not os.path.exists(path):
        return []
    specs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                specs.append(line)
    return specs


def stream_bulk_install(requirements_path: str, python_executable: str = None):
    """Installs every package in `requirements_path` one at a time --
    never a single `pip install -r`, which aborts the entire batch on the
    first failure (exactly the problem Step 61's audio-separator/
    diffq-fixed case would cause for everyone else in the same file).
    Yields {"package", "line"} per output line, {"package", "done", "ok"}
    per package, then a final {"bulk_done": True, "results": {package:
    ok}} once every package has been attempted, failures included."""
    specs = parse_requirements_file(requirements_path)
    results = {}
    for spec in specs:
        yield {"package": spec, "start": True}
        for item in stream_pip_install([spec], python_executable):
            if item.get("done"):
                results[spec] = item["ok"]
                yield {"package": spec, "done": True, "ok": item["ok"]}
            else:
                yield {"package": spec, "line": item["line"]}
    yield {"bulk_done": True, "results": results}


def _deno_default_install_path() -> str:
    """Where Deno's own official installer puts the binary, regardless of
    whether the CURRENT process's PATH has picked it up yet -- used to
    tell "installed, but this process hasn't seen it yet" apart from
    "genuinely not installed" after a real install attempt."""
    home = os.path.expanduser("~")
    name = "deno.exe" if platform.system() == "Windows" else "deno"
    return os.path.join(home, ".deno", "bin", name)


def stream_deno_install():
    """Installs Deno, a system tool rather than a pip package, so it needs
    its own mechanism distinct from stream_pip_install: winget on Windows
    when it's on PATH (the officially documented package-manager route),
    otherwise Deno's own official install script -- PowerShell's on
    Windows, the shell one everywhere else. Yields {"line"} per output
    line, then {"done", "ok", "on_path", "needs_restart"} -- installing a
    binary doesn't guarantee this same process's PATH picks it up without
    a restart, so "ok but needs_restart" is a real, distinct outcome from
    a plain "ok"."""
    if shutil.which("deno"):
        yield {"line": "deno is already on PATH -- nothing to do."}
        yield {"done": True, "ok": True, "on_path": True, "needs_restart": False}
        return
    system = platform.system()
    if system == "Windows" and shutil.which("winget"):
        cmd = ["winget", "install", "-e", "--id", "DenoLand.Deno"]
    elif system == "Windows":
        cmd = ["powershell", "-NoProfile", "-Command", "irm https://deno.land/install.ps1 | iex"]
    else:
        cmd = ["sh", "-c", "curl -fsSL https://deno.land/install.sh | sh"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        yield {"line": line.rstrip("\n")}
    returncode = proc.wait()
    on_path = bool(shutil.which("deno"))
    installed = on_path or os.path.exists(_deno_default_install_path())
    ok = returncode == 0 and installed
    yield {"done": True, "ok": ok, "on_path": on_path, "needs_restart": ok and not on_path}


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
        # "experimental" entries aren't on PyPI: a lookup by their name could
        # hit an unrelated package registered under it.
        if not info.get("installed") or info.get("tier") == "experimental":
            continue
        installed_version = get_installed_version(pip_install_name(name))
        latest_version = get_latest_pypi_version(pip_install_name(name), timeout=timeout)
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


# ---------------------------------------------------------------------------
# Step 47 item 4: warn (never block) before installing a heavy local
# voice-cloning/TTS backend when a functionally-equivalent one is already
# installed -- e.g. Chatterbox is already there and someone clicks Install
# on OmniVoice. Both an Install button covering the same four packages
# exist today (Dependencies' own per-tier buttons, and the Model & engine
# versions panel's own row buttons above), so this is shared by both
# rather than checked twice. "Hume" the user separately asked about isn't
# a distinct engine this app wires into anything -- "hume-tada" (TADA) is
# already the one Hume Labs engine here, so it's the only Hume-related
# entry in this group; nothing else to add without a real, separate
# candidate to evaluate.
# ---------------------------------------------------------------------------

REDUNDANT_LOCAL_TTS_PACKAGES = {"f5-tts", "omnivoice", "chatterbox-tts", "hume-tada"}
_REDUNDANT_LOCAL_TTS_LABELS = {
    "f5-tts": "F5-TTS", "omnivoice": "OmniVoice",
    "chatterbox-tts": "Chatterbox", "hume-tada": "TADA",
}


def redundant_tts_install_warning(package: str, installed_packages) -> str:
    """None unless `package` is one of the heavy local voice-cloning/TTS
    backends above AND at least one of the other three is already
    installed (per `installed_packages`, an iterable of pip/distribution
    names -- accepts either OPTIONAL_DEPENDENCIES's own keys, like
    "f5_tts", or MODEL_ENGINE_REGISTRY's, like "f5-tts"; both spellings
    normalize the same way pip itself treats "_"/"-" as equivalent).
    Otherwise a plain-English confirmation message naming what's already
    installed, for an Install button's own confirm-before-a-large-
    redundant-download step. Never a reason to block outright -- Step 38's
    Model Arena wants more than one installed to compare."""
    key = package.replace("_", "-").lower()
    if key not in REDUNDANT_LOCAL_TTS_PACKAGES:
        return None
    installed_norm = {p.replace("_", "-").lower() for p in installed_packages}
    already = [_REDUNDANT_LOCAL_TTS_LABELS[p] for p in sorted(REDUNDANT_LOCAL_TTS_PACKAGES)
               if p != key and p in installed_norm]
    if not already:
        return None
    names = " and ".join(already)
    return (f"{names} already installed and covers this -- also install "
            f"{_REDUNDANT_LOCAL_TTS_LABELS[key]}? It's a large download and won't replace "
            f"{names}; both stay available.")


# ---------------------------------------------------------------------------
# Step 47 item 5: when an "Upgrade" action can't actually reach the latest
# release for a real, known reason (a constraints.txt cap, or a package
# with no published wheel for the running Python version), say so instead
# of silently offering an upgrade that would fail, or offering nothing
# with no explanation. Seeded with the one real, already-confirmed case
# (Step 61's audio-separator/diffq-fixed/Python-3.14 finding) rather than
# a hypothetical one -- add to this dict as more real cases turn up, the
# same way OPTIONAL_DEPENDENCIES itself grows.
# ---------------------------------------------------------------------------

KNOWN_UPGRADE_LIMITATIONS = {
    "audio-separator": {
        "python_version": (3, 14),
        "reason": "its diffq-fixed sub-dependency has wheels only through cp313, and its "
                  "sdist build also fails independently (Step 61); Demucs, this app's "
                  "default vocal-separation backend, is unaffected.",
    },
    # Step 66: reproduced for real -- with huggingface_hub 2.0.0 installed
    # next to transformers 5.17.0, `import transformers` raises
    # "ImportError: huggingface-hub>=1.5.0,<2.0 is required ... but found
    # huggingface-hub==2.0.0", taking NLLB translation and Scanlate's ML
    # bubble detector down with it. This app's mocked test suite never
    # imports the real transformers, so only pip's own conflict report
    # caught it. Applies only while the installed transformers still
    # declares that cap, so it lifts itself once a transformers release
    # accepts huggingface_hub 2.x.
    "huggingface-hub": {
        "blocked_from": 2,
        "while_required_below_by": "transformers",
        "reason": "the installed transformers (NLLB translation, Scanlate's ML bubble "
                  "detector) requires huggingface_hub below 2.0 and refuses to import "
                  "with 2.x -- upgrade transformers first once a release accepts it.",
    },
}


def _constraints_cap(pip_name: str, project_root: str = None):
    """The raw constraint line (e.g. "torch<3") capping `pip_name` in
    constraints.txt, or None if it isn't capped there. Matches on the
    package name before the operator, normalizing "_"/"-" the same way
    pip itself treats them as equivalent."""
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(project_root, "constraints.txt")
    if not os.path.exists(path):
        return None
    target = pip_name.replace("_", "-").lower()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.match(r"([A-Za-z0-9_.\-]+)\s*<\s*([0-9]+)", line)
            if m and m.group(1).replace("_", "-").lower() == target:
                return line, int(m.group(2))
    return None


def _known_python_version_limitation(pip_name: str):
    """The KNOWN_UPGRADE_LIMITATIONS entry for `pip_name`, if one exists AND
    this process is actually running the affected Python version -- shared
    by upgrade_blocked_reason (an installed package that can't go further)
    and known_install_limitation_reason (Step 61: the same package failing
    to install in the first place, same root cause, different moment)."""
    known = KNOWN_UPGRADE_LIMITATIONS.get(pip_name.replace("_", "-").lower())
    if known and "python_version" in known and sys.version_info[:2] == known["python_version"]:
        return known
    return None


def _declared_major_cap(requirer: str, pip_name: str):
    """The major version `requirer`'s installed metadata caps `pip_name`
    below (e.g. 2 for "huggingface-hub<2.0,>=1.5.0"), or None if it isn't
    installed or declares no such cap."""
    try:
        requirements = importlib.metadata.requires(requirer) or []
    except importlib.metadata.PackageNotFoundError:
        return None
    target = re.sub(r"[-_.]+", "-", pip_name).lower()
    for req in requirements:
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*\(?([^;]*)", req)
        if not m or re.sub(r"[-_.]+", "-", m.group(1)).lower() != target:
            continue
        cap = re.search(r"<\s*([0-9]+)", m.group(2))
        if cap:
            return int(cap.group(1))
    return None


def _known_dependent_limitation(pip_name: str, latest_version: str):
    """The KNOWN_UPGRADE_LIMITATIONS entry for `pip_name` if it's a
    "blocked from version N while <requirer> caps it" entry that applies
    right now: `latest_version` reaches N and the installed requirer still
    declares a cap at or below N."""
    known = KNOWN_UPGRADE_LIMITATIONS.get(pip_name.replace("_", "-").lower())
    if not known or "blocked_from" not in known or not latest_version:
        return None
    if _version_sort_key(latest_version)[:1] < [known["blocked_from"]]:
        return None
    cap = _declared_major_cap(known["while_required_below_by"], pip_name)
    if cap is not None and cap <= known["blocked_from"]:
        return known
    return None


def known_install_limitation_reason(pip_name: str) -> str:
    """None, or a short, plain-English reason `pip_name` is known to fail
    to install at all on this Python version (Step 61) -- shown next to a
    "not installed" row before the user ever clicks Install, and again if
    they click it anyway and it fails, so a raw pip/Cython traceback is
    never the only signal. Also covers NOT_OFFERED_FOR_INSTALL (a package
    known not to work with this app at all, on any Python)."""
    not_offered = NOT_OFFERED_FOR_INSTALL.get(canonical_dist(pip_install_name(pip_name)))
    if not_offered:
        return not_offered
    known = _known_python_version_limitation(pip_name)
    if not known:
        return None
    py = ".".join(str(p) for p in known["python_version"])
    return f"known not to install on Python {py} -- {known['reason']}"


def upgrade_blocked_reason(pip_name: str, latest_version: str = None,
                           project_root: str = None) -> str:
    """None if a normal "Upgrade" should be offered for `pip_name`.
    Otherwise a short, plain-English reason the row should show INSTEAD
    of the button, so a known-doomed upgrade never just looks like a real
    option with no explanation (Step 47 item 5)."""
    known = _known_python_version_limitation(pip_name)
    if known:
        py = ".".join(str(p) for p in known["python_version"])
        return f"latest available for Python {py} -- {known['reason']}"
    known = _known_dependent_limitation(pip_name, latest_version)
    if known:
        return f"{latest_version} is known to break this app -- {known['reason']}"
    cap = _constraints_cap(pip_name, project_root)
    if cap and latest_version and _version_sort_key(latest_version)[:1] >= [cap[1]]:
        return f"capped at `{cap[0]}` in constraints.txt (see its own comment for why)"
    return None


# ---------------------------------------------------------------------------
# Step 66: "if I upgrade this, will it break the app?" -- answered by
# actually trying it, not by guessing from version numbers: install the
# candidate into a throwaway venv that otherwise sees this environment's
# own packages, run this app's own test suite there, and re-run anything
# that failed in a second throwaway venv WITHOUT the candidate, so a test
# that already fails today isn't blamed on the upgrade. The real
# environment is never modified -- pip refuses to uninstall anything that
# lives outside the throwaway venv's own prefix, and the candidate lands
# only inside it. Four outcomes, never a guess: "safe" (no new failures,
# no conflicts), "broken" (named tests that pass today fail with the
# candidate), "conflict" (tests pass, but pip reports an installed package
# that declares it won't work with the candidate), or "incomplete"
# (couldn't finish -- no network, no disk space, a timeout).
# No result at all means "untested", never "safe".
# ---------------------------------------------------------------------------

UPGRADE_CHECK_PIP_TIMEOUT = 900
UPGRADE_CHECK_TEST_TIMEOUT = 1800


def _env_package_dirs() -> list:
    """This process's own site-packages/dist-packages directories -- what a
    throwaway venv's .pth file points back at, so it sees exactly the
    packages this app is really running with."""
    return [p for p in sys.path if p and os.path.isdir(p)
            and os.path.basename(os.path.normpath(p)) in ("site-packages", "dist-packages")]


def _venv_python(venv_dir: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _make_throwaway_venv(base_dir: str, name: str, python_executable: str, parent_dirs: list):
    """(venv_python, None) on success, (None, reason) otherwise. No pip
    inside it -- installs go through the real pip's own --python flag, so
    this works even where ensurepip isn't available."""
    venv_dir = os.path.join(base_dir, name)
    try:
        proc = subprocess.run([python_executable, "-m", "venv", "--without-pip", venv_dir],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return None, (proc.stderr or proc.stdout).strip() or f"exit code {proc.returncode}"
        venv_py = _venv_python(venv_dir)
        purelib = subprocess.run(
            [venv_py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, timeout=60).stdout.strip()
        os.makedirs(purelib, exist_ok=True)
        with open(os.path.join(purelib, "_baihe_parent_env.pth"), "w", encoding="utf-8") as f:
            f.write("\n".join(parent_dirs) + "\n")
        return venv_py, None
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)


def _stream_process(cmd: list, timeout: float, cwd: str = None, env: dict = None):
    """Yields {"line"} per output line, then {"returncode", "timed_out"}.
    The process is killed if it outlives `timeout` or if the caller stops
    iterating early (a closed page), so nothing is left running."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, cwd=cwd, env=env)
    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        proc.kill()
    timer = threading.Timer(timeout, _kill)
    timer.start()
    try:
        for line in proc.stdout:
            yield {"line": line.rstrip("\n")}
        returncode = proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    yield {"returncode": returncode, "timed_out": timed_out.is_set()}


def _parse_pytest_failures(lines: list) -> list:
    """Node ids from pytest's -rfE short summary ("FAILED path::test - msg",
    "ERROR path - msg" for a collection error), in order, de-duplicated."""
    ids = []
    for line in lines:
        for prefix in ("FAILED ", "ERROR "):
            if line.startswith(prefix):
                node_id = line[len(prefix):].split(" - ", 1)[0].strip()
                if node_id and node_id not in ids:
                    ids.append(node_id)
    return ids


def _canonical_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


_PIP_CONFLICT_RE = re.compile(
    r"^(\S+) (\S+) requires (.+), but you have (\S+) (\S+) which is incompatible\.?$")


def _parse_pip_conflicts(lines: list) -> list:
    """pip's own post-install "X requires Y, but you have Z which is
    incompatible" lines, keeping only the ones this install caused -- a
    newly installed distribution (from pip's "Successfully installed ..."
    line) on either side. A conflict that already existed in the
    environment before (e.g. an unrelated package pinning numpy) isn't the
    candidate's fault and isn't reported."""
    installed = set()
    for line in lines:
        if line.startswith("Successfully installed "):
            for token in line[len("Successfully installed "):].split():
                installed.add(_canonical_dist(token.rsplit("-", 1)[0]))
    conflicts = []
    for line in lines:
        m = _PIP_CONFLICT_RE.match(line.strip())
        if not m:
            continue
        if _canonical_dist(m.group(1)) in installed or _canonical_dist(m.group(4)) in installed:
            if line.strip() not in conflicts:
                conflicts.append(line.strip())
    return conflicts


def _dist_version_in(venv_py: str, pip_name: str):
    try:
        out = subprocess.run(
            [venv_py, "-c", "import importlib.metadata, sys; "
                            "print(importlib.metadata.version(sys.argv[1]))", pip_name],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _flag_conflicts(result: dict) -> dict:
    """A "safe" test result that pip itself reports conflicts for becomes
    "conflict" -- tests passing doesn't outweigh an installed package
    declaring it won't work with the candidate."""
    if result["verdict"] == "safe" and result["conflicts"]:
        n = len(result["conflicts"])
        result.update(ok=False, verdict="conflict",
                      reason=f"{result['reason']}, but pip reports {n} installed package(s) "
                             f"that declare they don't support it -- this app's tests don't "
                             f"load those libraries for real, so they can't rule this out")
    return result


def check_upgrade_candidate(pip_name: str, version: str = None, project_root: str = None,
                            test_args: list = None, python_executable: str = None,
                            parent_dirs: list = None, pip_extra_args: list = None,
                            pip_timeout: float = UPGRADE_CHECK_PIP_TIMEOUT,
                            test_timeout: float = UPGRADE_CHECK_TEST_TIMEOUT):
    """Yields {"line"} as it goes, then a final {"done": True, "ok",
    "verdict", "reason", "version", "new_failures", "preexisting_failures",
    "conflicts"} -- "ok" is True only for verdict "safe". "conflicts" is
    pip's own "X requires Y, but you have Z" report for conflicts this
    install caused; a passing test run with conflicts reads "conflict",
    not "safe", since this app's mocked tests never import real ML
    libraries (huggingface_hub 2.0 breaking `import transformers` passed
    every test and was caught only this way). `version` pins the candidate
    (Diagnostics passes the latest release the real Upgrade would install);
    constraints.txt's caps apply exactly as they would to that real Upgrade.
    Runs this app's whole test suite by default, so it takes minutes, not
    seconds. `test_args`/`parent_dirs`/`pip_extra_args` exist so a test can
    point this at a tiny offline suite and local wheels."""
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    python_executable = python_executable or sys.executable
    parent_dirs = _env_package_dirs() if parent_dirs is None else list(parent_dirs)
    test_args = list(test_args or [os.path.join(project_root, "tests")])
    spec = f"{pip_name}=={version}" if version else pip_name
    pip_args = ["--upgrade", spec] + upgrade_pip_args(pip_name, project_root)[2:] + list(pip_extra_args or [])
    test_env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    result = {"done": True, "ok": False, "verdict": "incomplete", "reason": "",
              "version": version, "new_failures": [], "preexisting_failures": [],
              "conflicts": []}

    def _pytest(venv_py, args):
        cmd = [venv_py, "-m", "pytest", "-o", "addopts=", "-q", "-rfE", "-p", "no:cacheprovider"] + args
        return _stream_process(cmd, test_timeout, cwd=project_root, env=test_env)

    work = tempfile.mkdtemp(prefix="baihe_upgrade_check_")
    try:
        yield {"line": "Creating a throwaway environment -- your real install isn't touched."}
        trial_py, err = _make_throwaway_venv(work, "trial", python_executable, parent_dirs)
        if err:
            result["reason"] = f"couldn't create a throwaway environment: {err}"
            yield result
            return

        yield {"line": f"Installing {spec} into it..."}
        pip_lines, end = [], None
        for item in _stream_process([python_executable, "-m", "pip", "--python", trial_py,
                                     "install"] + pip_args, pip_timeout):
            if "line" in item:
                pip_lines.append(item["line"])
                yield item
            else:
                end = item
        if end["timed_out"]:
            result["reason"] = f"installing {spec} took longer than {int(pip_timeout // 60)} minutes"
            yield result
            return
        if end["returncode"] != 0:
            if any("No space left on device" in line for line in pip_lines):
                result["reason"] = "not enough disk space for the throwaway environment"
            else:
                result["reason"] = (f"{spec} couldn't be installed (no network, no such version, or "
                                    f"a build failure -- see the output above)")
            yield result
            return
        installed = _dist_version_in(trial_py, pip_name)
        if not installed:
            result["reason"] = f"{spec} reported success but isn't importable afterward"
            yield result
            return
        result["version"] = installed
        result["conflicts"] = _parse_pip_conflicts(pip_lines)

        yield {"line": f"Running this app's test suite against {pip_name} {installed}..."}
        test_lines, end = [], None
        for item in _pytest(trial_py, test_args):
            if "line" in item:
                test_lines.append(item["line"])
                yield item
            else:
                end = item
        failures = _parse_pytest_failures(test_lines)
        if end["timed_out"]:
            result["reason"] = f"the test suite took longer than {int(test_timeout // 60)} minutes"
            yield result
            return
        if end["returncode"] == 0:
            result.update(ok=True, verdict="safe",
                          reason=f"every test passed against {pip_name} {installed}")
            yield _flag_conflicts(result)
            return
        if end["returncode"] not in (1, 2) or not failures:
            result["reason"] = (f"the test run itself didn't complete (pytest exit code "
                                f"{end['returncode']}) -- see the output above")
            yield result
            return

        yield {"line": f"{len(failures)} test(s) failed -- re-running them without {pip_name} "
                       f"{installed} to see which already fail on the current version..."}
        base_py, err = _make_throwaway_venv(work, "baseline", python_executable, parent_dirs)
        if err:
            result["reason"] = (f"{len(failures)} test(s) failed, but a comparison environment "
                                f"couldn't be created to rule out already-failing ones: {err}")
            result["new_failures"] = failures
            yield result
            return
        base_lines, end = [], None
        for item in _pytest(base_py, failures):
            if "line" in item:
                base_lines.append(item["line"])
                yield item
            else:
                end = item
        if end["timed_out"] or end["returncode"] not in (0, 1, 2):
            result["reason"] = (f"{len(failures)} test(s) failed, but re-running them on the "
                                f"current version didn't complete -- see the output above")
            result["new_failures"] = failures
            yield result
            return
        baseline = set(_parse_pytest_failures(base_lines))
        new = [f for f in failures if f not in baseline]
        pre = [f for f in failures if f in baseline]
        result.update(new_failures=new, preexisting_failures=pre)
        if new:
            result.update(verdict="broken",
                          reason=f"{len(new)} test(s) that pass on the current version fail "
                                 f"against {pip_name} {installed}")
        else:
            result.update(ok=True, verdict="safe",
                          reason=f"no new failures against {pip_name} {installed} ({len(pre)} "
                                 f"test(s) already fail on the current version too)")
        yield _flag_conflicts(result)
    finally:
        shutil.rmtree(work, ignore_errors=True)


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


# Step 26d: how busy the GPU actually is, straight from the driver --
# independent of anything Baihe itself is tracking. background_jobs.py's
# in-process guard and db.py's cross-process gpu_lock (Step 25w) both only
# know about GPU-touching work Baihe itself started; neither can see a
# completely different application (Jellyfin doing hardware-accelerated
# transcoding on the same card, say) using the same physical GPU. This is
# the only signal that can.
EXTERNAL_GPU_BUSY_UTIL_PERCENT = 50
EXTERNAL_GPU_BUSY_MIN_FREE_MB = 1024


def external_gpu_load() -> dict | None:
    """Real utilization/VRAM for the first GPU nvidia-smi reports, or None
    if nvidia-smi isn't on PATH or the query fails for any reason --
    best-effort, same as the rest of this module's GPU detection, never
    raises. Deliberately reads the driver directly rather than anything
    torch-based, since torch may not even be installed/loaded at the
    point this gets called (background_jobs.py checks this before a job
    that would import torch has started)."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True)
        line = result.stdout.strip().splitlines()[0]
        util_percent, used_mb, total_mb = (float(x.strip()) for x in line.split(","))
        return {"utilization_percent": util_percent, "memory_used_mb": used_mb,
                "memory_total_mb": total_mb, "memory_free_mb": total_mb - used_mb}
    except Exception:
        return None


def external_gpu_is_busy() -> bool:
    """True if the GPU looks meaningfully loaded by *something* right now,
    per nvidia-smi -- whether or not Baihe itself started it. False (never
    blocks a job) if nvidia-smi isn't available: this is a belt-and-suspenders
    check layered on top of Baihe's own two GPU locks, not a replacement for
    either, so its absence shouldn't be treated as "GPU busy" any more than
    it already is today."""
    load = external_gpu_load()
    if load is None:
        return False
    return (load["utilization_percent"] >= EXTERNAL_GPU_BUSY_UTIL_PERCENT or
            load["memory_free_mb"] < EXTERNAL_GPU_BUSY_MIN_FREE_MB)


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
    not_offered = NOT_OFFERED_FOR_INSTALL.get(canonical_dist(pip_install_name(name)))
    if not_offered:
        yield {"line": f"{name}: {not_offered}"}
        yield {"done": True, "ok": False, "returncode": None}
        return
    if name == "torch" and shutil.which("nvidia-smi"):
        yield from stream_gpu_torch_reinstall(python_executable, project_root)
    else:
        yield from stream_pip_install([pip_install_name(name)], python_executable)


# ---------------------------------------------------------------------------
# GPU PyTorch setup (Diagnostics > "GPU PyTorch"). torch, torchvision and
# torchaudio are built against each other: each torchvision/torchaudio
# release requires one exact torch release, and pip resolving any of the
# three on its own is how a CUDA torch gets swapped for a CPU one or a
# torchvision ends up requiring a torch that isn't installed ("torchvision
# 0.29.0 requires torch==2.14.0, but you have torch 2.11.0+cu128"). So the
# app installs a matched triple, pinned exactly, from one fixed index, and
# every other install/upgrade pins whatever torch family is installed.
#
# Sources (checked 2026-09-29):
# - torch <-> torchvision pairs: the compatibility table in
#   https://github.com/pytorch/vision/blob/main/README.md
#   (2.13/0.28, 2.12/0.27, 2.11/0.26, 2.10/0.25, 2.9/0.24, 2.8/0.23).
#   torchaudio's version equals torch's (https://pytorch.org/audio/main/installation.html).
# - wheels actually published: https://download.pytorch.org/whl/cu128/torch/
#   (and /torchvision/, /torchaudio/): 2.11.0+cu128 / 0.26.0+cu128 /
#   2.11.0+cu128 is the newest cu128 triple, for CPython 3.10-3.14 on
#   Windows and Linux; https://download.pytorch.org/whl/cpu/ has the same
#   versions as +cpu.
# - driver floor: NVIDIA's CUDA Toolkit release notes, "CUDA Toolkit and
#   Corresponding Driver Versions" -- CUDA 12.8 GA needs >= 570.65 on
#   Windows, >= 570.26 on Linux; minor-version compatibility lets CUDA 12.x
#   run (without newer-GPU support or PTX JIT) from 525.60.13 / 528.33.
#   https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html
# Update this table (and constraints.txt's comment) when moving to a newer
# CUDA index; nothing here is ever taken from a request.
# ---------------------------------------------------------------------------

TORCH_FAMILY = ("torch", "torchvision", "torchaudio")

# variant -> the fixed index and the exact triple installed from it.
TORCH_VARIANTS = {
    "cu128": {
        "label": "NVIDIA GPU (CUDA 12.8)",
        "index_url": "https://download.pytorch.org/whl/cu128",
        "versions": {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128",
                     "torchaudio": "2.11.0+cu128"},
        "needs_nvidia": True,
    },
    "cpu": {
        "label": "CPU only (no NVIDIA GPU)",
        "index_url": "https://download.pytorch.org/whl/cpu",
        "versions": {"torch": "2.11.0+cpu", "torchvision": "0.26.0+cpu",
                     "torchaudio": "2.11.0+cpu"},
        "needs_nvidia": False,
    },
}
TORCH_RECOMMENDED_VARIANT_GPU = "cu128"
# CPython versions the triple above has wheels for (inclusive).
TORCH_SUPPORTED_PYTHON = ((3, 10), (3, 14))

# torch major.minor -> the torchvision major.minor built for it (README table above).
TORCHVISION_FOR_TORCH = {"2.8": "0.23", "2.9": "0.24", "2.10": "0.25", "2.11": "0.26",
                         "2.12": "0.27", "2.13": "0.28", "2.14": "0.29"}

# NVIDIA driver needed by the cu128 wheels, per OS: "recommended" is CUDA
# 12.8's own requirement; below "minimum" CUDA 12 can't run at all.
NVIDIA_DRIVER_FOR_CU128 = {
    "Windows": {"recommended": "570.65", "minimum": "528.33"},
    "Linux": {"recommended": "570.26", "minimum": "525.60.13"},
}

TORCH_SETUP_TIMEOUT_SECONDS = 3600    # ~2.5 GB of CUDA wheels on a slow link
TORCH_VERIFY_TIMEOUT_SECONDS = 180    # a cold `import torch` can take a while

_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+!_-]{0,63}$")


def _mm(version: str) -> str:
    """"2.11.0+cu128" -> "2.11"."""
    return ".".join(re.split(r"[.+]", version or "")[:2])


def nvidia_driver_info():
    """{"gpu_name", "driver_version"} for the first GPU nvidia-smi lists, or
    None when nvidia-smi isn't on PATH or fails. Never raises; bounded by a
    timeout like external_gpu_load."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=True)
        name, driver = (x.strip() for x in result.stdout.strip().splitlines()[0].rsplit(",", 1))
        return {"gpu_name": name[:120], "driver_version": driver[:40]}
    except Exception:
        return None


def driver_check(driver_version, system: str = None) -> dict:
    """{"status": "ok"|"old"|"too_old"|"unknown", "recommended", "minimum"}
    for the cu128 wheels on this OS. "old" works through CUDA's
    minor-version compatibility (a warning); "too_old" can't run CUDA 12."""
    system = system or platform.system()
    need = NVIDIA_DRIVER_FOR_CU128.get(system, NVIDIA_DRIVER_FOR_CU128["Linux"])
    out = {"recommended": need["recommended"], "minimum": need["minimum"]}
    if not driver_version:
        return {"status": "unknown", **out}
    have = _version_sort_key(driver_version)
    if have < _version_sort_key(need["minimum"]):
        return {"status": "too_old", **out}
    if have < _version_sort_key(need["recommended"]):
        return {"status": "old", **out}
    return {"status": "ok", **out}


def _build_of(version):
    """"cuda" for a +cuXXX local tag, "cpu" for +cpu, None when the version
    carries no build tag (e.g. PyPI's Linux wheels) or isn't installed."""
    if not version:
        return None
    tag = version.partition("+")[2].lower()
    if tag.startswith("cu") or tag.startswith("rocm"):
        return "cuda"
    if tag == "cpu":
        return "cpu"
    return None


def torch_family_versions() -> dict:
    """{name: {"version", "build"}} for torch/torchvision/torchaudio, from
    installed metadata only (no import, so it's right even after an install
    in this same process)."""
    out = {}
    for name in TORCH_FAMILY:
        version = get_installed_version(name)
        out[name] = {"version": version, "build": _build_of(version)}
    return out


def torch_family_problems(versions: dict) -> list:
    """Plain-English mismatches between installed torch, torchvision and
    torchaudio: a torchvision/torchaudio built for another torch, or CUDA
    and CPU builds mixed."""
    torch_v = (versions.get("torch") or {}).get("version")
    if not torch_v:
        return [f"{n} is installed without torch." for n in TORCH_FAMILY[1:]
                if (versions.get(n) or {}).get("version")]
    problems = []
    tv = (versions.get("torchvision") or {}).get("version")
    want_tv = TORCHVISION_FOR_TORCH.get(_mm(torch_v))
    if tv and want_tv and _mm(tv) != want_tv:
        problems.append(f"torchvision {tv} doesn't match torch {torch_v} "
                        f"(torch {_mm(torch_v)} needs torchvision {want_tv}.x).")
    ta = (versions.get("torchaudio") or {}).get("version")
    if ta and _mm(ta) != _mm(torch_v):
        problems.append(f"torchaudio {ta} doesn't match torch {torch_v} "
                        f"(it must be {_mm(torch_v)}.x).")
    builds = {(versions.get(n) or {}).get("build") for n in TORCH_FAMILY} - {None}
    if len(builds) > 1:
        problems.append("CUDA and CPU builds are mixed; reinstall all three together.")
    return problems


def torch_pin_lines() -> list:
    """Exact pins ("torch==2.11.0+cu128") for each installed torch-family
    package, for a constraints file every other install/upgrade passes to
    pip, so a package that depends on torch can't swap a CUDA build for a
    CPU one or move torchvision off its torch. Versions come from local
    metadata and are checked against a strict pattern before use."""
    lines = []
    for name in TORCH_FAMILY:
        version = get_installed_version(name)
        if version and _VERSION_RE.match(version):
            lines.append(f"{name}=={version}")
    return lines


def torch_setup_pip_args(variant: str, project_root: str = None) -> list:
    """Two pip argument lists (after `install`) for the matched triple of
    `variant` (a TORCH_VARIANTS key): first `--force-reinstall --no-deps`
    of all three pinned together, so pip downloads every wheel before it
    replaces anything and torchvision/torchaudio can't resolve against
    another torch; then the same pins without --force-reinstall to add
    any missing dependency (nvidia-* wheels, sympy, pillow, ...). Both from
    the variant's fixed index, with constraints.txt's caps. KeyError for an
    unknown variant."""
    spec = TORCH_VARIANTS[variant]
    pins = [f"{n}=={spec['versions'][n]}" for n in TORCH_FAMILY]
    tail = ["--index-url", spec["index_url"]]
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    constraints = os.path.join(project_root, "constraints.txt")
    if os.path.exists(constraints):
        tail += ["-c", constraints]
    return [["--force-reinstall", "--no-deps", *pins, *tail], [*pins, *tail]]


# Run by `python -c` after a setup, so the check sees the new wheels and not
# the torch this process may already have imported. Prints one JSON line.
TORCH_VERIFY_SCRIPT = """
import json
out = {}
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda_build"] = torch.version.cuda
    out["cuda_available"] = bool(torch.cuda.is_available())
    if out["cuda_available"]:
        out["device"] = torch.cuda.get_device_name(0)
        torch.zeros(1, device="cuda")
except Exception as e:
    out["error"] = type(e).__name__ + ": " + str(e)[:300]
for name in ("torchvision", "torchaudio"):
    try:
        out[name] = __import__(name).__version__
    except Exception as e:
        out[name + "_error"] = type(e).__name__ + ": " + str(e)[:300]
print(json.dumps(out))
"""


def parse_torch_verify_output(stdout: str) -> dict:
    """The JSON the verify script printed (its last line), or {"error"}."""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                break
            return data if isinstance(data, dict) else {"error": "unexpected output"}
    return {"error": "the check printed nothing usable"}


# ---------------------------------------------------------------------------
# Installed versions and an honest "is there an update I may install?"
# (Diagnostics > Packages). The PyPI read is a network call: callers run it
# only from an explicit "Check for updates" click and cache the result. The
# URL is built from the static dist name only (pypi_url's pattern).
# ---------------------------------------------------------------------------

# Other distributions that provide the same import (key -> dists), tried
# when the main one isn't installed, so a headless OpenCV still shows its
# version.
PIP_DIST_ALTERNATES = {
    "cv2": ("opencv-python-headless", "opencv-contrib-python",
            "opencv-contrib-python-headless"),
}

PYPI_JSON_TIMEOUT = 10.0
PYPI_JSON_MAX_BYTES = 20 * 1024 * 1024   # the largest project JSON is a few MB
_DIST_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def _packaging():
    """packaging's version/specifier/requirement modules: the standalone
    package when installed, else the copy pip vendors (pip is always there
    when these installs can run at all)."""
    try:
        from packaging import requirements, specifiers, version
    except ImportError:          # pragma: no cover - depends on the environment
        from pip._vendor.packaging import requirements, specifiers, version
    return version, specifiers, requirements


def installed_dist_version(name: str):
    """(dist, version) actually installed for a package key: its PyPI dist
    (pip_install_name), else a known alternate dist; (dist, None) if none."""
    dist = pip_install_name(name)
    for candidate in (dist, *PIP_DIST_ALTERNATES.get(name, ())):
        version = get_installed_version(candidate)
        if version:
            return candidate, version
    return dist, None


def pypi_release_versions(dist: str, timeout: float = PYPI_JSON_TIMEOUT):
    """Final (non-pre-release), non-yanked releases PyPI lists for `dist`
    that have at least one file, as version strings; None on any failure.
    One GET to https://pypi.org/pypi/<dist>/json, with a timeout, no
    redirects and at most PYPI_JSON_MAX_BYTES read."""
    if not _DIST_NAME_RE.fullmatch(dist or ""):
        return None
    import requests
    version_mod, _s, _r = _packaging()
    try:
        resp = requests.get(f"https://pypi.org/pypi/{canonical_dist(dist)}/json",
                            timeout=timeout, headers={"Accept": "application/json"},
                            stream=True, allow_redirects=False)
        try:
            if resp.status_code != 200:
                return None
            body = bytearray()
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) > PYPI_JSON_MAX_BYTES:
                    return None
        finally:
            resp.close()
        releases = json.loads(bytes(body)).get("releases") or {}
    except Exception:
        return None
    out = []
    for text, files in releases.items():
        if not isinstance(files, list) or not files or all(f.get("yanked") for f in files):
            continue
        try:
            v = version_mod.Version(text)
        except Exception:
            continue
        if not (v.is_prerelease or v.is_devrelease):
            out.append(text)
    return out


def constraint_specifiers(project_root: str = None) -> dict:
    """{canonical dist: (SpecifierSet, raw line)} from constraints.txt."""
    _v, specifiers, requirements = _packaging()
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(project_root, "constraints.txt")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                req = requirements.Requirement(line)
            except Exception:
                continue
            out[canonical_dist(req.name)] = (req.specifier, line)
    return out


def installed_requirements_on() -> dict:
    """{canonical dist: [(requirer dist, SpecifierSet)]} from every
    installed distribution's own requirements (markers evaluated for this
    interpreter, extras off): what an upgrade must keep satisfied so it
    doesn't break a package that's already installed."""
    _v, _s, requirements = _packaging()
    out = {}
    for d in importlib.metadata.distributions():
        requirer = (d.metadata or {}).get("Name")
        if not requirer:
            continue
        for text in d.requires or []:
            try:
                req = requirements.Requirement(text)
                if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                    continue
            except Exception:
                continue
            if str(req.specifier):
                out.setdefault(canonical_dist(req.name), []).append(
                    (requirer, req.specifier))
    return out


def classify_update(name: str, installed_version: str, releases, constraints: dict,
                    required_by: dict) -> dict:
    """{"status", "latest", "target", "reason"} for one installed package:
    "update" (target = newest release allowed by constraints.txt, the
    installed packages that depend on it and the known limitations),
    "held_back" (a newer release exists but none of the newer ones is
    allowed; reason says by what), "up_to_date", or "unknown" (PyPI didn't
    answer, or the version can't be read). Offline: releases come from
    pypi_release_versions."""
    version_mod, _s, _r = _packaging()
    dist = canonical_dist(pip_install_name(name))
    empty = {"latest": None, "target": None, "reason": None}
    if not installed_version or not releases:
        return {"status": "unknown", **empty}
    try:
        have = version_mod.Version(installed_version)
    except Exception:
        return {"status": "unknown", **empty}
    versions = set()
    for r in releases:
        try:
            versions.add(version_mod.Version(r))
        except Exception:
            continue          # never a pip argument: only str(Version) is used below
    if not versions:
        return {"status": "unknown", **empty}
    versions = sorted(versions)
    latest = versions[-1]
    # Compare on the public version: 2.11.0+cu128 is not "older" than 2.11.0.
    newer = [v for v in versions if v > version_mod.Version(have.public)]
    if not newer:
        return {"status": "up_to_date", "latest": str(latest), "target": None, "reason": None}

    reasons = []
    known = _known_python_version_limitation(dist)
    if known:
        py = ".".join(str(p) for p in known["python_version"])
        return {"status": "held_back", "latest": str(latest), "target": None,
                "reason": f"no newer release installs on Python {py} -- {known['reason']}"}
    allowed = newer
    spec = constraints.get(dist)
    if spec:
        kept = [v for v in allowed if spec[0].contains(v, prereleases=True)]
        if len(kept) < len(allowed):
            reasons.append(f"constraints.txt ({spec[1]})")
        allowed = kept
    for requirer, spec_set in required_by.get(dist, []):
        if canonical_dist(requirer) == dist:
            continue
        kept = [v for v in allowed if spec_set.contains(v, prereleases=True)]
        if len(kept) < len(allowed):
            reasons.append(f"{requirer} (needs {name} {spec_set})")
        allowed = kept
    kept = [v for v in allowed if not _known_dependent_limitation(dist, str(v))]
    if len(kept) < len(allowed):
        reasons.append(KNOWN_UPGRADE_LIMITATIONS[dist]["reason"])
    allowed = kept
    reason = ("held back by " + "; ".join(reasons)) if reasons else None
    if allowed:
        return {"status": "update", "latest": str(latest), "target": str(allowed[-1]),
                "reason": reason if allowed[-1] != latest else None}
    return {"status": "held_back", "latest": str(latest), "target": None, "reason": reason}


TASK_ROLES = ("required", "recommended", "optional")


def task_package_role(task: dict, name: str) -> str:
    """"required", "recommended" or "optional" for a package in an
    INSTALL_TASKS entry (unlisted packages are required)."""
    for role in ("optional", "recommended"):
        if name in task.get(role, ()):
            return role
    return "required"


# The requirements files whose `name>=X` lines are the app's minimums.
REQUIREMENTS_FILES = ("requirements-core.txt", "requirements-media.txt",
                      "requirements-optional.txt")


def required_min_versions(project_root: str = None) -> dict:
    """{canonical dist: minimum version} from the requirements files'
    active `>=` lines (commented-out lines are skipped, as pip would)."""
    _v, _s, requirements = _packaging()
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    out = {}
    for fname in REQUIREMENTS_FILES:
        for spec in parse_requirements_file(os.path.join(project_root, fname)):
            try:
                req = requirements.Requirement(spec)
            except Exception:
                continue
            for s in req.specifier:
                if s.operator == ">=":
                    out[canonical_dist(req.name)] = s.version
    return out


def below_min_version(installed_version, min_version) -> bool:
    """True when both are known and installed < minimum (public versions)."""
    if not installed_version or not min_version:
        return False
    version_mod, _s, _r = _packaging()
    try:
        return (version_mod.Version(version_mod.Version(installed_version).public)
                < version_mod.Version(min_version))
    except Exception:
        return False
