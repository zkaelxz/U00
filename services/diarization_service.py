"""
services/diarization_service.py -- Diarize-stage services for one drama,
used by the /api/diarization routes (api/routers/diarization_routes.py).

This re-detects speakers from the drama's already-stored audio -- it never
touches transcript text or timing (see diarize.merge_speakers's own
docstring).

No FastAPI import: plain functions, plain dicts in, plain
values out, so a CLI or another service could call them too. The HF
token itself is never returned (D2 -- server-side keys are never
exposed over an API), only whether one is configured.
"""
import os
from typing import Optional

import background_jobs
import db
import diarize
from services import drama_service, settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                      NotFoundError,
                                      UnsupportedOperationError)


def _drama_audio_path(drama_id: int, drama: dict) -> Optional[str]:
    """The on-disk path to this drama's stored audio, or None if there's
    no audio_filename set or the file isn't actually there."""
    audio_filename = drama.get("audio_filename")
    if not audio_filename:
        return None
    path = os.path.join(db.drama_dir(drama_id), audio_filename)
    return path if os.path.exists(path) else None


def speaker_time_summary(drama_id: int) -> Optional[dict]:
    """Share of speech per speaker from the saved diarization turns, or None
    when none are saved. Per speaker: label, seconds, percent (of the speakers'
    summed seconds, so overlapping speech counts for each speaker and the
    percents add to 100) and turns, biggest first. uncovered_seconds is the
    drama's audio not inside any turn (overlaps merged), None if the audio
    length is unknown. Raises NotFoundError for an unknown drama."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    turns = diarize.load_turns(db.drama_dir(drama_id))
    if not turns:
        return None
    per, spans = {}, []
    for t in turns:
        try:
            start, end = float(t["start"]), float(t["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        entry = per.setdefault(str(t.get("speaker") or "?"), {"seconds": 0.0, "turns": 0})
        entry["seconds"] += end - start
        entry["turns"] += 1
        spans.append((start, end))
    if not per:
        return None
    total = sum(e["seconds"] for e in per.values())
    covered, cur_s, cur_e = 0.0, None, None
    for start, end in sorted(spans):
        if cur_e is None or start > cur_e:
            if cur_e is not None:
                covered += cur_e - cur_s
            cur_s, cur_e = start, end
        else:
            cur_e = max(cur_e, end)
    covered += cur_e - cur_s
    uncovered = None
    audio_path = _drama_audio_path(drama_id, drama)
    if audio_path:
        from services import transcribe_service  # imports this module, so not at the top
        duration = transcribe_service._audio_duration_seconds(audio_path)
        if duration:
            uncovered = round(max(0.0, duration - covered), 1)
    speakers = [{"label": k, "seconds": round(v["seconds"], 1),
                 "percent": round(100 * v["seconds"] / total, 1), "turns": v["turns"]}
                for k, v in sorted(per.items(), key=lambda kv: -kv[1]["seconds"])]
    return {"speakers": speakers, "total_speech_seconds": round(total, 1),
            "uncovered_seconds": uncovered}


def get_diarization_config(drama_id: int) -> dict:
    """Read-only Diarize-stage summary for one drama: whether an HF token
    is configured, the expected-speaker-count default from the last real
    detection run (diarize.load_last_speaker_count), and whether audio is
    actually available to diarize. Raises NotFoundError for an unknown
    drama id."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    ddir = db.drama_dir(drama_id)
    audio_path = _drama_audio_path(drama_id, drama)
    last_run = diarize.load_last_run_info(ddir)

    return {
        "drama_id": drama_id,
        "hf_token_configured": bool(settings_service.resolve_key("hf_token")),
        "expected_speakers": diarize.load_last_speaker_count(ddir),
        "min_speakers": last_run["min_speakers"],
        "max_speakers": last_run["max_speakers"],
        # Step 101: the device the last run's pyannote pipeline actually
        # ran on ("cuda"/"cpu"), None before any run that recorded it.
        "last_device": last_run["device"],
        "audio_available": audio_path is not None,
        # Parity D06: lines whose speaker was corrected by hand, so the
        # client can ask before a run with overwrite_manual replaces them.
        "manual_speaker_count": sum(1 for r in db.load_lines(drama_id)
                                    if r.get("speaker_manual")),
        "speaker_summary": speaker_time_summary(drama_id),
    }


