"""
services/library_admin_service.py -- the Library tab's destructive and
admin actions (migration E0 remainder): bulk status/tags/delete, bulk
translate, export-all zip, full backup, restore and storage cleanup.

Service half only; no router yet. Rules this module keeps:
  - Destructive actions need `confirm is True` plus an exact typed word
    (the migration decision: match today's UI bar with a typed confirm).
    A wrong or missing confirm raises InvalidInputError before anything
    is read for writing, so nothing changes.
  - Delete refuses any drama with a running/queued job
    (drama_service.job_running_for_drama); restore refuses while ANY job
    runs, in this process or (via fresh job_records rows) another one.
  - Writes are field-whitelisted: status and the organizational tags only,
    through db.update_drama / db.set_custom_tag.
  - Bulk calls return one result per requested id.
  - No result or error message carries a filesystem path. Finished export
    and backup files live in library-level folders excluded from backups;
    `latest_admin_artifact` returns name/size, and
    `admin_artifact_path` (server-side only) is what a future download
    route streams.

No Streamlit or FastAPI import.
"""

import datetime
import io
import logging
import os
import re
import tempfile
import time
import zipfile

import background_jobs
import db
import storage
import subtitle_formats
import translate_engines
from core import Line, lines_to_bilingual_srt, lines_to_srt
from services import drama_service, translate_service
from services import workspace_job_service as wjs
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

STATUSES = ("not started", "aligned", "translated", "dubbed", "exported")
MAX_BULK_IDS = 500

DELETE_CONFIRM_TEXT = "DELETE"
RESTORE_CONFIRM_TEXT = "RESTORE"
CLEAN_CONFIRM_TEXT = "CLEAN"

BULK_TRANSLATE_JOB_ID = "bulk_series_translate"  # same id the Streamlit tab uses
EXPORT_JOB_ID = "library_export_zip"
BACKUP_JOB_ID = "library_backup"

# Both under LIBRARY_DIR/backups, which a full backup already skips, so an
# export or old backup is never zipped into the next backup.
_ARTIFACT_SUBDIRS = {"backup": ("backups",), "export": ("backups", "exports")}
_EXPORTABLE_STATUSES = ("translated", "dubbed", "exported")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _require_confirm(confirm, confirm_text, word: str, action: str):
    if confirm is not True or confirm_text != word:
        raise InvalidInputError(f"{action} needs confirm=true and confirm_text set to the "
                                f"word {word}, in capitals.")


def _check_ids(drama_ids) -> list:
    if not isinstance(drama_ids, (list, tuple)) or not drama_ids:
        raise InvalidInputError("Pick at least one drama.")
    if len(drama_ids) > MAX_BULK_IDS:
        raise InvalidInputError(f"At most {MAX_BULK_IDS} dramas per request.")
    out = []
    for did in drama_ids:
        if (isinstance(did, bool) or not isinstance(did, int)
                or not 1 <= did <= drama_service.MAX_ID):
            raise InvalidInputError("A drama id is a positive whole number.")
        if did not in out:
            out.append(did)
    return out


def _bulk_apply(drama_ids, fn) -> dict:
    ids = _check_ids(drama_ids)
    results = []
    for did in ids:
        if db.get_drama(did) is None:
            results.append({"drama_id": did, "ok": False, "error": "not_found"})
            continue
        fn(did)
        results.append({"drama_id": did, "ok": True})
    return {"results": results, "updated": sum(r["ok"] for r in results)}


def _any_job_running() -> bool:
    """In-process running/queued jobs, plus fresh running/queued
    job_records rows from another process (same staleness rule as
    drama_service.job_running_for_drama)."""
    if any(j.get("status") in ("running", "queued")
           for j in background_jobs.list_all_jobs().values()):
        return True
    cutoff = time.time() - drama_service._STALE_JOB_RECORD_SECONDS
    return any(r.get("status") in ("running", "queued") and (r.get("updated_at") or 0) >= cutoff
               for r in db.list_job_records())


def _refuse_duplicate(job_id: str, label: str):
    job = background_jobs.get_status(job_id)
    if job and job["status"] in ("running", "queued"):
        raise ConflictError(f"A {label} is already running.")


