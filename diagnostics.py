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

import diarize
import storage

from expected_files import EXPECTED_TOP_LEVEL_FILES

# name -> (import name, feature it powers, required vs optional)
OPTIONAL_DEPENDENCIES = {
    "faster_whisper": ("faster_whisper", "audio alignment/timing", "feature"),
    "onnxruntime": ("onnxruntime", "ASMR VAD", "feature"),
    "ctranslate2": ("ctranslate2", "Whisper GPU detection (installed with faster-whisper)", "feature"),
    "cv2": ("cv2", "Scanlate bubble detection/inpainting", "feature"),
    "anthropic": ("anthropic", "Claude translation engine", "engine"),
    "openai": ("openai", "DeepSeek translation engine", "engine"),
    "requests": ("requests", "metadata lookup/navigator", "engine"),
    "bs4": ("bs4", "metadata lookup, navigator, bulk import", "feature"),
    "pyannote.audio": ("pyannote.audio", "speaker diarization", "feature"),
    "soundfile": ("soundfile", "speaker diarization, vocal separation chunking, word-level realignment", "feature"),
    "pydub": ("pydub", "dub/narration track mixing", "feature"),
    # Keys are the real pip names -- Diagnostics' Install button runs
    # `pip install <key>`.
    "omnivoice": ("omnivoice", "local voice cloning + voice design (OmniVoice)", "feature"),
    "pytesseract": ("pytesseract", "OCR (Tesseract backend)", "feature"),
    "PIL": ("PIL", "OCR, Scanlate rendering, cover art upload", "feature"),
    "paddleocr": ("paddleocr", "OCR (PaddleOCR backend)", "feature"),
    "paddlepaddle": ("paddle", "PaddleOCR engine", "feature"),
    "manga_ocr": ("manga_ocr", "OCR (Japanese manga backend)", "feature"),
    "jieba": ("jieba", "Chinese word segmentation (Reader, meaning-based line re-segmentation)", "feature"),
    "pypinyin": ("pypinyin", "Chinese pinyin (Reader)", "feature"),
    "sudachipy": ("sudachipy", "Japanese word segmentation (Reader, meaning-based line re-segmentation)", "feature"),
    "pykakasi": ("pykakasi", "Japanese furigana (Reader)", "feature"),
    "kiwipiepy": ("kiwipiepy", "Korean word segmentation (Reader)", "feature"),
    "transformers": ("transformers", "Qwen3-ASR and Qwen3 forced alignment (5.15 or newer), "
                                     "ML bubble detection (Scanlate), PaddleOCR-VL-For-Manga "
                                     "(needs transformers 5+)", "feature"),
    "torch": ("torch", "ML bubble detection/inpainting (Scanlate), PaddleOCR-VL-For-Manga, "
                        "word-level realignment, several TTS/ASR backends", "feature"),
    "torchaudio": ("torchaudio", "word-level realignment (MMS forced alignment, experimental)",
                   "feature"),
    "uroman": ("uroman", "word-level realignment (romanizing non-Latin text for MMS)", "feature"),
    "sentencepiece": ("sentencepiece", "PaddleOCR-VL-For-Manga (tokenizer)", "feature"),
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
    "genanki": ("genanki", "Anki .apkg export (Reader vocab)", "feature"),
    "ebooklib": ("ebooklib", "EPUB import/export", "feature"),
    "plyer": ("plyer", "desktop notification when a background job finishes (Settings toggle, "
                       "off by default)", "feature"),
    "playwright": ("playwright", "reading JavaScript-rendered sites (baihehub, Fanjiao; the "
                                 "Sources tab's browser tier)", "feature"),
    "jiwer": ("jiwer", "Benchmark Lab: standard CER/WER scoring for transcription and OCR "
                       "(falls back to a built-in scorer)", "feature"),
    "sacrebleu": ("sacrebleu", "Benchmark Lab: chrF translation similarity (falls back to a "
                               "built-in character similarity ratio)", "feature"),
    "trafilatura": ("trafilatura", "Sources tab: pulling a novel chapter's main text out of a "
                                   "pasted URL (falls back to a simpler built-in extractor)",
                    "feature"),
    "audio-separator": ("audio_separator",
                        "background-music removal before transcription (Mel-Band RoFormer; "
                        "falls back to Demucs)", "feature"),
    "funasr": ("funasr", "audio emotion & sound tags (SenseVoice; model weights under the "
                         "FunASR Model Open Source License)", "feature"),
    "demucs": ("demucs", "background-music removal before transcription (fallback)", "feature"),
    # Qwen3's own tokenisation for forced alignment; transformers' processor
    # raises if the package for the title's language is missing.
    "nagisa": ("nagisa", "Qwen3 forced alignment of Japanese (word splitting)", "feature"),
    "soynlp": ("soynlp", "Qwen3 forced alignment of Korean (word splitting)", "feature"),
    "cryptography": ("cryptography", "Google sign-in token checks, live capture of AES-128 "
                                     "encrypted HLS streams", "feature"),
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
    # A separate program, not a library. GPL-3.0, so Baihe never
    # imports or ships it: it only runs the user-installed `lncrawl` command
    # (services/lncrawl_service.py). Detected by EXTERNAL_PROGRAMS below,
    # never offered for one-click install (NOT_OFFERED_FOR_INSTALL).
    "lightnovel-crawler": ("lncrawl", "Novel text: \"Import with lightnovel-crawler\" (a "
                                      "separate GPL-3.0 program you install yourself; Baihe "
                                      "only runs it and reads the EPUB it makes)", "feature"),
}

