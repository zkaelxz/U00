"""
services/resplit_service.py -- cuts a drama's over-long lines at
sentence/comma boundaries in place (no new transcription, no new speaker
detection), optionally timed by the forced aligner. Writes go through
services/restructure_service's guard (fresh load, id check, history
snapshot, full sync under the per-drama lock).

No FastAPI import: plain dicts in and out. Messages never echo
line text, keys or paths.
"""
import dataclasses
from typing import Optional

import background_jobs
import core as core_module
import db
import diarize
import long_line_split
import segment
import whisper_models
from segment_splitting import encode_line_words
from services import (diarization_service, drama_service, settings_service, timing_settings_service,
                      transcribe_options_service, transcribe_service)
from services.resplit_config import RESPLIT_SENSITIVITIES, _Resplit
from services.restructure_service import (_check_expected, _commit, _drama_lock, _id_list,
                                          _refuse_if_job_running, _require_drama, undo_handle)
from services.service_errors import ConflictError, DependencyUnavailableError, InvalidInputError, ServiceError


# ---------------------------------------------------------------------------
# Re-split over-long lines (no new transcription, no new speaker detection)
# ---------------------------------------------------------------------------

RESPLIT_JOB_PREFIX = "resplit_"  # in background_jobs.DRAMA_JOB_PREFIXES
_RESPLIT_CONFIRM = ("Some long lines already have a translation. A split piece can't inherit "
                    "it, so those lines would lose their English -- pass confirm=true.")


# None is no preset: only the fresh-transcription limits (plus any duration cap).
_RESPLIT_NEXT = {None: 'Try "More" or "Sentence by sentence".',
                 "more": 'Try "Sentence by sentence".',
                 "sentence": "Try a shorter duration cap."}


def _resplit_candidates(lines):
    return [ln for ln in lines if not ln.sfx and ln.id is not None and (ln.zh or "").strip()]


def _resplit_plan(lines, cfg: _Resplit) -> dict:
    """{line_id: [{"start","end","text"}, ...]} for each line the proportional
    splitter cuts in two or more pieces."""
    plan = {}
    for ln in _resplit_candidates(lines):
        pieces = cfg.split(ln)
        if len(pieces) >= 2:
            plan[ln.id] = pieces
    return plan


def _nothing_to_split(lines, cfg: _Resplit) -> str:
    return long_line_split.nothing_to_split(_resplit_candidates(lines), cfg,
                                            _RESPLIT_NEXT[cfg.sensitivity])


def _resplit_snapshot(lines, plan) -> dict:
    """{line_id: (start, end, zh, en)} of the planned lines, compared at commit
    so a timing, text or translation edit made meanwhile is never overwritten."""
    return {ln.id: (ln.start, ln.end, ln.zh, ln.en) for ln in lines if ln.id in plan}


def _check_resplit_confirm(lines, plan, confirm):
    if confirm is not True and any(ln.en.strip() for ln in lines if ln.id in plan):
        raise InvalidInputError(_RESPLIT_CONFIRM)


def _aligned_pieces(audio_path, ln, pieces, language, use_gpu):
    """Real word-based boundaries for one line's pieces from the Qwen3 forced
    aligner, or None when its timing is unusable (outside the line, backwards).
    The aligner takes one language per run, so each line is aligned in its own
    (title language when unset)."""
    import forced_align
    aligned = forced_align.align_with_qwen3(
        audio_path, [p["text"] for p in pieces],
        [{"start": ln.start, "end": ln.end, "text": ln.zh}],
        language=ln.lang or language, use_gpu=use_gpu)
    if len(aligned) != len(pieces):
        return None
    spans = [(max(a.start, ln.start), min(a.end, ln.end)) for a in aligned]
    if any(e <= s for s, e in spans) or any(b[0] < a[1] - 1e-6 for a, b in zip(spans, spans[1:])):
        return None
    # The aligner's timing_uncertain flag (repaired or fallen-back spans) rides
    # along so the saved piece carries it.
    return [{**p, "start": s, "end": e,
             **({"flag": a.flag, "flag_note": a.flag_note} if a.flag else {})}
            for p, (s, e), a in zip(pieces, spans, aligned)]


