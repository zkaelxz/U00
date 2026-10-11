"""
services/retime_service.py -- "Re-time with Qwen3 aligner" in Review: keep the
words of lines the title already has and let the forced aligner propose new
start/end times for them, without re-running ASR.

The job (`retime_<drama_id>`) writes nothing to the lines. Proposals live in
its in-process result (GET /api/jobs shows only counts) and are read back with
get_retime_result. apply_retime writes only `start` and `end`, per line as a
compare-and-set against the text and times the aligner was given, after a
history snapshot, so undo is the existing history restore.
"""
import subprocess

import background_jobs
import core as core_module
import whisper_models
import db
import forced_align
from services import (compare_transcription_service as compare, jobs_service,
                      settings_service, transcribe_pipeline, transcribe_service)
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)

MAX_LINES = compare.MAX_LINES
# Late starts mean the speech begins before the stored start, so the audio
# window reaches back (and forward) this far, never into a neighbouring line.
_PAD_S = 3.0
_BLOCKING_PREFIXES = transcribe_service._RETRANSCRIBE_BLOCKING_PREFIXES + ("comparetx_",)
_MOVED_EPSILON_S = 0.01
_MIN_LINE_S = 0.1
_SNAPSHOT_LABEL = "before re-time apply"


def retime_job_id(drama_id: int) -> str:
    return f"retime_{drama_id}"


def start_retime(drama_id: int, line_ids: list) -> dict:
    """Starts the GPU-queued re-time job for the ticked lines. Returns
    {job_id, drama_id, line_count}; poll GET /api/jobs/{job_id}, then read the
    proposals with get_retime_result.

    NotFoundError for an unknown drama; UnsupportedOperationError with no audio
    pipeline or stored audio; InvalidInputError for a bad or over-cap
    selection; DependencyUnavailableError when qwen-asr/torch are missing;
    ConflictError while a re-time run is active, or while a transcription,
    fix-flagged, re-segment, narration or compare run is running or queued."""
    drama = compare._drama_or_404(drama_id)
    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(f"Drama {drama_id} has no audio pipeline.")
    audio_path = transcribe_service._drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError("This title has no stored audio to re-time against.")
    picked = compare.select_lines(drama_id, {"kind": "line_ids", "line_ids": line_ids})
    transcribe_service.require_qwen3_packages("The Qwen3 forced aligner")
    for prefix in _BLOCKING_PREFIXES:
        other = background_jobs.get_status(f"{prefix}{drama_id}")
        if other and other.get("status") in ("running", "queued"):
            raise ConflictError("Another job is changing this drama's lines. "
                                "Try again when it finishes.")
    job_id = retime_job_id(drama_id)
    started = background_jobs.start_job(
        job_id, run_retime_job, job_id, drama_id, [ln.id for ln in picked], audio_path,
        drama.get("source_language") or "zh", settings_service.get("use_gpu"),
        gpu_touching=True,
        description=f"Re-timing {len(picked)} line(s) with the Qwen3 aligner (drama #{drama_id})")
    if not started:
        raise ConflictError("A re-time run is already active for this title.")
    return {"job_id": job_id, "drama_id": drama_id, "line_count": len(picked)}


def _groups(lines, wanted: set, default_language: str, errors: list) -> list:
    """[(language, [index into lines])] -- runs of neighbouring selected lines
    in one language, each short enough for one aligner call. A line the aligner
    can't take is reported and left out, never failing the run."""
    groups = []
    for i, ln in enumerate(lines):
        if ln.id not in wanted:
            continue
        language = ln.lang or default_language
        if language not in forced_align.ALIGNER_LANGUAGE_NAMES:
            errors.append(f"line {ln.idx + 1}: the aligner doesn't cover this line's "
                          f"language ({language}), skipped")
            continue
        if not (ln.zh or "").strip():
            errors.append(f"line {ln.idx + 1}: no text to align, skipped")
            continue
        if float(ln.end) - float(ln.start) > forced_align.MAX_CHUNK_SECONDS:
            # A group always starts with its first line whatever its length,
            # which would hand the aligner a slice past forced_align's own cap.
            errors.append(f"line {ln.idx + 1}: too long to re-time in one go, skipped")
            continue
        if groups:
            last_language, members = groups[-1]
            if (last_language == language and members[-1] == i - 1
                    and float(ln.end) - float(lines[members[0]].start)
                    <= forced_align.MAX_CHUNK_SECONDS):
                members.append(i)
                continue
        groups.append((language, [i]))
    return groups


