"""
services/restructure_service.py -- STRUCTURAL line
changes for one drama (add, delete, merge, split, re-segmentation) and
Version-history restore.

Correctness rules:
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
re-reading the id set immediately before `save_lines`. Another process
(e.g. the CLI) can still full-sync between that re-check and the save; a
failure between snapshot and save leaves only an extra snapshot.

No FastAPI import: plain dicts in and out. Messages never echo
line text, keys or paths.
"""
import contextlib
import dataclasses
import math
import threading
from typing import Optional

import background_jobs
import core as core_module
import db
import diarize
import raw_transcript
import resegment
import subtitle_formats
import translate_engines
from services import (diarization_service, drama_service, settings_service, transcribe_service,
                      translate_service)
from services.review_lines_service import line_dict
from services.review_records_service import get_line_history_snapshot
from services.service_errors import (
    ConflictError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    ServiceError,
    UnsupportedOperationError,
)

MAX_LINE_TEXT_CHARS = 2000
# Lines already past the cap (merged before it existed) are not re-split by the
# rules presets, whose quote and abbreviation checks cost more per character.
RESPLIT_MAX_CHARS = 10 * MAX_LINE_TEXT_CHARS
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


@contextlib.contextmanager
def exclusive_write(drama_id: int):
    """The structural-write guard (drama exists, no job running, drama lock
    held) for a writer that saves only some fields and so can't use
    structural_write's full sync."""
    _require_drama(drama_id)
    _refuse_if_job_running(drama_id)
    with _drama_lock(drama_id):
        yield


def structural_write(drama_id: int, expected_line_ids, label: str, build,
                     with_words: bool = False):
    """load fresh -> check ids -> build(current) -> snapshot -> save, under
    the drama lock. build returns (new_lines, result_line_objects).
    with_words loads the lines' stored word timings for a build that cuts or joins text."""
    _require_drama(drama_id)
    expected_line_ids = _id_list("expected_line_ids", expected_line_ids)
    _refuse_if_job_running(drama_id)
    with _drama_lock(drama_id):
        current = db.load_line_objects(drama_id, with_words=with_words)
        _check_expected(current, expected_line_ids)
        # build works on copies so the snapshot sees the untouched lines
        work = [dataclasses.replace(ln, orig=dict(ln.orig), merged_ids=[]) for ln in current]
        new_lines, touched = build(work)
        _commit(drama_id, current, new_lines, label)
    return {"line_ids": [ln.id for ln in new_lines],
            "lines": [line_dict(ln) for ln in touched]}


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
    return structural_write(drama_id, expected_line_ids, "before add line", build)


def delete_line(drama_id: int, line_id: int, expected_line_ids, confirm: bool = False) -> dict:
    """Deletes one line (its notes and emotion tag with it). Needs
    confirm=True, like the other destructive deletes."""
    if confirm is not True:
        raise InvalidInputError("Deleting a line needs confirm=true.")

    def build(lines):
        i = _index_of(lines, line_id)
        return lines[:i] + lines[i + 1:], []
    return structural_write(drama_id, expected_line_ids, "before delete line", build)


def merge_lines(drama_id: int, line_ids, expected_line_ids) -> dict:
    """Merges 2+ ADJACENT lines (given in order) into the first: text joined
    as core.merge_adjacent_short_lines joins it, end = last line's end, the
    first line keeps its id/speaker; its flag, else the first merged line's
    flag, is kept. Its lang stays only when every merged line shares it. The others' notes/emotions move onto it (the first
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
        # Joined only when every line's words are valid and in time order; else dropped.
        words = core_module.line_words(head)
        for ln in rest:
            more = core_module.line_words(ln)
            words = (words + more if words and more and more[0]["start"] >= words[-1]["start"]
                     else None)
        for ln in rest:
            head.zh = head.zh.rstrip() + ln.zh.strip()
            head.en = (head.en.rstrip() + " " + ln.en.strip()).strip()
            if len(head.zh) > MAX_LINE_TEXT_CHARS or len(head.en) > MAX_LINE_TEXT_CHARS:
                raise InvalidInputError(
                    f"The merged line would pass {MAX_LINE_TEXT_CHARS} characters.")
            if not head.flag and ln.flag:
                head.flag, head.flag_note = ln.flag, ln.flag_note
            head.merged_ids = list(head.merged_ids) + [ln.id]
            if ln.lang != head.lang:
                head.lang = None
        head.end = max(head.end, rest[-1].end)
        head.word_timings = core_module.encode_line_words(head.zh, words) if words else None
        return lines[:first + 1] + lines[first + len(line_ids):], [head]
    return structural_write(drama_id, expected_line_ids, "before merge", build, with_words=True)


def split_line(drama_id: int, line_id: int, expected_line_ids, *, at_char: int,
               expected_zh: str, at_time=None, en_at_char: Optional[int] = None) -> dict:
    """Splits one line's source text at character offset `at_char`. The
    first piece keeps the line's id (so its flag, notes and emotion stay
    on it); the second is a new line with the same speaker/sfx/lang and no
    flag. Its translation stays whole on the first piece unless
    `en_at_char` splits it too. The cut time is `at_time` (strictly inside
    the line), else the second piece's first word start when the line has
    valid stored word timings and the cut falls between words, else
    proportional to piece length. `expected_zh` must equal
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
        index = core_module.line_word_index(ln)
        words = ([core_module.span_words(index, 0, at_char, pieces[0]),
                  core_module.span_words(index, at_char, len(ln.zh), pieces[1])]
                 if index is not None else [None, None])
        second = core_module.Line(idx=0, start=cut, end=ln.end, zh=pieces[1], en=en_second,
                                  speaker=ln.speaker, speaker_manual=ln.speaker_manual,
                                  sfx=ln.sfx, lang=ln.lang, word_timings=words[1])
        ln.zh, ln.en, ln.end, ln.word_timings = pieces[0], en_first, cut, words[0]
        return lines[:i + 1] + [second] + lines[i + 1:], [ln, second]
    return structural_write(drama_id, expected_line_ids, "before split", build, with_words=True)


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