def _apply_resplit(drama_id: int, expected_line_ids, confirm, timed: dict, timing: str,
                   note: str, expected: dict, cfg: _Resplit,
                   own_job_id: Optional[str] = None) -> dict:
    """Commit step shared by both modes: fresh lines, id check, plan check,
    snapshot, one save, then speakers from the saved turns for the split
    lines only. `expected` is _resplit_snapshot from when the split was asked."""
    with _drama_lock(drama_id):
        # Another job (e.g. a translation) started while aligning would write
        # its per-line result onto the first piece, which keeps the parent's id.
        if own_job_id and drama_service.job_running_for_drama(drama_id, own_job_id):
            raise ConflictError("Another job started on this title while the lines were being "
                                "aligned -- nothing was changed; wait for it to finish and try "
                                "again.")
        current = db.load_line_objects(drama_id, with_words=True)
        _check_expected(current, expected_line_ids)
        plan = _resplit_plan(current, cfg)
        if set(plan) != set(expected) or _resplit_snapshot(current, plan) != expected:
            raise ConflictError("A line's text, timing or translation changed while splitting "
                                "-- nothing was changed; reload and try again.")
        _check_resplit_confirm(current, plan, confirm)
        work = [dataclasses.replace(ln, orig=dict(ln.orig), merged_ids=[]) for ln in current]
        new_lines, split_out, cleared, aligned = [], [], 0, 0
        for ln in work:
            pieces = timed.get(ln.id) or plan.get(ln.id)
            if ln.id not in plan or not pieces:
                new_lines.append(ln)
                continue
            if ln.en.strip():
                cleared += 1
            aligned += ln.id in timed
            first, *rest = pieces
            ln.start, ln.end, ln.zh, ln.en = first["start"], first["end"], first["text"], ""
            ln.word_timings = encode_line_words(first["text"], first.get("words"))
            if first.get("flag"):
                ln.flag, ln.flag_note = first["flag"], first["flag_note"]
            new_pieces = [core_module.Line(idx=0, start=p["start"], end=p["end"], zh=p["text"],
                                           speaker=ln.speaker, speaker_manual=ln.speaker_manual,
                                           sfx=ln.sfx, lang=ln.lang, flag=p.get("flag"),
                                           flag_note=p.get("flag_note", ""),
                                           word_timings=encode_line_words(
                                               p["text"], p.get("words")))
                          for p in rest]
            new_lines.append(ln)
            new_lines.extend(new_pieces)
            split_out += [ln, *new_pieces]
        split = sum(1 for ln in work if ln.id in plan)
        if not split:
            return {"split_lines": 0, "line_count": len(current), "lines_before": len(current),
                    "timing": timing, "aligned_lines": 0, "cleared_translations": 0,
                    "speakers_reassigned": False, "note": _nothing_to_split(current, cfg)}
        history_id = _commit(drama_id, current, new_lines, "before re-split")
        reassigned = False
        try:
            if diarize.load_turns(db.drama_dir(drama_id)) is not None:
                # save_lines wrote the new pieces' ids back onto them
                diarization_service.relabel_from_saved_turns(
                    drama_id, only_ids=[ln.id for ln in split_out])
                reassigned = True
        except Exception as exc:  # the split is saved: report it, never "nothing changed"
            reason = (str(exc).rstrip(".") if isinstance(exc, ServiceError)
                      else "the saved speaker detection couldn't be read")
            note = f"Split saved; speakers were not re-assigned: {reason}. {note}".strip()
        # after the relabel, so the fingerprint covers the speakers it set
        handle = undo_handle(drama_id, history_id)
    return {"split_lines": split, "line_count": len(new_lines), "lines_before": len(current),
            "timing": timing, "aligned_lines": aligned, "cleared_translations": cleared,
            "speakers_reassigned": reassigned, "note": note, **handle}


def _run_resplit_job(job_id, drama_id, expected_line_ids, confirm, audio_path, language, use_gpu,
                     cfg):
    lines = db.load_line_objects(drama_id, with_words=True)
    plan = _resplit_plan(lines, cfg)
    by_id = {ln.id: ln for ln in lines}
    expected = _resplit_snapshot(lines, plan)
    timed, notes, aligner_down = {}, [], None
    try:
        for n, (line_id, pieces) in enumerate(plan.items()):
            if background_jobs.is_cancel_requested(job_id):
                background_jobs.set_result(job_id, {"failed_reason": "cancelled"})
                return
            background_jobs.update_progress(
                job_id, min(0.95, 0.05 + 0.9 * n / len(plan)),
                f"Aligning line {n + 1} of {len(plan)} to the audio...")
            if aligner_down:
                break
            try:
                got = _aligned_pieces(audio_path, by_id[line_id], pieces, language, use_gpu)
            except (ImportError, whisper_models.ModelDownloadError) as exc:
                aligner_down = ("The Qwen3 forced aligner isn't available "
                                f"({type(exc).__name__}; see Diagnostics)")
                break
            except Exception as exc:  # one bad line or a GPU error: that line stays proportional
                got = None
                if not isinstance(exc, ValueError):
                    aligner_down = ("The audio alignment failed "
                                    f"({type(exc).__name__})")
                    break
            if got:
                timed[line_id] = got
    finally:  # a cancelled or failed run must not leave the aligner holding VRAM
        whisper_models.release_gpu_models()
    if background_jobs.is_cancel_requested(job_id):
        background_jobs.set_result(job_id, {"failed_reason": "cancelled"})
        return
    if aligner_down:
        timed = {}
        notes.append(aligner_down + "; lines were split with estimated timing.")
    elif plan and len(timed) < len(plan):
        notes.append(f"{len(plan) - len(timed)} line(s) had unusable alignment and use "
                     "estimated timing.")
    timing = "aligned" if timed else "proportional"
    try:
        result = _apply_resplit(drama_id, expected_line_ids, confirm, timed, timing,
                                " ".join(notes), expected, cfg, own_job_id=job_id)
    except (ConflictError, InvalidInputError) as exc:
        background_jobs.set_result(job_id, {"failed_reason": "not_applied", "detail": str(exc)})
        return
    background_jobs.set_result(job_id, result)