def _artifact_dir(kind: str, create: bool) -> str:
    if kind not in _ARTIFACT_SUBDIRS:
        raise InvalidInputError("Unknown artifact kind.")
    path = os.path.join(db.LIBRARY_DIR, *_ARTIFACT_SUBDIRS[kind])
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def admin_artifact_path(kind: str) -> dict:
    """Newest finished file of `kind` ("backup"/"export"): {path
    (SERVER-SIDE ONLY, never put in a response), name, size}. Symlinks
    and anything outside the folder are ignored."""
    base = _artifact_dir(kind, create=False)
    base_real = os.path.realpath(base)
    best = None
    try:
        names = os.listdir(base)
    except OSError:
        raise NotFoundError("No artifact available.")
    for name in names:
        if not name.endswith(".zip") or name.startswith("."):
            continue
        path = os.path.join(base, name)
        if (os.path.islink(path) or not os.path.isfile(path)
                or os.path.dirname(os.path.realpath(path)) != base_real):
            continue
        mtime = os.path.getmtime(path)
        if best is None or mtime > best[0]:
            best = (mtime, name, path)
    if best is None:
        raise NotFoundError("No artifact available.")
    return {"path": best[2], "name": best[1], "size": os.path.getsize(best[2])}


def latest_admin_artifact(kind: str) -> dict:
    """Same as admin_artifact_path without the path: {kind, name, size}."""
    info = admin_artifact_path(kind)
    return {"kind": kind, "name": info["name"], "size": info["size"]}


# --------------------------------------------------------------------------
# bulk status / tags / delete / translate
# --------------------------------------------------------------------------

def bulk_set_status(drama_ids, status: str) -> dict:
    """Sets only `status` on each drama. Per-id results."""
    if status not in STATUSES:
        raise InvalidInputError("Unknown status.", details={"allowed": list(STATUSES)})
    return _bulk_apply(drama_ids, lambda did: db.update_drama(did, status=status))


def bulk_set_tags(drama_ids, tag: str, present: bool) -> dict:
    """Adds (present=True) or removes one organizational list tag
    (db.ORGANIZATIONAL_TAGS), leaving every other tag untouched."""
    if tag not in db.ORGANIZATIONAL_TAGS:
        raise InvalidInputError("Unknown list.", details={"allowed": list(db.ORGANIZATIONAL_TAGS)})
    if not isinstance(present, bool):
        raise InvalidInputError("present must be true or false.")
    return _bulk_apply(drama_ids, lambda did: db.set_custom_tag(did, tag, present))


def bulk_delete(drama_ids, confirm=False, confirm_text="") -> dict:
    """Permanently deletes each drama (drama_service._hard_delete_drama).
    Needs confirm=True and confirm_text "DELETE" (the same word the
    single-drama delete uses); checked before anything is touched. A drama
    with a running/queued job is skipped with error "job_running"."""
    ids = _check_ids(drama_ids)
    _require_confirm(confirm, confirm_text, DELETE_CONFIRM_TEXT, "Deleting dramas")
    results = []
    for did in ids:
        if db.get_drama(did) is None:
            results.append({"drama_id": did, "ok": False, "error": "not_found"})
            continue
        if drama_service.job_running_for_drama(did):
            results.append({"drama_id": did, "ok": False, "error": "job_running"})
            continue
        try:
            leftover = drama_service._hard_delete_drama(did)
        except ServiceError as e:
            results.append({"drama_id": did, "ok": False, "error": "delete_failed",
                            "message": str(e)})
            continue
        entry = {"drama_id": did, "ok": True}
        if leftover:
            entry["warning"] = drama_service._LEFTOVER_FILES_MESSAGE
        results.append(entry)
    return {"results": results, "deleted": sum(r["ok"] for r in results)}