def _needs_confirm(drama_id: int, lines, language: str) -> bool:
    """True when any line long enough to be split carries a translation,
    flag or translation note (all lost on a split)."""
    need = _affected_counts(drama_id, lines, _candidate_ids(lines, language))
    return bool(need["translated"] or need["flagged"] or need["notes"])


def _reseg_inputs(drama_id: int, drama: dict):
    language = drama.get("source_language") or "zh"
    raw = raw_transcript.load_latest(db.drama_dir(drama_id))
    return (language, drama.get("chinese_script") or "simplified", (raw or {}).get("segments"),
            transcribe_service.stored_min_pause_sec(drama))


def preview_resegmentation(drama_id: int) -> dict:
    """Read-only, rules only (no LLM call, nothing written). `needs_confirm`
    is True when any line long enough to be split carries a translation,
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
            "needs_confirm": bool(need["translated"] or need["flagged"] or need["notes"])}


def _apply_resegmented(drama_id: int, new_lines, source_ids: list) -> dict:
    """The job's write step: same guard as Apply."""
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
                       segments, script, min_pause):
    usage = _usage_logger(drama_id, engine_name, engine) if engine is not None else None
    new_lines, changed = resegment.resegment_lines(lines, language, engine=engine,
                                                   segments=segments, chinese_script=script,
                                                   usage_cb=usage, min_pause=min_pause)
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
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    if engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't suggest split points.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
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
    if confirm is not True and _needs_confirm(drama_id, lines, language):
        raise InvalidInputError(_CONFIRM_NEEDED)
    job_id = f"{RESEGMENT_JOB_PREFIX}{drama_id}"
    desc = f"Re-segmenting (drama #{drama_id})"
    if engine_name == "ollama":
        started = background_jobs.start_process_job(
            job_id, resegment.resegment_subprocess_worker,
            args=(lines, language, eng, segments, script, min_pause), gpu_touching=True,
            description=desc, on_done=_make_on_done(drama_id, expected_line_ids, engine_name, eng))
    else:
        started = background_jobs.start_job(
            job_id, _run_resegment_job, job_id, drama_id, lines, expected_line_ids, language,
            eng, engine_name, segments, script, min_pause, description=desc)
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
                         script, min_pause):
    new_lines, changed = resegment.resegment_lines(
        [dataclasses.replace(ln) for ln in lines], language, engine=engine, segments=segments,
        chinese_script=script, usage_cb=_usage_logger(drama_id, engine_name, engine),
        min_pause=min_pause)
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
    language, script, segments, min_pause = _reseg_inputs(drama_id, drama)
    engine_name, eng = _build_engine(drama, engine, model)
    translate_run_service.refuse_when_cap_spent(engine_name,
                                                settings_service.get_gemini_free_tier())
    lines = db.load_line_objects(drama_id, with_words=True)
    job_id = f"{RESEGMENT_PREVIEW_JOB_PREFIX}{drama_id}"
    if background_jobs.is_running(job_id):
        raise ConflictError("A re-segmentation preview is already running for this drama.")
    with _llm_previews_lock:
        _llm_previews.pop(drama_id, None)
    desc = f"Re-segmentation preview (drama #{drama_id})"
    if engine_name == "ollama":
        started = background_jobs.start_process_job(
            job_id, resegment.resegment_subprocess_worker,
            args=(lines, language, eng, segments, script, min_pause), gpu_touching=True,
            description=desc, on_done=_make_preview_on_done(drama_id, lines, language, engine_name, eng))
    else:
        started = background_jobs.start_job(
            job_id, _run_llm_preview_job, job_id, drama_id, lines, language, eng, engine_name,
            segments, script, min_pause, description=desc)
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