# Import-name slots that are really external programs, mapped to the service
# whose is_installed() finds them where they will run (PATH or Settings path);
# their Python code is never looked up or imported.
EXTERNAL_PROGRAMS = {"lncrawl": "services.lncrawl_service"}


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
    "faster-whisper": 80, "ctranslate2": 40, "opencv-python": 45, "anthropic": 2, "openai": 2,
    "requests": 1, "beautifulsoup4": 1, "pyannote-audio": 20, "soundfile": 2,
    "pydub": 1, "omnivoice": 60, "pytesseract": 1, "pillow": 5, "paddleocr": 600, "paddlepaddle": 200, "manga-ocr": 20,
    "jieba": 20, "pypinyin": 1, "sudachipy": 5, "pykakasi": 3,
    "kiwipiepy": 90, "transformers": 20, "torch": 2500, "torchaudio": 10, "uroman": 1,
    "sentencepiece": 2, "yt-dlp": 3, "opencc-python-reimplemented": 1,
    "sudachidict-core": 70, "safetensors": 1, "huggingface-hub": 1, "pypdf": 1,
    "genanki": 1, "ebooklib": 1, "plyer": 1,
    "lightnovel-crawler": 30,
    "playwright": 40, "trafilatura": 5, "audio-separator": 30, "funasr": 5, "demucs": 1,
    "cryptography": 4, "authlib": 1, "numpy": 15, "httpx": 1, "nagisa": 22, "soynlp": 1,
    "jiwer": 3, "sacrebleu": 2, "onnxruntime": 15,
}
PULLS_TORCH = {"pyannote-audio", "omnivoice", "manga-ocr", "audio-separator", "funasr", "demucs", "torchaudio"}


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


def package_source_url(name: str):
    """Where Diagnostics links a package: nothing for an "experimental" entry
    (not necessarily on PyPI), else pypi_url()."""
    dep = OPTIONAL_DEPENDENCIES.get(name)
    if dep and dep[2] == "experimental":
        return None
    return pypi_url(name)


