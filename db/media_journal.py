"""Media journal for backup imports and restores: staging folders, the
journal that proves which dramas/<id> folders an import moved in, and the
crash recovery that settles them."""

import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import time
import uuid

import db

# An import stages drama folders in DRAMAS_DIR/.import-<token>/ and holds an
# OS lock on <staging>/lock while it runs, so recovery in another process
# leaves it alone. Inside its database transaction (new rows inserted, not
# committed) it writes a marker file with a fresh random token into each
# staged folder, then <staging>/journal.json -- each new drama id with that
# token and the folder's identity (st_dev, st_ino) -- and only then renames
# the folders into place and commits. The commit is the commit point: when
# the journal's drama rows don't exist the transaction never committed, and
# a dramas/<id> folder is removed only when the journal lists that id and
# the folder provably is the one it recorded (its marker token matches, or
# its identity does; FAT, exFAT and some network shares change or omit the
# inode on a rename, the marker moves with the folder). Anything else --
# a folder that can't be proven, a damaged journal, a library database that
# can't be read -- is left alone and reported.
MEDIA_STAGING_PREFIX = ".import-"
_MEDIA_STAGING_RE = re.compile(r"\.import-[0-9a-f]{32}", re.ASCII)
_MEDIA_JOURNAL = "journal.json"
_MEDIA_LOCK_FILE = "lock"
MEDIA_MARKER = ".baihe-import-marker"
_TOKEN_RE = re.compile(r"[0-9a-f]{32}", re.ASCII)
_MAX_JOURNAL_BYTES = 1024 * 1024
_MAX_JOURNAL_IDS = 10000
_MAX_DRAMA_ID = 2 ** 63 - 1
_DAMAGED_JOURNAL = object()
# Imports running in this process: staging name -> {"lock": [fd or None],
# "moved": {drama id: staged folder it was renamed from}}.
_active_media_stagings = {}

log = logging.getLogger(__name__)


class DramaFolderConflict(RuntimeError):
    """dramas/<id> already exists for a drama id being created and could not
    be renamed aside."""


def _is_link(st) -> bool:
    """A symlink, or on Windows any reparse point (a junction too)."""
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _real_dir(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not _is_link(st)


def _folder_identity(path: str):
    """[st_dev, st_ino] of a real directory (not a link), else None; also
    None when the file system reports no inode number (it can't prove which
    folder is which)."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode) or _is_link(st) or not st.st_ino:
        return None
    return [int(st.st_dev), int(st.st_ino)]


def _marker_matches(folder: str, token) -> bool:
    if not (isinstance(token, str) and _TOKEN_RE.fullmatch(token)):
        return False
    path = os.path.join(folder, db.MEDIA_MARKER)
    try:
        st = os.lstat(path)
        if _is_link(st) or not stat.S_ISREG(st.st_mode) or st.st_size > 64:
            return False
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as fh:
            return fh.read(65).strip() == token.encode("ascii")
    except OSError:
        return False


def _is_journalled_folder(final: str, entry: dict) -> bool:
    """True only when dramas/<id> is provably the folder the journal entry
    recorded: a real directory whose marker token or identity matches."""
    if not _real_dir(final):
        return False
    marker = entry.get("marker")
    if marker:
        # A marker was written into the staged folder, so it is the proof.
        # Inode numbers are reused: a folder someone else put in its place
        # can carry the same identity, so identity alone must not count.
        return _marker_matches(final, marker)
    ident = entry.get("ident")
    return ident is not None and _folder_identity(final) == ident


def _lock_file(path: str, create: bool):
    """Opens `path` and takes an exclusive OS lock on it without waiting;
    the fd, or None when another process (or another open of it) holds the
    lock or it can't be opened."""
    try:
        fd = os.open(path, os.O_RDWR | (os.O_CREAT if create else 0), 0o600)
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_lock(holder: list):
    """Unlocks and closes holder[0] (once). Windows can't delete an open
    file, so this runs before the staging folder is removed."""
    fd, holder[0] = holder[0], None
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def new_media_staging() -> str:
    """Creates an empty staging folder DRAMAS_DIR/.import-<token>/ for one
    import, locked and registered as running (recovery leaves it alone)
    until finish_media_staging; returns its path."""
    os.makedirs(db.DRAMAS_DIR, exist_ok=True)
    name = db.MEDIA_STAGING_PREFIX + uuid.uuid4().hex
    path = os.path.join(db.DRAMAS_DIR, name)
    with db._media_lock:
        os.mkdir(path)
        # A file system without locks still imports; recovery elsewhere
        # then can't lock the folder either and leaves it alone.
        _active_media_stagings[name] = {
            "lock": [_lock_file(os.path.join(path, _MEDIA_LOCK_FILE), True)], "moved": {}}
    return path


def write_media_journal(staging: str, folders: dict):
    """Marks each staged folder and records {new drama id: its marker token
    and identity} in the staging folder's journal, flushed to disk; call
    inside the import's transaction, before any folder is moved into place."""
    ids = {}
    for did, path in folders.items():
        token = uuid.uuid4().hex
        with open(os.path.join(path, db.MEDIA_MARKER), "w", encoding="ascii") as fh:
            fh.write(token)
            fh.flush()
            os.fsync(fh.fileno())
        ids[str(int(did))] = {"marker": token, "ident": _folder_identity(path)}
    # Markers and journal are fsynced, and the journal lands by os.replace
    # (then a directory fsync), before any folder moves: after a crash,
    # recovery trusts the journal as proof of which folders it may remove,
    # so it must be durable and never seen half-written.
    tmp = os.path.join(staging, _MEDIA_JOURNAL + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"format": 2, "staging": os.path.basename(staging), "ids": ids}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, os.path.join(staging, _MEDIA_JOURNAL))
    db.fsync_dir(staging)