def restore_version(drama_id: int, history_id: int, expected_line_ids) -> dict:
    """Restores a history snapshot over the current lines, after taking a
    "before restore" snapshot (so the restore itself can be undone). Lines
    are matched by permanent id (core.restore_saved_lines / adopt_ids):
    a line whose id still exists keeps its notes/emotion; flag,
    flag note and SFX mark come from the snapshot (or, for a snapshot saved
    before those were recorded, stay as the line has them now); a line
    merged/deleted since gets a fresh id and nothing is reattached by
    position. Refused while a job
    runs on the drama. No confirm field."""
    get_line_history_snapshot(drama_id, history_id)  # 404 unless it's this drama's
    # the raw rows: the read above returns dub_filename as a bare basename
    rows = db.get_line_history_snapshot(history_id)
    if rows is None:
        raise NotFoundError(f"No history snapshot {history_id} for drama {drama_id}.")

    def build(lines):
        restored = core_module.restore_saved_lines(rows, lines)
        return restored, []
    out = structural_write(drama_id, expected_line_ids, "before restore", build)
    return {"history_id": history_id, "line_ids": out["line_ids"]}


# ---------------------------------------------------------------------------
# Re-split over-long lines (no new transcription, no new speaker detection)
# ---------------------------------------------------------------------------

RESPLIT_JOB_PREFIX = "resplit_"  # in background_jobs.DRAMA_JOB_PREFIXES
_RESPLIT_CONFIRM = ("Some long lines already have a translation. A split piece can't inherit "
                    "it, so those lines would lose their English -- pass confirm=true.")


RESPLIT_SENSITIVITIES = {"normal": "Normal", "more": "More", "sentence": "Sentence by sentence"}
_RESPLIT_NEXT = {"normal": 'Try "More" or "Sentence by sentence".',
                 "more": 'Try "Sentence by sentence".',
                 "sentence": "Try a shorter duration cap."}


