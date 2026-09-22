"""
diagnostics.py -- environment self-check: which optional dependencies
are actually installed, whether ffmpeg is on PATH, whether every
expected project file is present, and which API keys are configured.

Built directly in response to a real support issue during setup: a
missing file produced a cryptic ModuleNotFoundError that took several
back-and-forth messages to diagnose. This turns that into one glance.
"""

import os
import shutil
import subprocess
import importlib.util

# Every top-level .py file and tabs/*.py file expected to exist for the
# app to run. Kept as an explicit list (not auto-discovered) so a
# missing file shows up as "missing" rather than just not being checked.
EXPECTED_TOP_LEVEL_FILES = [
    "app.py", "common.py", "core.py", "db.py", "translate_engines.py",
    "diarize.py", "dub.py", "video_export.py", "ocr.py", "segment.py",
    "dictionary.py", "reader.py", "scanlate.py", "metadata_lookup.py",
    "navigator.py", "known_sites.py", "title_library.py", "vocab_export.py",
    "qa.py", "bulk_import.py", "epub_io.py", "cli.py", "diagnostics.py",
    "export_package.py", "run_tests.py", "translation_guide.py",
    "story_context.py", "storage.py", "universe_wiki.py", "background_jobs.py",
    "adaptive_style.py", "line_tools.py", "emotion.py", "ui_theme.py", "page_fetch.py",
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
    "edge_tts": ("edge_tts", "free online dubbing", "feature"),
    "pydub": ("pydub", "dub/narration track mixing", "feature"),
    "f5_tts": ("f5_tts", "local voice cloning", "feature"),
    "elevenlabs": ("elevenlabs", "hosted voice cloning", "feature"),
    "pytesseract": ("pytesseract", "OCR (Tesseract backend)", "feature"),
    "PIL": ("PIL", "OCR, Scanlate rendering", "required"),
    "paddleocr": ("paddleocr", "OCR (PaddleOCR backend)", "feature"),
    "manga_ocr": ("manga_ocr", "OCR (Japanese manga backend)", "feature"),
    "piper": ("piper", "offline TTS", "feature"),
    "jieba": ("jieba", "Chinese word segmentation (Reader)", "feature"),
    "pypinyin": ("pypinyin", "Chinese pinyin (Reader)", "feature"),
    "sudachipy": ("sudachipy", "Japanese word segmentation (Reader)", "feature"),
    "pykakasi": ("pykakasi", "Japanese furigana (Reader)", "feature"),
    "kiwipiepy": ("kiwipiepy", "Korean word segmentation (Reader)", "feature"),
    "ultralytics": ("ultralytics", "ML bubble detection (Scanlate)", "feature"),
    "huggingface_hub": ("huggingface_hub", "ML bubble detection, voice cloning model downloads", "feature"),
    "genanki": ("genanki", "Anki .apkg export (Reader vocab)", "feature"),
    "ebooklib": ("ebooklib", "EPUB import/export", "feature"),
    "playwright": ("playwright", "reading JavaScript-rendered sites (baihehub, Fanjiao)", "feature"),
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


def run_full_diagnostics(project_root: str, library_dir: str, api_keys_set: dict):
    """api_keys_set: dict like {"claude": bool, "deepseek": bool, ...}
    -- pass whatever's currently in session state, since diagnostics.py
    itself has no access to Streamlit session state."""
    return {
        "python": check_python_version(),
        "ffmpeg": check_ffmpeg(),
        "dependencies": check_all_dependencies(),
        "files": check_file_completeness(project_root),
        "library_writable": check_library_writable(library_dir),
        "api_keys": api_keys_set,
    }