def move_staged_folder(staging: str, drama_id: int, staged: str) -> bool:
    """Renames a staged folder to dramas/<drama_id> (after
    write_media_journal) and remembers the move, so a failure before the
    commit can rename it straight back. False, moving nothing, when that
    path already exists."""
    final = os.path.join(db.DRAMAS_DIR, str(int(drama_id)))
    with db._media_lock:
        if os.path.lexists(final):
            return False
        os.rename(staged, final)
        entry = _active_media_stagings.get(os.path.basename(staging))
        if entry is not None:
            entry["moved"][int(drama_id)] = staged
    return True


def _journal_id(key) -> int:
    if not (isinstance(key, str) and 0 < len(key) <= 19 and key.isascii() and key.isdigit()
            and str(int(key)) == key and 0 < int(key) <= _MAX_DRAMA_ID):
        raise ValueError
    return int(key)


def _journal_entry(value) -> dict:
    if not (isinstance(value, dict) and isinstance(value.get("marker"), str)
            and _TOKEN_RE.fullmatch(value["marker"])):
        raise ValueError
    ident = value.get("ident")
    if ident is not None and not (isinstance(ident, list) and len(ident) == 2 and all(
            isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2 ** 64 for v in ident)):
        raise ValueError
    return {"marker": value["marker"], "ident": ident}


def _read_media_journal(staging: str):
    """{drama id: {"marker", "ident"}}; None when there is no journal (the
    import stopped before moving anything); _DAMAGED_JOURNAL when it can't
    be read or doesn't belong to this staging folder. Never raises."""
    path = os.path.join(staging, _MEDIA_JOURNAL)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except Exception:
        return _DAMAGED_JOURNAL
    try:
        if _is_link(st) or not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_JOURNAL_BYTES:
            return _DAMAGED_JOURNAL
        with open(path, "rb") as fh:
            data = json.loads(fh.read(_MAX_JOURNAL_BYTES + 1).decode("utf-8"))
        if not (isinstance(data, dict) and data.get("format") == 2
                and data.get("staging") == os.path.basename(staging)
                and isinstance(data.get("ids"), dict) and len(data["ids"]) <= _MAX_JOURNAL_IDS):
            return _DAMAGED_JOURNAL
        return {_journal_id(k): _journal_entry(v) for k, v in data["ids"].items()}
    except Exception:   # OSError, ValueError, RecursionError, OverflowError, ...
        return _DAMAGED_JOURNAL