def _segments(lines, members: list) -> list:
    """The group's lines as aligner input, the outer edges widened into the gap
    next to the neighbouring (unselected or other-group) lines."""
    first, last = lines[members[0]], lines[members[-1]]
    floor = float(lines[members[0] - 1].end) if members[0] > 0 else 0.0
    ceiling = (float(lines[members[-1] + 1].start) if members[-1] + 1 < len(lines)
               else float(last.end) + _PAD_S)
    window_start = min(float(first.start), max(floor, float(first.start) - _PAD_S))
    window_end = max(float(last.end), min(ceiling, float(last.end) + _PAD_S))
    segments = [{"start": float(lines[i].start), "end": float(lines[i].end), "text": lines[i].zh}
                for i in members]
    segments[0]["start"], segments[-1]["end"] = window_start, window_end
    return segments


def _make_consistent(proposals: list, lines, media_duration) -> list:
    """Repeats the clamp pass until no proposal is dropped: a dropped (unmoved)
    neighbour goes back to its stored times, which can leave the proposal
    before it overlapping that stored start, a row apply would always refuse."""
    while True:
        kept = _clamp_pass(proposals, lines, media_duration)
        if len(kept) == len(proposals):
            return kept
        proposals = kept


def _clamp_pass(proposals: list, lines, media_duration) -> list:
    """Clamps each proposal against its neighbours (proposed times where the
    neighbour has a proposal, stored times otherwise) and the media end, so
    applying any subset of a run's proposals can't start with overlaps the run
    itself created. A proposal that can't be made consistent is kept as
    proposed but marked uncertain; apply re-checks it against live times."""
    position = {ln.id: i for i, ln in enumerate(lines)}
    by_index = {position[p["line_id"]]: p for p in proposals}

    def times(i, key_new, key_old):
        p = by_index.get(i)
        return float(p[key_new] if p else getattr(lines[i], key_old))

    kept = []
    for i in sorted(by_index):
        p = by_index[i]
        start, end = p["new_start"], p["new_end"]
        if i > 0:
            start = max(start, times(i - 1, "new_end", "end"))
        if i + 1 < len(lines):
            end = min(end, times(i + 1, "new_start", "start"))
        if media_duration:
            end = min(end, media_duration)
        if end - start >= _MIN_LINE_S:
            p["new_start"], p["new_end"] = start, end
        else:
            p["uncertain"] = True
        if (abs(p["new_start"] - p["start"]) < _MOVED_EPSILON_S
                and abs(p["new_end"] - p["end"]) < _MOVED_EPSILON_S):
            del by_index[i]
            continue
        kept.append(p)
    return kept


