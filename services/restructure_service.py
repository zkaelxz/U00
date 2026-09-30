"""
services/restructure_service.py -- Migration Slice 45: STRUCTURAL line
changes for one drama (add, delete, merge, split, re-segmentation) and
Version-history restore. Mirrors `tabs/workspace_tab.py`'s "Restructure
lines" popover (Apply merge, Preview/Apply re-segmentation) and "Version
history / undo" -> Restore; add/delete/split of a single line have no tab
equivalent yet and follow the same rules.

Correctness rules (Steps 2, 6c, 6f, 25l, 25m):
- Every change loads the drama's lines FRESH from the database, checks the
  client's `expected_line_ids` (the drama's line ids, in order, as the
  client last saw them) against them -- any difference is a 409 with
  nothing written -- takes a history snapshot of the current lines, THEN
  writes.
- The write is one `db.save_lines(..., fields=None)` call over that fresh
  list (a single SQLite transaction). Lines are matched by permanent id:
  a kept line keeps its id, so its flag/flag_note/speaker/notes/emotion
  stay on it; a merged-away line's notes/emotion are re-pointed onto the
  line it merged into (`merged_ids`); a deleted/split-away line's notes
  and emotion are deleted with its row (never left orphaned).
- Refused (409) while any job is running on the drama, so a job's
  field-scoped result can't land on a line whose text just changed shape.

Atomicity gap: the snapshot, the id-set re-check and `save_lines` are three
separate transactions (db.py has no API to run them in one). Guarded by a
per-drama lock held across load -> check -> snapshot -> save for every write
in this module (including the re-segmentation job's apply step), and by
re-reading the id set immediately before `save_lines`. Another process (the
Streamlit app) can still full-sync between that re-check and the save; a
failure between snapshot and save leaves only an extra snapshot.

No Streamlit/FastAPI import: plain dicts in and out. Messages never echo
line text, keys or paths.
"""
import dataclasses
import math
import threading
from typing import Optional

import background_jobs
import core as core_module
import db
import raw_transcript
import resegment
import translate_engines
from services import drama_service, settings_service, translate_service
from services.review_lines_service import _line_dict
from services.review_records_service import get_line_history_snapshot, list_line_history
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)

MAX_LINE_TEXT_CHARS = 2000
MAX_SPEAKER_CHARS = 100
MAX_MERGE_LINES = 50
RESEGMENT_JOB_PREFIX = "resegment_"  # already in background_jobs.DRAMA_JOB_PREFIXES

_locks_guard = threading.Lock()
_drama_locks = {}


def _drama_lock(drama_id: int) -> threading.Lock:
    with _locks_guard:
        return _drama_locks.setdefault(drama_id, threading.Lock())


# ---------------------------------------------------------------------------
# Shared checks
# ---------------------------------------------------------------------------

def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _refuse_if_job_running(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this drama -- wait for it "
                            "to finish or cancel it before restructuring lines.")


def _id_list(name: str, value) -> list:
    if not isinstance(value, (list, tuple)) or any(
            isinstance(i, bool) or not isinstance(i, int) for i in value):
        raise InvalidInputError(f"{name} must be a list of integer line ids.")
    if len(set(value)) != len(value):
        raise InvalidInputError(f"{name} must not repeat an id.")
    return list(value)


def _check_expected(current, expected_line_ids):
    if [ln.id for ln in current] != list(expected_line_ids):
        raise ConflictError("This drama's lines changed since you loaded them -- reload and "
                            "try again.")


def _text(name: str, value, cap: int) -> str:
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be text.")
    if len(value) > cap:
        raise InvalidInputError(f"{name} is too long (max {cap} characters).")
    return value


