"""
services/media_upload_service.py -- upload an audio/video file into a
drama's folder: the file is saved as `source<ext>` in the drama folder; a video also gets its audio
track extracted to `audio.wav` and both filenames recorded, an audio file
records `audio_filename` and clears `source_video_filename` (`-2`, `-3`... is
added while the old file is still there).

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
and the drama is left as it was. A staged upload a crash left behind, and a
`source[-n]<ext>` / `audio[-n].wav` the drama does not name (a crash between
placing files and the DB write, a failed rollback, an old file that was in
use), is moved there at the drama's next upload or at startup once it is
a few minutes old (recover_stale_uploads); until then the media status
counts it as kept.
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
to a temp file in the drama folder (capped by `max_upload_bytes`). Every new
file is fsynced before it is put in place and recorded. No path is ever
returned.

No FastAPI import: takes a binary file-like object.
"""
import contextlib
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time

import background_jobs
import db
import video_export
from services import drama_service, settings_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
_CHUNK = 1024 * 1024
_MB = 1024 * 1024
# Free space kept back after an upload, so a fill-to-the-brim upload doesn't
# leave the library's database unable to write.
_DISK_MARGIN_BYTES = 256 * _MB
UPLOAD_LIMIT_HINT = "Change it in Settings > Advanced > Uploads."
_NO_DISK_ROOM = ("There is not enough free disk space for this upload. Free up space on the "
                 "Baihe PC or upload a smaller file.")
# The content modes with an audio pipeline (source_service's
# `has_audio_pipeline`); keep in sync by hand.
UPLOAD_CONTENT_MODES = ("audio_drama", "streamer_vod")
NO_UPLOAD_MODE = ("This drama has no audio to upload (it is set to work from a novel). "
                   "Change what you are working from to Audio drama or Streamer/VOD first.")
_BAD_TYPE = "Unsupported file type. Upload an audio or video file."
_EXTRACT_FAILED = ("Could not read audio from that video file. The uploaded video was kept "
                   "in this title's folder; the title's audio is unchanged.")
_SAVE_FAILED = ("Could not put the upload in place as this title's media. The uploaded file was "
                "kept in this title's folder and is counted with the old copies; the title's "
                "audio and video are unchanged.")
_CONFIRM_REPLACE = "This drama already has audio. Confirm replacing it first."
KEPT_DIRNAME = "kept_media"
_MEDIA_FIELDS = ("audio_filename", "source_video_filename")
# A new title has no transcript_mode (it reads as have_transcript), which
# needs pasted text; a title that was reading burned-in subtitles has none,
# so it moves to the one audio mode that works without it.
AUDIO_TRANSCRIPT_MODE = "whisper"
# The in-place names install_media gives new media; one the drama doesn't
# name is a leftover (see recover_stale_uploads).
_IN_PLACE_RE = re.compile(r"(?:source(?:-\d+)?(?:%s)|audio(?:-\d+)?\.wav)\Z" % "|".join(
    re.escape(e) for e in AUDIO_EXTENSIONS + VIDEO_EXTENSIONS))
EXTRACT_JOB_PREFIX = "extract_audio_"
# Wall-clock cap on one ffmpeg extraction, so a stuck ffmpeg can't leave the
# job "running" forever; cancel kills it sooner.
EXTRACT_TIMEOUT_SECONDS = 2 * 60 * 60
_FOLLOW_POLL_SECONDS = 0.5
# The follow-up transcription's own watchdog is per worker; this bounds the
# wait for a run that never ends (a hung queue slot). The floor covers short
# media, where model loading dominates; the multiple covers slow CPU runs.
_FOLLOW_FLOOR_SECONDS = 2 * 60 * 60
_FOLLOW_PER_MEDIA_SECOND = 5
FOLLOW_TIMEOUT_MESSAGE = "Transcription took much longer than expected and was stopped."


def follow_deadline_seconds(media_seconds) -> float:
    return max(_FOLLOW_FLOOR_SECONDS, _FOLLOW_PER_MEDIA_SECOND * float(media_seconds or 0.0))
