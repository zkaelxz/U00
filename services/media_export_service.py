"""
services/media_export_service.py -- audiobook and burned-in-video export as
background jobs (Migration Slices 29 and 30).

Each start function validates everything up front (fixed-text errors), then
starts a thread job that does the whole export: ffmpeg runs through
subprocess with fixed argument lists (no shell, no client-supplied path or
filter string) and the result is moved into the artifact folder
(`artifact_service.output_path`), so it downloads via
GET /api/artifacts/dramas/{id}/{kind}. Output is built in a temp folder
first, so a failed run never leaves a truncated file for the download
endpoint to serve. Job errors carry fixed text only (no paths). Real ffmpeg
is not exercised by the tests.

Parity E17/E19 add the soft-subtitle video (a toggleable subtitle track
muxed in, video_export.mux_soft_subtitles_cmd) and the dubbed video (the
dub track replacing the audio, or mixed over the original at -20 dB,
video_export.replace_audio_with_dub_cmd), written to their own artifact
kinds ("softsub_video", "dubbed_video"; the burned-in video stays "video")
so each download serves its own file. Only one video job runs per drama at
a time; their ffmpeg runs are cancellable and time-limited.
"""
import contextlib
import os
import shutil
import subprocess
import tempfile
import threading

import background_jobs
import db
import dub_narration
import video_export
from services import artifact_service, export_service
from services.media_upload_service import VIDEO_EXTENSIONS
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError)

_FFMPEG_MISSING = "ffmpeg is not installed or not on PATH, which this export requires."
# Stream-copy and audio-only re-encodes are fast; this only stops a hung ffmpeg.
_VIDEO_TIMEOUT_S = 4 * 3600
_VIDEO_JOB_PREFIXES = ("burned_video_", "softsub_video_", "dubbed_video_")
_DUB_ORIGINAL_DB = -20.0   # "mix original audio in quietly"


def _get_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _require_ffmpeg():
    if shutil.which("ffmpeg") is None:
        raise DependencyUnavailableError(_FFMPEG_MISSING)


def _refuse_duplicate(job_id: str, label: str, drama_id: int):
    job = background_jobs.get_status(job_id)
    if job and job["status"] in ("running", "queued"):
        raise ConflictError(f"The {label} export is already running for drama {drama_id}.")


_video_start_lock = threading.Lock()


def _start_video_job(drama_id: int, job_id: str, target, *args, description: str) -> dict:
    """One video export per drama at a time (each is a long ffmpeg run over
    the same source video). The check and the start happen under one lock,
    so two requests at once can't both pass the check."""
    with _video_start_lock:
        for prefix in _VIDEO_JOB_PREFIXES:
            job = background_jobs.get_status(f"{prefix}{drama_id}")
            if job and job["status"] in ("running", "queued"):
                raise ConflictError(f"A video export is already running for drama {drama_id}.")
        if not background_jobs.start_job(job_id, target, *args, description=description):
            raise ConflictError(f"A video export is already running for drama {drama_id}.")
    return {"job_id": job_id}


def _source_video(drama: dict, drama_id: int) -> str:
    filename = drama.get("source_video_filename") or ""
    video_path = os.path.join(db.drama_dir(drama_id), filename) if filename else ""
    if (not filename or filename != os.path.basename(filename)
            or not os.path.isfile(video_path)):
        raise InvalidInputError("No source video uploaded for this drama.")
    return video_path


@contextlib.contextmanager
def _fixed_write_errors():
    """Job errors are served on GET /api/jobs, so an OSError from writing the
    temp files or moving the output (which names absolute paths) becomes
    fixed text."""
    try:
        yield
    except OSError:
        raise RuntimeError("Could not write the export file.") from None