def apply_diarization_result(drama_id: int, result: dict,
                             expected_speakers: Optional[int] = None,
                             overwrite_manual: bool = False,
                             min_speakers: Optional[int] = None,
                             max_speakers: Optional[int] = None) -> None:
    """The process job's on_done hook, so a diarization run persists its
    own result: saves the turns (+model,
    embeddings, the count the job was started with), re-merges speakers
    onto the saved lines (speaker_manual lines are kept), upserts a
    character row per label, and writes ONLY the speaker/speaker_manual
    fields -- never a full sync.

    There is no user to ask when the job finishes, so the merge runs with
    overwrite_manual=False by default -- manual corrections are never undone unless the caller
    started the run with an explicit, confirmed overwrite_manual=True
    (see start_diarization_run).

    Applying the same result twice is harmless: save_turns overwrites
    the same file, and merge_speakers over the same turns is idempotent
    (a second pass changes nothing; manual lines stay manual)."""
    turns = (result or {}).get("segments")
    if turns is None:
        return
    diarize.save_turns(db.drama_dir(drama_id), turns, num_speakers=expected_speakers or None,
                       model=result.get("model"), embeddings=result.get("embeddings", {}),
                       min_speakers=min_speakers or None, max_speakers=max_speakers or None,
                       device=result.get("device"))
    lines = db.load_line_objects(drama_id)
    diarize.merge_speakers(lines, turns, overwrite_manual=overwrite_manual)
    for label in sorted({ln.speaker for ln in lines if ln.speaker}):
        db.upsert_character(drama_id, label)
    db.save_lines(drama_id, lines, fields=("speaker", "speaker_manual"))


