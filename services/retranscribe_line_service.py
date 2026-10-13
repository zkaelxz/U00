"""
services/retranscribe_line_service.py -- Review's "Re-transcribe" for one
line: the GPU-queued job that re-runs Whisper on the line's timing window,
the in-memory proposal it leaves, and the compare-and-set apply.

The full-title run is services/transcribe_service.py; this reuses its saved
Whisper settings and automatic prompt.
"""
import functools
import background_jobs
import whisper_models
import db
import sensitivity_preset as presets
import source_change
import storage
from services import settings_service, timing_settings_service, transcribe_pipeline
from services.transcribe_options_service import (
    _DEFAULT_TUNING, stored_hallucination_silence_sec, stored_whisper_size)
from services.transcribe_service import _drama_audio_path, _resolve_initial_prompt
from services.retranscribe_worker import (
    apply_retranscribe_outcome as _apply_retranscribe_outcome,
    retranscribe_timeout_s as _retranscribe_timeout_s,
    retranscribe_worker as _retranscribe_worker)
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, UnsupportedOperationError

# --- Re-transcribe one line (parity audit B1, inventory R23) ----------------
# Review's "Re-transcribe" re-runs Whisper on one line's own timing window,
# shows what it heard, and only on "Use this" replaces that line's source
# text: the job cuts the window, transcribes it
# with the drama's full-transcribe Whisper settings and the same automatic
# prompt, and keeps the proposal in its in-process result WITHOUT writing.
# The line text never goes through GET /api/jobs (only line_id does): it is
# read back raw with get_retranscribe_result (lines.read, like auto-tune's
# "results are only readable here"), and apply_retranscribe_line writes only
# `zh` for that line id when the client's expected base and proposal match
# the raw values held here and the line is unchanged since the job started (a
# compare-and-set, so the user's edits win). Proposals live in memory only:
# after an API restart the user re-transcribes. Local Whisper even when the
# drama's full transcribe uses Groq, so no paid-engine gate.

# Running/queued jobs that replace this drama's lines or also write `zh`, so a
# one-line re-transcription alongside them would be pointless or race them.
_RETRANSCRIBE_BLOCKING_PREFIXES = ("transcribe_", "fixflag_", "resegment_", "narration_")


def retranscribe_line_job_id(drama_id: int) -> str:
    return f"retranscribe_{drama_id}"


def _find_line(drama_id: int, line_id: int):
    return next((ln for ln in db.load_line_objects(drama_id) if ln.id == line_id), None)


def start_retranscribe_line(drama_id: int, line_id: int, initial_prompt: str = "",
                            extra_names: str = "") -> dict:
    """Starts the GPU-queued job that re-transcribes one line's timing window
    and proposes new text for it (nothing is written; see
    apply_retranscribe_line). initial_prompt / extra_names resolve exactly as
    for a full transcribe run (_resolve_initial_prompt). Returns {job_id,
    drama_id, line_id}; poll GET /api/jobs/{job_id} for status (its result
    shows only line_id), then read the proposal with get_retranscribe_result.

    NotFoundError for an unknown drama or a line id that isn't this drama's;
    UnsupportedOperationError when the drama has no audio pipeline or no
    audio, or the line has no timing window; InvalidInputError for a
    non-text prompt; ConflictError while a re-transcription, a full
    transcription, fix-flagged, a re-segment or a narration run is running
    or queued for this drama."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    line = _find_line(drama_id, line_id)
    if line is None:
        raise NotFoundError(f"No line with id {line_id} in title {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(f"Drama {drama_id} has no audio pipeline.")
    audio_path = _drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError(f"No audio available for title {drama_id}.")
    if not float(line.end) > float(line.start):
        raise UnsupportedOperationError("This line has no timing window to re-transcribe.")
    prompt = _resolve_initial_prompt(drama_id, initial_prompt, extra_names)
    for prefix in _RETRANSCRIBE_BLOCKING_PREFIXES:
        other = background_jobs.get_status(f"{prefix}{drama_id}")
        if other and other.get("status") in ("running", "queued"):
            raise ConflictError("Another job is changing this title's lines. "
                                "Try again when it finishes.")
    job_id = retranscribe_line_job_id(drama_id)
    # The worker's slice and temp files go here; removed by on_finish however
    # the run ends, since a killed worker cannot clean up after itself.
    scratch_dir = storage.new_workdir(job_id)
    window = float(line.end) - float(line.start)
    try:
        started = background_jobs.start_process_job(
            job_id, _retranscribe_worker,
            args=(audio_path, float(line.start), float(line.end),
                  line.lang or drama.get("source_language") or "zh",
                  stored_whisper_size(drama),
                  drama.get("beam_size") or _DEFAULT_TUNING["beam_size"],
                  drama.get("min_silence_ms") or _DEFAULT_TUNING["min_silence_ms"],
                  timing_settings_service.vad_threshold_for(drama), settings_service.get("use_gpu"), prompt,
                  stored_hallucination_silence_sec(drama),
                  bool(drama.get("whisper_repeat_guard")),
                  presets.normalize(drama.get("sensitivity_preset")),
                  transcribe_pipeline._model_loading_message(stored_whisper_size(drama), whisper_models.is_whisper_model_cached(
                      stored_whisper_size(drama))),
                  _retranscribe_timeout_s(window), scratch_dir),
            gpu_touching=True, description=f"Re-transcribing a line (title #{drama_id})",
            kill_whole_tree=True, initial_result={"line_id": line_id},
            # Spawn, not Linux's default fork: a forked child of a process
            # that has already initialised CUDA cannot use the GPU.
            start_method="spawn",
            on_done=functools.partial(_apply_retranscribe_outcome, drama_id=drama_id,
                                      line_id=line_id, zh_before=line.zh,
                                      start=float(line.start), end=float(line.end)),
            on_finish=functools.partial(transcribe_pipeline._remove_scratch_dir, scratch_dir))
    except BaseException:
        transcribe_pipeline._remove_scratch_dir(scratch_dir)
        raise
    if not started:
        transcribe_pipeline._remove_scratch_dir(scratch_dir)
        raise ConflictError("A line is already being re-transcribed for this title.")
    return {"job_id": job_id, "drama_id": drama_id, "line_id": line_id}


def _finished_proposal(drama_id: int, line_id: int) -> dict:
    """This drama's finished re-transcription result for this line, as held
    in this process. NotFoundError for an unknown drama or line, or when there
    is none (not run, still running, failed, another line's, or the API
    restarted since)."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    if _find_line(drama_id, line_id) is None:
        raise NotFoundError(f"No line with id {line_id} in title {drama_id}.")
    job = background_jobs.get_status(retranscribe_line_job_id(drama_id)) or {}
    result = job.get("result") if job.get("status") == "done" else None
    if (not isinstance(result, dict) or result.get("line_id") != line_id
            or not result.get("proposed_zh")):
        raise NotFoundError("No finished re-transcription for this line.")
    return result


