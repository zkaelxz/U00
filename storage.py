"""
storage.py -- disk management for a library that grows fast.

At 150-200 dramas with audio, dub clips, and page images, storage
becomes the real ceiling. This module reports where the space is going
and cleans the parts that are safely regenerable, while never touching
irreplaceable files (your source audio, the database).
"""

import contextlib
import errno
import os
import re
import shutil
import stat
import tempfile
import threading
import time


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


# ---------------------------------------------------------------------------
# Library temp folder: the one place job work folders and partial export
# files live, so a startup sweep can clear what a crash or a cancelled
# queued job left behind, and backups and restores can skip it.
# ---------------------------------------------------------------------------

TEMP_DIRNAME = "tmp"
STALE_TEMP_SECONDS = 24 * 3600
_OWNER_SEP = "~"


def temp_root() -> str:
    """<library>/tmp, created on demand."""
    import db
    root = os.path.join(db.LIBRARY_DIR, TEMP_DIRNAME)
    os.makedirs(root, exist_ok=True)
    return root


def _owner_token(job_id) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id or "anon"))[:80]


def new_workdir(job_id=None) -> str:
    """A fresh folder in the temp root named after the job that owns it
    ("<job id>~<random>"), so the sweep never removes a live job's folder."""
    return tempfile.mkdtemp(prefix=_owner_token(job_id) + _OWNER_SEP, dir=temp_root())


def new_partial_file(prefix: str, suffix: str = ".part") -> str:
    """A fresh empty file in the temp root (caller removes or renames it)."""
    fd, path = tempfile.mkstemp(prefix=_owner_token(prefix) + _OWNER_SEP, suffix=suffix,
                                dir=temp_root())
    os.close(fd)
    return path


# Work folders in use by this process. A sweep never touches them, so "clean
# now" can't pull a folder out from under a browser or export that is not a
# registered job (the sign-in window, a restore).
_held_lock = threading.Lock()
_held = set()


@contextlib.contextmanager
def job_workdir(job_id=None, dir=None):
    """A work folder removed on exit. `dir` overrides the parent (tests)."""
    if dir:
        path = tempfile.mkdtemp(dir=dir)
    else:
        path = new_workdir(job_id)
    with _held_lock:
        _held.add(path)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        with _held_lock:
            _held.discard(path)


