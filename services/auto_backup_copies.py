"""
services/auto_backup_copies.py -- the copies in the backup folder: who wrote
each one (the library identity), which ones this library manages, the rotation
that keeps a few, and picking one to restore from.

- Copies are named baihe_snapshot-YYYYMMDD-HHMMSS.zip (UTC; library.db, sources.db +
  manifest.json, plus media when include_media); the single snapshot kept
  before rotation had the fixed legacy name and is still read.
- Ownership: each library has a random id, made once and kept in
  app_settings (IDENTITY_KEY) with the time it was made, a sequence number
  that goes up by one for every copy it writes, and the (sequence, name)
  of each copy it wrote that is still in the folder (never taken from a
  restored backup, see library_restore_service._RESTORE_KEPT_APP_SETTINGS).
  The id and sequence go into every copy's manifest. A backup folder may
  be shared (a synced folder used by two PCs, possibly two PCs running a
  hand-copied library with the same id), so neither a file's name nor the
  id alone proves who wrote it: the rotation, "delete all", and a folder
  move touch only MANAGED copies -- ones whose manifest carries this
  library's id and a sequence this library recorded writing under that
  name, plus copies written before copies carried an id, dated before the
  id was made, when they sit in this library's own default folder inside
  the library (adopted). Every other copy (another library's or a clone's,
  an id-less copy in a custom folder, one that can't be read) is listed
  but takes no slot and is only deleted when the owner names it or asks
  for include_unmanaged. When the folder holds a copy with this id
  numbered above the stored counter (a clone, or a library.db restored by
  hand), that run prunes nothing and logs a warning.
- Retention (deterministic, applied after each successful run, under
  _snapshot_lock): one copy per day for the last 2 days, plus the first
  copy of each of the last 2 weeks. Precisely, among the managed copies
  dated no later than the copy just written (and, for this library's own,
  with a lower sequence number): "daily" = the new copy, plus
  the newest copy of the most recent earlier UTC day that has one (so extra
  runs on one day replace that day's earlier copy, never yesterday's);
  "weekly" = among the remaining copies, the FIRST (oldest) copy of each ISO
  week (UTC), keeping the 2 most recent such weeks. Every other such copy
  is deleted, so at most 4 remain. The copy just written is always kept and
  is the anchor: a copy dated after it (a future-dated name, a legacy file
  with a future time, a clock set back) takes no slot and is never deleted.
  Two runs within one second get names one second apart. A copy's date is
  the one in its name; the legacy single snapshot "baihe_snapshot.zip" from
  before rotation counts as a copy dated by its file time and ages out
  under the same rule. A copy that can't be opened right now (an OS error:
  locked, no permission, drive hiccup) or is damaged (not a zip, bad
  manifest) proves no owner: it takes no slot and is never deleted by the
  rotation. Pruning only ever touches regular files in the backup folder
  whose name is exactly the copy pattern or the legacy name: never other
  files, never symlinks, never a name taken from a request.
- The default restore pick (no copy named) is this library's copy with the
  highest sequence number, never a guess from file or wall-clock times.
  When that can't be told for sure (none of this library's copies is
  readable, another copy with this id is numbered as high or higher, a
  lower-numbered copy claims to be more than _ORDER_TOLERANCE newer, an
  unmanaged copy claims to be newer, a damaged copy's name is newer, or a
  copy can't be opened right now) nothing is picked: a ConflictError
  with details {reason: "choose_copy", candidates} asks the owner to name
  one.

No result or error message carries a filesystem path (copies are named by
file name only). No FastAPI import.
"""

import contextlib
import datetime
import json
import logging
import os
import re
import stat
import threading
import uuid
import zipfile
import zlib

import db
from services.auto_backup_settings import _iso, _later, _parse, _now, _target_dir, folder_path
from lib.errors import ConflictError, InvalidInputError, NotFoundError

log = logging.getLogger(__name__)