def reassign_speakers_from_saved_turns(drama_id: int) -> dict:
    """Relabels the drama's lines from the diarization turns already on disk,
    without running detection again. speaker_manual lines are left alone and
    only speaker/speaker_manual are written. Raises NotFoundError for an
    unknown drama, ConflictError if no turns were saved yet or a job is running
    for the drama. The saved turns are tied to whatever audio detection last
    ran on, so this is only ever done on request, never after a transcription."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this drama -- wait for it "
                            "to finish or cancel it before re-assigning speakers.")
    return relabel_from_saved_turns(drama_id)


def relabel_from_saved_turns(drama_id: int, only_ids=None) -> dict:
    """reassign_speakers_from_saved_turns without the busy check, for a caller
    that already holds the drama (the re-split action, whose own job is running).
    only_ids: relabel just these lines; every other line keeps its speaker."""
    turns = diarize.load_turns(db.drama_dir(drama_id))
    if turns is None:
        raise ConflictError("No saved speaker detection for this drama; run Detect speakers first.")
    lines = db.load_line_objects(drama_id)
    if only_ids is not None:
        keep = set(only_ids)
        lines = [ln for ln in lines if ln.id in keep]
    counts = diarize.merge_speakers(lines, turns)
    for label in sorted({ln.speaker for ln in lines if ln.speaker}):
        db.upsert_character(drama_id, label)
    # Only where the database still holds what was loaded: a speaker the user
    # set while this ran is kept.
    db.save_lines(drama_id, lines, fields=("speaker", "speaker_manual"), only_if_unchanged=True)
    return counts


def _record_run_speed(drama_id: int, result) -> None:
    """Feeds the run's duration to the speaker-detection estimate. Never
    raises: a finished detection must not fail over its own bookkeeping."""
    try:
        seconds = (result or {}).get("seconds")
        drama = db.get_drama(drama_id)
        audio_path = _drama_audio_path(drama_id, drama) if drama else None
        if seconds is None or audio_path is None:
            return
        from services import transcribe_service  # imports this module, so not at the top
        transcribe_service.record_diarize_speed(
            result.get("device") == "cuda", transcribe_service._audio_duration_seconds(audio_path),
            seconds)
    except Exception:
        pass


def make_apply_on_done(drama_id: int, expected_speakers: Optional[int] = None,
                       overwrite_manual: bool = False, min_speakers: Optional[int] = None,
                       max_speakers: Optional[int] = None):
    """The on_done hook for a diarize_<drama_id> process job."""
    def _on_done(job_id, result):
        fell_back = bool((result or {}).get("fell_back_to_cpu"))
        notice = diarize.fallback_done_message((result or {}).get("fallback_kind"))
        background_jobs.update_progress(
            job_id, 0.97, (notice + " " if fell_back else "") + "Matching speakers to lines...")
        apply_diarization_result(drama_id, result, expected_speakers, overwrite_manual,
                                 min_speakers=min_speakers, max_speakers=max_speakers)
        _record_run_speed(drama_id, result)
        if fell_back:
            # Replaces the stored result so the finished job still says it.
            return {"device": "cpu", "gpu_fallback": notice, "device_notice": notice}
    return _on_done


def worker_options(min_speakers: Optional[int] = None,
                   max_speakers: Optional[int] = None) -> dict:
    """The options dict diarize.diarize_subprocess_worker takes: the
    persisted use_gpu setting (Step 101) and the speaker range (Step 105)."""
    return {"use_gpu": settings_service.get_use_gpu(),
            "min_speakers": min_speakers or None, "max_speakers": max_speakers or None}


def start_diarization_run(drama_id: int, expected_speakers: Optional[int] = None,
                          overwrite_manual: bool = False, confirm: bool = False,
                          min_speakers: Optional[int] = None,
                          max_speakers: Optional[int] = None) -> dict:
    """Starts a real background job to re-detect speakers from this
    drama's stored audio. The transcript text/timing are never touched;
    the on_done hook (apply_diarization_result) merges the resulting turns
    back onto the lines' speakers. Raises
    NotFoundError for an unknown drama id or if no audio is available,
    DependencyUnavailableError if no Hugging Face token is configured,
    ConflictError if a diarization job is already running for this drama.
    overwrite_manual=True lets the result replace hand-corrected speakers
    (destructive), so it needs confirm=True too, else InvalidInputError
    (HTTP 422). Default False keeps manual speakers.
    min_speakers/max_speakers (Step 105): an optional speaker-count range
    for pyannote; InvalidInputError if min > max, either is below 1, or it
    is combined with an exact expected_speakers.
    Returns {"job_id": ...} -- poll it via the existing GET /api/jobs/
    {job_id}."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    if overwrite_manual and confirm is not True:
        raise InvalidInputError("overwrite_manual=true replaces speakers you corrected by hand "
                                "and needs confirm=true as well.")
    try:
        expected_speakers, min_speakers, max_speakers = diarize.validate_speaker_hints(
            expected_speakers, min_speakers, max_speakers)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc

    hf_token = settings_service.resolve_key("hf_token")
    if not hf_token:
        raise DependencyUnavailableError(
            "No Hugging Face token is configured. Set one in Settings first.")

    audio_path = _drama_audio_path(drama_id, drama)
    if audio_path is None:
        # The drama itself exists (checked above) -- it just has no audio
        # yet, which is a well-formed request this record can't currently
        # satisfy (HTTP 400), not "the drama doesn't exist" (404).
        raise UnsupportedOperationError(f"No audio available for drama {drama_id}.")

    job_id = f"diarize_{drama_id}"
    # start_process_job returns False without starting anything if this job
    # id is already running/queued: report it instead of silently no-opping.
    started = background_jobs.start_process_job(
        job_id, diarize.diarize_subprocess_worker,
        args=(audio_path, hf_token, expected_speakers or None,
              worker_options(min_speakers, max_speakers)),
        gpu_touching=True, description=f"Diarization (drama #{drama_id})",
        on_done=make_apply_on_done(drama_id, expected_speakers, overwrite_manual,
                                   min_speakers, max_speakers))
    if not started:
        raise ConflictError(f"A diarization job is already running for drama {drama_id}.")
    return {"job_id": job_id}


def diarization_estimate_caption(audio_duration_seconds):
    """pyannote's pipeline makes one call and only returns a result at the
    end -- no incremental progress callback exists in its public API, so
    unlike Whisper's segment-by-segment real progress bar, this is the best
    honest estimate available: diarization runtime scales roughly linearly
    with audio length, so a range scaled off the audio's own length (rather
    than a fixed number that ignores it) is truthful without pretending to
    more precision than a single spinner can back up."""
    if not audio_duration_seconds or audio_duration_seconds <= 0:
        return "Usually takes anywhere from under a minute to a few minutes, depending on audio length and hardware."

    def _mmss(seconds):
        m, s = divmod(int(round(seconds)), 60)
        return f"{m}:{s:02d}"

    return (f"For audio this long (~{_mmss(audio_duration_seconds)}), usually takes roughly "
            f"{_mmss(audio_duration_seconds)}–{_mmss(audio_duration_seconds * 2)}, depending on "
            f"your hardware -- there's no incremental progress to show here (pyannote's pipeline "
            f"doesn't expose one), just this spinner until it finishes.")