# An unnamed in-place file younger than this may be one another Baihe process
# has just put in place and not yet recorded (the upload claim is per
# process), so recovery leaves it for a later pass.
UNNAMED_MIN_AGE_SECONDS = 10 * 60
_BUSY = "A job is running for this drama. Wait for it to finish or cancel it."
# Per-drama upload claim: held from the running-job check until the
# file is in place and any extraction job is registered, so a concurrent
# upload is refused before it touches `source<ext>`. In-process only;
# another process is covered by job_running_for_drama's job_records check.
claims_lock = threading.Lock()
claimed = set()


def _env_limit_mb():
    """BAIHE_MAX_UPLOAD_MB as a positive finite number, else None. A zero,
    negative or unparseable value is ignored rather than read as a 0-byte
    limit, which would silently block every upload."""
    try:
        mb = float(os.environ.get("BAIHE_MAX_UPLOAD_MB", ""))
    except ValueError:
        return None
    if not (math.isfinite(mb) and mb > 0):
        return None
    # 1e303 is finite, but int(mb * _MB) would overflow to inf.
    return min(mb, settings_service.MAX_UPLOAD_MB)


def upload_limit_from_env() -> bool:
    return _env_limit_mb() is not None


def max_upload_bytes() -> int:
    """The environment variable wins over the saved setting so an owner who
    pinned a limit there isn't overridden from the UI. Every caller sits on
    a local_only() route (the household listener refuses uploads outright),
    so there is no separate household limit to apply."""
    mb = _env_limit_mb()
    if mb is None:
        mb = settings_service.get_preference("max_upload_mb")
    return int(mb * _MB)


def too_large_message(limit: int) -> str:
    return f"That file is larger than the {limit // _MB} MB upload limit. {UPLOAD_LIMIT_HINT}"


def _check_disk_room(folder, fileobj):
    """Refuses before copying when the drive can't hold the upload. Skipped
    when the size or free space can't be read, so an odd file object or a
    drive that doesn't report free space never blocks an upload."""
    try:
        here = fileobj.tell()
        size = fileobj.seek(0, os.SEEK_END) - here
        fileobj.seek(here)
    except (AttributeError, OSError, ValueError):
        return
    check_room_for(folder, size)


def check_room_for(folder, size):
    """Same refusal for a size known up front (a Content-Length)."""
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < size + _DISK_MARGIN_BYTES:
        raise InvalidInputError(_NO_DISK_ROOM)


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


def has_media(drama, drama_id) -> bool:
    ddir = db.drama_dir(drama_id)
    return any(name and os.path.exists(os.path.join(ddir, name))
               for name in (drama.get("audio_filename"), drama.get("source_video_filename")))


def _fsync_file(path):
    # Before the DB names the file, so a power cut can't leave it pointing at
    # a file whose data never reached the disk. r+b: Windows fsync needs a
    # writable handle. Best effort, like _fsync_dir.
    with contextlib.suppress(OSError):
        with open(path, "r+b") as f:
            os.fsync(f.fileno())


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
    moved (Windows: it is still being played) just stays where it is.
    Only a file or a link is moved: an imported title can name a folder
    (e.g. "pages"), which must never be carried off into kept_media."""
    if not name or name != os.path.basename(name):
        return
    try:
        mode = os.lstat(os.path.join(ddir, name)).st_mode
    except OSError:
        return
    if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
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
    drama naming a consistent, complete set of files. new_files is the
    drama's whole media set: a media field it leaves out is cleared, so
    audio installed alone also unnames the old video. Only after the commit
    are the files the drama no longer names moved into kept_media/. On an
    error before the commit the new files are moved back to their paths and
    the error raised, with the drama unchanged."""
    ddir = db.drama_dir(drama_id)
    old = db.get_drama(drama_id) or {}
    # An old video kept beside new audio would be shown in Review and muxed
    # into exports with sound that isn't its own.
    for field in _MEDIA_FIELDS:
        if field not in new_files:
            fields.setdefault(field, None)
    # hardsub_ocr needs a video (source_service refuses to set it without
    # one), and every transcribe run of such a title fails once it is gone.
    if old.get("transcript_mode") == "hardsub_ocr" and "source_video_filename" not in new_files:
        fields.setdefault("transcript_mode", AUDIO_TRANSCRIPT_MODE)
    placed = []
    try:
        for field, (src, stem, ext) in new_files.items():
            _fsync_file(src)
            dst = _move_no_clobber(src, _in_place_names(ddir, stem, ext))
            placed.append((dst, src))
            # recover_stale_uploads leaves an unnamed file alone while it is
            # young, but a link or rename keeps an old mtime and on Windows
            # its ctime too (the creation time there), so it is set to now.
            if not os.path.islink(dst):
                with contextlib.suppress(OSError):
                    os.utime(dst)
            fields[field] = os.path.basename(dst)
        _fsync_dir(ddir)
        db.update_drama(drama_id, **fields)
    except BaseException:
        for dst, src in reversed(placed):
            with contextlib.suppress(OSError):
                os.rename(dst, src)
        raise
    named = {fields[f] for f in _MEDIA_FIELDS}
    for name in {old.get(f) for f in _MEDIA_FIELDS} - named:
        try:
            _retire(ddir, name)
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