def _run_video_ffmpeg(job_id, cmd, cwd):
    try:
        background_jobs.run_cancellable(job_id, cmd, cwd=cwd, timeout=_VIDEO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg took too long and was stopped.") from None
    except (subprocess.CalledProcessError, OSError):
        raise RuntimeError("ffmpeg failed to produce the export.") from None


def _audiobook_job(job_id, drama_id, lines, ddir, title, narrate_original, language):
    background_jobs.update_progress(job_id, 0.1, "Encoding audiobook...")
    with _fixed_write_errors(), tempfile.TemporaryDirectory() as tmp:
        tmp_out = os.path.join(tmp, "audiobook.m4b")
        try:
            dub_narration.export_narration_m4b(lines, ddir, title=title, out_path=tmp_out,
                                     narrate_original=narrate_original,
                                     cancel_job_id=job_id)
        except (subprocess.CalledProcessError, OSError):
            raise RuntimeError("ffmpeg failed to produce the export.") from None
        final = artifact_service.output_path(drama_id, "audio", f"audiobook_{drama_id}.m4b")
        shutil.move(tmp_out, final)
        artifact_service.set_download_language(drama_id, "audio", os.path.basename(final), language)
    background_jobs.update_progress(job_id, 1.0, "Audiobook ready.")


def start_audiobook_export(drama_id: int) -> dict:
    """Starts the Export stage's "Generate audiobook (.m4b)" as thread job
    `audiobook_<drama_id>` (narration track -> AAC m4b with chapter markers,
    output kind "audio"). Raises NotFoundError (unknown drama),
    InvalidInputError (no lines / no narration track yet),
    DependencyUnavailableError (ffmpeg missing), ConflictError (already
    running). Returns {"job_id": ...}."""
    drama = _get_drama(drama_id)
    lines = db.load_line_objects(drama_id)
    if not lines:
        raise InvalidInputError("This drama has no lines to export.")
    ddir = db.drama_dir(drama_id)
    if not os.path.isfile(os.path.join(ddir, "narration_track.wav")):
        raise InvalidInputError("There is no narration yet. Create it in Dub first.")
    _require_ffmpeg()
    job_id = f"audiobook_{drama_id}"
    _refuse_duplicate(job_id, "audiobook", drama_id)
    narrate_original = drama.get("narration_language") == "original"
    title = drama.get("title_en") or drama.get("title_zh") or None
    started = background_jobs.start_job(
        job_id, _audiobook_job, job_id, drama_id, lines, ddir, title, narrate_original,
        export_service.narration_language(drama),
        description=f"Audiobook export (drama #{drama_id})")
    if not started:
        raise ConflictError(f"The audiobook export is already running for drama {drama_id}.")
    return {"job_id": job_id}


def _burned_video_job(job_id, drama_id, video_path, ass_text, ext, language):
    background_jobs.update_progress(job_id, 0.1, "Rendering video...")
    with _fixed_write_errors(), tempfile.TemporaryDirectory() as tmp:
        # A fixed, plain subtitle filename in the working folder means the
        # filter string needs no path escaping and holds nothing client-supplied.
        with open(os.path.join(tmp, "subs.ass"), "w", encoding="utf-8") as f:
            f.write(ass_text)
        out_name = f"out{ext}"
        cmd = ["ffmpeg", "-y", *video_export.local_input(), "-i", video_path,
               "-vf", "subtitles=subs.ass",
               "-c:a", "copy", out_name]
        _run_video_ffmpeg(job_id, cmd, tmp)
        final = artifact_service.output_path(drama_id, "video", f"burned_video_{drama_id}{ext}")
        shutil.move(os.path.join(tmp, out_name), final)
        artifact_service.set_download_language(drama_id, "video", os.path.basename(final), language)
    background_jobs.update_progress(job_id, 1.0, "Video ready.")


def start_burned_video_export(drama_id: int, **ass_options) -> dict:
    """Starts the Export stage's hardsub "Generate subtitled episode" (ASS,
    burned in with libass) as thread job `burned_video_<drama_id>`, output
    kind "video". ass_options are export_service.generate_ass_text's
    keyword arguments (field, style, preset, speaker_colors, ...), validated
    there. Raises NotFoundError (unknown drama), InvalidInputError (no
    lines, no source video, bad style), DependencyUnavailableError (ffmpeg
    missing), ConflictError (already running). Returns {"job_id": ...}."""
    drama = _get_drama(drama_id)
    if not db.load_line_objects(drama_id):
        raise InvalidInputError("This drama has no lines to export.")
    video_path = _source_video(drama, drama_id)
    ass_text = export_service.generate_ass_text(drama_id, **ass_options)
    _require_ffmpeg()
    job_id = f"burned_video_{drama_id}"
    ext = os.path.splitext(video_path)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        ext = ".mp4"
    return _start_video_job(drama_id, job_id, _burned_video_job, job_id, drama_id, video_path,
                            ass_text, ext,
                            export_service.field_language(drama, ass_options.get("field", "en")),
                            description=f"Burned-in video export (drama #{drama_id})")


# ---------------------------------------------------------------------------
# Parity E17: soft subtitle track muxed into the video
# ---------------------------------------------------------------------------

def _softsub_video_job(job_id, drama_id, video_path, srt_text, ext, language, label):
    background_jobs.update_progress(job_id, 0.1, "Adding the subtitle track...")
    with _fixed_write_errors(), tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "subs.srt"), "w", encoding="utf-8") as f:
            f.write(srt_text)
        out_name = f"out{ext}"
        cmd = video_export.mux_soft_subtitles_cmd(video_path, "subs.srt", out_name, language)
        _run_video_ffmpeg(job_id, cmd, tmp)
        final = artifact_service.output_path(drama_id, "softsub_video",
                                             f"softsub_video_{drama_id}{ext}")
        shutil.move(os.path.join(tmp, out_name), final)
        artifact_service.set_download_language(drama_id, "softsub_video", os.path.basename(final), label)
    background_jobs.update_progress(job_id, 1.0, "Video ready.")


