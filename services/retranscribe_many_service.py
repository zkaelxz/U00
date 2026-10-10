"""
services/retranscribe_many_service.py -- Review's "Re-transcribe selected": one
cancellable process job re-hears the ticked lines' own timing windows in order
(one model load, Fast mode off, the title's full-transcribe Whisper settings,
exactly as the one-line job) and proposes new source text for each. Also what
"Transcribe this gap" runs on the lines it just added.

It shares the one-line job's id (`retranscribe_<drama_id>`), so the two exclude
each other, Cancel and the GPU slot work the same, and jobs_service already
files it as a transcribe job. Its result tells them apart: a many-line result
holds `proposals`, a one-line one `line_id`.

The job writes nothing. Proposals live in its in-process result: GET /api/jobs
shows only counts (jobs_service's allow-list), and the text is read back with
get_retranscribe_many_result. apply_retranscribe_many writes `zh` and clears
`en` for the chosen lines only, each as a compare-and-set against the line as
the run read it, after a history snapshot; the translation is cleared because
it describes the old source text, and a title with a blank English is what
Translate counts as "left".
"""
import functools

import background_jobs
import bulk_translate
import core as core_module
import whisper_models
import db
import sensitivity_preset as presets
import storage
from services import (compare_transcription_service as compare, settings_service,
                      transcribe_pipeline, transcribe_service, translate_service)
from services.retranscribe_worker import (apply_retranscribe_many_outcome,
                                          retranscribe_many_worker, retranscribe_timeout_s)
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)

MAX_LINES = compare.MAX_LINES
_SNAPSHOT_LABEL = "before re-transcribing lines"


def _job_id(drama_id: int) -> str:
    return transcribe_service.retranscribe_line_job_id(drama_id)


def start_retranscribe_many(drama_id: int, line_ids: list, initial_prompt: str = "",
                            extra_names: str = "") -> dict:
    """Starts the GPU-queued job over these lines. Returns {job_id, drama_id,
    line_count}; poll GET /api/jobs/{job_id} (it shows counts only), then read
    the proposals with get_retranscribe_many_result.

    NotFoundError for an unknown drama or any line id that isn't this
    drama's; UnsupportedOperationError with no audio pipeline, no stored audio
    or a line without a timing window; InvalidInputError for a bad or over-cap
    selection or a non-text prompt; ConflictError while a re-transcription
    (one line or many), a full transcription, fix-flagged, a re-segment or a
    narration run is running or queued for this drama."""
    drama = compare._drama_or_404(drama_id)
    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(f"Drama {drama_id} has no audio pipeline.")
    audio_path = transcribe_service._drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError(f"No audio available for title {drama_id}.")
    if (not isinstance(line_ids, list) or not line_ids or len(line_ids) > MAX_LINES
            or any(isinstance(i, bool) or not isinstance(i, int) for i in line_ids)):
        raise InvalidInputError(f"Choose between 1 and {MAX_LINES} lines.")
    lines = db.load_line_objects(drama_id)
    wanted = set(line_ids)
    picked = [ln for ln in lines if ln.id in wanted]
    if len(picked) != len(wanted):
        raise NotFoundError("Some of those lines are not in this title any more.")
    if any(not float(ln.end) > float(ln.start) for ln in picked):
        raise UnsupportedOperationError("A chosen line has no timing window to re-transcribe.")
    prompt = transcribe_service._resolve_initial_prompt(drama_id, initial_prompt, extra_names)
    for prefix in transcribe_service._RETRANSCRIBE_BLOCKING_PREFIXES:
        other = background_jobs.get_status(f"{prefix}{drama_id}")
        if other and other.get("status") in ("running", "queued"):
            raise ConflictError("Another job is changing this title's lines. "
                                "Try again when it finishes.")
    job_id = _job_id(drama_id)
    default_language = drama.get("source_language") or "zh"
    windows = [(ln.id, float(ln.start), float(ln.end), ln.lang or default_language)
               for ln in picked]
    bases = {ln.id: {"zh": ln.zh or "", "en": ln.en or "", "start": float(ln.start),
                     "end": float(ln.end), "number": ln.idx + 1} for ln in picked}
    size = transcribe_service.stored_whisper_size(drama)
    tuning = transcribe_service._DEFAULT_TUNING
    scratch_dir = storage.new_workdir(job_id)
    try:
        started = background_jobs.start_process_job(
            job_id, retranscribe_many_worker,
            args=(audio_path, windows, size,
                  drama.get("beam_size") or tuning["beam_size"],
                  drama.get("min_silence_ms") or tuning["min_silence_ms"],
                  presets.stored_vad_threshold(drama), settings_service.get_use_gpu(), prompt,
                  transcribe_service.stored_hallucination_silence_sec(drama),
                  bool(drama.get("whisper_repeat_guard")),
                  presets.normalize(drama.get("sensitivity_preset")),
                  transcribe_pipeline._model_loading_message(
                      size, whisper_models.is_whisper_model_cached(size)),
                  retranscribe_timeout_s(sum(w[2] - w[1] for w in windows)), scratch_dir),
            gpu_touching=True,
            description=f"Re-transcribing {len(windows)} lines (title #{drama_id})",
            kill_whole_tree=True, initial_result={"line_count": len(windows)},
            start_method="spawn",
            on_done=functools.partial(apply_retranscribe_many_outcome, drama_id=drama_id,
                                      bases=bases),
            on_finish=functools.partial(transcribe_pipeline._remove_scratch_dir, scratch_dir))
    except BaseException:
        transcribe_pipeline._remove_scratch_dir(scratch_dir)
        raise
    if not started:
        transcribe_pipeline._remove_scratch_dir(scratch_dir)
        raise ConflictError("A re-transcription is already running for this title.")
    return {"job_id": job_id, "drama_id": drama_id, "line_count": len(windows)}


