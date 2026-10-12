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


def _kept(rel: str, keep) -> bool:
    """`rel` is, or is a folder holding, one of the `keep` paths (relative
    to the drama folder): a title's pinned originals, which no category may
    remove even if its name patterns happen to match them."""
    rel = os.path.normpath(rel)
    return any(k == rel or k.startswith(rel + os.sep) for k in keep)


def scan_drama_storage(drama_dir: str, keep=()) -> dict:
    """Reports how much space each cleanable category is using in one
    drama's folder, plus the total and how much is safely reclaimable.
    Paths in `keep` are not counted as reclaimable."""
    keep = [os.path.normpath(k) for k in keep]
    result = {"total_bytes": 0, "categories": {}, "reclaimable_bytes": 0}
    if not os.path.isdir(drama_dir):
        return result

    result["total_bytes"] = _dir_size(drama_dir)
    for key, cfg in CLEANABLE_CATEGORIES.items():
        size = 0
        for d in cfg.get("pattern_dirs", []):
            p = os.path.join(drama_dir, d)
            if os.path.isdir(p) and not _kept(d, keep):
                size += _dir_size(p)
        for rel, full in _matching_files(drama_dir, cfg):
            if _kept(rel, keep):
                continue
            try:
                size += os.path.getsize(full)
            except OSError:
                pass
        result["categories"][key] = size
        result["reclaimable_bytes"] += size
    return result


def scan_library_storage(library_dir: str, drama_ids, keep_for=None) -> dict:
    """Library-wide storage report, with a per-drama breakdown sorted
    biggest-first so the space hogs are obvious. `keep_for(drama_id)`: the
    paths in that drama's folder never counted as reclaimable."""
    dramas_dir = os.path.join(library_dir, "dramas")
    per_drama = []
    totals = {"total_bytes": 0, "reclaimable_bytes": 0,
              "categories": {k: 0 for k in CLEANABLE_CATEGORIES}}

    for did in drama_ids:
        ddir = os.path.join(dramas_dir, str(did))
        scan = scan_drama_storage(ddir, keep_for(did) if keep_for else ())
        if scan["total_bytes"]:
            per_drama.append({"drama_id": did, **scan})
            totals["total_bytes"] += scan["total_bytes"]
            totals["reclaimable_bytes"] += scan["reclaimable_bytes"]
            for k, v in scan["categories"].items():
                totals["categories"][k] += v

    per_drama.sort(key=lambda d: d["total_bytes"], reverse=True)
    totals["per_drama"] = per_drama
    return totals


def clean_drama_storage(drama_dir: str, categories, keep=()) -> dict:
    """Deletes the given cleanable categories from one drama's folder.
    Only touches regenerable artifacts -- source audio/video, the
    reference novel, voice-clone reference clips, and the mixed dub
    track are never removed by this, regardless of what's requested.
    Paths in `keep` (relative to the folder) are never removed."""
    keep = [os.path.normpath(k) for k in keep]
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
            if os.path.isdir(p) and not _kept(d, keep):
                freed += _dir_size(p)
                shutil.rmtree(p, ignore_errors=True)
                removed.append(d + "/")
        for rel, full in _matching_files(drama_dir, cfg):
            if _kept(rel, keep):
                continue
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
# now" can't pull a folder out from under a browser, an import or an export
# that is not a registered job (the sign-in window, a restore). Paths are kept
# normalised: a hand-edited portable marker can spell the library root with
# other separators or case than mkdtemp returns.
_held_lock = threading.Lock()
_held = set()
# Lets another Baihe process on the same library see that a folder is in use;
# the in-memory set only protects this process.
HOLD_MARKER = ".baihe-hold"
# A pid can be reused after a crash, so a marker this old is ignored.
HOLD_MARKER_MAX_AGE = 3 * 24 * 3600


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