# Packages the generic Install button must not offer, with the reason shown
# instead (dist canonical name -> reason).
NOT_OFFERED_FOR_INSTALL = {
    "lightnovel-crawler": "not offered: it's a separate program under the GPL-3.0 licence that "
                          "you install yourself, e.g. `pipx install lightnovel-crawler` (or "
                          "`pip install lightnovel-crawler` in its own environment). Baihe only "
                          "runs it. If it isn't on PATH, set its program path in Settings > "
                          "Advanced.",
}

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
     "packages": ["faster_whisper", "ctranslate2", "soundfile", "numpy"]},
    {"id": "music_removal", "group": "Audio", "label": "Remove background music",
     "help": "Clean the audio before transcribing so dialogue is easier to hear.",
     "packages": ["demucs", "audio-separator", "torch", "soundfile", "numpy"],
     "recommended": ["audio-separator"]},
    {"id": "speakers", "group": "Audio", "label": "Speaker detection",
     "help": "Split and label lines by who is speaking (needs a Hugging Face token).",
     "packages": ["pyannote.audio", "soundfile", "torch"]},
    {"id": "alt_asr", "group": "Audio", "label": "Qwen3-ASR / SenseVoice transcription",
     "help": "Alternative transcription engines; SenseVoice also tags emotion and sounds.",
     "packages": ["transformers", "nagisa", "soynlp", "funasr", "soundfile", "torch", "onnxruntime"],
     "recommended": ["transformers", "nagisa", "soynlp", "funasr", "soundfile"], "optional": ["onnxruntime"]},
    {"id": "word_timing", "group": "Audio", "label": "Word-level timing",
     "help": "Re-align lines to individual words (experimental).",
     "packages": ["torch", "torchaudio", "uroman", "soundfile"]},
    {"id": "tts_omnivoice", "group": "Dubbing", "label": "Voice cloning: OmniVoice",
     "help": "Clone or design a voice locally.",
     "packages": ["omnivoice", "torch", "pydub", "huggingface_hub"]},
    {"id": "hardsub_ocr", "group": "Video", "label": "Read burned-in captions (OCR)",
     "help": "Pull hard-coded subtitles out of video frames.",
     "packages": ["cv2", "numpy", "PIL", "pytesseract", "paddleocr", "paddlepaddle"],
     "recommended": ["pytesseract"],
     "optional": ["paddleocr", "paddlepaddle"]},
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
     "packages": ["bs4", "trafilatura", "playwright", "lightnovel-crawler"],
     "recommended": ["trafilatura"],
     "optional": ["playwright", "lightnovel-crawler"]},
    {"id": "scanlate", "group": "Scanlate", "label": "Scanlate (manga/manhua pages)",
     "help": "Bubble detection, Japanese OCR, inpainting and PDF import.",
     "packages": ["cv2", "PIL", "numpy", "manga_ocr", "paddleocr", "paddlepaddle", "pypdf",
                  "transformers", "torch", "safetensors", "huggingface_hub", "sentencepiece"],
     "recommended": ["manga_ocr", "paddleocr", "paddlepaddle", "pypdf", "transformers", "torch",
                     "safetensors", "huggingface_hub"],
     "optional": ["sentencepiece"]},
    {"id": "paid_engines", "group": "Translation", "label": "Claude and DeepSeek",
     "help": "Client libraries for the paid translation engines (keys go in Settings).",
     "packages": ["anthropic", "openai"],
     "recommended": ["anthropic", "openai"]},
    {"id": "sign_in", "group": "App", "label": "Google sign-in for household access",
     "help": "Needed only when BAIHE_API_AUTH=on.",
     "packages": ["authlib", "httpx", "cryptography"]},
    {"id": "notifications", "group": "App", "label": "Desktop notifications",
     "help": "A notification when a background job finishes.",
     "packages": ["plyer"]},
    {"id": "benchmark_scoring", "group": "App", "label": "Benchmark Lab: standard CER/WER",
     "help": "Score transcription and OCR benchmarks with jiwer instead of the built-in scorer.",
     "packages": ["jiwer"]},
    {"id": "benchmark_translation_scoring", "group": "App",
     "label": "Benchmark Lab: chrF translation score",
     "help": "Score translation benchmarks with chrF (sacrebleu) instead of the built-in similarity ratio.",
     "packages": ["sacrebleu"]},
]


def check_python_version():
    import sys
    v = sys.version_info
    return {"version": f"{v.major}.{v.minor}.{v.micro}", "ok": v.major == 3 and v.minor >= 10}


def check_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        return {"found": False, "path": None, "version": None, "libass": None}
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, errors="replace", timeout=5)
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


YTDLP_STALE_DAYS = 90
DENO_MIN_VERSION = (2, 3)
PYANNOTE_MIN_VRAM_GB = 12
# PyTorch reports a card's usable memory, a little under its label (a 12 GB
# RTX 3080 Ti shows about 11.7-11.9), so the warning compares with a margin.
PYANNOTE_VRAM_MARGIN_GB = 0.5


def _ints(text: str, n: int):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(g or 0) for g in m.groups()[:n]) if m else None


def _warn_ytdlp_old(today=None):
    import datetime
    parts = _ints(get_installed_version("yt-dlp"), 3)
    if not parts:
        return None
    age = ((today or datetime.date.today()) - datetime.date(*parts)).days
    if age > YTDLP_STALE_DAYS:
        return ("yt-dlp is more than 3 months old, so video downloads may fail. "
                "Upgrade it in the Packages list.")
    return None


def _warn_deno_old():
    if not shutil.which("deno"):
        return None
    out = subprocess.run(["deno", "--version"], capture_output=True, errors="replace", timeout=5).stdout
    ver = _ints(out, 2)
    if ver and ver < DENO_MIN_VERSION:
        return ("Deno is older than 2.3, which yt-dlp may not work with. "
                "Reinstall Deno from deno.com.")
    return None


