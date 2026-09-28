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
"""
import os
import shutil
import subprocess
import tempfile

import background_jobs
import db
import dub
from services import artifact_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError)

_FFMPEG_MISSING = "ffmpeg is not installed or not on PATH, which this export requires."


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


def _audiobook_job(job_id, drama_id, lines, ddir, title, narrate_original):
    background_jobs.update_progress(job_id, 0.1, "Encoding audiobook...")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_out = os.path.join(tmp, "audiobook.m4b")
        try:
            dub.export_narration_m4b(lines, ddir, title=title, out_path=tmp_out,
                                     narrate_original=narrate_original)
        except (subprocess.CalledProcessError, OSError):
            raise RuntimeError("ffmpeg failed to produce the export.") from None
        final = artifact_service.output_path(drama_id, "audio", f"audiobook_{drama_id}.m4b")
        shutil.move(tmp_out, final)
    background_jobs.update_progress(job_id, 1.0, "Audiobook ready.")


def start_audiobook_export(drama_id: int) -> dict:
    """Starts the Export tab's "Generate audiobook (.m4b)" as thread job
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
        raise InvalidInputError("No narration audio yet -- generate the narration first.")
    _require_ffmpeg()
    job_id = f"audiobook_{drama_id}"
    _refuse_duplicate(job_id, "audiobook", drama_id)
    narrate_original = drama.get("narration_language") == "original"
    title = drama.get("title_en") or drama.get("title_zh") or None
    started = background_jobs.start_job(
        job_id, _audiobook_job, job_id, drama_id, lines, ddir, title, narrate_original,
        description=f"Audiobook export (drama #{drama_id})")
    if not started:
        raise ConflictError(f"The audiobook export is already running for drama {drama_id}.")
    return {"job_id": job_id}