def start_bulk_translate(drama_ids, default_locale: str = "en-US") -> dict:
    """Starts the existing bulk-series translate job
    (workspace_job_service.run_bulk_series_translate_job) for the picked
    dramas whose status is "aligned", the same filter the tab applies.
    Keys, Ollama URL, monthly cap and Gemini free tier come from Settings
    server-side. Returns {job_id, queued: [ids], skipped: [{drama_id, reason}]}."""
    from services import settings_service
    ids = _check_ids(drama_ids)
    if not isinstance(default_locale, str) or not re.fullmatch(r"[A-Za-z]{2}(-[A-Za-z]{2})?",
                                                               default_locale):
        raise InvalidInputError("default_locale looks like en-US.")
    _refuse_duplicate(BULK_TRANSLATE_JOB_ID, "bulk translation")
    queued, skipped = [], []
    for did in ids:
        drama = db.get_drama(did)
        if drama is None:
            skipped.append({"drama_id": did, "reason": "not_found"})
        elif drama.get("status") != "aligned":
            skipped.append({"drama_id": did, "reason": "not_aligned"})
        else:
            queued.append(did)
    if not queued:
        raise InvalidInputError("None of the picked dramas are untranslated (status 'aligned').")
    api_keys = {}
    for engine in translate_engines.ENGINES:
        try:
            api_keys[engine] = translate_service.resolve_api_key(engine)
        except Exception:
            api_keys[engine] = None
    try:
        cap = max(0.0, float(settings_service.resolve_key("monthly_cap_usd") or 0))
    except (TypeError, ValueError):
        cap = 0.0
    started = background_jobs.start_job(
        BULK_TRANSLATE_JOB_ID, wjs.run_bulk_series_translate_job,
        BULK_TRANSLATE_JOB_ID, queued, api_keys,
        default_locale=default_locale,
        ollama_base_url=settings_service.resolve_key("ollama_url") or None,
        gemini_free_tier=settings_service.get_gemini_free_tier(),
        models={}, monthly_cap=cap)
    if not started:
        raise ConflictError("A bulk translation is already running.")
    return {"job_id": BULK_TRANSLATE_JOB_ID, "queued": queued, "skipped": skipped}


# --------------------------------------------------------------------------
# export zip / backup (jobs)
# --------------------------------------------------------------------------

def _export_job(job_id, drama_ids):
    final_dir = _artifact_dir("export", create=True)
    name = f"dramas_export_{_timestamp()}.zip"
    fd, tmp = tempfile.mkstemp(prefix=".partial_", suffix=".zip", dir=final_dir)
    os.close(fd)
    exported = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for n, did in enumerate(drama_ids):
                background_jobs.update_progress(job_id, n / max(1, len(drama_ids)),
                                                f"Exporting {n + 1}/{len(drama_ids)}")
                d = db.get_drama(did)
                rows = db.load_lines(did) if d else []
                lns = [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"],
                            en=r["en"] or "", speaker=r.get("speaker"), sfx=bool(r.get("sfx")))
                       for r in rows]
                if not lns:
                    continue
                lns, _ = subtitle_formats.clamp_overlaps(lns)
                safe = re.sub(r"[^\w\- ]", "", d["title_en"] or d["title_zh"] or "") or str(did)
                folder = f"{did} {safe}"
                zf.writestr(f"{folder}/english.srt", lines_to_srt(lns, "en"))
                zf.writestr(f"{folder}/chinese.srt", lines_to_srt(lns, "zh"))
                zf.writestr(f"{folder}/bilingual.srt", lines_to_bilingual_srt(lns))
                dub_path = os.path.join(db.DRAMAS_DIR, str(did), "dub_track.wav")
                if os.path.isfile(dub_path) and not os.path.islink(dub_path):
                    zf.write(dub_path, f"{folder}/dub_track.wav")
                exported += 1
        os.replace(tmp, os.path.join(final_dir, name))
        tmp = None
    except OSError:
        raise RuntimeError("The export could not be written.") from None
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.remove(tmp)
    background_jobs.set_result(job_id, {"exported": exported, "name": name})
    background_jobs.update_progress(job_id, 1.0, "Export ready.")


