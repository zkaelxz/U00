"""
services/media_upload_service.py -- upload an audio/video file into a
drama's folder: the file is saved as `source<ext>` in the drama folder; a video also gets its audio
track extracted to `audio.wav` and both filenames recorded, an audio file
records just `audio_filename` (`-2`, `-3`... is added while the old file is
still there).

A drama that already has audio or a source video needs
`confirm_replace_audio` (422 with details.reason "confirm_replace_audio"
otherwise, the same rule as url_media_service). An existing file is never
overwritten: new media goes to a free `<stem>[-n]<ext>` name, one DB write
switches the drama to it (the commit point), and only then are the files the
drama no longer names moved to `kept_media/replaced-<UTC time><ext>` (a file
that can't be moved, e.g. open in a player on Windows, stays where it is,
unreferenced). A video is staged under a hidden `.upload_*` name and only put
in place after its audio was extracted; if extraction or saving fails or is
cancelled, the upload is kept as `kept_media/failed-upload-<UTC time><ext>`
and the drama is left as it was. A staged upload a crash left behind is moved
there at the drama's next upload or at startup (recover_stale_uploads).
Nothing in kept_media/ is referenced by the database, and the app never
deletes it on its own: it goes only when the user clears it in Storage or
deletes the title. "Remove audio/video" leaves it. Backups with media and
imports carry it with the rest of the drama folder; the media status reports
its file count and size.

A video's audio extraction (ffmpeg) runs in background job
`extract_audio_<drama_id>`, not in the request. The job owns the whole
step (extraction, then the field-scoped DB write) and, for
upload-and-transcribe, starts the transcribe run and follows it, so the
client still polls one job id. Cancel kills the ffmpeg process tree.

The client's filename is never used for storage or returned: only its
extension is read, and only if it is on the whitelist. The body is streamed
to a temp file in the drama folder (capped at BAIHE_MAX_UPLOAD_MB, default
2048) and fsynced before it is renamed into place. No path is ever returned.

No FastAPI import: takes a binary file-like object.
"""
import contextlib
import os
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time

import background_jobs
import db
from services import drama_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
_CHUNK = 1024 * 1024
_DEFAULT_MAX_MB = 2048
_TOO_LARGE = "The uploaded file is too large."
# The content modes with an audio pipeline (source_service's
# `has_audio_pipeline`); keep in sync by hand.
UPLOAD_CONTENT_MODES = ("audio_drama", "streamer_vod")
NO_UPLOAD_MODE = ("This drama has no audio to upload (it is set to work from a novel). "
                   "Change what you are working from to Audio drama or Streamer/VOD first.")
_BAD_TYPE = "Unsupported file type. Upload an audio or video file."
_EXTRACT_FAILED = ("Could not read audio from that video file. The uploaded video was kept "
                   "in this title's folder; the title's audio is unchanged.")
_SAVE_FAILED = ("Could not save the upload into this title's folder. The uploaded file was kept "
                "there; the title's audio and video are unchanged.")
_CONFIRM_REPLACE = "This drama already has audio. Confirm replacing it first."
KEPT_DIRNAME = "kept_media"
_MEDIA_FIELDS = ("audio_filename", "source_video_filename")
EXTRACT_JOB_PREFIX = "extract_audio_"
# Wall-clock cap on one ffmpeg extraction, so a stuck ffmpeg can't leave the
# job "running" forever; cancel kills it sooner.
EXTRACT_TIMEOUT_SECONDS = 2 * 60 * 60
_FOLLOW_POLL_SECONDS = 0.5
_BUSY = "A job is running for this drama. Wait for it to finish or cancel it."
# Per-drama upload claim: held from the running-job check until the
# file is in place and any extraction job is registered, so a concurrent
# upload is refused before it touches `source<ext>`. In-process only;
# another process is covered by job_running_for_drama's job_records check.
claims_lock = threading.Lock()
claimed = set()


def max_upload_bytes() -> int:
    try:
        mb = float(os.environ.get("BAIHE_MAX_UPLOAD_MB", _DEFAULT_MAX_MB))
    except ValueError:
        mb = _DEFAULT_MAX_MB
    return int(max(mb, 0) * 1024 * 1024)


