"""
storage.py -- disk management for a library that grows fast.

At 150-200 dramas with audio, dub clips, and page images, storage
becomes the real ceiling. This module reports where the space is going
and cleans the parts that are safely regenerable, while never touching
irreplaceable files (your source audio, the database).
"""

import os
import shutil


# What each category is, and whether losing it is recoverable.
# "regenerable" = the app can rebuild it; safe to clean.
# "source" = irreplaceable, never auto-cleaned.
CLEANABLE_CATEGORIES = {
    "dub_clips": {
        "label": "Per-line dub clips",
        "pattern_dirs": ["dub_clips"],
        "regenerable": True,
        "note": "Intermediate TTS clips. The mixed dub track is kept. "
                "Regenerating costs API calls if you used cloud cloning.",
    },
    "ocr_temp": {
        "label": "OCR temp images",
        "pattern_prefixes": ["ocr_", "_bubble_crop"],
        "regenerable": True,
        "note": "Page scans copied in for OCR. Extracted text is already saved.",
    },
    "typeset_pages": {
        "label": "Rendered typeset pages",
        # The comic editor renders pages/typeset_NNNN.png (plus the
        # typeset_pages.zip/.pdf bundles) next to the source scans in pages/.
        "pattern_subdir": "pages",
        "pattern_prefixes": ["typeset_"],
        "regenerable": True,
        "note": "Re-renderable from saved bubbles at no API cost.",
    },
    "temp_files": {
        "label": "Leftover temp files",
        "pattern_suffixes": [".tmp.png", ".tmp"],
        "regenerable": True,
        "note": "Stray working files from interrupted operations.",
    },
}

STORAGE_QUALITY_PRESETS = {
    "archival": {
        "label": "Archival — keep everything",
        "keep_dub_clips": True, "keep_typeset_pages": True, "keep_ocr_temp": True,
        "dub_bitrate": "192k",
        "note": "Nothing auto-removed. Largest footprint, fastest re-export.",
    },
    "balanced": {
        "label": "Balanced — keep finals, drop intermediates",
        "keep_dub_clips": False, "keep_typeset_pages": True, "keep_ocr_temp": False,
        "dub_bitrate": "128k",
        "note": "Keeps finished output, clears per-line clips and OCR scratch. "
                "Recommended for most libraries.",
    },
    "minimal": {
        "label": "Minimal — sources and text only",
        "keep_dub_clips": False, "keep_typeset_pages": False, "keep_ocr_temp": False,
        "dub_bitrate": "96k",
        "note": "Smallest footprint. Re-export anything you need again.",
    },
}


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _matching_files(drama_dir: str, cfg: dict) -> list:
    """(relative name, full path) of the files a category's name patterns
    match, in the drama folder itself or in its `pattern_subdir`."""
    subdir = cfg.get("pattern_subdir", "")
    folder = os.path.join(drama_dir, subdir) if subdir else drama_dir
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for entry in entries:
        full = os.path.join(folder, entry)
        if not os.path.isfile(full):
            continue
        if (any(entry.startswith(pre) for pre in cfg.get("pattern_prefixes", []))
                or entry in cfg.get("pattern_names", [])
                or any(entry.endswith(suf) for suf in cfg.get("pattern_suffixes", []))):
            out.append((os.path.join(subdir, entry) if subdir else entry, full))
    return out


def format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


def scan_drama_storage(drama_dir: str) -> dict:
    """Reports how much space each cleanable category is using in one
    drama's folder, plus the total and how much is safely reclaimable."""
    result = {"total_bytes": 0, "categories": {}, "reclaimable_bytes": 0}
    if not os.path.isdir(drama_dir):
        return result

    result["total_bytes"] = _dir_size(drama_dir)
    for key, cfg in CLEANABLE_CATEGORIES.items():
        size = 0
        for d in cfg.get("pattern_dirs", []):
            p = os.path.join(drama_dir, d)
            if os.path.isdir(p):
                size += _dir_size(p)
        for _rel, full in _matching_files(drama_dir, cfg):
            try:
                size += os.path.getsize(full)
            except OSError:
                pass
        result["categories"][key] = size
        result["reclaimable_bytes"] += size
    return result


def scan_library_storage(library_dir: str, drama_ids) -> dict:
    """Library-wide storage report, with a per-drama breakdown sorted
    biggest-first so the space hogs are obvious."""
    dramas_dir = os.path.join(library_dir, "dramas")
    per_drama = []
    totals = {"total_bytes": 0, "reclaimable_bytes": 0,
              "categories": {k: 0 for k in CLEANABLE_CATEGORIES}}

    for did in drama_ids:
        ddir = os.path.join(dramas_dir, str(did))
        scan = scan_drama_storage(ddir)
        if scan["total_bytes"]:
            per_drama.append({"drama_id": did, **scan})
            totals["total_bytes"] += scan["total_bytes"]
            totals["reclaimable_bytes"] += scan["reclaimable_bytes"]
            for k, v in scan["categories"].items():
                totals["categories"][k] += v

    per_drama.sort(key=lambda d: d["total_bytes"], reverse=True)
    totals["per_drama"] = per_drama
    return totals


def clean_drama_storage(drama_dir: str, categories) -> dict:
    """Deletes the given cleanable categories from one drama's folder.
    Only touches regenerable artifacts -- source audio/video, the
    reference novel, voice-clone reference clips, and the mixed dub
    track are never removed by this, regardless of what's requested."""
    freed = 0
    removed = []
    if not os.path.isdir(drama_dir):
        return {"freed_bytes": 0, "removed": []}

    for key in categories:
        cfg = CLEANABLE_CATEGORIES.get(key)
        if not cfg or not cfg.get("regenerable"):
            continue
        for d in cfg.get("pattern_dirs", []):
            p = os.path.join(drama_dir, d)
            if os.path.isdir(p):
                freed += _dir_size(p)
                shutil.rmtree(p, ignore_errors=True)
                removed.append(d + "/")
        for rel, full in _matching_files(drama_dir, cfg):
            try:
                size = os.path.getsize(full)
                os.remove(full)
            except OSError:
                continue
            freed += size
            removed.append(rel)
    return {"freed_bytes": freed, "removed": removed}


def categories_for_preset(preset_key: str):
    """Which categories a storage-quality preset would clean."""
    preset = STORAGE_QUALITY_PRESETS.get(preset_key, STORAGE_QUALITY_PRESETS["balanced"])
    cats = ["temp_files"]  # always safe
    if not preset["keep_dub_clips"]:
        cats.append("dub_clips")
    if not preset["keep_ocr_temp"]:
        cats.append("ocr_temp")
    if not preset["keep_typeset_pages"]:
        cats.append("typeset_pages")
    return cats