@dataclasses.dataclass(frozen=True)
class _Resplit:
    """What to cut: the sensitivity preset, an optional duration cap and the
    title language lines fall back to."""
    language: str = "zh"
    sensitivity: str = "normal"
    max_seconds: Optional[float] = None
    min_pause: float = core_module.MIN_WORD_GAP_SECONDS

    @property
    def label(self) -> str:
        return RESPLIT_SENSITIVITIES[self.sensitivity] + (
            f", {self.max_seconds:g} s cap" if self.max_seconds else "")

    def rules(self, ln) -> Optional[core_module.SplitRules]:
        """None is today's fixed 8 s / 40 CJK characters. The cap replaces the
        8 s limit rather than adding to it: a cap above 8 would otherwise do nothing."""
        if self.sensitivity == "normal" and not self.max_seconds:
            return None
        seconds = self.max_seconds or core_module.SPLIT_MAX_SECONDS
        if self.sensitivity == "normal":
            return core_module.SplitRules(seconds, core_module.SPLIT_MAX_CJK_CHARS, count_latin=False)
        if self.sensitivity == "more":
            return core_module.SplitRules(
                seconds, subtitle_formats.line_char_limit(ln.lang or self.language))
        return core_module.SplitRules(self.max_seconds, None, per_sentence=True)

    def too_long(self, ln) -> bool:
        return len(ln.zh) > RESPLIT_MAX_CHARS and self.rules(ln) is not None

    def split(self, ln) -> list:
        """Pieces cut by split_long_segments; with the line's valid stored words
        it cuts at real pauses with real times and hands each piece its words."""
        seg = {"start": ln.start, "end": ln.end, "text": ln.zh}
        if self.too_long(ln):
            return [seg]
        words = core_module.line_words(ln)
        if not words:
            return core_module.split_long_segments([seg], rules=self.rules(ln),
                                                   min_pause=self.min_pause)
        pieces = core_module.split_long_segments([{**seg, "words": words}], rules=self.rules(ln),
                                                 min_pause=self.min_pause)
        # Word times tighten a piece to its speech, but the line's outer edges
        # may have been re-timed on purpose (by hand or a re-time run); the
        # Review split and the AI re-split keep them, so this does too.
        if len(pieces) >= 2:
            pieces[0] = {**pieces[0], "start": ln.start}
            pieces[-1] = {**pieces[-1], "end": ln.end}
        return pieces


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
    """Why a run cut nothing: lines over the limits with nowhere to cut are
    not the same problem as no line being over them."""
    stuck = 0
    for ln in _resplit_candidates(lines):
        if cfg.too_long(ln):
            stuck += 1
            continue
        rules = cfg.rules(ln) or core_module.SplitRules(max_chars=core_module.SPLIT_MAX_CJK_CHARS,
                                                         count_latin=False)
        stuck += core_module.exceeds_limits({"start": ln.start, "end": ln.end, "text": ln.zh}, rules)
    if stuck:
        return (f"{stuck} line{'s' if stuck != 1 else ''} over the limits at {cfg.label} "
                "sensitivity, but none has a sentence or comma break to cut at.")
    return f"No line is over the limits at {cfg.label} sensitivity. {_RESPLIT_NEXT[cfg.sensitivity]}"


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
            raise ConflictError("Another job started on this drama while the lines were being "
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
            ln.word_timings = core_module.encode_line_words(first["text"], first.get("words"))
            if first.get("flag"):
                ln.flag, ln.flag_note = first["flag"], first["flag_note"]
            new_pieces = [core_module.Line(idx=0, start=p["start"], end=p["end"], zh=p["text"],
                                           speaker=ln.speaker, speaker_manual=ln.speaker_manual,
                                           sfx=ln.sfx, lang=ln.lang, flag=p.get("flag"),
                                           flag_note=p.get("flag_note", ""),
                                           word_timings=core_module.encode_line_words(
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
        _commit(drama_id, current, new_lines, "before re-split")
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
    return {"split_lines": split, "line_count": len(new_lines), "lines_before": len(current),
            "timing": timing, "aligned_lines": aligned, "cleared_translations": cleared,
            "speakers_reassigned": reassigned, "note": note}


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
            except (ImportError, core_module.ModelDownloadError) as exc:
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
        core_module.release_gpu_models()
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
                       confirm: bool = False, sensitivity: str = "normal",
                       max_seconds: Optional[float] = None, dry_run: bool = False) -> dict:
    """Cuts the drama's over-long lines at sentence/comma boundaries in place --
    no transcription, no speaker detection. The first piece keeps the line's id
    and flag; the pieces take the parent's speaker, then saved diarization
    turns (if any) relabel the split lines only, manual speakers kept.
    Translations can't be shared across pieces: a long line that has one needs
    confirm=true, and its pieces start empty.

    sensitivity: "normal" (the limits of a fresh transcription: 8 s / 40 CJK
    characters), "more" (the per-language subtitle line length, in each line's
    own language) or "sentence" (every sentence end). max_seconds replaces the
    preset's duration limit. Pieces under 0.8 s or 4 CJK characters / 2 words
    are folded into a neighbour in "more" and "sentence".

    dry_run only counts: {dry_run, split_lines, pieces, lines_before, line_count,
    cleared_translations, note}, no write, no confirm, allowed while a job runs.

    Timing is proportional to text length (instant, returned as the summary
    {split_lines, lines_before, line_count, timing, aligned_lines,
    cleared_translations, speakers_reassigned, note}), or with
    align_to_audio a `resplit_<id>` job that uses the Qwen3 forced aligner on
    the stored audio and returns {"job_id", "drama_id"}; its result carries
    the same summary, and falls back to proportional with a note when the
    aligner or audio is missing. History snapshot "before re-split" first.
    Refused (409) while any job runs on the drama."""
    drama = _require_drama(drama_id)
    expected_line_ids = _id_list("expected_line_ids", expected_line_ids)
    if sensitivity not in RESPLIT_SENSITIVITIES:
        raise InvalidInputError(f"sensitivity must be one of {', '.join(RESPLIT_SENSITIVITIES)}.")
    if max_seconds is not None and not 2 <= max_seconds <= 120:
        raise InvalidInputError("max_seconds must be between 2 and 120.")
    cfg = _Resplit(drama.get("source_language") or "zh", sensitivity, max_seconds,
                   transcribe_service.stored_min_pause_sec(drama))
    if not dry_run:
        _refuse_if_job_running(drama_id)
    lines = db.load_line_objects(drama_id, with_words=True)
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
            "This drama has no stored audio, so the lines were split with estimated timing.",
            _resplit_snapshot(lines, plan), cfg)
    job_id = f"{RESPLIT_JOB_PREFIX}{drama_id}"
    started = background_jobs.start_job(
        job_id, _run_resplit_job, job_id, drama_id, expected_line_ids, confirm, audio_path,
        cfg.language, settings_service.get_use_gpu(), cfg,
        gpu_touching=True, description=f"Re-splitting lines (drama #{drama_id})")
    if not started:
        raise ConflictError("Lines are already being re-split for this drama.")
    return {"job_id": job_id, "drama_id": drama_id}