def start_export_zip(drama_ids=None) -> dict:
    """Job: zips english/chinese/bilingual SRT (+ dub track) for each
    translated/dubbed/exported drama -- the picked ones, or all of them.
    Returns {job_id, drama_ids}."""
    if drama_ids is None:
        ids = [d["id"] for d in db.list_dramas() if d.get("status") in _EXPORTABLE_STATUSES]
    else:
        ids = [did for did in _check_ids(drama_ids)
               if (db.get_drama(did) or {}).get("status") in _EXPORTABLE_STATUSES]
    if not ids:
        raise InvalidInputError("No translated dramas to export.")
    _refuse_duplicate(EXPORT_JOB_ID, "library export")
    if not background_jobs.start_job(EXPORT_JOB_ID, _export_job, EXPORT_JOB_ID, ids,
                                     description="Library export"):
        raise ConflictError("A library export is already running.")
    return {"job_id": EXPORT_JOB_ID, "drama_ids": ids}


def _backup_job(job_id):
    from sources import store as src_store
    library_dir = db.LIBRARY_DIR
    final_dir = _artifact_dir("backup", create=True)
    name = f"baihe_library_backup_{_timestamp()}.zip"
    skip = {db.DB_PATH, db.DB_PATH + "-wal", db.DB_PATH + "-shm"}
    fd, tmp = tempfile.mkstemp(prefix=".partial_", suffix=".zip", dir=final_dir)
    os.close(fd)
    try:
        with tempfile.TemporaryDirectory() as snapdir:
            snap = os.path.join(snapdir, "library.db")
            db.snapshot_database(snap)
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(library_dir, topdown=True):
                    if root == library_dir:
                        # Same exclusions as the tab: old backups/exports and
                        # saved site sign-ins never go into a backup.
                        dirs[:] = [d for d in dirs
                                   if d not in ("backups", src_store.BROWSER_PROFILES_DIRNAME)]
                    for fname in files:
                        full = os.path.join(root, fname)
                        if full in skip or os.path.islink(full):
                            continue
                        zf.write(full, os.path.relpath(full, library_dir))
                zf.write(snap, "library.db")
        os.replace(tmp, os.path.join(final_dir, name))
        tmp = None
    except OSError:
        raise RuntimeError("The backup could not be written.") from None
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.remove(tmp)
    background_jobs.set_result(job_id, {"name": name,
                                        "size": os.path.getsize(os.path.join(final_dir, name))})
    background_jobs.update_progress(job_id, 1.0, "Backup ready.")


def start_backup() -> dict:
    """Job: full library backup zip (database snapshot + media)."""
    _refuse_duplicate(BACKUP_JOB_ID, "backup")
    if not background_jobs.start_job(BACKUP_JOB_ID, _backup_job, BACKUP_JOB_ID,
                                     description="Library backup"):
        raise ConflictError("A backup is already running.")
    return {"job_id": BACKUP_JOB_ID}


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------

_BAD_ZIP = "This file is not a valid Baihe library backup."


def _unsafe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    norm = name.replace("\\", "/")
    if not norm or "\x00" in norm or norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return True
    if ".." in norm.split("/"):
        return True
    return (info.external_attr >> 16) & 0o170000 == 0o120000  # symlink entry


def validate_backup_zip(zip_bytes) -> None:
    """Every check restore runs before touching the library: a real zip,
    library.db present, no absolute/traversal/symlink member, within the
    Step 52 member-count and size limits, no corrupt member. Fixed-text
    InvalidInputError on failure (no member names or paths echoed)."""
    if not isinstance(zip_bytes, (bytes, bytearray)) or not zip_bytes:
        raise InvalidInputError("Upload a backup .zip file.")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            infos = zf.infolist()
            if len(infos) > wjs._MAX_RESTORE_MEMBERS:
                raise InvalidInputError("The backup has too many files; it looks corrupted "
                                        "or unsafe to extract.")
            if "library.db" not in zf.namelist():
                raise InvalidInputError(_BAD_ZIP + " (no library.db inside).")
            total = 0
            for info in infos:
                if _unsafe_member(info):
                    raise InvalidInputError("The backup contains an unsafe file path.")
                if info.file_size > wjs._MAX_RESTORE_MEMBER_BYTES:
                    raise InvalidInputError("The backup contains a file that is too large; it "
                                            "looks corrupted or unsafe to extract.")
                total += info.file_size
                if total > wjs._MAX_RESTORE_TOTAL_BYTES:
                    raise InvalidInputError("The backup would expand too large; it looks "
                                            "corrupted or unsafe to extract.")
            if zf.testzip() is not None:
                raise InvalidInputError("The backup zip is corrupted.")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError, NotImplementedError):
        raise InvalidInputError(_BAD_ZIP) from None