@contextlib.contextmanager
def holding(path: str):
    """Keeps sweeps (this process and others) off `path` while the block runs.
    Does not remove it."""
    key = _norm(path)
    with _held_lock:
        _held.add(key)
    try:
        with open(os.path.join(path, HOLD_MARKER), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass  # the in-memory hold still covers this process
    try:
        yield path
    finally:
        with _held_lock:
            _held.discard(key)
        # A folder that outlives the hold (a live session's) must not keep
        # claiming to be in use.
        with contextlib.suppress(OSError):
            os.remove(os.path.join(path, HOLD_MARKER))


def _held_by_other_process(path: str, now: float) -> bool:
    marker = os.path.join(path, HOLD_MARKER)
    try:
        if now - os.lstat(marker).st_mtime > HOLD_MARKER_MAX_AGE:
            return False
        with open(marker, encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False  # ours but not in _held: left by a block that has ended
    import background_jobs
    return background_jobs.owner_process_alive(pid)


def _read_small_regular_file(path: str, limit: int = 4096) -> str | None:
    """The text of `path` if it is a plain file (never a link) of at most
    `limit` bytes, else None."""
    # O_NONBLOCK: opening a FIFO planted under the marker's name would
    # otherwise wait for a writer forever and hang startup.
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        if _is_link(path) or not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        return os.read(fd, limit + 1).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)


# Next to the hold marker: one file per external process a job started in its
# folder, naming the process by pid, start time and command, so a later start
# can stop it if this server dies first without trusting the pid alone.
CHILD_MARKER_PREFIX = ".baihe-child-"
# Pids are 32-bit everywhere Baihe runs; anything larger is a corrupt note.
_MAX_PID = 2**31 - 1


def record_job_child(job_id, pid: int) -> int:
    """Notes job `job_id`'s external process in the work folders this process
    holds for that job (job_workdir(job_id)). Returns how many folders got the
    note; 0 when the job holds none or the process can't be identified."""
    import json

    from lib import proc_kill
    identity = proc_kill.process_identity(pid)
    if identity is None:
        return 0
    owner = os.path.normcase(_owner_token(job_id))
    with _held_lock:
        folders = [p for p in _held
                   if _OWNER_SEP in os.path.basename(p)
                   and os.path.basename(p).rsplit(_OWNER_SEP, 1)[0] == owner]
    note = json.dumps({"pid": int(pid), "identity": identity})
    written = 0
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_BINARY", 0))
    for folder in folders:
        try:
            fd = os.open(os.path.join(folder, f"{CHILD_MARKER_PREFIX}{int(pid)}"), flags, 0o600)
        except OSError:
            continue
        try:
            os.write(fd, note.encode("utf-8"))
            written += 1
        except OSError:
            pass
        finally:
            os.close(fd)
    return written


def _hold_owner_pid(path: str) -> int | None:
    text = _read_small_regular_file(os.path.join(path, HOLD_MARKER), limit=64)
    try:
        pid = int(text.strip())
    except (AttributeError, ValueError):
        return None
    return pid if 0 < pid <= _MAX_PID else None


def _child_notes(path: str) -> list | None:
    """[(pid, identity)] for every child note in `path`; None if any note is
    unreadable, because a note that can't be read might name a live child."""
    import json
    notes = []
    try:
        names = [n for n in os.listdir(path) if n.startswith(CHILD_MARKER_PREFIX)]
    except OSError:
        return None
    for name in names:
        try:
            data = json.loads(_read_small_regular_file(os.path.join(path, name)) or "")
            pid, identity = int(data["pid"]), data["identity"]
        except (ValueError, TypeError, KeyError, OverflowError):
            return None
        if not isinstance(identity, dict) or not 0 < pid <= _MAX_PID:
            return None
        notes.append((pid, identity))
    return notes


def stop_orphaned_job_children() -> dict:
    """Startup only. For each folder directly in <library>/tmp whose hold
    marker names a Baihe process that is no longer running: stops the
    external processes its jobs noted (record_job_child) if each is still
    the very process noted, then removes the folder. A folder is left alone
    when it is a link, has no readable hold marker, its owner is alive
    (another Baihe on this library, or a pid now used by something else), it
    belongs to a job or hold of this process, or any noted process could not
    be confirmed stopped. Returns {"stopped", "removed"}; logs counts only."""
    import background_jobs
    import db
    from lib import proc_kill
    out = {"stopped": 0, "removed": 0}
    root = os.path.join(db.LIBRARY_DIR, TEMP_DIRNAME)
    if _is_link(root):
        return out
    try:
        names = os.listdir(root)
    except OSError:
        return out
    live_jobs = {_owner_token(j) for j in background_jobs.active_job_ids()}
    with _held_lock:
        held = set(_held)
    deadline = time.monotonic() + SWEEP_MAX_SECONDS
    for examined, name in enumerate(names):
        if examined >= SWEEP_MAX_EXAMINED or time.monotonic() > deadline:
            break
        path = os.path.join(root, name)
        owner_job = name.rsplit(_OWNER_SEP, 1)[0] if _OWNER_SEP in name else None
        if (owner_job in live_jobs or _norm(path) in held or _is_link(path)
                or not os.path.isdir(path)):
            continue
        owner = _hold_owner_pid(path)
        if owner is None or owner == os.getpid() or background_jobs.owner_process_alive(owner):
            continue
        notes = _child_notes(path)
        if notes is None:
            continue
        results = [proc_kill.stop_if_same(pid, identity) for pid, identity in notes]
        out["stopped"] += results.count("stopped")
        if "kept" in results:
            continue
        try:
            shutil.rmtree(path)
            out["removed"] += 1
        except OSError:
            pass   # a file still open on Windows; the age-based sweep retries later
    if out["stopped"] or out["removed"]:
        import logging
        logging.getLogger("baihe").info(
            "stopped %d leftover job process(es) and removed %d work folder(s) of a "
            "Baihe that is no longer running", out["stopped"], out["removed"])
    return out


@contextlib.contextmanager
def job_workdir(job_id=None, dir=None):
    """A work folder removed on exit. `dir` overrides the parent (tests)."""
    if dir:
        path = tempfile.mkdtemp(dir=dir)
    else:
        path = new_workdir(job_id)
    try:
        with holding(path):
            yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _is_link(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    # Windows junctions and other reparse points
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


# Folders Baihe creates straight in the system temp folder (older versions
# made more of them than current code does). Matched by
# name prefix only: the system temp is shared with every other program, so a
# folder is Baihe's only when it carries one of these.
SYSTEM_TEMP_PREFIXES = (
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
        if owner in live or _norm(path) in held or _is_link(path) or not accept(name, path):
            continue
        try:
            if now - os.lstat(path).st_mtime < max_age:
                continue
            is_dir = os.path.isdir(path)
            if is_dir and _held_by_other_process(path, now):
                continue
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
    """Removes stale folders Baihe left in the system temp folder
    (SYSTEM_TEMP_PREFIXES, folders only). Nothing else there is touched."""
    now = time.time() if now is None else now
    root = system_temp or tempfile.gettempdir()
    return _sweep_folder(
        root, lambda name, path: name.startswith(SYSTEM_TEMP_PREFIXES) and os.path.isdir(path),
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
