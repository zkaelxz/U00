"""
services/picture_subtitle_service.py -- reads a picture subtitle track (PGS, DVD) inside a title's own
video into lines: the track is drawn over black, read with the hard-sub OCR, and the cues are written
the way a subtitle file import writes them (history snapshot, undo). UI-free.
"""
import background_jobs
import db
import hardsub_ocr
import media_inspect
import ocr
from jobs import job_launch
from lib.errors import ConflictError, DependencyUnavailableError, InvalidInputError
from services import drama_service, metadata_service, ownership_service, settings_service
from services import subtitle_import_service

JOB_PREFIX = "picsub_"  # in background_jobs.DRAMA_JOB_PREFIXES
# A shorter step finds shorter captions; the hard-sub reader needs two samples to keep a cue.
SAMPLE_INTERVAL_SECONDS = 0.5
_FAILED = {
    "not_subtitle": "That isn't a subtitle track of this file.",
    "text": "That track is text, so import it with the Import button instead.",
    "missing": "Reading picture subtitles needs ffmpeg, which isn't installed or isn't on PATH.",
    "failed": "ffprobe couldn't read that subtitle track.",
}


def _srt(cues: list) -> bytes:
    def stamp(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    blocks = [f"{n}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text'].strip()}\n"
              for n, c in enumerate((c for c in cues if c["text"].strip()), 1)]
    return "\n".join(blocks).encode("utf-8")


def start_read(drama_id: int, track_index: int, *, confirm_replace_lines: bool = False) -> dict:
    """Starts the `picsub_<id>` job. Raises InvalidInputError for a missing media file, a track that
    isn't a picture subtitle or a replace that wasn't confirmed (details.reason), ConflictError
    while another job works on the title."""
    drama = ownership_service.require_drama(drama_id)
    path = metadata_service.stored_media_path(drama_id, drama)
    if path is None:
        raise InvalidInputError("This title has no media file yet.")
    try:
        track = media_inspect.picture_track_info(path, track_index)
    except media_inspect.SubtitleExtractError as e:
        raise InvalidInputError(_FAILED.get(e.reason, _FAILED["failed"])) from None
    existing = len(db.load_line_objects(drama_id))
    if existing and not confirm_replace_lines:
        raise InvalidInputError(
            f"This title already has {existing} line(s); reading the track replaces them. Confirm to "
            "continue (the old lines are saved to history first).",
            details={"reason": "confirm_replace_lines", "existing_line_count": existing})
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this title -- wait for it "
                            "to finish or cancel it first.")
    language = drama.get("source_language") or "zh"
    job_id = f"{JOB_PREFIX}{drama_id}"
    started = job_launch.start_job(
        job_id, _run_job, job_id, drama_id, path, track, language,
        drama.get("hardsub_ocr_backend") or ocr.default_hardsub_backend(language),
        drama.get("chinese_script") or "simplified", settings_service.get("tesseract_cmd") or None,
        gpu_touching=True, description=f"Reading picture subtitles (title #{drama_id})",
        line_fields=db.FULL_SYNC)
    if not started:
        raise ConflictError("The picture subtitles are already being read for this title.")
    return {"job_id": job_id, "drama_id": drama_id}


def _cancel_check(job_id: str):
    def check():
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
    return check


def _run_job(job_id, drama_id, path, track, language, backend, chinese_script, tesseract_cmd):
    info = {}
    try:
        cues = hardsub_ocr.extract_hardsub_subtitles(
            path, language=language, sample_interval=SAMPLE_INTERVAL_SECONDS, ocr_backend=backend,
            chinese_script=chinese_script, tesseract_cmd=tesseract_cmd, job_id=job_id, info=info,
            cancel_check=_cancel_check(job_id), picture_track=track,
            progress_cb=lambda frac: background_jobs.update_progress(
                job_id, min(0.95, frac * 0.95), f"Reading picture subtitles... {frac * 100:.0f}%"))
    except ImportError:
        background_jobs.set_result(job_id, {
            "failed_reason": "dependency_missing",
            "detail": "Reading picture subtitles needs an OCR package that isn't installed yet. "
                      "Open Diagnostics to install one."})
        return
    data = _srt(cues)
    if not data:
        background_jobs.set_result(job_id, {"failed_reason": "no_text"})
        return
    try:
        result = subtitle_import_service.import_subtitle(
            drama_id, data, "picture-subtitles.srt", mode="source", confirm_replace_lines=True,
            own_job_id=job_id)
    except (ConflictError, InvalidInputError, DependencyUnavailableError) as exc:
        background_jobs.set_result(job_id, {"failed_reason": "not_applied", "detail": str(exc)})
        return
    background_jobs.set_result(job_id, {**result, "ocr_backend": info.get("backend"),
                                        "note": info.get("note")})