def restore_backup(zip_bytes, confirm=False, confirm_text="") -> dict:
    """Replaces the whole library with an uploaded backup. Order: confirm
    (confirm=True, confirm_text "RESTORE") -> refuse if any job is running
    or queued -> full validation -> workspace_job_service.
    restore_library_backup (staging extract, rename-aside, rename-in,
    roll back on failure). Nothing is touched unless every check passes."""
    _require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Restoring a backup")
    if _any_job_running():
        raise ConflictError("A background job is running -- wait for it to finish or cancel "
                            "it before restoring.")
    validate_backup_zip(zip_bytes)
    try:
        wjs.restore_library_backup(bytes(zip_bytes), db.LIBRARY_DIR)
    except (ValueError, zipfile.BadZipFile):
        raise InvalidInputError(_BAD_ZIP) from None
    except OSError as e:
        log.exception("Library restore failed")
        raise ServiceError("The restore could not be completed; the current library was "
                           "left in place.") from e
    return {"restored": True}


# --------------------------------------------------------------------------
# storage cleanup
# --------------------------------------------------------------------------

def _check_preset(preset: str) -> list:
    if preset not in storage.STORAGE_QUALITY_PRESETS:
        raise InvalidInputError("Unknown storage preset.",
                                details={"allowed": list(storage.STORAGE_QUALITY_PRESETS)})
    return storage.categories_for_preset(preset)


def storage_scan(preset: str = "balanced") -> dict:
    """Dry run: what `storage_cleanup(preset, ...)` would remove, with
    sizes, per category and per drama (no paths, nothing removed)."""
    cats = _check_preset(preset)
    scan = storage.scan_library_storage(db.LIBRARY_DIR, [d["id"] for d in db.list_dramas()])
    per_drama = []
    for d in scan["per_drama"]:
        would = sum(d["categories"].get(k, 0) for k in cats)
        per_drama.append({"drama_id": d["drama_id"], "total_bytes": d["total_bytes"],
                          "would_free_bytes": would,
                          "job_running": drama_service.job_running_for_drama(d["drama_id"])})
    return {
        "preset": preset, "categories_to_clean": cats,
        "total_bytes": scan["total_bytes"], "reclaimable_bytes": scan["reclaimable_bytes"],
        "would_free_bytes": sum(scan["categories"].get(k, 0) for k in cats),
        "categories": [{"key": k, "label": cfg["label"], "note": cfg["note"],
                        "bytes": scan["categories"].get(k, 0), "selected": k in cats}
                       for k, cfg in storage.CLEANABLE_CATEGORIES.items()],
        "per_drama": per_drama,
    }


def storage_cleanup(preset: str, confirm=False, confirm_text="") -> dict:
    """Applies the preset's cleanup to every drama folder. Needs
    confirm=True and confirm_text "CLEAN". A drama with a running job is
    skipped. Returns per-drama freed bytes and the total."""
    cats = _check_preset(preset)
    _require_confirm(confirm, confirm_text, CLEAN_CONFIRM_TEXT, "Cleaning storage")
    results, freed = [], 0
    for d in db.list_dramas():
        did = d["id"]
        if drama_service.job_running_for_drama(did):
            results.append({"drama_id": did, "ok": False, "error": "job_running"})
            continue
        r = storage.clean_drama_storage(os.path.join(db.DRAMAS_DIR, str(did)), cats)
        freed += r["freed_bytes"]
        results.append({"drama_id": did, "ok": True, "freed_bytes": r["freed_bytes"]})
    return {"preset": preset, "freed_bytes": freed, "results": results}