def _drama_rows_exist(ids):
    """True when any of these drama rows exists, False when none does, None
    when the library database or its dramas table can't be read (then
    nothing can be proven either way)."""
    ids = list(ids)
    if not ids:
        return False
    if not os.path.isfile(db.DB_PATH):
        return None
    try:
        # A separate connection: callers hold a get_conn() connection
        # mid-transaction, so this sees only committed rows.
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as conn:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" for _ in chunk)
                if conn.execute(f"SELECT 1 FROM dramas WHERE id IN ({marks}) LIMIT 1",
                                chunk).fetchone() is not None:
                    return True
    except Exception:
        return None
    return False


def _settle_media_staging(name: str, holder: list, moved: dict | None = None):
    """Resolves one staging folder whose lock `holder` has (released here
    before the folder is removed). Returns (ended, ids left in place): ended
    is False when the staging folder and its journal were kept -- a damaged
    journal, an unreadable library database, or dramas/<id> folders that
    could not be proven to be this import's or could not be removed (the
    next start tries again)."""
    staging = os.path.join(db.DRAMAS_DIR, name)
    journal = _read_media_journal(staging)
    if journal is _DAMAGED_JOURNAL:
        log.warning("An import journal could not be read; its folders were left alone")
        return False, []
    journal = journal if isinstance(journal, dict) else {}
    committed = _drama_rows_exist(journal)
    if committed is None:
        log.warning("The library database could not be read; an interrupted import's "
                    "folders were left alone")
        return False, []
    if committed:
        # Every folder is in place: only the markers, the journal and the
        # staging folder go (anything else still in it is left alone).
        for did, entry in journal.items():
            final = os.path.join(db.DRAMAS_DIR, str(did))
            if _real_dir(final) and _marker_matches(final, entry["marker"]):
                try:
                    os.remove(os.path.join(final, db.MEDIA_MARKER))
                except OSError:
                    log.warning("Could not remove the import marker of drama %d", did)
        os.remove(os.path.join(staging, _MEDIA_JOURNAL))
        _release_lock(holder)
        for leftover in (_MEDIA_JOURNAL + ".tmp", _MEDIA_LOCK_FILE):
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(staging, leftover))
        try:
            os.rmdir(staging)
        except OSError:
            log.warning("A finished import's staging folder was not empty; it was left alone")
        return True, []
    moved = moved or {}
    left = []
    for did, entry in sorted(journal.items()):
        final = os.path.join(db.DRAMAS_DIR, str(did))
        if not os.path.lexists(final):
            continue
        if not _is_journalled_folder(final, entry):
            left.append(did)    # can't prove it's the folder this import moved there
            continue
        staged = moved.get(did)
        if staged is not None and not os.path.lexists(staged):
            try:
                os.rename(final, staged)    # straight back; removed with the staging folder
                continue
            except OSError:
                pass
        try:
            shutil.rmtree(final)
        except FileNotFoundError:
            pass
        except OSError:
            left.append(did)
    if left:
        log.warning("The folders of uncommitted imported dramas %s were left in place",
                    ",".join(str(i) for i in left))
        return False, left
    _release_lock(holder)
    shutil.rmtree(staging)
    return True, []


def finish_media_staging(staging: str) -> bool:
    """Ends this process's import staging: after a commit only the markers,
    the journal and the staging folder go; otherwise the folders it moved
    into place are renamed back and removed with the staging folder. False
    when something was left in place (logged by id; the journal stays for
    the next start). Raises OSError when the staging folder itself can't be
    removed. Calling it again is harmless."""
    name = os.path.basename(staging)
    with db._media_lock:
        entry = _active_media_stagings.pop(name, None)
        if entry is None:
            return not os.path.lexists(staging)     # already ended
        try:
            if not os.path.isdir(staging):
                return True
            return _settle_media_staging(name, entry["lock"], entry["moved"])[0]
        finally:
            _release_lock(entry["lock"])