def _warn_qwen_asr_package():
    """The removed qwen-asr package pins transformers to 4.57.6, so while it
    is installed Diagnostics cannot move transformers up to what Qwen3-ASR
    now needs."""
    if not get_installed_version("qwen-asr"):
        return None
    if not below_min_version(get_installed_version("transformers"), "5.15"):
        return None
    return ("Qwen3-ASR no longer uses the qwen-asr package, which holds transformers back "
            "from the version it needs. Run `pip uninstall qwen-asr` in this app's Python, "
            "then update transformers in Diagnostics.")


def _warn_low_vram_pyannote():
    pyannote = _ints(get_installed_version("pyannote.audio"), 1)
    if not pyannote or pyannote[0] < 4:
        return None
    # diagnostics_torch imports this module, so it can only be imported at call time.
    import diagnostics_torch
    gpu = diagnostics_torch.get_gpu_status()
    total = gpu.get("vram_total_gb") if gpu.get("available") else None
    if total is not None and total < PYANNOTE_MIN_VRAM_GB - PYANNOTE_VRAM_MARGIN_GB:
        return ("This GPU has less than 12 GB of memory, so speaker detection may run out "
                "and switch to the CPU, which is slower. No action needed unless it fails.")
    return None


def startup_warnings() -> list:
    """Short, path-free warnings about risky dependency combinations. Each
    check is local and cheap; one that fails for any reason adds nothing."""
    out = []
    for check in (_warn_ytdlp_old, _warn_deno_old, _warn_qwen_asr_package,
                  _warn_low_vram_pyannote):
        try:
            msg = check()
        except Exception:
            msg = None
        if msg:
            out.append(msg)
    return out


def check_browser() -> dict:
    """{found, name, package}: the browser for JavaScript-only sites and
    whether the Playwright package is installed. No path is returned."""
    import browser_support
    import page_fetch
    return {**page_fetch.browser_status(), "package": browser_support.package_installed()}