# {"library_id": 32 hex, "sequence": last copy number, "created_at": ISO
# time the id was made, "written": [[sequence, file name], ...] of this
# library's copies still in the folder, "sources_keep": [file name, ...]
# (see _keep_last_sources_copy)}. A whole-library restore keeps the
# current value (library_restore_service copies this key from the live
# library).
IDENTITY_KEY = "auto_backup.identity"


_LIBRARY_ID_RE = re.compile(r"[0-9a-f]{32}", re.ASCII)


# A bound on the written list; a copy that falls off it only becomes
# unmanaged (kept), never someone else's.
_MAX_WRITTEN = 200


# A bound on sources_keep: one name per spell of damaged sources.db runs.
_MAX_SOURCES_KEEP = 20


# The sequence decides which copy is newest, even when a clock correction
# makes created_at disagree; but a lower-numbered copy claiming to be more
# than this much newer means the clock or the counter can't be trusted, and
# the owner chooses.
_ORDER_TOLERANCE = datetime.timedelta(days=1)


# What a copy's manifest says about who made it (see _classify).
_THIS, _LEGACY, _OTHER = "this", "legacy", "other"


_MANAGED = (_THIS, _LEGACY)


# Copies are named after their UTC time; the single snapshot kept before
# rotation had the fixed legacy name and is still read (and aged out).
_COPY_NAME_RE = re.compile(r"baihe_snapshot-([0-9]{8})-([0-9]{6})\.zip", re.ASCII)


LEGACY_SNAPSHOT_NAME = "baihe_snapshot.zip"


KEEP_DAILY = 2


KEEP_WEEKLY = 2


_MAX_COPY_NAME_LEN = 64


MANIFEST_NAME = "manifest.json"


MANIFEST_FORMAT = 1


_MANIFEST_MAX_BYTES = 8 * 1024 * 1024


_NO_SNAPSHOT = "There is no backup snapshot yet."


_NO_COPY = "That backup copy doesn't exist (it may have been rotated out)."


_BAD_SNAPSHOT = "The backup snapshot could not be read; it may be damaged."


_CHOOSE = ("Choose which backup copy to use: the newest one can't be told for sure "
           "(copies from another library or from before this update, or dates that "
           "disagree).")


# Held while a copy is added, pruned, moved, deleted, or read for a restore,
# so none of those see a half-written folder (and Windows never deletes or
# renames a file another thread has open).
_snapshot_lock = threading.Lock()


def _is_sequence(value) -> bool:
    return type(value) is int and value >= 1


def _normalise_identity(stored):
    """The stored identity with every field checked ({library_id, sequence,
    created_at (str or None), written, sources_keep}), or None when it has
    no valid id."""
    if not (isinstance(stored, dict) and isinstance(stored.get("library_id"), str)
            and _LIBRARY_ID_RE.fullmatch(stored["library_id"])):
        return None
    seq = stored.get("sequence")
    created = stored.get("created_at")
    written = stored.get("written")
    keep = stored.get("sources_keep")
    return {"library_id": stored["library_id"],
            "sequence": seq if _is_sequence(seq) else 0,
            "created_at": created if _parse(created) is not None else None,
            "written": [[w[0], w[1]] for w in written
                        if isinstance(w, list) and len(w) == 2 and _is_sequence(w[0])
                        and isinstance(w[1], str)][-_MAX_WRITTEN:]
            if isinstance(written, list) else [],
            "sources_keep": [k for k in keep if isinstance(k, str)][-_MAX_SOURCES_KEEP:]
            if isinstance(keep, list) else []}