def _leftover_stagings() -> list:
    try:
        names = os.listdir(db.DRAMAS_DIR)
    except OSError:
        return []
    return sorted(n for n in names if _MEDIA_STAGING_RE.fullmatch(n)
                  and n not in _active_media_stagings
                  and _real_dir(os.path.join(db.DRAMAS_DIR, n)))


def _recover_staging(name: str, now: float, max_age: float):
    """(ended, ids left in place) for a staging folder no import in this
    process owns; (False, []) when its import may still be running in
    another process (its lock is held, or it has no lock or journal yet and
    is younger than max_age)."""
    staging = os.path.join(db.DRAMAS_DIR, name)
    holder = [_lock_file(os.path.join(staging, _MEDIA_LOCK_FILE), False)]
    try:
        if holder[0] is None:
            if os.path.lexists(os.path.join(staging, _MEDIA_LOCK_FILE)):
                return False, []    # held by a running import
        if holder[0] is None or _read_media_journal(staging) is None:
            if now - os.lstat(staging).st_mtime < max_age:
                return False, []
        return _settle_media_staging(name, holder)
    finally:
        _release_lock(holder)


def recover_media_imports(max_age: float | None = None, now: float | None = None) -> dict:
    """Crash recovery for imports that are not running (run at startup and
    before an import or restore; a staging folder whose lock another process
    holds is skipped): a journal whose drama rows exist is deleted; one whose
    rows don't removes the dramas/<id> folders it provably recorded, then
    its staging folder; a staging folder with no journal (the import stopped
    before moving anything) is removed once older than max_age. Nothing a
    journal doesn't list is touched. Returns {"settled": n, "failed_ids":
    [ids left in place]}. Never raises."""
    # Read at call time so a changed db.STALE_MEDIA_STAGING_SECONDS is honoured.
    max_age = db.STALE_MEDIA_STAGING_SECONDS if max_age is None else max_age
    now = time.time() if now is None else now
    settled, failed = 0, []
    with db._media_lock:
        for name in _leftover_stagings():
            try:
                ended, left = _recover_staging(name, now, max_age)
            except Exception:
                log.warning("An interrupted import's leftovers could not be removed")
                continue
            failed += left
            settled += bool(ended)
    return {"settled": settled, "failed_ids": failed}


def claim_new_drama_folder(drama_id: int):
    """Call with a new drama's row inserted but not committed (so no other
    import is mid-transaction). A dramas/<id> folder is never inherited: one
    that an uncommitted import's journal provably recorded is removed with
    that import's other leftovers; anything else there is renamed aside to
    <id>.orphan-<hex> (kept, never deleted). Raises DramaFolderConflict when
    it can't be renamed aside."""
    final = os.path.join(db.DRAMAS_DIR, str(drama_id))
    if not os.path.lexists(final):
        return
    with db._media_lock:
        for name in _leftover_stagings():
            journal = _read_media_journal(os.path.join(db.DRAMAS_DIR, name))
            if isinstance(journal, dict) and drama_id in journal:
                try:
                    _recover_staging(name, time.time(), db.STALE_MEDIA_STAGING_SECONDS)
                except Exception:
                    log.warning("An interrupted import's leftovers could not be removed")
                break
        if not os.path.lexists(final):
            return
        try:
            os.rename(final, f"{final}.orphan-{uuid.uuid4().hex[:8]}")
        except OSError:
            raise db.DramaFolderConflict(
                f"A folder for new drama {int(drama_id)} is already in the library's dramas "
                "folder and could not be moved aside; move it out and try again.") from None
        log.warning("A folder for new drama %d was already in the dramas folder; it was "
                    "renamed aside and kept", int(drama_id))