def _is_link(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    # Windows junctions and other reparse points
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


# Folders Baihe once created straight in the system temp folder. Matched by
# name prefix only: the system temp is shared with every other program, so a
# folder is Baihe's only when it carries one of these.
LEGACY_TEMP_PREFIXES = (
    "baihe_live_", "baihe_vocab_", "baihe_scanlate_", "baihe_vocalsep_chunks_",
    "baihe_pronounce_", "baihe_anki_", "baihe_deno_", "baihe_upgrade_check_",
    "baihe_qwen3_asr_", "baihe_forced_align_", "baihe_sensevoice_",
)
# A start must stay quick even if a temp folder holds a huge number of entries.
SWEEP_MAX_EXAMINED = 5000
SWEEP_MAX_REMOVED = 500
SWEEP_MAX_SECONDS = 10.0


def _tree_bytes(path: str) -> int:
    """Size of a tree without following links; unreadable parts count as 0."""
    total = 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_symlink() or _is_link(entry.path):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _sweep_folder(root: str, accept, max_age: float, now: float, live: set,
                  measure: bool) -> dict:
    """Removes entries of `root` that `accept(name, path)` allows, are older
    than `max_age`, are not links, are not owned by a live job and are not a
    folder this process holds. Best effort: Windows keeps files open, so any
    OS error just leaves the entry for next time. Bounded by
    SWEEP_MAX_EXAMINED / SWEEP_MAX_REMOVED / SWEEP_MAX_SECONDS."""
    out = {"removed": 0, "freed_bytes": 0}
    if _is_link(root):
        return out
    try:
        names = os.listdir(root)
    except OSError:
        return out
    with _held_lock:
        held = set(_held)
    deadline = time.monotonic() + SWEEP_MAX_SECONDS
    for examined, name in enumerate(names):
        if (examined >= SWEEP_MAX_EXAMINED or out["removed"] >= SWEEP_MAX_REMOVED
                or time.monotonic() > deadline):
            break
        path = os.path.join(root, name)
        owner = name.rsplit(_OWNER_SEP, 1)[0] if _OWNER_SEP in name else None
        if owner in live or path in held or _is_link(path) or not accept(name, path):
            continue
        try:
            if now - os.lstat(path).st_mtime < max_age:
                continue
            is_dir = os.path.isdir(path)
            size = (_tree_bytes(path) if is_dir else os.lstat(path).st_size) if measure else 0
            if is_dir:
                shutil.rmtree(path)
            else:
                os.remove(path)
            out["removed"] += 1
            out["freed_bytes"] += size
        except OSError:
            continue
    return out


def sweep_library_temp(max_age: float = STALE_TEMP_SECONDS, now: float = None,
                       measure: bool = False) -> dict:
    """{"removed", "freed_bytes"} for <library>/tmp (see sweep_stale_temp).
    freed_bytes is only measured when `measure` is set: it costs a walk."""
    import background_jobs
    import db
    now = time.time() if now is None else now
    live = {_owner_token(j) for j in background_jobs.active_job_ids()}
    root = os.path.join(db.LIBRARY_DIR, TEMP_DIRNAME)
    return _sweep_folder(root, lambda name, path: True, max_age, now, live, measure)


def sweep_legacy_system_temp(max_age: float = STALE_TEMP_SECONDS, now: float = None,
                             system_temp: str = None) -> int:
    """Removes folders left in the system temp folder by older Baihe versions
    (LEGACY_TEMP_PREFIXES, folders only). Nothing else there is touched."""
    now = time.time() if now is None else now
    root = system_temp or tempfile.gettempdir()
    return _sweep_folder(
        root, lambda name, path: name.startswith(LEGACY_TEMP_PREFIXES) and os.path.isdir(path),
        max_age, now, set(), False)["removed"]


def sweep_stale_temp(max_age: float = STALE_TEMP_SECONDS, now: float = None) -> int:
    """Startup sweep of the library temp folder: entries older than
    `max_age` whose owning job is not queued or running. Links (symlinks,
    junctions) are left alone and never followed. Besides the temp folder it
    removes separator checkpoints left truncated by a killed download (see
    audio_preprocess.sweep_interrupted_downloads). Returns the number of
    temp entries removed."""
    try:
        import audio_preprocess
        audio_preprocess.sweep_interrupted_downloads()
    except Exception:
        pass  # a model-folder hiccup must not stop the temp sweep
    return sweep_library_temp(max_age, now)["removed"]


# ---------------------------------------------------------------------------
# Playwright/Chromium temp folders. The node driver makes
# playwright_chromiumdev_profile-* and playwright-artifacts-* with os.tmpdir(),
# which follows TMPDIR/TEMP/TMP of the driver process. Only that process gets
# the override: changing os.environ would redirect every other thread and
# subprocess of the server, and Baihe must never touch another program's
# Playwright folders.
# ---------------------------------------------------------------------------

_driver_env_lock = threading.Lock()


@contextlib.contextmanager
def _driver_tmp_env(path: str):
    """While active, the driver Playwright spawns gets TMPDIR/TEMP/TMP=path.
    The environment is read once, when the driver starts, so this only needs
    to wrap that start. Without the hook (another Playwright version) the
    driver keeps the default temp folder and the launch still works."""
    try:
        from playwright._impl import _transport
        original = _transport.get_driver_env
    except (ImportError, AttributeError):
        original = None
    if original is None:
        yield
        return

    def confined():
        env = original()
        env.update(TMPDIR=path, TEMP=path, TMP=path)
        return env

    with _driver_env_lock:
        _transport.get_driver_env = confined
        try:
            yield
        finally:
            _transport.get_driver_env = original


@contextlib.contextmanager
def playwright_session(sync_playwright):
    """`with sync_playwright() as p`, with the driver's temp folder confined
    and removed after the driver has stopped, whatever happens inside."""
    with job_workdir("playwright") as path:
        with contextlib.ExitStack() as stack:
            with _driver_tmp_env(path):
                pw = stack.enter_context(sync_playwright())
            yield pw


def playwright_start(sync_playwright):
    """(playwright, release) for a launch the caller closes later (a
    persistent browser profile). The caller stops the driver first, then calls
    `release()` once to remove the temp folder."""
    workdir = job_workdir("playwright")
    path = workdir.__enter__()
    try:
        with _driver_tmp_env(path):
            pw = sync_playwright().start()
    except BaseException:
        workdir.__exit__(None, None, None)
        raise
    return pw, lambda: workdir.__exit__(None, None, None)


def move_into_place(src, dst):
    """Moves src over dst so a reader never sees a partial dst: os.replace
    on one volume (shutil.move would copy then delete when dst exists on
    Windows). Across volumes, copies next to dst first and replaces from
    there."""
    try:
        os.replace(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    fd, tmp = tempfile.mkstemp(prefix=".part-", suffix=os.path.splitext(dst)[1],
                               dir=os.path.dirname(dst) or None)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    with contextlib.suppress(OSError):
        os.remove(src)
