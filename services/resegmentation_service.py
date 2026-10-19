"""
services/resegmentation_service.py -- re-segmentation of one drama's lines
(rules preview, the LLM preview job, and the apply job). Writes go through
services/restructure_service's guard (fresh load, id check, history
snapshot, full sync under the per-drama lock).

No FastAPI import: plain dicts in and out. Messages never echo
line text, keys or paths.
"""
import contextlib
import dataclasses
import threading
from typing import Optional

import background_jobs
from jobs import job_launch
import db
import long_line_split
import raw_transcript
import resegment
import translate_engines
from engine_backends import llm_tasks, paid_calls
from services import paid_job_service, settings_service, transcribe_options_service, translate_service
from services.restructure_service import (_check_expected, _commit, _drama_lock, _id_list,
                                          _refuse_if_job_running, _require_drama, note_ids)
from services.service_errors import (
    ConflictError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    UnsupportedOperationError,
)

RESEGMENT_JOB_PREFIX = "resegment_"  # already in background_jobs.DRAMA_JOB_PREFIXES


# ---------------------------------------------------------------------------
# Re-segmentation
# ---------------------------------------------------------------------------

def _affected_counts(drama_id: int, lines, ids) -> dict:
    by_id = {ln.id: ln for ln in lines}
    return {"translated": sum(1 for i in ids if i in by_id and by_id[i].en.strip()),
            "flagged": sum(1 for i in ids if i in by_id and by_id[i].flag),
            "notes": sum(1 for n in db.list_translation_notes(drama_id) if n["line_id"] in ids)}


def _candidate_ids(lines, language: str) -> set:
    """Lines resegment_lines could split at all (longer than the cap) -- a
    superset of what any run (rules or LLM) actually changes."""
    cap = resegment.max_line_chars(language)
    return {ln.id for ln in lines if resegment.length(ln.zh or "") > cap}


_CONFIRM_NEEDED = ("Re-segmenting would clear translations, flags or notes on the lines being "
                   "split -- pass confirm=true.")


def _needs_confirm(drama_id: int, lines, ids) -> bool:
    """True when any of the lines `ids` (those a run splits, or could split)
    carries a translation, flag or translation note (all lost on a split)."""
    need = _affected_counts(drama_id, lines, ids)
    return bool(need["translated"] or need["flagged"] or need["notes"])


def _reseg_inputs(drama_id: int, drama: dict):
    language = drama.get("source_language") or "zh"
    raw = raw_transcript.load_latest(db.drama_dir(drama_id))
    return (language, drama.get("chinese_script") or "simplified", (raw or {}).get("segments"),
            transcribe_options_service.stored_min_pause_sec(drama))


def preview_resegmentation(drama_id: int) -> dict:
    """Read-only, rules only (no LLM call, nothing written). `needs_confirm`
    is True if any line long enough to be split carries a translation,
    flag or note -- Apply then needs confirm=true."""
    drama = _require_drama(drama_id)
    language, script, segments, min_pause = _reseg_inputs(drama_id, drama)
    lines = db.load_line_objects(drama_id, with_words=True)
    new_lines, changed = resegment.resegment_lines(lines, language, segments=segments,
                                                   chinese_script=script, min_pause=min_pause)
    changed_ids = {ln.id for ln, _ in changed}
    candidates = _candidate_ids(lines, language)
    need = _affected_counts(drama_id, lines, candidates)
    return {"drama_id": drama_id, "source_line_ids": [ln.id for ln in lines],
            "line_count_before": len(lines), "line_count_after": len(new_lines),
            "changed": [{"line_id": ln.id, "idx": ln.idx, "zh": ln.zh, "pieces": list(p)}
                        for ln, p in changed],
            **_affected_counts(drama_id, lines, changed_ids),
            "needs_confirm": bool(need["translated"] or need["flagged"] or need["notes"]),
            "reason": long_line_split.resegment_reason(lines, language, changed, new_lines)}


def _note_ids_by_line(drama_id: int) -> dict:
    out = {}
    for note in db.list_translation_notes(drama_id):
        out.setdefault(note["line_id"], set()).add(note["id"])
    return out