def _is_staged_upload(name) -> bool:
    return (name.startswith(".upload_")
            and os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS + VIDEO_EXTENSIONS)


def _unnamed_media(ddir, drama, names) -> list:
    """The regular files in `names` with an in-place media name that no
    `*_filename` field of the drama names. A field names a file by spelling
    or by identity: Windows and macOS resolve `Audio.wav` to `audio.wav`,
    so a file that is the same (st_dev, st_ino) as a named one is never a
    leftover."""
    named, named_ids = set(), set()
    for key, value in drama.items():
        if key.endswith("_filename") and isinstance(value, str) and value:
            # A stored path's last part counts too: never move a file the
            # drama might reach some other way.
            for ref in (value, value.replace("\\", "/").rsplit("/", 1)[-1]):
                named.add(os.path.normcase(ref))
                with contextlib.suppress(OSError, ValueError):
                    st = os.lstat(os.path.join(ddir, ref))
                    named_ids.add((st.st_dev, st.st_ino))
    out = []
    for name in names:
        if os.path.normcase(name) in named or not _IN_PLACE_RE.match(name):
            continue
        with contextlib.suppress(OSError):
            st = os.lstat(os.path.join(ddir, name))
            if stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) not in named_ids:
                out.append(name)
    return out


def recover_stale_uploads(drama_id, now=None) -> int:
    """Moves media that a crash, restart, failed job start or failed move
    left in the drama folder into kept_media/: staged uploads
    (`.upload_*<ext>`, only once older than EXTRACT_TIMEOUT_SECONDS) as
    failed-upload-*, and in-place media files the drama doesn't name (only
    once untouched for UNNAMED_MIN_AGE_SECONDS) as unreferenced-*. Nothing
    while a job runs for the drama, so a live extraction's input or a job's
    not-yet-recorded file is never moved; the caller holds the drama's
    upload claim for the same reason. The claim only covers this process
    and an audio upload starts no job, so another process's just-placed
    file is protected by the age rule alone. Returns how many were moved."""
    if drama_service.job_running_for_drama(drama_id):
        return 0
    drama = db.get_drama(drama_id)
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
        if not _is_staged_upload(name):
            continue
        try:
            if os.path.islink(path) or now - os.lstat(path).st_mtime < EXTRACT_TIMEOUT_SECONDS:
                continue
        except OSError:
            continue
        _keep_failed_upload(ddir, path, ext)
        moved += not os.path.lexists(path)
    # Without the drama's row nothing tells a leftover from its media.
    for name in _unnamed_media(ddir, drama, names) if drama is not None else ():
        try:
            st = os.lstat(os.path.join(ddir, name))
        except OSError:
            continue
        # ctime as well: a link or rename into place keeps the old mtime but
        # updates the POSIX ctime. (Windows ctime is the creation time;
        # install_media sets the mtime of what it places.)
        if now - max(st.st_mtime, st.st_ctime) < UNNAMED_MIN_AGE_SECONDS:
            continue
        _retire(ddir, name, "unreferenced")
        moved += not os.path.lexists(os.path.join(ddir, name))
    return moved


def recover_all_stale_uploads() -> int:
    """Startup pass of recover_stale_uploads over every drama folder. A
    drama with an upload in progress in this process is skipped: its new
    file may be in place before the DB names it (another process's upload
    is left alone by the age rule in recover_stale_uploads)."""
    try:
        names = os.listdir(db.DRAMAS_DIR)
    except OSError:
        return 0
    moved = 0
    for drama_id in (int(n) for n in names if n.isdigit()):
        with claims_lock:
            if drama_id in claimed:
                continue
            claimed.add(drama_id)
        try:
            moved += recover_stale_uploads(drama_id)
        finally:
            with claims_lock:
                claimed.discard(drama_id)
    return moved