def run_retime_job(job_id, drama_id, line_ids, audio_path, language, use_gpu):
    """Job body. Aligns each group of neighbouring ticked lines against its own
    audio window. Cancel ends the run cleanly, keeping the proposals so far.
    Result: {proposals, line_count, candidate_count, errors, partial, device,
    ...}; GET /api/jobs shows only the counts."""
    lines = db.load_line_objects(drama_id)
    errors, proposals, fallback, device = [], [], [], {}
    progress = [0.0]
    groups = _groups(lines, set(line_ids), language, errors)
    cancelled, failed_reason, detail = False, None, None
    model_loaded = [False]

    def on_device(label):
        model_loaded[0] = True
        device["label"] = label
        background_jobs.update_progress(job_id, progress[0], f"Aligning on {label}...")

    try:
        for n, (group_language, members) in enumerate(groups):
            if background_jobs.is_cancel_requested(job_id):
                cancelled = True
                break
            progress[0] = n / max(len(groups), 1)
            background_jobs.update_progress(job_id, progress[0], f"Group {n + 1} of {len(groups)}")
            try:
                out = forced_align.refine_segment_timing(
                    audio_path, [_segments(lines, members)], group_language,
                    # A failed GPU load would otherwise be retried for every group.
                    use_gpu=use_gpu and not fallback, on_device=on_device,
                    on_gpu_fallback=lambda exc: fallback.append(whisper_models.short_reason(exc)),
                    cancel_check=lambda: compare._cancel_check(job_id))
            except background_jobs.JobCancelled:
                cancelled = True
                break
            except whisper_models.ModelDownloadError as exc:
                failed_reason, detail = "model_download", jobs_service.scrub_text(str(exc))
                break
            except ImportError:
                failed_reason = "dependency_missing"
                detail = "The Qwen3 forced aligner isn't installed yet. Open Diagnostics to install it."
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                if isinstance(exc, OSError) and not model_loaded[0]:
                    # A corrupt or missing local model fails the same way for
                    # every group, so retrying only repeats the load.
                    errors.append("the aligner model couldn't be loaded")
                    break
                # str() of these carries the ffmpeg argv with repr-escaped
                # (doubled-backslash) Windows paths the scrubber can't recognise.
                errors.append(f"lines {lines[members[0]].idx + 1}-{lines[members[-1]].idx + 1}: "
                              "couldn't cut this part of the audio")
                continue
            except Exception as exc:
                errors.append(jobs_service.scrub_text(
                    f"lines {lines[members[0]].idx + 1}-{lines[members[-1]].idx + 1}: {exc}"))
                continue
            for i, seg in zip(members, out):
                ln = lines[i]
                if (seg.get("flag") == "timing_uncertain"
                        and seg.get("flag_note") == forced_align.TIMING_FALLBACK_NOTE):
                    errors.append(f"line {ln.idx + 1}: the aligner couldn't place this line, "
                                  "left as it is")
                    continue
                new_start, new_end = float(seg["start"]), float(seg["end"])
                if (abs(new_start - float(ln.start)) < _MOVED_EPSILON_S
                        and abs(new_end - float(ln.end)) < _MOVED_EPSILON_S):
                    continue
                proposals.append({
                    "line_id": ln.id, "number": ln.idx + 1, "base_zh": ln.zh or "",
                    "start": float(ln.start), "end": float(ln.end),
                    "new_start": new_start, "new_end": new_end,
                    "uncertain": seg.get("flag_note") == forced_align.TIMING_REPAIRED_NOTE})
    finally:
        whisper_models.release_gpu_models()
    proposals = _make_consistent(proposals, lines, transcribe_pipeline._audio_duration_seconds(audio_path))
    result = {"proposals": proposals, "line_count": len(line_ids),
              "candidate_count": len(proposals), "errors": errors[:20],
              "partial": cancelled, "alignment_method": "qwen3_forced_align"}
    if device.get("label"):
        result["device"] = device["label"]
    if fallback:
        result["gpu_fallback"] = fallback[0]
        result["device_notice"] = whisper_models.gpu_fallback_notice(
            "Re-timing with the Qwen3 aligner", fallback[0])
    if failed_reason and not proposals:
        result = {"failed_reason": failed_reason, "detail": detail}
    elif failed_reason:
        result["errors"] = [detail] + result["errors"]
    if cancelled and not proposals:
        result = {"failed_reason": "cancelled"}
    background_jobs.set_result(job_id, result)


def _finished_job(drama_id: int) -> dict:
    compare._drama_or_404(drama_id)
    job = background_jobs.get_status(retime_job_id(drama_id)) or {}
    result = job.get("result") if job.get("status") == "done" else None
    if not isinstance(result, dict) or not isinstance(result.get("proposals"), list):
        raise NotFoundError("No finished re-time run for this title.")
    return job


def get_retime_result(drama_id: int) -> dict:
    """The finished run's proposals (before and after times per line).
    NotFoundError when there is none (not run, running, failed, or the API
    restarted since)."""
    result = _finished_job(drama_id)["result"]

    def scrub(text):
        return jobs_service.scrub_text(str(text)) if text else None
    return {"job_id": retime_job_id(drama_id), "proposals": result["proposals"],
            "line_count": result.get("line_count", 0), "partial": bool(result.get("partial")),
            "device": scrub(result.get("device")),
            "device_notice": scrub(result.get("device_notice")),
            "errors": [jobs_service.scrub_text(str(e)) for e in result.get("errors") or []]}