def _safe_extension(client_filename) -> str:
    """Only the extension of the last path component, lowercased, and only
    if whitelisted; everything else in the client's name is discarded."""
    name = str(client_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if any(ord(c) < 32 or ord(c) == 127 for c in name):  # includes NUL
        raise InvalidInputError(_BAD_TYPE)
    ext = os.path.splitext(name)[1].lower()
    if ext not in AUDIO_EXTENSIONS + VIDEO_EXTENSIONS:
        raise InvalidInputError(_BAD_TYPE)
    return ext


def _has_media(drama, drama_id) -> bool:
    ddir = db.drama_dir(drama_id)
    return any(name and os.path.exists(os.path.join(ddir, name))
               for name in (drama.get("audio_filename"), drama.get("source_video_filename")))


def _fsync_dir(path):
    # Best effort: a folder that refuses fsync must not fail an upload whose
    # renames already happened.
    with contextlib.suppress(OSError):
        db.fsync_dir(path)


def _kept_dir(ddir) -> str:
    """kept_media/ in ddir, created on demand. A link or junction there could
    send kept originals (or their hard links) outside the title's folder, so
    anything but a plain folder directly inside ddir is refused."""
    kept = os.path.join(ddir, KEPT_DIRNAME)
    if os.path.islink(kept) or getattr(os.path, "isjunction", lambda _p: False)(kept):
        raise OSError("kept_media is a link")
    os.makedirs(kept, exist_ok=True)
    if os.path.dirname(os.path.realpath(kept)) != os.path.realpath(ddir):
        raise OSError("kept_media is outside the title's folder")
    return kept


def _kept_names(ddir, label, ext):
    kept = _kept_dir(ddir)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return (os.path.join(kept, f"{label}-{stamp}{'' if n == 1 else f'-{n}'}{ext}")
            for n in range(1, 1000))


def _in_place_names(ddir, stem, ext):
    return (os.path.join(ddir, f"{stem}{'' if n == 1 else f'-{n}'}{ext}") for n in range(1, 1000))


def _move_no_clobber(src, candidates) -> str:
    """Moves src to the first free path in `candidates` and returns it; never
    replaces an existing file. A hard link plus unlink fails with
    FileExistsError on a taken name on every OS, where a POSIX rename would
    silently overwrite; the rename fallback (no hard links on this
    filesystem, or src is a symlink, which is moved itself, never followed)
    re-checks the name first and only races this process, which holds the
    drama's job. If src can't be unlinked (Windows: it is open) the new name
    is removed again so the file never has two names."""
    for dst in candidates:
        if os.path.lexists(dst):
            continue
        if not os.path.islink(src):
            try:
                os.link(src, dst)
            except FileExistsError:
                continue
            except OSError:
                pass
            else:
                try:
                    os.unlink(src)
                except OSError:
                    with contextlib.suppress(OSError):
                        os.unlink(dst)
                    raise
                return dst
        if os.path.lexists(dst):
            continue
        try:
            os.rename(src, dst)
        except FileExistsError:
            continue
        return dst
    raise OSError("no free file name")


def _retire(ddir, name, label="replaced"):
    """Moves a file the drama no longer names into kept_media/. Best effort:
    the database already points at the new file, so a file that can't be
    moved (Windows: it is still being played) just stays where it is."""
    if not name or name != os.path.basename(name) or not os.path.lexists(os.path.join(ddir, name)):
        return
    with contextlib.suppress(OSError):
        _move_no_clobber(os.path.join(ddir, name),
                         _kept_names(ddir, label, os.path.splitext(name)[1]))
        _fsync_dir(os.path.join(ddir, KEPT_DIRNAME))
        _fsync_dir(ddir)


def install_media(drama_id, new_files, **fields):
    """Puts new media in place for drama_id. new_files maps a DB field
    (audio_filename / source_video_filename) to (path, stem, ext): each file
    is moved to a free `<stem>[-n]<ext>` in the drama folder, never over an
    existing file, so a file that is open elsewhere is never touched. One
    update_drama call then switches every field at once (with `fields`): the
    database is the commit point, so a crash or error at any step leaves the
    drama naming a consistent, complete set of files. Only after it are the
    files the drama no longer names moved into kept_media/. On an error
    before the commit the new files are moved back to their paths and the
    error raised, with the drama unchanged."""
    ddir = db.drama_dir(drama_id)
    old = db.get_drama(drama_id) or {}
    placed = []
    try:
        for field, (src, stem, ext) in new_files.items():
            dst = _move_no_clobber(src, _in_place_names(ddir, stem, ext))
            placed.append((dst, src))
            fields[field] = os.path.basename(dst)
        _fsync_dir(ddir)
        db.update_drama(drama_id, **fields)
    except BaseException:
        for dst, src in reversed(placed):
            with contextlib.suppress(OSError):
                os.rename(dst, src)
        raise
    named = {fields.get(f, old.get(f)) for f in _MEDIA_FIELDS}
    for field in new_files:
        if old.get(field) not in named:
            try:
                _retire(ddir, old.get(field))
            except Exception:  # committed: a retire hiccup must not report the swap as failed
                pass
    return {f: fields[f] for f in new_files}


def _keep_failed_upload(ddir, staged, ext):
    """Moves a video whose audio couldn't be extracted to kept_media/. If
    even that fails it stays at its hidden staged name, where
    recover_stale_uploads finds it later: never deleted."""
    if not os.path.lexists(staged):
        return
    with contextlib.suppress(OSError):
        _move_no_clobber(staged, _kept_names(ddir, "failed-upload", ext))
        _fsync_dir(os.path.join(ddir, KEPT_DIRNAME))


def recover_stale_uploads(drama_id, now=None) -> int:
    """Moves staged uploads (`.upload_*<ext>`) that a crash, restart or failed
    job start left in the drama folder into kept_media/failed-upload-*.
    Only files older than EXTRACT_TIMEOUT_SECONDS, and none while a job runs
    for the drama, so a live extraction's input is never moved. Returns how
    many were moved."""
    if drama_service.job_running_for_drama(drama_id):
        return 0
    ddir = db.drama_dir(drama_id)
    now = time.time() if now is None else now
    try:
        names = os.listdir(ddir)
    except OSError:
        return 0
    moved = 0
    for name in names:
        ext = os.path.splitext(name)[1].lower()
        path = os.path.join(ddir, name)
        if not name.startswith(".upload_") or ext not in AUDIO_EXTENSIONS + VIDEO_EXTENSIONS:
            continue
        try:
            if os.path.islink(path) or now - os.lstat(path).st_mtime < EXTRACT_TIMEOUT_SECONDS:
                continue
        except OSError:
            continue
        _keep_failed_upload(ddir, path, ext)
        moved += not os.path.lexists(path)
    return moved


def recover_all_stale_uploads() -> int:
    """Startup pass of recover_stale_uploads over every drama folder."""
    try:
        names = os.listdir(db.DRAMAS_DIR)
    except OSError:
        return 0
    return sum(recover_stale_uploads(int(n)) for n in names if n.isdigit())


def _save_upload(drama_id, client_filename, fileobj, confirm_replace_audio=False):
    """Streams the body to a hidden file in the drama folder; returns
    (ext, size, staged path). Nothing is put in place here."""
    ext = _safe_extension(client_filename)
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in UPLOAD_CONTENT_MODES:
        raise InvalidInputError(NO_UPLOAD_MODE)
    if _has_media(drama, drama_id) and not confirm_replace_audio:
        raise InvalidInputError(_CONFIRM_REPLACE, details={"reason": "confirm_replace_audio"})
    limit = max_upload_bytes()
    ddir = db.drama_dir(drama_id)
    fd, tmp_path = tempfile.mkstemp(prefix=".upload_", suffix=".part", dir=ddir)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = fileobj.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise InvalidInputError(_TOO_LARGE)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if size == 0:
            raise InvalidInputError("The uploaded file is empty.")
        # ffmpeg picks some demuxers by extension, so the staged video keeps it.
        staged = tmp_path[:-len(".part")] + ext
        os.replace(tmp_path, staged)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return ext, size, staged


def upload_media(drama_id, client_filename, fileobj, transcribe_options=None,
                 confirm_replace_audio=False) -> dict:
    """Only one upload per drama at a time, and none while a drama job runs.
    Replacing existing audio/video needs confirm_replace_audio (422
    details.reason "confirm_replace_audio"). Saves the upload. An audio
    file is put in place and recorded at once (job_id None).
    A video starts job `extract_audio_<id>` and returns its job_id before
    extraction runs (poll GET /api/jobs/{job_id}). With transcribe_options
    (kwargs for transcribe_service.start_transcribe_run), that same job
    starts and follows the transcribe run once the audio is extracted; for
    an audio file the run is started here, under the upload claim, and its
    id returned as "transcribe_job_id" (the upload is kept if it fails)."""
    if not isinstance(confirm_replace_audio, bool):
        raise InvalidInputError("confirm_replace_audio must be true or false.")
    with claims_lock:
        if drama_id in claimed:
            raise ConflictError("Another upload is in progress for this drama.")
        claimed.add(drama_id)
    try:
        if drama_service.job_running_for_drama(drama_id):
            raise ConflictError(_BUSY)
        recover_stale_uploads(drama_id)
        ext, size, staged = _save_upload(drama_id, client_filename, fileobj, confirm_replace_audio)
        ddir = db.drama_dir(drama_id)
        if ext not in VIDEO_EXTENSIONS:
            try:
                name = install_media(drama_id, {"audio_filename": (staged, "source", ext)})[
                    "audio_filename"]
            except BaseException as exc:
                _keep_failed_upload(ddir, staged, ext)
                if isinstance(exc, (OSError, sqlite3.Error)):
                    raise ConflictError(_SAVE_FAILED) from None
                raise
            result = {"name": name, "size": size, "kind": "audio", "job_id": None}
            if transcribe_options is not None:  # started while the claim is still held
                from services import transcribe_service
                run = transcribe_service.start_transcribe_run(drama_id, **transcribe_options)
                result["transcribe_job_id"] = run["job_id"]
            return result
        job_id = f"{EXTRACT_JOB_PREFIX}{drama_id}"
        try:
            started = background_jobs.start_job(
                job_id, _extract_audio_job, job_id, drama_id, ext, staged, transcribe_options,
                description=f"Audio extraction (drama #{drama_id})")
        except BaseException:
            _keep_failed_upload(ddir, staged, ext)
            raise
        if not started:  # unreachable while the claim and running-job check hold
            _keep_failed_upload(ddir, staged, ext)
            raise ConflictError(_BUSY)
        return {"name": f"source{ext}", "size": size, "kind": "video", "job_id": job_id}
    finally:
        with claims_lock:
            claimed.discard(drama_id)


def _extract_audio_job(job_id, drama_id, ext, staged, transcribe_options=None):
    ddir = db.drama_dir(drama_id)
    part_path = os.path.join(ddir, ".audio.extract.wav")
    background_jobs.update_progress(job_id, 0.05, "Extracting audio from the video...")
    # -protocol_whitelist file: an upload named .mp4 could really be an HLS
    # playlist naming network URLs; ffmpeg may only open local files.
    cmd = ["ffmpeg", "-y", "-protocol_whitelist", "file", "-i", staged, "-vn",
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", part_path]
    failed = _EXTRACT_FAILED
    try:
        background_jobs.run_cancellable(job_id, cmd, cwd=ddir, timeout=EXTRACT_TIMEOUT_SECONDS)
        failed = _SAVE_FAILED
        install_media(drama_id, {"source_video_filename": (staged, "source", ext),
                                 "audio_filename": (part_path, "audio", ".wav")})
    except BaseException as exc:
        if os.path.exists(part_path):
            os.remove(part_path)
        # The upload may be the user's only copy, so a failure or cancel keeps it.
        _keep_failed_upload(ddir, staged, ext)
        if isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError,
                            sqlite3.Error)):
            raise RuntimeError(failed) from None
        raise
    if transcribe_options is None:
        background_jobs.update_progress(job_id, 1.0, "Audio extracted.")
        return
    from services import transcribe_service
    if background_jobs.is_cancel_requested(job_id):  # never start a GPU run after a cancel
        raise background_jobs.JobCancelled(job_id)
    background_jobs.update_progress(job_id, 0.1, "Audio extracted. Starting transcription...")
    run = transcribe_service.start_transcribe_run(drama_id, **transcribe_options)
    _follow_job(job_id, run["job_id"])


