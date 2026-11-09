"""services/inbox_service.py -- the watch folder: audio and video files dropped into one folder on the
PC become titles, and each title's Transcribe goes on the job queue (or, in a series with auto-run on,
the series' own pipeline takes over, inside its spending ceiling).

Off until the PC owner turns it on. The folder is read flat (no sub-folders, no links) and only
by polling: a file is taken when its size and modified time have stayed the same across two passes
at least STABLE_SECONDS apart and it opens for reading, so a copy in progress or a file another
program holds is left for a later pass. Originals are never changed or deleted; with "move" on they are
moved into a `Done` sub-folder after the title exists. The inbox never queues Translate itself: that can
spend money, and only a series' auto-run has a ceiling for it.
"""

import contextlib
import hashlib
import logging
import os
import shutil
import threading
import time
from typing import Optional

import background_jobs
import db
from engine_backends import shared as engine_shared
from lib.errors import InvalidInputError, ServiceError
from lib.safe_paths import plain_file_in
from services import drama_service, media_upload_service, series_service, settings_service

KEY = "inbox"
STABLE_SECONDS = 20.0
DONE_FOLDER = "Done"
SEEN_CAP = 5000
MAX_PATH = 1024
MEDIA_EXTENSIONS = media_upload_service.AUDIO_EXTENSIONS + media_upload_service.VIDEO_EXTENSIONS
LANGUAGES = ("zh", "ja", "ko")

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_worker: Optional[threading.Thread] = None
# name -> (size, mtime_ns, first time seen with these values); a pass that sees different values restarts it.
_candidates: dict = {}
# Video titles whose audio is still being extracted: drama_id -> job_id. Lost on restart, which leaves a
# title with its media and no queue item; the Queue page adds it by hand.
_awaiting: dict = {}
_status = {"message": "", "last_title_id": None, "imported": 0}


def _clock() -> float:
    return time.monotonic()


def _empty() -> dict:
    return {"on": False, "path": "", "source_language": "ja", "series_id": None, "move": False, "seen": []}


def _read() -> dict:
    cfg = _empty()
    raw = settings_service.get(KEY)
    if not isinstance(raw, dict):
        return cfg
    cfg["on"] = raw.get("on") is True
    if isinstance(raw.get("path"), str):
        cfg["path"] = raw["path"][:MAX_PATH]
    if raw.get("source_language") in LANGUAGES:
        cfg["source_language"] = raw["source_language"]
    if type(raw.get("series_id")) is int:
        cfg["series_id"] = raw["series_id"]
    cfg["move"] = raw.get("move") is True
    if isinstance(raw.get("seen"), list):
        cfg["seen"] = [s for s in raw["seen"] if isinstance(s, str)][-SEEN_CAP:]
    return cfg


def _remember(fingerprint: str) -> None:
    def change(raw):
        cfg = raw if isinstance(raw, dict) else {}
        seen = [s for s in cfg.get("seen", []) if isinstance(s, str)]
        if fingerprint not in seen:
            seen.append(fingerprint)
        return {**cfg, "seen": seen[-SEEN_CAP:]}
    db.update_app_setting(KEY, change, {})


def _fingerprint(name: str, size: int) -> str:
    # The modified time is left out on purpose: a file copied again (new time, same name and size) is
    # the same episode, not a new title.
    return hashlib.sha1(f"{name.lower()}|{size}".encode("utf-8")).hexdigest()


def _check_folder(raw) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_PATH or "\x00" in raw:
        raise InvalidInputError("Give the full path of a folder on the Baihe PC.")
    path = os.path.realpath(raw.strip())
    if not os.path.isdir(path):
        raise InvalidInputError("That folder doesn't exist on the Baihe PC.")
    # The app's own library holds titles' files; reading it as an inbox would import them again.
    library = os.path.realpath(db.LIBRARY_DIR)
    if library and (path == library or path.startswith(library + os.sep)
                    or library.startswith(path + os.sep)):
        raise InvalidInputError("Pick a folder outside the Baihe library.")
    return path


def get_state() -> dict:
    cfg = _read()
    return {"on": cfg["on"], "folder_set": bool(cfg["path"]),
            "folder_name": os.path.basename(cfg["path"].rstrip("\\/")), "source_language": cfg["source_language"],
            "series_id": cfg["series_id"], "move": cfg["move"], "message": _status["message"],
            "imported": _status["imported"], "last_title_id": _status["last_title_id"]}


def configure(on, path, source_language, series_id, move) -> dict:
    """A blank `path` keeps the saved folder."""
    path = (path or "").strip() or _read()["path"]
    if not isinstance(on, bool) or not isinstance(move, bool):
        raise InvalidInputError("on and move must be true or false.")
    if source_language not in LANGUAGES:
        raise InvalidInputError("Pick the language the audio is in: Chinese, Japanese or Korean.")
    if series_id is not None:
        if type(series_id) is not int or series_id not in {s["id"] for s in series_service.list_series(None)}:
            raise InvalidInputError("That series doesn't exist.")
    folder = _check_folder(path) if (on or (path or "").strip()) else ""

    def change(raw):
        cfg = raw if isinstance(raw, dict) else {}
        # A new folder starts a fresh seen list: files there were never imported.
        seen = cfg.get("seen", []) if cfg.get("path") == folder else []
        return {"on": on, "path": folder, "source_language": source_language,
                "series_id": series_id, "move": move, "seen": seen}
    db.update_app_setting(KEY, change, {})
    with _lock:
        _candidates.clear()
        _status["message"] = ""
    return get_state()