def _save_upload(drama_id, client_filename, fileobj, confirm_replace_audio=False):
    """Streams the body to a hidden file in the drama folder; returns
    (ext, size, staged path). Nothing is put in place here."""
    ext = _safe_extension(client_filename)
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in UPLOAD_CONTENT_MODES:
        raise InvalidInputError(NO_UPLOAD_MODE)
    if has_media(drama, drama_id) and not confirm_replace_audio:
        raise InvalidInputError(_CONFIRM_REPLACE, details={"reason": "confirm_replace_audio"})
    limit = max_upload_bytes()
    ddir = db.drama_dir(drama_id)
    _check_disk_room(ddir, fileobj)
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
                    raise InvalidInputError(too_large_message(limit))
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
        # The job picks the video's name when it puts it in place.
        return {"name": None, "size": size, "kind": "video", "job_id": job_id}
    finally:
        with claims_lock:
            claimed.discard(drama_id)


def _extract_audio_job(job_id, drama_id, ext, staged, transcribe_options=None):
    ddir = db.drama_dir(drama_id)
    part_path = os.path.join(ddir, ".audio.extract.wav")
    background_jobs.update_progress(job_id, 0.05, "Extracting audio from the video...")
    cmd = ["ffmpeg", "-y", *video_export.local_input(), "-i", staged, "-vn",
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", part_path]
    failed = _EXTRACT_FAILED
    try:
        background_jobs.run_cancellable(job_id, cmd, cwd=ddir, timeout=EXTRACT_TIMEOUT_SECONDS)
        failed = _SAVE_FAILED
        from services import transcribe_service
        media_seconds = transcribe_service._audio_duration_seconds(part_path)
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
    if background_jobs.is_cancel_requested(job_id):  # never start a GPU run after a cancel
        raise background_jobs.JobCancelled(job_id)
    background_jobs.update_progress(job_id, 0.1, "Audio extracted. Starting transcription...")
    run = transcribe_service.start_transcribe_run(drama_id, **transcribe_options)
    _follow_job(job_id, run["job_id"], follow_deadline_seconds(media_seconds))


def _follow_job(job_id, child_id, deadline_s):
    """Mirrors child_id's progress onto job_id until the child ends and
    forwards a cancel to it; a child error or cancel ends job_id the same way.
    A queued child is removed outright by cancel_queued, so once a cancel
    has been forwarded a vanished child also counts as cancelled. A child
    still going after deadline_s seconds is stopped and job_id ends as an
    error, so this job can't wait forever."""
    forwarded = False
    give_up_at = time.monotonic() + deadline_s
    while True:
        if time.monotonic() >= give_up_at:
            if not background_jobs.cancel_queued(child_id):
                background_jobs.request_cancel(child_id)
            raise RuntimeError(FOLLOW_TIMEOUT_MESSAGE)
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
    """Count and total size of the files in the drama's kept_media/, plus
    the kept files recover_stale_uploads hasn't moved there yet (staged
    uploads and in-place media the drama doesn't name); numbers only, never
    a name. Links are not followed or counted. While a job runs those
    files may be its live input or not yet recorded, so they aren't counted."""
    ddir = db.drama_dir(drama_id)
    kept = os.path.join(ddir, KEPT_DIRNAME)
    files = size = 0
    names = []
    if not drama_service.job_running_for_drama(drama_id):
        with contextlib.suppress(OSError):
            names = os.listdir(ddir)
    leftovers = [n for n in names if _is_staged_upload(n)]
    leftovers += _unnamed_media(ddir, db.get_drama(drama_id) or {}, names)
    for name in leftovers:
        with contextlib.suppress(OSError):
            st = os.lstat(os.path.join(ddir, name))
            if stat.S_ISREG(st.st_mode):
                files += 1
                size += st.st_size
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
        "reads_burned_in_subtitles": drama.get("transcript_mode") == "hardsub_ocr",
        "upload_max_mb": max_upload_bytes() // _MB,
        **kept_media_usage(drama_id),
    }