def resplit_long_lines(drama_id: int, expected_line_ids, *, align_to_audio: bool = False,
                       confirm: bool = False, sensitivity: Optional[str] = None,
                       max_seconds: Optional[float] = None, dry_run: bool = False) -> dict:
    """Cuts the drama's over-long lines at sentence/comma boundaries in place --
    no transcription, no speaker detection. The first piece keeps the line's id
    and flag; pieces take the parent's speaker, then saved diarization turns (if
    any) relabel the split lines only, manual speakers kept. Translations can't
    be shared across pieces: a long line that has one needs confirm=true, and
    its pieces start empty.

    sensitivity: None (the limits of a fresh transcription: 8 s / 40 CJK
    characters unless Settings changed them), "more" (the per-language subtitle line length, in each line's
    own language) or "sentence" (every sentence end). max_seconds replaces the
    preset's duration limit. Pieces under 0.8 s or 4 CJK characters / 2 words
    are folded into a neighbour in "more" and "sentence".

    dry_run only counts (no write, no confirm, allowed while a job runs).
    Timing is proportional to text length, or with align_to_audio a
    `resplit_<id>` job uses the Qwen3 aligner (proportional, with a note, when
    it or the audio is missing). Snapshot "before re-split" first."""
    drama = _require_drama(drama_id)
    expected_line_ids = _id_list("expected_line_ids", expected_line_ids)
    if sensitivity is not None and sensitivity not in RESPLIT_SENSITIVITIES:
        raise InvalidInputError(f"sensitivity must be one of {', '.join(RESPLIT_SENSITIVITIES)}.")
    if max_seconds is not None and not 2 <= max_seconds <= 120:
        raise InvalidInputError("max_seconds must be between 2 and 120.")
    cfg = _Resplit(drama.get("source_language") or "zh", sensitivity, max_seconds,
                   transcribe_options_service.stored_min_pause_sec(drama),
                   **timing_settings_service.resplit_limits())
    if not dry_run:
        _refuse_if_job_running(drama_id)
    lines = db.load_line_objects(drama_id, with_words=True)
    if sensitivity == "more" and (missing := segment.missing_line_word_segmenter(
            _resplit_candidates(lines), cfg.language)):
        raise DependencyUnavailableError(
            f'"More" needs the {missing} word splitter. Install it in Diagnostics.')
    _check_expected(lines, expected_line_ids)
    plan = _resplit_plan(lines, cfg)
    if dry_run:
        pieces = sum(len(p) for p in plan.values())
        return {"dry_run": True, "split_lines": len(plan), "pieces": pieces,
                "lines_before": len(lines), "line_count": len(lines) - len(plan) + pieces,
                "cleared_translations": sum(1 for ln in lines if ln.id in plan and ln.en.strip()),
                "note": "" if plan else _nothing_to_split(lines, cfg)}
    _check_resplit_confirm(lines, plan, confirm)
    if not align_to_audio or not plan:
        return _apply_resplit(drama_id, expected_line_ids, confirm, {}, "proportional", "",
                              _resplit_snapshot(lines, plan), cfg)
    audio_path = transcribe_service._drama_audio_path(drama_id, drama)
    if audio_path is None:
        return _apply_resplit(
            drama_id, expected_line_ids, confirm, {}, "proportional",
            "This title has no stored audio, so the lines were split with estimated timing.",
            _resplit_snapshot(lines, plan), cfg)
    job_id = f"{RESPLIT_JOB_PREFIX}{drama_id}"
    started = background_jobs.start_job(
        job_id, _run_resplit_job, job_id, drama_id, expected_line_ids, confirm, audio_path,
        cfg.language, settings_service.get("use_gpu"), cfg,
        gpu_touching=True, description=f"Re-splitting lines (title #{drama_id})")
    if not started:
        raise ConflictError("Lines are already being re-split for this title.")
    return {"job_id": job_id, "drama_id": drama_id}