def _stable(name: str, size: int, mtime_ns: int, now: float) -> bool:
    seen = _candidates.get(name)
    if seen is None or seen[0] != size or seen[1] != mtime_ns:
        _candidates[name] = (size, mtime_ns, now)
        return False
    return now - seen[2] >= STABLE_SECONDS


def _openable(path: str) -> bool:
    # Windows refuses to open a file another program has locked, which is what a copy in progress does.
    try:
        with open(path, "rb") as fh:
            fh.read(1)
        return True
    except OSError:
        return False


def _eligible(root: str, cfg: dict, now: float) -> Optional[tuple]:
    """The first dropped file that is ready: (name, path, size), or None."""
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError as exc:
        _status["message"] = f"The inbox folder can't be read: {exc.strerror or 'error'}."
        return None
    names = set()
    limit = media_upload_service.max_upload_bytes()
    ready = None
    for entry in entries:
        name = entry.name
        if name.startswith(".") or os.path.splitext(name)[1].lower() not in MEDIA_EXTENSIONS:
            continue
        try:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        path = plain_file_in(root, name)
        if path is None or info.st_size <= 0 or info.st_size > limit:
            continue
        names.add(name)
        if _fingerprint(name, info.st_size) in cfg["seen"]:
            continue
        if _stable(name, info.st_size, info.st_mtime_ns, now) and ready is None and _openable(path):
            ready = (name, path, info.st_size)
    for gone in set(_candidates) - names:
        del _candidates[gone]
    return ready


def _move_to_done(root: str, name: str, path: str) -> None:
    folder = os.path.join(root, DONE_FOLDER)
    os.makedirs(folder, exist_ok=True)
    stem, ext = os.path.splitext(name)
    target, n = os.path.join(folder, name), 1
    while os.path.exists(target):
        n += 1
        target = os.path.join(folder, f"{stem}-{n}{ext}")
    shutil.move(path, target)


def _import(root: str, cfg: dict, name: str, path: str, size: int) -> None:
    title = os.path.splitext(name)[0][:200] or "Dropped file"
    created = drama_service.create_drama(
        source_language=cfg["source_language"], title_en=title, series_id=cfg["series_id"],
        media_type="video_drama" if os.path.splitext(name)[1].lower()
        in media_upload_service.VIDEO_EXTENSIONS else "audio_drama")
    drama_id = created["id"]
    try:
        with open(path, "rb") as fh:
            result = media_upload_service.upload_media(drama_id, name, fh)
    except BaseException:
        # Nothing was stored for it, so an empty title would only clutter the library.
        with contextlib.suppress(Exception):
            drama_service.delete_drama(drama_id, confirm=True, confirm_text="DELETE")
        raise
    _remember(_fingerprint(name, size))
    with _lock:
        _status["imported"] += 1
        _status["last_title_id"] = drama_id
        _status["message"] = f"Added \"{title}\"."
        if result.get("job_id"):
            _awaiting[drama_id] = result["job_id"]
    if cfg["series_id"] is None and not result.get("job_id"):
        _queue_transcribe(drama_id)
    if cfg["move"]:
        try:
            _move_to_done(root, name, path)
        except OSError as exc:
            _log.warning("Inbox: could not move %s to Done: %s", name, engine_shared.redact_secrets(str(exc)))


def _queue_transcribe(drama_id: int) -> None:
    from services import queue_service   # queue_service imports this module
    try:
        queue_service.add_item(None, drama_id, "transcribe", True, at_pc=True)
    except ServiceError as exc:
        _status["message"] = f"Title {drama_id} was added but could not be queued: {exc.message}"


def _finish_extractions(cfg: dict) -> None:
    for drama_id, job_id in list(_awaiting.items()):
        job = background_jobs.get_status(job_id)
        if job is not None and job.get("status") == "running":
            continue
        del _awaiting[drama_id]
        if job is not None and job.get("status") == "done" and cfg["series_id"] is None:
            _queue_transcribe(drama_id)


def _run(cfg: dict, root: str, name: str, path: str, size: int) -> None:
    global _worker
    try:
        _import(root, cfg, name, path, size)
    except ServiceError as exc:
        with _lock:
            _status["message"] = f"\"{name}\" was not added: {exc.message}"
        # Retrying a refused file every pass would repeat the same refusal.
        _remember(_fingerprint(name, size))
    except Exception as exc:
        _log.warning("Inbox import of a dropped file failed: %s", engine_shared.redact_secrets(str(exc)))
        with _lock:
            _status["message"] = f"\"{name}\" could not be read. It will be tried again."
        _candidates.pop(name, None)


def scan(now: Optional[float] = None) -> None:
    """One pass, on the queue runner's thread. A file's copy into the library runs on its own
    thread so a large file doesn't hold up the queue. Never raises."""
    global _worker
    try:
        cfg = _read()
        if not cfg["on"] or not cfg["path"]:
            return
        _finish_extractions(cfg)
        if _worker is not None and _worker.is_alive():
            return
        if not os.path.isdir(cfg["path"]):
            _status["message"] = "The inbox folder is missing."
            return
        root = os.path.realpath(cfg["path"])
        found = _eligible(root, cfg, _clock() if now is None else now)
        if found is None:
            return
        name, path, size = found
        _worker = threading.Thread(target=_run, args=(cfg, root, name, path, size),
                                   daemon=True, name="api-inbox-import")
        _worker.start()
    except Exception as exc:
        _log.warning("Inbox scan failed: %s", engine_shared.redact_secrets(str(exc)))