def start_softsub_video_export(drama_id: int, field: str = "en",
                               include_notes: bool = False) -> dict:
    """Starts the Export stage's softsub "Generate subtitled episode" as
    thread job `softsub_video_<drama_id>`: the SRT for `field` (en, zh or
    bilingual) is added as a selectable subtitle track, video and audio
    are stream-copied. .mp4/.mkv sources keep their container, anything
    else (.webm, .mov, ...) becomes .mkv (srt). Output kind "softsub_video".
    Raises NotFoundError, InvalidInputError (no lines, no source video,
    bad field), DependencyUnavailableError (ffmpeg missing), ConflictError
    (a video export already running). Returns {"job_id": ...}."""
    drama = _get_drama(drama_id)
    if not db.load_line_objects(drama_id):
        raise InvalidInputError("This drama has no lines to export.")
    video_path = _source_video(drama, drama_id)
    srt_text = export_service.generate_subtitle_text(drama_id, "srt", field,
                                                     include_notes=include_notes)
    _require_ffmpeg()
    job_id = f"softsub_video_{drama_id}"
    ext = video_export.softsub_output_extension(video_path)
    language = "eng" if field == "en" else "und"
    return _start_video_job(drama_id, job_id, _softsub_video_job, job_id, drama_id, video_path,
                            srt_text, ext, language, export_service.field_language(drama, field),
                            description=f"Soft-subtitle video export (drama #{drama_id})")


# ---------------------------------------------------------------------------
# Parity E19: the video with the dub audio
# ---------------------------------------------------------------------------

def _dubbed_video_job(job_id, drama_id, video_path, dub_path, ext, keep_original_at_db, label):
    background_jobs.update_progress(job_id, 0.1, "Rendering dubbed video...")
    with _fixed_write_errors(), tempfile.TemporaryDirectory() as tmp:
        out_name = f"out{ext}"
        cmd = video_export.replace_audio_with_dub_cmd(video_path, dub_path, out_name,
                                                      keep_original_at_db)
        _run_video_ffmpeg(job_id, cmd, tmp)
        final = artifact_service.output_path(drama_id, "dubbed_video",
                                             f"dubbed_video_{drama_id}{ext}")
        shutil.move(os.path.join(tmp, out_name), final)
        artifact_service.set_download_language(drama_id, "dubbed_video", os.path.basename(final), label)
    background_jobs.update_progress(job_id, 1.0, "Video ready.")


def start_dubbed_video_export(drama_id: int, keep_original: bool = False) -> dict:
    """Starts the Export stage's "Export video with dub audio" as thread job
    `dubbed_video_<drama_id>`: the drama's dub track replaces the video's
    audio, or with keep_original the original audio is mixed in quietly
    underneath (-20 dB). Output kind "dubbed_video", same container as the
    source. Raises NotFoundError, InvalidInputError (no source video, no
    dub track yet), DependencyUnavailableError (ffmpeg missing),
    ConflictError (a video export or the drama's dub job already running).
    Returns {"job_id": ...}."""
    drama = _get_drama(drama_id)
    video_path = _source_video(drama, drama_id)
    dub_path = os.path.join(db.drama_dir(drama_id), "dub_track.wav")
    if not os.path.isfile(dub_path):
        raise InvalidInputError("There is no dub yet. Create it in Dub first.")
    if not isinstance(keep_original, bool):
        raise InvalidInputError("keep_original must be true or false.")
    _require_ffmpeg()
    # The dub job rewrites dub_track.wav in place, so wait for it to finish.
    dub_job = background_jobs.get_status(f"dub_{drama_id}")
    if dub_job and dub_job["status"] in ("running", "queued"):
        raise ConflictError("The dub is still being generated; export the video when it finishes.")
    job_id = f"dubbed_video_{drama_id}"
    ext = os.path.splitext(video_path)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        ext = ".mp4"
    return _start_video_job(drama_id, job_id, _dubbed_video_job, job_id, drama_id, video_path,
                            dub_path, ext, _DUB_ORIGINAL_DB if keep_original else None,
                            export_service.narration_language(drama),
                            description=f"Dubbed video export (drama #{drama_id})")