def _apply_guard(drama_id: int, lines) -> tuple:
    """What the job's write is checked against: the lines and their notes as
    the job read them."""
    return _line_fingerprint(lines), _note_ids_by_line(drama_id), db.line_emotion_ids(drama_id)


def _apply_resegmented(drama_id: int, new_lines, pieces_by_id: dict, guard: tuple) -> dict:
    """The job's write step: same guard as Apply. A kept line only gets the
    fields the job changed (save_lines diffs against `orig`). A split line is
    deleted with its text, translation, flag and notes, so one edited or
    given a note while the job ran (the start's confirm covered only what was
    there then) is left whole as it is now, and the rest of the split is
    still saved: the run's paid LLM calls aren't thrown away for one edit.

    pieces_by_id: {line_id: number of pieces} for each line the run split,
    in the order resegment_lines emitted them."""
    fingerprint, notes_then, emotions_then = guard
    with _drama_lock(drama_id):
        current = db.load_line_objects(drama_id, with_words=True)
        if [ln.id for ln in current] != [lid for lid, _ in fingerprint]:
            raise RuntimeError("This title's lines changed while re-segmenting -- nothing was "
                               "changed; run it again.")
        now = dict(_line_fingerprint(current))
        notes_now = _note_ids_by_line(drama_id)
        edited = {lid for lid, was in fingerprint if lid in pieces_by_id
                  and (now[lid] != was or notes_now.get(lid, set()) - notes_then.get(lid, set()))}
        if db.line_emotion_ids(drama_id) - emotions_then:
            # Refused here, before the history snapshot _commit takes, so no
            # orphan "before re-segment" entry is left behind.
            raise RuntimeError("An emotion tag was added while re-segmenting -- nothing was "
                               "changed; run it again.")
        if edited:
            new_lines = _keep_whole(new_lines, [lid for lid, _ in fingerprint], pieces_by_id,
                                    {ln.id: ln for ln in current}, edited)
        if len(edited) < len(pieces_by_id):
            _commit(drama_id, current, list(new_lines), "before re-segment",
                    known_note_ids=set().union(*notes_then.values()),
                    known_emotion_ids=emotions_then)
    return {"line_count": len(new_lines), "kept_your_edit": len(edited)}


def _keep_whole(new_lines, source_ids, pieces_by_id, current_by_id, keep) -> list:
    """new_lines with each line in `keep` back as one line, as it is now."""
    out, pos = [], 0
    for lid in source_ids:
        n = pieces_by_id.get(lid, 1)
        if lid in keep:
            ln = current_by_id[lid]
            out.append(dataclasses.replace(ln, merged_ids=list(ln.merged_ids)))
        else:
            out.extend(new_lines[pos:pos + n])
        pos += n
    if pos != len(new_lines):
        raise RuntimeError("The re-segmented lines don't match the lines they came from -- "
                           "nothing was changed; run it again.")
    return out


def _raise_if_cancelled(job_id):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


def _bounded(job_id, drama_id, engine, engine_name):
    if engine is None:
        return contextlib.nullcontext()
    return paid_job_service.job_calls(
        job_id, drama_id, engine, engine_name, "resegment",
        deadline=llm_tasks.request_deadline_for(engine))


def _run_resegment_job(job_id, drama_id, lines, guard, language, engine, engine_name,
                       segments, script, min_pause, split=None):
    """split: the rules-only (new_lines, changed) worked out at the start;
    None runs the LLM pass here."""
    if split is None:
        usage = paid_calls.usage_logger(drama_id, engine_name, engine, "resegment")
        with _bounded(job_id, drama_id, engine, engine_name):
            split = resegment.resegment_lines(
                lines, language, engine=engine, segments=segments, chinese_script=script,
                usage_cb=usage, min_pause=min_pause,
                check_cancel=lambda: _raise_if_cancelled(job_id))
    new_lines, changed = split
    result = {"changed": len(changed)}
    if changed:
        result.update(_apply_resegmented(drama_id, new_lines,
                                         {ln.id: len(p) for ln, p in changed}, guard))
        result["changed"] -= result["kept_your_edit"]
    background_jobs.set_result(job_id, result)