def _finished_run(drama_id: int) -> dict:
    compare._drama_or_404(drama_id)
    job = background_jobs.get_status(_job_id(drama_id)) or {}
    result = job.get("result") if job.get("status") == "done" else None
    if not isinstance(result, dict) or not isinstance(result.get("proposals"), list):
        raise NotFoundError("No finished re-transcription of several lines for this title.")
    return job


def get_retranscribe_many_result(drama_id: int) -> dict:
    """The finished run's proposals, raw (the same line text lines.read
    returns), for lines still in the drama, plus the lines that heard nothing
    ({line_id, reason}). A proposal says whether applying it would clear an
    English line (`had_english`) without carrying that text. NotFoundError when
    there is none (not run, running, failed, one-line run, or the API restarted
    since)."""
    result = _finished_run(drama_id)["result"]
    return {
        "job_id": _job_id(drama_id), "line_count": result.get("line_count", 0),
        "proposals": [{"line_id": p["line_id"], "number": p["number"],
                       "base_zh": p["base_zh"], "proposed_zh": p["proposed_zh"],
                       "had_english": bool(p["base_en"].strip())}
                      for p in result["proposals"]],
        "failures": result.get("failures") or [],
        "unchanged_count": result.get("skipped_count", 0),
        "truncated": bool(result.get("truncated")),
        "device_notice": result.get("device_notice"),
    }


def apply_retranscribe_many(drama_id: int, job_id, items) -> dict:
    """Writes the chosen proposals. Each item is {line_id, expected_zh,
    expected_proposed}: it must equal what this run proposed (what
    get_retranscribe_many_result showed), and the line must still hold its
    zh, en, start and end from when the run read it -- one compare-and-set per
    line inside one transaction -- so a line edited, re-timed or translated
    since is skipped and reported, never overwritten. Writes `zh` and clears
    `en` on the applied lines only (speaker, flag and the rest stay), then
    reopens a translated title so Translate counts them. A history snapshot is
    taken first when at least one item still matches, unless this run's
    earlier apply already took the latest one. Returns {applied: [line_id],
    skipped: [line_id], untranslated_count}.

    InvalidInputError for malformed items or another drama's job id;
    NotFoundError with no finished run; ConflictError when an item isn't a
    proposal of this run (nothing written)."""
    if not isinstance(job_id, str) or job_id != _job_id(drama_id):
        raise InvalidInputError("job_id is not this title's re-transcription.")
    if not isinstance(items, list) or not items or len(items) > MAX_LINES:
        raise InvalidInputError(f"Choose between 1 and {MAX_LINES} lines to apply.")
    job = _finished_run(drama_id)
    by_id = {p["line_id"]: p for p in job["result"]["proposals"]}
    writes, order = [], []
    for item in items:
        line_id = item.get("line_id") if isinstance(item, dict) else None
        if line_id in order:
            raise InvalidInputError("Each line can be applied once.")
        p = by_id.get(line_id)
        if (p is None or item.get("expected_zh") != p["base_zh"]
                or item.get("expected_proposed") != p["proposed_zh"]):
            raise ConflictError("These aren't the proposals you were shown. "
                                "Re-transcribe again.")
        order.append(line_id)
        writes.append((line_id, {"zh": p["proposed_zh"], "en": ""},
                       {"zh": p["base_zh"], "en": p["base_en"],
                        "start": p["base_start"], "end": p["base_end"]}))
    with compare._apply_lock:
        lines = db.load_line_objects(drama_id)
        current = {ln.id: ln for ln in lines}
        if any(compare._still_matches(current.get(lid), expected)
               for lid, _values, expected in writes):
            compare._snapshot_once_per_run(
                drama_id, (job_id, job.get("finished_at")), lines, _SNAPSHOT_LABEL)
        skipped = set(db.update_lines_fields_if_many(drama_id, writes))
    applied = [lid for lid in order if lid not in skipped]
    if applied:
        translate_service.sync_translation_status(drama_id)
    return {"applied": applied, "skipped": [lid for lid in order if lid in skipped],
            "untranslated_count": bulk_translate.untranslated_line_count(drama_id)}