def get_retranscribe_result(drama_id: int, line_id: int) -> dict:
    """{job_id, line_id, status: "done", proposed_zh, base_zh}: the finished
    proposal for this line, raw (the same line text lines.read already
    returns). NotFoundError as _finished_proposal."""
    result = _finished_proposal(drama_id, line_id)
    return {"job_id": retranscribe_line_job_id(drama_id), "line_id": line_id, "status": "done",
            "proposed_zh": result["proposed_zh"], "base_zh": result.get("base_zh") or ""}


def apply_retranscribe_line(drama_id: int, line_id: int, job_id, expected_zh,
                            expected_proposed) -> dict:
    """Writes a finished re-transcription's
    proposed_zh to that line's `zh`, keeping its English (a translated line
    whose text changes is flagged `source_changed`). expected_zh and
    expected_proposed must equal the raw base_zh and proposed_zh held for this
    run (what get_retranscribe_result showed), so what was shown is exactly
    what is written and an apply is tied to the run the user saw. The write
    is ONE compare-and-set (db.update_line_fields_if) on the line's zh, start
    and end as they were when the job started, so a line edited or re-timed
    since then is left alone (ConflictError). The old text is kept first as a
    line-history snapshot (restorable from History). Returns {drama_id,
    line_id, zh}.

    InvalidInputError for non-text input or a job_id that isn't this drama's
    re-transcription; NotFoundError as _finished_proposal; ConflictError when
    the expected values don't match this run or the line changed since the
    job started."""
    if (not isinstance(job_id, str) or not isinstance(expected_zh, str)
            or not isinstance(expected_proposed, str)):
        raise InvalidInputError("job_id, expected_zh and expected_proposed must be text.")
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    if job_id != retranscribe_line_job_id(drama_id):
        raise InvalidInputError("job_id is not this title's re-transcription.")
    result = _finished_proposal(drama_id, line_id)
    base = result.get("base_zh") or ""
    proposed = result["proposed_zh"]
    if expected_zh != base or expected_proposed != proposed:
        raise ConflictError("This isn't the re-transcription you were shown. Run it again.")
    line = _find_line(drama_id, line_id)
    unchanged = {"zh": base, "start": result["base_start"], "end": result["base_end"]}
    if line is None or (line.zh or "", float(line.start), float(line.end)) != (
            base, unchanged["start"], unchanged["end"]):
        raise ConflictError("This line changed since it was re-transcribed; your edit was kept.")
    db.save_line_history_snapshot(drama_id, db.load_line_objects(drama_id),
                                  f"before re-transcribing line {line.idx + 1}")
    values = {"zh": proposed, **source_change.flag_values(base, proposed, line.en)}
    # The flag was decided from this English, so it must still be there.
    if not db.update_line_fields_if(drama_id, line_id, values, {**unchanged, "en": line.en or ""}):
        raise ConflictError("This line changed since it was re-transcribed; your edit was kept.")
    return {"drama_id": drama_id, "line_id": line_id, "zh": proposed}