def _follow_job(job_id, child_id):
    """Mirrors child_id's progress onto job_id until the child ends and
    forwards a cancel to it; a child error or cancel ends job_id the same way.
    A queued child is removed outright by cancel_queued, so once a cancel
    has been forwarded a vanished child also counts as cancelled."""
    forwarded = False
    while True:
        child = background_jobs.get_status(child_id) or {}
        status = child.get("status")
        if status not in ("running", "queued"):
            break
        if background_jobs.is_cancel_requested(job_id) and not forwarded:
            forwarded = True
            if not background_jobs.cancel_queued(child_id):
                background_jobs.request_cancel(child_id)
            continue
        background_jobs.update_progress(job_id, 0.1 + 0.9 * float(child.get("progress") or 0.0),
                                        child.get("message") or "Transcribing...")
        time.sleep(_FOLLOW_POLL_SECONDS)
    if status == "cancelled" or (forwarded and status is None):
        raise background_jobs.JobCancelled(job_id)
    if status != "done":
        raise RuntimeError(child.get("error") or "Transcription failed.")
    # A "done" run can still carry result.failed_reason; pass it through.
    background_jobs.set_result(job_id, child.get("result"))


def kept_media_usage(drama_id) -> dict:
    """Count and total size of the files in the drama's kept_media/; numbers
    only, never a name. Links are not followed or counted."""
    kept = os.path.join(db.drama_dir(drama_id), KEPT_DIRNAME)
    files = size = 0
    if os.path.isdir(kept) and not os.path.islink(kept):
        for root, dirs, names in os.walk(kept):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for name in names:
                with contextlib.suppress(OSError):
                    st = os.lstat(os.path.join(root, name))
                    if stat.S_ISREG(st.st_mode):
                        files += 1
                        size += st.st_size
    return {"kept_media_files": files, "kept_media_bytes": size}


def get_media_status(drama_id) -> dict:
    """Booleans, counts and the upload cap only -- never a path or filename."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    audio = drama.get("audio_filename")
    return {
        "drama_id": drama_id,
        "has_audio": bool(audio and os.path.exists(os.path.join(db.drama_dir(drama_id), audio))),
        "has_source_video": bool(drama.get("source_video_filename")),
        "upload_max_mb": max_upload_bytes() // (1024 * 1024),
        **kept_media_usage(drama_id),
    }