def _make_on_done(drama_id, guard, engine_name, engine):
    def on_done(job_id, result):
        for inp, out in (result or {}).get("usage_calls", []):
            paid_calls.usage_logger(drama_id, engine_name, engine, "resegment")(inp, out)
        if (result or {}).get("changed"):
            _apply_resegmented(drama_id, result["lines"],
                               {lid: len(pieces) for lid, _idx, _zh, pieces in result["changed"]},
                               guard)
    return on_done


def _build_engine(drama: dict, engine_name: Optional[str], model: Optional[str]):
    from services import translate_run_service   # lazy: heavy import
    engine_name = engine_name or drama.get("translation_engine") or settings_service.get("default_engine")
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    if engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't suggest split points.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=settings_service.get("gemini_free_tier"),
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    translate_run_service.refuse_when_cap_spent(engine_name,
                                                settings_service.get("gemini_free_tier"))
    return engine_name, engine


def start_resegmentation(drama_id: int, expected_line_ids, confirm: bool = False,
                         use_llm: bool = False, engine: Optional[str] = None,
                         model: Optional[str] = None, use_preview: bool = False) -> dict:
    """Starts a `resegment_<drama_id>` job that re-segments AND saves (with
    a "before re-segment" snapshot). Split lines lose their translation,
    flag, notes and emotion tag; confirm=true is required when
    any line long enough to be split carries one. A local Ollama LLM pass
    runs in a subprocess (cancellable) and saves via on_done.

    use_preview=true (parity R47) commits the drama's stored LLM preview
    (start_llm_resegment_preview) as shown, with no second LLM call; it
    can't be combined with use_llm/engine/model, and is refused if any
    line changed since the preview."""
    drama = _require_drama(drama_id)
    expected_line_ids = _id_list("expected_line_ids", expected_line_ids)
    if use_preview:
        if use_llm or engine or model:
            raise InvalidInputError("use_preview applies the stored preview; don't pass use_llm, "
                                    "engine or model with it.")
        return _start_preview_apply(drama_id, expected_line_ids, confirm)
    language, script, segments, min_pause = _reseg_inputs(drama_id, drama)
    engine_name, eng = _build_engine(drama, engine, model) if use_llm else (None, None)
    _refuse_if_job_running(drama_id)
    lines = db.load_line_objects(drama_id, with_words=True)
    _check_expected(lines, expected_line_ids)
    guard = _apply_guard(drama_id, lines)
    if eng is None:
        # Rules only: the split is known now, so confirm covers exactly the
        # lines it splits, and the job saves this split.
        split = resegment.resegment_lines(lines, language, segments=segments,
                                          chinese_script=script, min_pause=min_pause)
        at_risk = {ln.id for ln, _ in split[1]}
    else:
        # The LLM's split isn't known until it runs, so every splittable line counts.
        split, at_risk = None, _candidate_ids(lines, language)
    if confirm is not True and _needs_confirm(drama_id, lines, at_risk):
        raise InvalidInputError(_CONFIRM_NEEDED)
    job_id = f"{RESEGMENT_JOB_PREFIX}{drama_id}"
    desc = f"Re-segmenting (title #{drama_id})"
    if engine_name == "ollama":
        started = job_launch.start_process_job(
            job_id, resegment.resegment_subprocess_worker,
            args=(lines, language, eng, segments, script, min_pause), gpu_touching=True,
            description=desc, kill_whole_tree=True, start_method="spawn", on_done=_make_on_done(drama_id, guard, engine_name, eng),
            line_fields=db.FULL_SYNC)
    else:
        started = job_launch.start_job(
            job_id, _run_resegment_job, job_id, drama_id, lines, guard, language,
            eng, engine_name, segments, script, min_pause, split=split, description=desc,
            line_fields=db.FULL_SYNC)
    if not started:
        raise ConflictError("A re-segmentation is already running for this title.")
    return {"job_id": job_id, "drama_id": drama_id}


# Parity R47: the LLM re-segmentation as a preview. A job (the local Ollama
# pass in its own process, as for the apply job) works out the split and
# keeps the result here, in memory; nothing is written to the drama's lines.
# The preview carries line text, so it is read back through
# get_llm_resegment_preview (lines.read), never through the job's result.
RESEGMENT_PREVIEW_JOB_PREFIX = "resegpreview_"   # in background_jobs.DRAMA_JOB_PREFIXES
_llm_previews = {}
_llm_previews_lock = threading.Lock()