def _number(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidInputError(f"{name} must be a number.")
    if value < 0:
        raise InvalidInputError(f"{name} must not be negative.")
    return float(value)


def _commit(drama_id: int, current, new_lines, label: str):
    """Snapshot `current` (fresh from the DB), then full-sync `new_lines`.
    Caller holds the drama lock and has already checked expected ids."""
    db.save_line_history_snapshot(drama_id, current, label)
    if db.load_line_ids(drama_id) != {ln.id for ln in current}:
        raise ConflictError("This drama's lines changed while saving -- nothing was changed; "
                            "reload and try again.")
    for i, ln in enumerate(new_lines):
        ln.idx = i
    db.save_lines(drama_id, new_lines)


def _structural_write(drama_id: int, expected_line_ids, label: str, build):
    """load fresh -> check ids -> build(current) -> snapshot -> save, under
    the drama lock. build returns (new_lines, result_line_objects)."""
    _require_drama(drama_id)
    expected_line_ids = _id_list("expected_line_ids", expected_line_ids)
    _refuse_if_job_running(drama_id)
    with _drama_lock(drama_id):
        current = db.load_line_objects(drama_id)
        _check_expected(current, expected_line_ids)
        # build works on copies so the snapshot sees the untouched lines
        work = [dataclasses.replace(ln, orig=dict(ln.orig), merged_ids=[]) for ln in current]
        new_lines, touched = build(work)
        _commit(drama_id, current, new_lines, label)
    return {"line_ids": [ln.id for ln in new_lines],
            "lines": [_line_dict(ln) for ln in touched]}


def _index_of(lines, line_id) -> int:
    for i, ln in enumerate(lines):
        if ln.id == line_id:
            return i
    raise NotFoundError(f"No line with id {line_id} in this drama.")


# ---------------------------------------------------------------------------
# Add / delete / merge / split
# ---------------------------------------------------------------------------

def add_line(drama_id: int, expected_line_ids, *, after_line_id: Optional[int] = None,
             start=None, end=None, zh: str = "", en: str = "",
             speaker: Optional[str] = None) -> dict:
    """Inserts a new line after `after_line_id` (None = at the start)."""
    start, end = _number("start", start), _number("end", end)
    if end <= start:
        raise InvalidInputError("end must be after start.")
    zh = _text("zh", zh, MAX_LINE_TEXT_CHARS)
    en = _text("en", en, MAX_LINE_TEXT_CHARS)
    if speaker is not None:
        speaker = _text("speaker", speaker, MAX_SPEAKER_CHARS).strip() or None

    def build(lines):
        pos = 0 if after_line_id is None else _index_of(lines, after_line_id) + 1
        new = core_module.Line(idx=pos, start=start, end=end, zh=zh, en=en, speaker=speaker,
                               speaker_manual=speaker is not None)
        return lines[:pos] + [new] + lines[pos:], [new]
    return _structural_write(drama_id, expected_line_ids, "before add line", build)


def delete_line(drama_id: int, line_id: int, expected_line_ids, confirm: bool = False) -> dict:
    """Deletes one line (its notes and emotion tag with it). Needs
    confirm=True, like the tab's other destructive deletes."""
    if confirm is not True:
        raise InvalidInputError("Deleting a line needs confirm=true.")

    def build(lines):
        i = _index_of(lines, line_id)
        return lines[:i] + lines[i + 1:], []
    return _structural_write(drama_id, expected_line_ids, "before delete line", build)


def merge_lines(drama_id: int, line_ids, expected_line_ids) -> dict:
    """Merges 2+ ADJACENT lines (given in order) into the first: text joined
    as core.merge_adjacent_short_lines joins it, end = last line's end, the
    first line keeps its id/speaker; its flag, else the first merged line's
    flag, is kept. The others' notes/emotions move onto it (the first
    line's own win on a conflict)."""
    line_ids = _id_list("line_ids", line_ids)
    if not 2 <= len(line_ids) <= MAX_MERGE_LINES:
        raise InvalidInputError(f"line_ids must name 2 to {MAX_MERGE_LINES} lines.")

    def build(lines):
        first = _index_of(lines, line_ids[0])
        if [ln.id for ln in lines[first:first + len(line_ids)]] != line_ids:
            for lid in line_ids:
                _index_of(lines, lid)  # 404 for an unknown id first
            raise InvalidInputError("line_ids must be adjacent lines, in order.")
        head, rest = lines[first], lines[first + 1:first + len(line_ids)]
        for ln in rest:
            head.zh = head.zh.rstrip() + ln.zh.strip()
            head.en = (head.en.rstrip() + " " + ln.en.strip()).strip()
            if not head.flag and ln.flag:
                head.flag, head.flag_note = ln.flag, ln.flag_note
            head.merged_ids = list(head.merged_ids) + [ln.id]
        head.end = max(head.end, rest[-1].end)
        return lines[:first + 1] + lines[first + len(line_ids):], [head]
    return _structural_write(drama_id, expected_line_ids, "before merge", build)


def split_line(drama_id: int, line_id: int, expected_line_ids, *, at_char: int,
               expected_zh: str, at_time=None, en_at_char: Optional[int] = None) -> dict:
    """Splits one line's source text at character offset `at_char`. The
    first piece keeps the line's id (so its flag, notes and emotion stay
    on it); the second is a new line with the same speaker/sfx and no
    flag. Its translation stays whole on the first piece unless
    `en_at_char` splits it too. The cut time is `at_time` (strictly inside
    the line) or proportional to piece length. `expected_zh` must equal
    the line's current text (409 otherwise), since the offset refers to it."""
    for name, v in (("at_char", at_char), ("en_at_char", en_at_char)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            raise InvalidInputError(f"{name} must be an integer.")
    _text("expected_zh", expected_zh, MAX_LINE_TEXT_CHARS)
    if at_time is not None:
        at_time = _number("at_time", at_time)

    def build(lines):
        i = _index_of(lines, line_id)
        ln = lines[i]
        if ln.zh != expected_zh:
            raise ConflictError("This line's text changed since you loaded it.")
        if not 0 < at_char < len(ln.zh):
            raise InvalidInputError("at_char must fall inside the line's text.")
        if en_at_char is not None and not 0 < en_at_char < len(ln.en):
            raise InvalidInputError("en_at_char must fall inside the line's translation.")
        pieces = [ln.zh[:at_char], ln.zh[at_char:]]
        if at_time is None:
            cut = resegment.split_times(ln, pieces)[0]
        elif ln.start < at_time < ln.end:
            cut = at_time
        else:
            raise InvalidInputError("at_time must fall strictly inside the line's timing.")
        en_first, en_second = ln.en, ""
        if en_at_char is not None:
            en_first, en_second = ln.en[:en_at_char].rstrip(), ln.en[en_at_char:].strip()
        second = core_module.Line(idx=0, start=cut, end=ln.end, zh=pieces[1], en=en_second,
                                  speaker=ln.speaker, speaker_manual=ln.speaker_manual,
                                  sfx=ln.sfx)
        ln.zh, ln.en, ln.end = pieces[0], en_first, cut
        return lines[:i + 1] + [second] + lines[i + 1:], [ln, second]
    return _structural_write(drama_id, expected_line_ids, "before split", build)


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
    return {ln.id for ln in lines if resegment._length(ln.zh or "") > cap}


_CONFIRM_NEEDED = ("Re-segmenting would clear translations, flags or notes on the lines being "
                   "split -- pass confirm=true.")


def _needs_confirm(drama_id: int, lines, language: str) -> bool:
    """True when any line long enough to be split carries a translation,
    flag or translation note (all lost on a split)."""
    need = _affected_counts(drama_id, lines, _candidate_ids(lines, language))
    return bool(need["translated"] or need["flagged"] or need["notes"])


def _reseg_inputs(drama_id: int, drama: dict):
    language = drama.get("source_language") or "zh"
    raw = raw_transcript.load_latest(db.drama_dir(drama_id))
    return language, drama.get("chinese_script") or "simplified", (raw or {}).get("segments")


def preview_resegmentation(drama_id: int) -> dict:
    """Read-only, rules only (no LLM call, nothing written). `needs_confirm`
    is True when any line long enough to be split carries a translation,
    flag or note -- Apply then needs confirm=true."""
    drama = _require_drama(drama_id)
    language, script, segments = _reseg_inputs(drama_id, drama)
    lines = db.load_line_objects(drama_id)
    new_lines, changed = resegment.resegment_lines(lines, language, segments=segments,
                                                   chinese_script=script)
    changed_ids = {ln.id for ln, _ in changed}
    candidates = _candidate_ids(lines, language)
    need = _affected_counts(drama_id, lines, candidates)
    return {"drama_id": drama_id, "source_line_ids": [ln.id for ln in lines],
            "line_count_before": len(lines), "line_count_after": len(new_lines),
            "changed": [{"line_id": ln.id, "idx": ln.idx, "zh": ln.zh, "pieces": list(p)}
                        for ln, p in changed],
            **_affected_counts(drama_id, lines, changed_ids),
            "needs_confirm": bool(need["translated"] or need["flagged"] or need["notes"])}


def _apply_resegmented(drama_id: int, new_lines, source_ids: list) -> dict:
    """The job's write step: same guard as the tab's Apply (Step 6f)."""
    with _drama_lock(drama_id):
        current = db.load_line_objects(drama_id)
        if [ln.id for ln in current] != source_ids:
            raise RuntimeError("This drama's lines changed while re-segmenting -- nothing was "
                               "changed; run it again.")
        _commit(drama_id, current, list(new_lines), "before re-segment")
    return {"line_count": len(new_lines)}


def _usage_logger(drama_id, engine_name, engine):
    def log(inp, out):
        db.log_usage(drama_id, engine_name, getattr(engine, "model", engine_name), "resegment",
                     inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out))
    return log


def _run_resegment_job(job_id, drama_id, lines, source_ids, language, engine, engine_name,
                       segments, script):
    usage = _usage_logger(drama_id, engine_name, engine) if engine is not None else None
    new_lines, changed = resegment.resegment_lines(lines, language, engine=engine,
                                                   segments=segments, chinese_script=script,
                                                   usage_cb=usage)
    result = {"changed": len(changed)}
    if changed:
        result.update(_apply_resegmented(drama_id, new_lines, source_ids))
    background_jobs.set_result(job_id, result)


def _make_on_done(drama_id, source_ids, engine_name, engine):
    def on_done(job_id, result):
        for inp, out in (result or {}).get("usage_calls", []):
            _usage_logger(drama_id, engine_name, engine)(inp, out)
        if (result or {}).get("changed"):
            _apply_resegmented(drama_id, result["lines"], source_ids)
    return on_done


def _build_engine(drama: dict, engine_name: Optional[str], model: Optional[str]):
    engine_name = engine_name or drama.get("translation_engine") or settings_service.get_default_engine()
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError("Unknown engine.")
    if engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't suggest split points.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=settings_service.get_gemini_free_tier(),
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    return engine_name, engine


def start_resegmentation(drama_id: int, expected_line_ids, confirm: bool = False,
                         use_llm: bool = False, engine: Optional[str] = None,
                         model: Optional[str] = None, use_preview: bool = False) -> dict:
    """Starts a `resegment_<drama_id>` job that re-segments AND saves (with
    a "before re-segment" snapshot). Split lines lose their translation,
    flag, notes and emotion tag (Step 6c); confirm=true is required when
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
    language, script, segments = _reseg_inputs(drama_id, drama)
    engine_name, eng = _build_engine(drama, engine, model) if use_llm else (None, None)
    _refuse_if_job_running(drama_id)
    lines = db.load_line_objects(drama_id)
    _check_expected(lines, expected_line_ids)
    if confirm is not True and _needs_confirm(drama_id, lines, language):
        raise InvalidInputError(_CONFIRM_NEEDED)
    job_id = f"{RESEGMENT_JOB_PREFIX}{drama_id}"
    desc = f"Re-segmenting (drama #{drama_id})"
    if engine_name == "ollama":
        started = background_jobs.start_process_job(
            job_id, resegment.resegment_subprocess_worker,
            args=(lines, language, eng, segments, script), gpu_touching=True, description=desc,
            on_done=_make_on_done(drama_id, expected_line_ids, engine_name, eng))
    else:
        started = background_jobs.start_job(
            job_id, _run_resegment_job, job_id, drama_id, lines, expected_line_ids, language,
            eng, engine_name, segments, script, description=desc)
    if not started:
        raise ConflictError("A re-segmentation is already running for this drama.")
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


def _store_llm_preview(drama_id, source_lines, language, new_lines, changed, engine_name):
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
        "needs_confirm": _needs_confirm(drama_id, source_lines, language)}
    # language: the apply step recounts what a split would lose on the lines
    # as they are THEN (a note added since the preview isn't in the
    # fingerprint, so needs_confirm above can be stale).
    preview["_apply"] = {"lines": list(new_lines), "fingerprint": _line_fingerprint(source_lines),
                         "language": language}
    with _llm_previews_lock:
        _llm_previews[drama_id] = preview


def _run_llm_preview_job(job_id, drama_id, lines, language, engine, engine_name, segments,
                         script):
    new_lines, changed = resegment.resegment_lines(
        [dataclasses.replace(ln) for ln in lines], language, engine=engine, segments=segments,
        chinese_script=script, usage_cb=_usage_logger(drama_id, engine_name, engine))
    _store_llm_preview(drama_id, lines, language, new_lines,
                       [(ln.id, ln.idx, ln.zh, p) for ln, p in changed], engine_name)
    background_jobs.set_result(job_id, {"line_count": len(new_lines)})


def _make_preview_on_done(drama_id, lines, language, engine_name, engine):
    def on_done(job_id, result):
        for inp, out in (result or {}).get("usage_calls", []):
            _usage_logger(drama_id, engine_name, engine)(inp, out)
        _store_llm_preview(drama_id, lines, language, (result or {}).get("lines") or [],
                           (result or {}).get("changed") or [], engine_name)
    return on_done


def start_llm_resegment_preview(drama_id: int, engine: Optional[str] = None,
                                model: Optional[str] = None) -> dict:
    """Starts a `resegpreview_<drama_id>` job that asks the LLM where to
    split the lines the rules can't, and keeps the result as a preview
    (get_llm_resegment_preview). Writes nothing to the lines; the LLM's
    usage is logged like any other paid call, and a spent monthly cap
    refuses it. Applying still goes through start_resegmentation."""
    from services import translate_run_service   # lazy: it imports many services
    drama = _require_drama(drama_id)
    language, script, segments = _reseg_inputs(drama_id, drama)
    engine_name, eng = _build_engine(drama, engine, model)
    translate_run_service.refuse_when_cap_spent(engine_name,
                                                settings_service.get_gemini_free_tier())
    lines = db.load_line_objects(drama_id)
    job_id = f"{RESEGMENT_PREVIEW_JOB_PREFIX}{drama_id}"
    if background_jobs.is_running(job_id):
        raise ConflictError("A re-segmentation preview is already running for this drama.")
    with _llm_previews_lock:
        _llm_previews.pop(drama_id, None)
    desc = f"Re-segmentation preview (drama #{drama_id})"
    if engine_name == "ollama":
        started = background_jobs.start_process_job(
            job_id, resegment.resegment_subprocess_worker,
            args=(lines, language, eng, segments, script), gpu_touching=True, description=desc,
            on_done=_make_preview_on_done(drama_id, lines, language, engine_name, eng))
    else:
        started = background_jobs.start_job(
            job_id, _run_llm_preview_job, job_id, drama_id, lines, language, eng, engine_name,
            segments, script, description=desc)
    if not started:
        raise ConflictError("A re-segmentation preview is already running for this drama.")
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
        raise NotFoundError("No LLM re-segmentation preview is ready for this drama.")
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
        current = db.load_line_objects(drama_id)
        if _line_fingerprint(current) != state["fingerprint"]:
            raise RuntimeError("This drama's lines changed since the preview -- nothing was "
                               "changed; run the preview again.")
        if confirm is not True and _needs_confirm(drama_id, current, state["language"]):
            raise RuntimeError("Re-segmenting would now clear translations, flags or notes on "
                               "the lines being split -- nothing was changed; apply again "
                               "with confirm=true.")
        new_lines = [dataclasses.replace(ln, merged_ids=list(ln.merged_ids))
                     for ln in state["lines"]]
        _commit(drama_id, current, new_lines, "before re-segment")
    with _llm_previews_lock:
        if _llm_previews.get(drama_id) is preview:
            _llm_previews.pop(drama_id, None)   # applied: it can't be applied twice
    background_jobs.set_result(job_id, {"changed": len(preview["changed"]),
                                        "line_count": len(new_lines)})


def _start_preview_apply(drama_id: int, expected_line_ids: list, confirm: bool) -> dict:
    with _llm_previews_lock:
        preview = _llm_previews.get(drama_id)
    if preview is None:
        raise NotFoundError("No LLM re-segmentation preview is ready for this drama -- run the "
                            "preview first.")
    _refuse_if_job_running(drama_id)
    lines = db.load_line_objects(drama_id)
    _check_expected(lines, expected_line_ids)
    if _line_fingerprint(lines) != preview["_apply"]["fingerprint"]:
        raise ConflictError("This drama's lines changed since the preview -- run the preview "
                            "again.")
    # Recounted on the lines as they are now, not the preview's needs_confirm:
    # a translation note added since isn't in the fingerprint.
    if confirm is not True and _needs_confirm(drama_id, lines, preview["_apply"]["language"]):
        raise InvalidInputError(_CONFIRM_NEEDED)
    job_id = f"{RESEGMENT_JOB_PREFIX}{drama_id}"
    started = background_jobs.start_job(
        job_id, _apply_llm_preview_job, job_id, drama_id, preview, confirm is True,
        description=f"Applying re-segmentation preview (drama #{drama_id})")
    if not started:
        raise ConflictError("A re-segmentation is already running for this drama.")
    return {"job_id": job_id, "drama_id": drama_id}


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------

def list_versions(drama_id: int) -> list:
    """Snapshot metadata, newest first (Slice 48's read)."""
    return list_line_history(drama_id)


def restore_version(drama_id: int, history_id: int, expected_line_ids) -> dict:
    """Restores a history snapshot over the current lines, after taking a
    "before restore" snapshot (so the restore itself can be undone). Lines
    are matched by permanent id (core.restore_saved_lines / adopt_ids,
    Step 25l): a line whose id still exists keeps its notes/emotion; flag,
    flag note and SFX mark come from the snapshot (or, for a snapshot saved
    before those were recorded, stay as the line has them now); a line
    merged/deleted since gets a fresh id and nothing is reattached by
    position. Refused while a job
    runs on the drama. No confirm field: the tab's Restore has none."""
    get_line_history_snapshot(drama_id, history_id)  # 404 unless it's this drama's
    # the raw rows: the read above returns dub_filename as a bare basename
    rows = db.get_line_history_snapshot(history_id)
    if rows is None:
        raise NotFoundError(f"No history snapshot {history_id} for drama {drama_id}.")

    def build(lines):
        restored = core_module.restore_saved_lines(rows, lines)
        return restored, []
    out = _structural_write(drama_id, expected_line_ids, "before restore", build)
    return {"history_id": history_id, "line_ids": out["line_ids"]}