def _overlapping_ids(lines, current: dict, writes: list) -> set:
    """Ids whose new times would overlap a neighbour as it stands right now
    (a neighbour in the same apply counts at its new times). Dropping one
    changes what its neighbours are checked against, so it repeats to a fixed
    point."""
    matching = {lid: v for lid, v, expected in writes
                if compare._still_matches(current.get(lid), expected)}
    position = {ln.id: i for i, ln in enumerate(lines)}
    dropped = set()
    while True:
        def at(i, col):
            ln = lines[i]
            chosen = matching.get(ln.id) if ln.id not in dropped else None
            return float(chosen[col] if chosen else getattr(ln, col))
        newly = set()
        for lid, v in matching.items():
            if lid in dropped:
                continue
            i = position[lid]
            if ((i > 0 and v["start"] < at(i - 1, "end") - 1e-6)
                    or (i + 1 < len(lines) and v["end"] > at(i + 1, "start") + 1e-6)):
                newly.add(lid)
        if not newly:
            return dropped
        dropped |= newly


def apply_retime(drama_id: int, job_id, items) -> dict:
    """Writes the chosen proposals' times. Each item is {line_id,
    expected_new_start, expected_new_end}: it must equal what this run
    proposed, and the line must still hold its text, start and end from when
    the run read it (one compare-and-set per line inside one transaction), so a
    line edited or moved since is skipped and reported, never overwritten.
    Writes only `start` and `end`. A history snapshot ('before re-time apply')
    is taken first when at least one item still matches, unless this run's
    earlier apply already took the latest one. A proposal that would now overlap a neighbouring
    line's current times is skipped too, and listed in `overlapping`. Returns
    {applied: [line_id], skipped: [line_id], overlapping: [line_id]}.

    InvalidInputError for malformed items or another drama's job id;
    NotFoundError with no finished run; ConflictError when an item isn't a
    proposal of this run (nothing written)."""
    if not isinstance(job_id, str) or job_id != retime_job_id(drama_id):
        raise InvalidInputError("job_id is not this title's re-time run.")
    if not isinstance(items, list) or not items or len(items) > MAX_LINES:
        raise InvalidInputError(f"Choose between 1 and {MAX_LINES} lines to apply.")
    job = _finished_job(drama_id)
    by_id = {p["line_id"]: p for p in job["result"]["proposals"]}
    writes, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or item.get("line_id") in seen:
            raise InvalidInputError("Each line can be applied once.")
        seen.add(item.get("line_id"))
        p = by_id.get(item.get("line_id"))
        if (p is None or item.get("expected_new_start") != p["new_start"]
                or item.get("expected_new_end") != p["new_end"]):
            raise ConflictError("These aren't the proposals you were shown. Re-time again.")
        writes.append((p["line_id"], {"start": p["new_start"], "end": p["new_end"]},
                       {"zh": p["base_zh"], "start": p["start"], "end": p["end"]}))
    seen_order = [w[0] for w in writes]
    with compare._apply_lock:
        lines = db.load_line_objects(drama_id)
        current = {ln.id: ln for ln in lines}
        overlapping = _overlapping_ids(lines, current, writes)
        writes = [w for w in writes if w[0] not in overlapping]
        if not any(compare._still_matches(current.get(lid), expected)
                   for lid, _v, expected in writes):
            return {"applied": [], "skipped": seen_order,
                    "overlapping": sorted(overlapping)}
        compare._snapshot_once_per_run(drama_id, (job_id, job.get("finished_at")), lines,
                                       _SNAPSHOT_LABEL)
        skipped = set(db.update_lines_fields_if_many(drama_id, writes))
    return {"applied": [lid for lid, _v, _e in writes if lid not in skipped],
            "skipped": [lid for lid in seen_order if lid in skipped or lid in overlapping],
            "overlapping": sorted(overlapping)}