def _line_fingerprint(lines) -> list:
    """What a stored preview was computed from: each line's id plus every
    compared Line field (text, translation, timing, speaker, flag...). Any
    edit since the preview changes it, so applying the preview can't
    overwrite that edit."""
    return [(ln.id, dataclasses.replace(ln, orig=None, merged_ids=[])) for ln in lines]


def _store_llm_preview(drama_id, source_lines, new_lines, changed, engine_name):
    """changed: [(line_id, idx, zh, pieces), ...] as the subprocess returns it.
    Keeps the new lines themselves too (private), so Apply with
    use_preview=true commits exactly what was shown without a second LLM
    call."""
    changed_ids = {c[0] for c in changed}
    preview = {
        "drama_id": drama_id, "engine": engine_name,
        "source_line_ids": [ln.id for ln in source_lines],
        "line_count_before": len(source_lines), "line_count_after": len(new_lines),
        "changed": [{"line_id": lid, "idx": idx, "zh": zh, "pieces": list(pieces)}
                    for lid, idx, zh, pieces in changed],
        **_affected_counts(drama_id, source_lines, changed_ids),
        "needs_confirm": _needs_confirm(drama_id, source_lines, changed_ids)}
    # changed_ids: the apply step recounts what the split would lose on the
    # lines as they are THEN (a note added since the preview isn't in the
    # fingerprint, so needs_confirm above can be stale).
    preview["_apply"] = {"lines": list(new_lines), "fingerprint": _line_fingerprint(source_lines),
                         "changed_ids": changed_ids}
    with _llm_previews_lock:
        _llm_previews[drama_id] = preview


def _run_llm_preview_job(job_id, drama_id, lines, language, engine, engine_name, segments,
                         script, min_pause):
    with _bounded(job_id, drama_id, engine, engine_name):
        new_lines, changed = resegment.resegment_lines(
            [dataclasses.replace(ln) for ln in lines], language, engine=engine, segments=segments,
            chinese_script=script, usage_cb=paid_calls.usage_logger(drama_id, engine_name, engine, "resegment"),
            min_pause=min_pause, check_cancel=lambda: _raise_if_cancelled(job_id))
    _store_llm_preview(drama_id, lines, new_lines,
                       [(ln.id, ln.idx, ln.zh, p) for ln, p in changed], engine_name)
    background_jobs.set_result(job_id, {"line_count": len(new_lines)})


def _make_preview_on_done(drama_id, lines, engine_name, engine):
    def on_done(job_id, result):
        for inp, out in (result or {}).get("usage_calls", []):
            paid_calls.usage_logger(drama_id, engine_name, engine, "resegment")(inp, out)
        _store_llm_preview(drama_id, lines, (result or {}).get("lines") or [],
                           (result or {}).get("changed") or [], engine_name)
    return on_done


def start_llm_resegment_preview(drama_id: int, engine: Optional[str] = None,
                                model: Optional[str] = None) -> dict:
    """Starts a `resegpreview_<drama_id>` job that asks the LLM where to
    split the lines the rules can't, and keeps the result as a preview
    (get_llm_resegment_preview). Writes nothing to the lines; the LLM's
    usage is logged like any other paid call, and a spent monthly cap
    refuses it. Applying still goes through start_resegmentation."""
    drama = _require_drama(drama_id)
    language, script, segments, min_pause = _reseg_inputs(drama_id, drama)
    engine_name, eng = _build_engine(drama, engine, model)
    lines = db.load_line_objects(drama_id, with_words=True)
    job_id = f"{RESEGMENT_PREVIEW_JOB_PREFIX}{drama_id}"
    if background_jobs.is_running(job_id):
        raise ConflictError("A re-segmentation preview is already running for this title.")
    with _llm_previews_lock:
        _llm_previews.pop(drama_id, None)
    desc = f"Re-segmentation preview (title #{drama_id})"
    if engine_name == "ollama":
        started = job_launch.start_process_job(
            job_id, resegment.resegment_subprocess_worker,
            args=(lines, language, eng, segments, script, min_pause), gpu_touching=True,
            description=desc, kill_whole_tree=True, start_method="spawn", on_done=_make_preview_on_done(drama_id, lines, engine_name, eng),
            line_fields=())
    else:
        started = job_launch.start_job(
            job_id, _run_llm_preview_job, job_id, drama_id, lines, language, eng, engine_name,
            segments, script, min_pause, description=desc, line_fields=())
    if not started:
        raise ConflictError("A re-segmentation preview is already running for this title.")
    return {"job_id": job_id, "drama_id": drama_id}