def check_cuda() -> dict:
    """Whether a GPU is actually usable, for start.bat's own "print
    anything missing in plain words" launcher check -- this is
    deliberately the minimal "is it there at all" answer, not the
    driver/CUDA-build version-mismatch detail a later check adds to the
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
    like torch-backed packages). External programs (EXTERNAL_PROGRAMS)
    are looked up as programs instead."""
    if module_name in EXTERNAL_PROGRAMS:
        try:
            return bool(importlib.import_module(EXTERNAL_PROGRAMS[module_name]).is_installed())
        except Exception:
            return False
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
    return {
        "missing_top_level": missing_top_level,
        "all_present": not missing_top_level,
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
# Hugging Face model-cache visibility & cleanup.
#
# Whisper/pyannote/Qwen3-ASR/ForcedAligner/OmniVoice weights live in
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
        return audio_preprocess.MODEL_DIR
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
                # Still "installed" (the Update button fixes it), but the row
                # must not read as ready.
                if below_min_version(version, entry.get("min_version")):
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
    text = PATH_PATTERN.sub(lambda m: ".../" + m.group(1), text)
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
# In-app "Install" buttons for optional dependencies, run against
# the CURRENTLY RUNNING interpreter (sys.executable) -- when this app was
# launched via start.bat/portable.py's own venv activation, that's already
# the venv's own python, never a bare system `pip`.
# ---------------------------------------------------------------------------

# Only these two tiers ever get a generic Install button -- "required" is
# already installed by definition (the app wouldn't be running otherwise)
# and "dev" (pytest) has nothing to do with a running app session.
# "experimental" entries are listed but never installed from here.
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




# ---------------------------------------------------------------------------
# Install a whole requirements tier, and a real Deno install
# action -- both real subprocess actions triggered only from an explicit
# button click.
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


def deno_default_install_path() -> str:
    """Where Deno's own official installer puts the binary, regardless of
    whether the CURRENT process's PATH has picked it up yet -- used to
    tell "installed, but this process hasn't seen it yet" apart from
    "genuinely not installed" after a real install attempt."""
    home = os.path.expanduser("~")
    name = "deno.exe" if platform.system() == "Windows" else "deno"
    return os.path.join(home, ".deno", "bin", name)


# ---------------------------------------------------------------------------
# "Is this dependency outdated?" + an Upgrade action. Like
# check_pyannote_gated_access above, this reaches the network (PyPI's own
# public JSON API, a plain unauthenticated GET) -- so it must only ever run
# from an explicit button click, never automatically on page load, and the
# caller must cache the result rather than re-querying on every request.
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
    try:
        from lib import http
        resp = http.get(f"https://pypi.org/pypi/{pip_name}/json", timeout=timeout,
                        max_bytes=PYPI_JSON_MAX_BYTES, guard=None)
        if resp.status != 200:
            return None
        return (json.loads(resp.body).get("info") or {}).get("version") or None
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


def constraints_pip_args(project_root: str = None) -> list:
    """`["-c", <constraints.txt>]` when the file exists, else []. Every pip
    install that resolves dependencies passes this so a transitive pull
    can't cross a cap (e.g. av 19 breaking faster-whisper). Portable and
    installer layouts may ship without the file, so absence is not an error."""
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    constraints_path = os.path.join(project_root, "constraints.txt")
    return ["-c", constraints_path] if os.path.exists(constraints_path) else []


def upgrade_pip_args(pip_name: str, project_root: str = None) -> list:
    """pip args for `python -m pip install --upgrade <pip_name>`, adding
    constraints.txt's existing version caps (pyannote.audio<5,
    transformers<6, torch<3, faster-whisper<2, ...) via pip's
    own `-c` flag whenever the file exists -- the same mechanism
    the GPU PyTorch setup already uses for torch/torchaudio,
    generalized here since an Upgrade click can just as easily target any
    of constraints.txt's other pinned packages (e.g. transformers). A
    constraint for a package not named in the file is a no-op, so passing
    it unconditionally is always safe."""
    return ["--upgrade", pip_name, *constraints_pip_args(project_root)]


# ---------------------------------------------------------------------------
# When an "Upgrade" action can't actually reach the latest release for a
# real, known reason (a constraints.txt cap, or a package with no published
# wheel for the running Python version), say so instead of silently
# offering an upgrade that would fail, or offering nothing with no
# explanation. Only confirmed cases go here, not hypothetical ones.
# ---------------------------------------------------------------------------

KNOWN_UPGRADE_LIMITATIONS = {
    "audio-separator": {
        "python_version": (3, 14),
        "reason": "its diffq-fixed sub-dependency has wheels only through cp313, and its "
                  "sdist build also fails independently; Demucs, this app's "
                  "default vocal-separation backend, is unaffected.",
    },
    # Reproduced for real -- with huggingface_hub 2.0.0 installed
    # next to transformers 5.17.0, `import transformers` raises
    # "ImportError: huggingface-hub>=1.5.0,<2.0 is required ... but found
    # huggingface-hub==2.0.0", taking Scanlate's ML bubble
    # detector and the speech models down with it. This app's mocked test suite never
    # imports the real transformers, so only pip's own conflict report
    # caught it. Applies only while the installed transformers still
    # declares that cap, so it lifts itself once a transformers release
    # accepts huggingface_hub 2.x: transformers 5.15.0 declares
    # huggingface-hub<2.0,>=1.5 and 5.19.0 declares <3.0,>=1.31 (wheel METADATA).
    "huggingface-hub": {
        "blocked_from": 2,
        "while_required_below_by": "transformers",
        "reason": "the installed transformers (Scanlate's ML bubble detector, "
                  "speech models) requires huggingface_hub below 2.0 and refuses to import "
                  "with 2.x -- upgrade transformers (5.19 or newer accepts it) first.",
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
    and known_install_limitation_reason (the same package failing
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
    to install at all on this Python version -- shown next to a
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
    option with no explanation."""
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
    except ImportError:  # pragma: no cover - depends on the environment
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
    version_mod, _s, _r = _packaging()
    try:
        from lib import http
        resp = http.get(f"https://pypi.org/pypi/{canonical_dist(dist)}/json", timeout=timeout,
                        headers={"Accept": "application/json"}, max_bytes=PYPI_JSON_MAX_BYTES,
                        guard=None)
        if resp.status != 200:
            return None
        releases = json.loads(resp.body).get("releases") or {}
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


# Installed packages whose declared requirements never hold another package
# back: qwen-asr pins transformers==4.57.6, and the app no longer uses it
# (the startup warning tells the user to uninstall it).
IGNORED_REQUIRERS = {"qwen-asr"}


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
        if canonical_dist(requirer) in IGNORED_REQUIRERS:
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