def _change_identity(change=None) -> dict:
    """The identity after one write transaction (so two processes never
    hand out the same number): made at random the first time (a stored
    value without a valid id is replaced, which only ever makes old copies
    unmanaged, never someone else's managed), created_at filled in when
    missing, then change(identity) applied in place."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                               (IDENTITY_KEY,)).fetchone()
            try:
                stored = json.loads(row[0]) if row else None
            except ValueError:
                stored = None
            ident = _normalise_identity(stored) or {
                "library_id": uuid.uuid4().hex, "sequence": 0, "created_at": None,
                "written": [], "sources_keep": []}
            if ident["created_at"] is None:
                ident["created_at"] = _iso(_now())
            if change is not None:
                change(ident)
            if ident != stored:
                conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                             (IDENTITY_KEY, json.dumps(ident)))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return ident


def _identity_info() -> dict:
    """The identity (see IDENTITY_KEY), made or completed when needed."""
    try:
        ident = _normalise_identity(db.get_app_setting(IDENTITY_KEY, None))
    except ValueError:
        ident = None
    if ident is None or ident["created_at"] is None:
        ident = _change_identity()
    return ident


def _identity(bump_from=None) -> tuple:
    """(library id, sequence). With bump_from, the sequence becomes
    max(stored, bump_from) + 1 and that number is returned."""
    if bump_from is None:
        ident = _identity_info()
    else:
        def bump(i):
            i["sequence"] = max(i["sequence"], bump_from) + 1
        ident = _change_identity(bump)
    return ident["library_id"], ident["sequence"]


def _record_copy(sequence: int, name: str, present: set):
    """Notes that this library wrote copy `name` numbered `sequence`, and
    drops the notes for copies no longer in the folder (`present`: the
    names there now)."""
    def record(ident):
        ident["written"] = [w for w in ident["written"]
                            if w[1] in present and w[1] != name][-(_MAX_WRITTEN - 1):]
        ident["written"].append([sequence, name])
        ident["sources_keep"] = [k for k in ident["sources_keep"] if k in present]
    _change_identity(record)


def _keep_last_sources_copy(folder: str, new_name: str):
    """After copy `new_name` was written without sources.db (it was
    damaged): marks the newest older managed copy that still has one, so
    the rotation never deletes it (a copy whose manifest says
    sources_left_out has none). Over a long spell of such runs, and after
    the owner replaces the damaged file, that copy may be the only one
    holding their followed series and import history; it stays until the
    owner deletes it by name. The caller holds _snapshot_lock."""
    entries = _classify(folder)
    new = next((e for e in entries if e["name"] == new_name), None)
    if new is None:
        return
    last = next((e for e in entries
                 if e is not new and e["owner"] in _MANAGED and e["state"] == _OK
                 and e["at"] <= new["at"] and not e["manifest"].get("sources_left_out")),
                None)
    if last is None:
        return

    def keep(ident):
        if last["name"] not in ident["sources_keep"]:
            ident["sources_keep"] = (ident["sources_keep"] + [last["name"]])[-_MAX_SOURCES_KEEP:]
    _change_identity(keep)


def _recorded(ident: dict, manifest, name: str) -> bool:
    """The copy called `name` with this manifest is one this library wrote:
    its id, and its sequence recorded under that name."""
    if not isinstance(manifest, dict) or manifest.get("library_id") != ident["library_id"]:
        return False
    seq = manifest.get("sequence")
    return _is_sequence(seq) and [seq, name] in ident["written"]


def _list_copies(folder: str) -> list:
    """Every copy in `folder`, newest first: [{name, path, at, size,
    dated}]. Only regular files (checked without following links) whose
    name is exactly the copy pattern or the legacy name; `at` is the UTC
    time in the name, or the legacy file's modification time. A name
    whose date isn't a real date (or a legacy file with an impossible
    time) is still listed, dated=False, so it can be seen and deleted by
    name; it is never managed."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for name in names:
        match = _COPY_NAME_RE.fullmatch(name)
        if match is None and name != LEGACY_SNAPSHOT_NAME:
            continue
        path = os.path.join(folder, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        dated = True
        try:
            if match is not None:
                at = datetime.datetime.strptime(match.group(1) + match.group(2),
                                                "%Y%m%d%H%M%S").replace(
                    tzinfo=datetime.timezone.utc)
            else:
                at = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            dated = False
            at = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
        out.append({"name": name, "path": path, "at": at, "size": st.st_size,
                    "dated": dated})
    out.sort(key=lambda c: (c["at"], c["name"]), reverse=True)
    return out


def _adopts_legacy(folder: str) -> bool:
    """Copies without a library id (written before copies carried one) count
    as this library's only in its own default folder, and only while that
    folder really is inside the library (not a link out to a shared
    drive): nothing else writes there."""
    try:
        lib = os.path.realpath(db.LIBRARY_DIR)
        default = os.path.realpath(folder_path(""))
        return default.startswith(lib + os.sep) and os.path.realpath(folder) == default
    except (OSError, ValueError):
        return False


def _classify(folder: str, ident: dict = None) -> list:
    """_list_copies(folder) (newest first by name date), each copy with what
    its manifest says: state (see _read_copy), manifest, sequence (int or
    None), created (the manifest's created_at, else the name's date),
    same_id (the manifest carries this library's id and a sequence, whoever
    wrote it) and owner:
      _THIS   -- same_id, and this library recorded writing that sequence
                 under this name (see IDENTITY_KEY);
      _LEGACY -- a readable copy with neither id nor sequence, dated (its
                 manifest's created_at) before this library's id was made,
                 in the folder _adopts_legacy accepts;
      _OTHER  -- anything else: another library's id, a clone's or a
                 hand-restored library's copy with this id, no id
                 elsewhere, an id without a sequence, a copy that can't be
                 read, a name that isn't a real date.
    Only _THIS and _LEGACY (_MANAGED) take rotation slots or are deleted
    without the owner naming them. The caller holds _snapshot_lock."""
    ident = ident or _identity_info()
    adopt = _adopts_legacy(folder)
    id_made = _parse(ident["created_at"])
    out = []
    for copy in _list_copies(folder):
        state, manifest = _read_copy(copy["path"])
        owner, sequence, created, same_id = _OTHER, None, copy["at"], False
        if state == _OK:
            written_at = _parse(manifest.get("created_at"))
            created = written_at or copy["at"]
            seq = manifest.get("sequence")
            sequence = seq if _is_sequence(seq) else None
            same_id = manifest.get("library_id") == ident["library_id"] and sequence is not None
            if not copy["dated"]:
                pass
            elif same_id and _recorded(ident, manifest, copy["name"]):
                owner = _THIS
            elif (adopt and "library_id" not in manifest and "sequence" not in manifest
                  and written_at is not None and id_made is not None
                  and written_at < id_made):
                owner = _LEGACY
        out.append({**copy, "state": state, "manifest": manifest, "sequence": sequence,
                    "created": created, "same_id": same_id, "owner": owner})
    return out


def _default_pick(entries: list) -> tuple:
    """(entry, False) for the copy a restore uses when none is named: this
    library's copy with the highest sequence. (None, True) when that can't
    be told for sure (see the module docstring); (None, False) when no copy
    can be read at all."""
    readable = [e for e in entries if e["state"] == _OK]
    if not readable:
        return None, False
    own = [e for e in readable if e["owner"] == _THIS]
    if not own or any(e["state"] == _UNREADABLE for e in entries):
        return None, True
    top = max(own, key=lambda e: e["sequence"])
    for e in entries:
        if e is top:
            continue
        if e["state"] != _OK:
            # A damaged copy named after the pick may have been the newest.
            if _later(e["at"], top["at"]):
                return None, True
        elif e["same_id"] and e["sequence"] >= top["sequence"]:
            # Two with one number, or one numbered above this library's own
            # (a clone of this library, or a library.db restored by hand).
            return None, True
        elif e["owner"] == _THIS:
            if _later(e["created"], top["created"], _ORDER_TOLERANCE):
                return None, True
        elif _later(e["created"], top["created"]):
            return None, True
    return top, False


def _candidates(entries: list) -> list:
    """The copies a choose_copy answer offers: every readable one, newest
    first by name date. Names only, never paths."""
    return [{"name": e["name"], "created_at": _iso(e["created"]), "sequence": e["sequence"],
             "size": e["size"], "managed": e["owner"] in _MANAGED}
            for e in entries if e["state"] == _OK]


# What _read_copy found: a readable copy; one the OS wouldn't let us read
# right now (locked, no permission, a drive hiccup -- maybe fine later, so
# never deleted); or a damaged one (not a zip, no valid manifest).
_OK, _UNREADABLE, _DAMAGED = "ok", "unreadable", "damaged"


def _read_copy(path: str) -> tuple:
    """(_OK, manifest), (_UNREADABLE, None) or (_DAMAGED, None)."""
    try:
        with zipfile.ZipFile(path) as zf:
            return _OK, _read_manifest(zf)
    except OSError:
        return _UNREADABLE, None
    except Exception:
        # Anything else a foreign or broken file can make zipfile or the
        # manifest check raise (BadZipFile, NotImplementedError for an
        # unknown compression, struct/zlib errors, ...) is damage.
        return _DAMAGED, None


def _retention(readable: list) -> dict:
    """{name: "daily" | "weekly"} for the copies the rotation keeps (see the
    module docstring), from readable copies ordered newest first. The first
    is the anchor (the copy just written) and is always kept; the caller
    leaves out copies dated after it."""
    if not readable:
        return {}
    kept = {readable[0]["name"]: "daily"}
    days = {readable[0]["at"].date()}
    rest = []
    for copy in readable[1:]:       # newest first: the first seen of a day is its newest
        day = copy["at"].date()
        if day not in days and len(days) < KEEP_DAILY:
            kept[copy["name"]] = "daily"
            days.add(day)
        else:
            rest.append(copy)
    first_of_week = {}
    for copy in rest:               # newest first: the last one seen is the oldest
        first_of_week[copy["at"].isocalendar()[:2]] = copy
    for week in sorted(first_of_week, reverse=True)[:KEEP_WEEKLY]:
        kept[first_of_week[week]["name"]] = "weekly"
    return kept


def _rotation_pool(entries: list, anchor: dict) -> list:
    """The copies the rotation weighs against `anchor` (one of this
    library's), newest first: the anchor, then every managed copy dated no
    later than it and, if it is this library's, numbered lower. A copy
    dated after the anchor (a clock set back, a future-dated file) or
    numbered higher is never in the pool, so never deleted."""
    return [anchor] + [
        e for e in entries
        if e is not anchor and e["owner"] in _MANAGED and e["at"] <= anchor["at"]
        and (e["owner"] == _LEGACY or e["sequence"] < anchor["sequence"])]


def _prune(folder: str, new_name: str) -> int:
    """Deletes the copies the rotation no longer keeps; returns how many.
    The caller holds _snapshot_lock and has just put the validated copy
    `new_name` in place (and recorded it); it is always kept and anchors
    the rule. Only managed copies in _rotation_pool are ever deleted: never
    another library's or a clone's, never one that can't be read or proves
    no owner, never one kept for its sources.db (_keep_last_sources_copy).
    Nothing is deleted unless the new copy reads back as this library's."""
    ident = _identity_info()
    entries = _classify(folder, ident)
    new = next((e for e in entries if e["name"] == new_name), None)
    if new is None or new["owner"] != _THIS:
        return 0
    pool = _rotation_pool(entries, new)
    kept = _retention(pool)
    removed = 0
    for copy in pool:
        if copy["name"] in kept or copy["name"] in ident["sources_keep"]:
            continue
        try:
            os.remove(copy["path"])     # removes a name, never follows a link
            removed += 1
        except OSError:
            log.warning("Could not delete an old automatic backup copy")
    return removed


def _pick_copy(name=None) -> tuple:
    """(copy, manifest) for the copy called `name` -- matched against the
    backup folder's own listing, never used as a path; any listed copy,
    managed or not -- or, with no name, _default_pick's copy (a
    ConflictError with reason "choose_copy" and the candidates when it
    can't tell). The caller holds _snapshot_lock."""
    folder = _target_dir(create=False)
    if name is None:
        entries = _classify(folder)
        if not entries:
            raise NotFoundError(_NO_SNAPSHOT)
        pick, ambiguous = _default_pick(entries)
        if ambiguous:
            raise ConflictError(_CHOOSE, details={"reason": "choose_copy",
                                                  "candidates": _candidates(entries)})
        if pick is None:
            raise InvalidInputError(_BAD_SNAPSHOT)
        return pick, pick["manifest"]
    copies = _list_copies(folder)
    if not isinstance(name, str) or not name or len(name) > _MAX_COPY_NAME_LEN:
        raise InvalidInputError("A backup copy is named by its file name.")
    for copy in copies:
        if copy["name"] == name:
            state, manifest = _read_copy(copy["path"])
            if state != _OK:
                raise InvalidInputError(_BAD_SNAPSHOT)
            return copy, manifest
    raise NotFoundError(_NO_COPY)


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        info = zf.getinfo(MANIFEST_NAME)
    except KeyError:
        raise InvalidInputError(_BAD_SNAPSHOT) from None
    if info.file_size > _MANIFEST_MAX_BYTES:
        raise InvalidInputError(_BAD_SNAPSHOT)
    # An OSError (the file couldn't be read) is not damage: it propagates,
    # so _read_copy can tell the two apart.
    try:
        data = json.loads(zf.read(info).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError,
            EOFError, zlib.error, NotImplementedError):
        raise InvalidInputError(_BAD_SNAPSHOT) from None
    if (not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT
            or data.get("kind") not in ("db-only", "full")
            or not isinstance(data.get("dramas"), list)):
        raise InvalidInputError(_BAD_SNAPSHOT)
    return data


def snapshot_info() -> dict:
    """{exists, copies} plus, for the copy a restore picks by default (see
    _default_pick), readable, default_copy (its name), choose_copy (False),
    created_at, kind, size, app_version and drama_count. When no copy can
    be picked safely: {exists, readable: True, choose_copy: True,
    default_copy: None, copies}. copies lists every copy newest first (by
    name date) as {name, created_at, size, kind, drama_count, readable,
    kept_as, managed, sequence, sources_left_out}: names only, never paths
    (kept_as "sources": kept only for its sources.db). When copies exist
    but none can be read: {exists: True, readable: False, copies}."""
    with _snapshot_lock:
        ident = _identity_info()
        entries = _classify(_target_dir(create=False), ident)
    if not entries:
        return {"exists": False, "copies": []}
    # As the next run would see it: anchored on this library's newest copy.
    own = [e for e in entries if e["owner"] == _THIS]
    anchor = max(own, key=lambda e: e["sequence"]) if own else None
    kept = _retention(_rotation_pool(entries, anchor)) if anchor else {}
    copies = []
    for e in entries:
        manifest = e["manifest"]
        copies.append({"name": e["name"],
                       "created_at": _iso(e["created"]),
                       "size": e["size"], "readable": manifest is not None,
                       "kind": manifest["kind"] if manifest else None,
                       "drama_count": len(manifest["dramas"]) if manifest else None,
                       "kept_as": kept.get(e["name"]) or (
                           "sources" if e["name"] in ident["sources_keep"] else None),
                       "managed": e["owner"] in _MANAGED, "sequence": e["sequence"],
                       "sources_left_out": bool(manifest and manifest.get("sources_left_out"))})
    pick, ambiguous = _default_pick(entries)
    if ambiguous:
        return {"exists": True, "readable": True, "choose_copy": True, "default_copy": None,
                "copies": copies}
    if pick is None:
        return {"exists": True, "readable": False, "copies": copies}
    entry = next(c for c in copies if c["name"] == pick["name"])
    return {"exists": True, "readable": True, "choose_copy": False,
            "default_copy": pick["name"], "created_at": entry["created_at"],
            "sources_left_out": entry["sources_left_out"],
            "kind": entry["kind"], "size": entry["size"],
            "app_version": pick["manifest"].get("app_version") or "unknown",
            "drama_count": entry["drama_count"], "copies": copies}