def get_llm_resegment_preview(drama_id: int) -> dict:
    """The last finished LLM preview for this drama (same shape as
    preview_resegmentation, plus the engine). NotFoundError when none is
    ready: none was started, it is still running, or it failed -- or it was
    made for other lines (the drama's line ids no longer match: its lines
    were replaced, or the drama was deleted and its id reused), in which
    case the stale preview is dropped."""
    _require_drama(drama_id)
    with _llm_previews_lock:
        preview = _llm_previews.get(drama_id)
    if preview is not None and (
            [ln.id for ln in db.load_line_objects(drama_id)] != preview["source_line_ids"]):
        with _llm_previews_lock:
            if _llm_previews.get(drama_id) is preview:
                _llm_previews.pop(drama_id, None)
        preview = None
    if preview is None:
        raise NotFoundError("No LLM re-segmentation preview is ready for this title.")
    return {k: v for k, v in preview.items() if not k.startswith("_")}


def _apply_llm_preview_job(job_id, drama_id, preview, confirm: bool = False):
    """Commits a stored LLM preview's lines (no LLM call). Refused, with
    nothing written, if any line changed since the preview was made, or if
    (without confirm) a split would now lose a translation, flag or note."""
    state = preview["_apply"]
    if not preview["changed"]:
        background_jobs.set_result(job_id, {"changed": 0})
        return
    with _drama_lock(drama_id):
        # Read before the lines, so a note added after either read is unknown.
        known = note_ids(drama_id)
        known_emotions = db.line_emotion_ids(drama_id)
        current = db.load_line_objects(drama_id)
        if _line_fingerprint(current) != state["fingerprint"]:
            raise RuntimeError("This title's lines changed since the preview -- nothing was "
                               "changed; run the preview again.")
        if confirm is not True and _needs_confirm(drama_id, current, state["changed_ids"]):
            raise RuntimeError("Re-segmenting would now clear translations, flags or notes on "
                               "the lines being split -- nothing was changed; apply again "
                               "with confirm=true.")
        new_lines = [dataclasses.replace(ln, merged_ids=list(ln.merged_ids))
                     for ln in state["lines"]]
        _commit(drama_id, current, new_lines, "before re-segment", known_note_ids=known,
                known_emotion_ids=known_emotions)
    with _llm_previews_lock:
        if _llm_previews.get(drama_id) is preview:
            _llm_previews.pop(drama_id, None)   # applied: it can't be applied twice
    background_jobs.set_result(job_id, {"changed": len(preview["changed"]),
                                        "line_count": len(new_lines)})


def _start_preview_apply(drama_id: int, expected_line_ids: list, confirm: bool) -> dict:
    with _llm_previews_lock:
        preview = _llm_previews.get(drama_id)
    if preview is None:
        raise NotFoundError("No LLM re-segmentation preview is ready for this title -- run the "
                            "preview first.")
    _refuse_if_job_running(drama_id)
    lines = db.load_line_objects(drama_id)
    _check_expected(lines, expected_line_ids)
    if _line_fingerprint(lines) != preview["_apply"]["fingerprint"]:
        raise ConflictError("This title's lines changed since the preview -- run the preview "
                            "again.")
    # Recounted on the lines as they are now, not the preview's needs_confirm:
    # a translation note added since isn't in the fingerprint.
    if confirm is not True and _needs_confirm(drama_id, lines, preview["_apply"]["changed_ids"]):
        raise InvalidInputError(_CONFIRM_NEEDED)
    job_id = f"{RESEGMENT_JOB_PREFIX}{drama_id}"
    started = job_launch.start_job(
        job_id, _apply_llm_preview_job, job_id, drama_id, preview, confirm is True,
        description=f"Applying re-segmentation preview (title #{drama_id})",
        line_fields=db.FULL_SYNC)
    if not started:
        raise ConflictError("A re-segmentation is already running for this title.")
    return {"job_id": job_id, "drama_id": drama_id}
