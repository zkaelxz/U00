"""
bulk_apply.py -- applies finished bulk results (translation, flag,
consistency, emotion, translation notes) to a drama's CURRENT lines, by line
id. Reflect's stages are in bulk_reflect.py; which applier runs for a job is
decided in bulk_poll.py.
"""
import json
from typing import NamedTuple

from bulk_translate import BATCH_PRICE_FACTOR, zh_hash
import db
import emotion
import source_change
import translate_engines
import translation_guide as tguide
from translation_finish import mark_translated_if_complete, untranslated_line_count


class _ApplyContext(NamedTuple):
    row_by_id: dict
    ids_by_key: dict
    current: list
    cur_by_id: dict


def _load_apply_context(bulk_job_id: int, drama_id: int, with_current: bool = True) -> _ApplyContext:
    """The bulk job's submitted rows indexed by line id and request key,
    and (unless the stage reads lines itself) the drama's CURRENT lines --
    the state every applier compares the submission against."""
    row_by_id, ids_by_key = {}, {}
    for r in db.list_bulk_job_lines(bulk_job_id):
        row_by_id[r["line_id"]] = r
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    current = db.load_line_objects(drama_id) if with_current else []
    return _ApplyContext(row_by_id, ids_by_key, current, {ln.id: ln for ln in current})


def _log_generic_usage(job: dict, usage: dict, usage_kind: str):
    if usage and usage.get("input_tokens"):
        db.log_usage(
            job["drama_id"], job["engine"], job["model"], usage_kind,
            usage.get("input_tokens", 0), usage.get("output_tokens", 0),
            translate_engines.estimate_cost(
                job["model"], usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0))
            * BATCH_PRICE_FACTOR,
            cache_read_tokens=usage.get("cache_read_tokens", 0))


def _parse_strict(text: str, expected_ids: list) -> dict:
    """Only an id-keyed JSON object is accepted for a bulk result (as on
    the live path). Keys outside this request's own ids are ignored."""
    stripped = (text or "").strip()
    for fence in ("```json", "```"):
        stripped = stripped.replace(fence, "")
    return translate_engines.parse_id_keyed_json(stripped, expected_ids)


def _save_flags(job: dict, current: list, counts: dict, guard=()) -> set:
    """Compare-and-set flag save; the ids it left unwritten. A source-changed
    flag that didn't land isn't counted."""
    new = {ln.id for ln in current if ln.flag == "bulk_source_changed" != ln.orig["flag"]}
    left = db.save_lines(job["drama_id"], current, fields=("flag", "flag_note"),
                         only_if_unchanged=True, guard_fields=guard)
    counts["flagged_source_changed"] -= len(new & left)
    return left


def _save_applied_english(job: dict, current: list, counts: dict, label: str):
    did = job["drama_id"]
    if counts["applied"]:
        from services import reading_speed_dismissal_service as dismissals
        dismissals.flag_dense_lines(did, current)
    # Compare-and-set against the load: an edit saved since then is kept,
    # and nothing lands that was computed from text or timing changed since.
    kept = db.save_lines(did, [ln for ln in current if ln.en != ln.orig["en"]], fields=("en",),
                         only_if_unchanged=True, guard_fields=("zh",))
    _save_flags(job, current, counts, ("zh", "en", "start", "end"))
    counts["applied"] -= len(kept)
    counts["kept_your_edit"] += len(kept)
    if counts["applied"]:
        # From the database, not `current`: restoring the version must not
        # bring stale English back over an edit kept above.
        db.save_translation_version(did, db.load_line_objects(did), label=label,
                                    engine=job["engine"], model=job["model"] or "", make_active=True)
        # Applying SOME lines must not claim the whole drama is translated.
        _status = dict(translation_engine=job["engine"])
        if untranslated_line_count(job["drama_id"]) == 0:
            _status["status"] = "translated"
        db.update_drama(job["drama_id"], **_status)
    return kept


def apply_bulk_results(bulk_job_id: int, results) -> dict:
    """Applies (request_key, text, usage, error) results to the drama's
    CURRENT lines, by line id. Returns counts of what happened."""
    job = db.get_bulk_job(bulk_job_id)
    row_by_id, ids_by_key, current, cur_by_id = _load_apply_context(bulk_job_id, job["drama_id"])
    args = job.get("translate_args") or {}
    enforced = [t for t in (args.get("glossary_terms") or []) if t.get("enforce_exact")]
    substitute_terms = tguide.build_hard_term_substituter(enforced)
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "kept_your_edit": 0, "missing": 0, "failed_requests": 0, "unknown_requests": 0}

    for key, text, usage, error in results:
        if usage and usage.get("input_tokens"):
            db.log_usage(
                job["drama_id"], job["engine"], job["model"], "translate_bulk",
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                translate_engines.estimate_cost(
                    job["model"], usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                    usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0))
                * BATCH_PRICE_FACTOR,
                cache_read_tokens=usage.get("cache_read_tokens", 0))
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        request_ids = ids_by_key[key]
        if error or text is None:
            counts["failed_requests"] += 1
            counts["missing"] += len(request_ids)
            continue
        parsed = _parse_strict(text, request_ids)
        for lid in request_ids:
            translation = (parsed.get(str(lid)) or "").strip()
            if not translation:
                counts["missing"] += 1
                continue
            ln = cur_by_id.get(lid)
            if ln is None:
                counts["dropped_deleted"] += 1
                continue
            if zh_hash(ln.zh) != row_by_id[lid]["zh_hash"]:
                counts["flagged_source_changed"] += 1
                if not ln.flag:
                    ln.flag = "bulk_source_changed"
                    ln.flag_note = f"Bulk result not applied: {translation}"
                continue
            if (ln.en or "") != (row_by_id[lid]["en_at_submit"] or ""):
                counts["kept_your_edit"] += 1
                continue
            if enforced:
                translation = substitute_terms(translation)
            ln.en = translation
            source_change.clear_for_new_english(ln)
            counts["applied"] += 1

    _save_applied_english(job, current, counts, f"{job['engine']} bulk · {args.get('style_preset', '')}")
    # Nothing newly applied can still complete the title (lines typed meanwhile).
    mark_translated_if_complete(job["drama_id"])
    return counts


def _stale_line(ln, expected_hash: str) -> bool:
    """A line whose source has been deleted, or whose text no longer
    hashes to what it was at submission -- the drop-or-flag guard every
    kind below applies before touching anything, same as apply_bulk_results'
    own zh_hash check."""
    return ln is None or zh_hash(ln.zh) != expected_hash


def apply_flag_results(bulk_job_id: int, results) -> dict:
    """Applies review-queue flags (submit_bulk_flag) by line id, with the
    same drop-or-flag-on-source-changed handling as apply_bulk_results,
    plus its own "kept your edit" check: a line whose .flag/.flag_note the
    user already changed since submission (state_at_submit) is left
    alone rather than overwritten."""
    job = db.get_bulk_job(bulk_job_id)
    row_by_id, ids_by_key, current, cur_by_id = _load_apply_context(bulk_job_id, job["drama_id"])
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "kept_your_edit": 0, "failed_requests": 0, "unknown_requests": 0,
              "unparsable_entries": 0}
    mine = set()

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "flag_review_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        flagged = translate_engines.parse_json_array(text, 0)
        by_id, bad = translate_engines.parse_flag_entries(flagged)
        counts["unparsable_entries"] += bad
        for lid in ids_by_key[key]:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                if ln is not None:
                    counts["flagged_source_changed"] += 1
                    if not ln.flag:
                        ln.flag = "bulk_source_changed"
                        ln.flag_note = "Source text changed while bulk flagging was pending -- re-run it"
                else:
                    counts["dropped_deleted"] += 1
                continue
            state = json.loads(row["state_at_submit"]) if row.get("state_at_submit") else {}
            if (ln.flag, ln.flag_note or "") != (state.get("flag"), state.get("flag_note") or ""):
                counts["kept_your_edit"] += 1
                continue
            f = by_id.get(lid)
            if f is None or ln.flag in translate_engines.SYSTEM_FLAG_REASONS:
                continue  # the model didn't flag this line -- nothing to apply, not an error
            reason = (f.get("reason") if f.get("reason") in translate_engines.FLAG_REASONS
                     else "uncertain_translation")
            ln.flag = reason
            ln.flag_note = f.get("note", "")
            counts["applied"] += 1
            mine.add(lid)

    if counts["applied"] or counts["flagged_source_changed"]:
        # A review flag describes the English the model saw.
        kept = mine & _save_flags(job, current, counts, ("zh", "en"))
        counts["applied"] -= len(kept)
        counts["kept_your_edit"] += len(kept)
    return counts


def apply_consistency_results(bulk_job_id: int, results) -> dict:
    """Applies consistency-check issues (submit_bulk_consistency). An
    issue names a TERM, not a line (see build_consistency_prompt's own
    docstring), so there's no single line id to drop a stale issue by --
    instead, a whole WINDOW's issues are dropped together if any line in
    that window was edited or deleted since submission. Replaces the
    drama's consistency issues wholesale, same as a live check already
    does (db.save_consistency_issues) -- but only once at least one
    window's results were actually usable, so a bulk job that turned out
    entirely stale doesn't wipe out a still-valid earlier check."""
    job = db.get_bulk_job(bulk_job_id)
    row_by_id, ids_by_key, _, cur_by_id = _load_apply_context(bulk_job_id, job["drama_id"])
    counts = {"issues_found": 0, "dropped_windows": 0, "failed_requests": 0, "unknown_requests": 0}
    issues = []
    usable_windows = 0

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "consistency_check_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        window_ids = ids_by_key[key]
        if any(_stale_line(cur_by_id.get(lid), row_by_id[lid]["zh_hash"]) for lid in window_ids):
            counts["dropped_windows"] += 1
            continue
        usable_windows += 1
        window_issues = translate_engines.parse_json_array(text, 0)
        if isinstance(window_issues, list):
            issues.extend(i for i in window_issues if isinstance(i, dict) and i.get("term"))

    counts["issues_found"] = len(issues)
    if usable_windows:
        db.save_consistency_issues(job["drama_id"], issues)
    return counts


def apply_emotion_results(bulk_job_id: int, results) -> dict:
    """Applies emotion tags (submit_bulk_emotion) by line id, with the
    same drop-or-flag-on-source-changed handling as apply_bulk_results,
    plus its own "kept your edit" check against whatever tag the line
    already had at submission (db.save_emotions is itself a per-line
    upsert, so applying this incrementally never disturbs any OTHER
    line's tag, live-set or from a different bulk job)."""
    job = db.get_bulk_job(bulk_job_id)
    row_by_id, ids_by_key, current, cur_by_id = _load_apply_context(bulk_job_id, job["drama_id"])
    existing = db.load_emotions(job["drama_id"])
    existing_by_id = {ln.id: existing.get(ln.idx) for ln in current}
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "kept_your_edit": 0, "failed_requests": 0, "unknown_requests": 0}
    to_save = {}
    changed_flag = False

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "emotion_detect_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        tagged = emotion.parse_emotion_tags(text)
        for lid in ids_by_key[key]:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                if ln is not None:
                    counts["flagged_source_changed"] += 1
                    if not ln.flag:
                        ln.flag = "bulk_source_changed"
                        ln.flag_note = "Source text changed while bulk emotion tagging was pending"
                        changed_flag = True
                else:
                    counts["dropped_deleted"] += 1
                continue
            state = json.loads(row["state_at_submit"]) if row.get("state_at_submit") else None
            if state != existing_by_id.get(lid):
                counts["kept_your_edit"] += 1
                continue
            tag = tagged.get(lid)
            if tag is None:
                continue
            to_save[lid] = tag
            counts["applied"] += 1

    if to_save:
        # db.save_emotions expects its emotion_map keyed by line_idx (with
        # id_by_idx resolving idx -> id), not by line_id directly -- to_save
        # is keyed by line_id, so it's re-keyed by each line's CURRENT idx
        # here rather than passing line ids where idx values are expected.
        by_idx = {cur_by_id[lid].idx: tag for lid, tag in to_save.items()}
        id_by_idx = {cur_by_id[lid].idx: lid for lid in to_save}
        db.save_emotions(job["drama_id"], by_idx, id_by_idx=id_by_idx)
    if changed_flag:
        _save_flags(job, current, counts)
    return counts


def apply_notes_results(bulk_job_id: int, results) -> dict:
    """Applies translation notes (submit_bulk_translation_notes) by line
    id. No "kept your edit" check -- notes are additive
    (db.save_translation_notes upserts per drama+line+term), never
    overwriting a whole field the way flag/emotion do -- but a line whose
    source changed or was deleted since submission is still dropped, same
    as every other kind, since a note about since-changed text would be
    about the wrong thing."""
    job = db.get_bulk_job(bulk_job_id)
    row_by_id, ids_by_key, _, cur_by_id = _load_apply_context(bulk_job_id, job["drama_id"])
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "failed_requests": 0, "unknown_requests": 0}
    notes = []

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "translation_notes_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        found = translate_engines.parse_json_array(text, 0)
        by_id = {}
        if isinstance(found, list):
            for n in found:
                if isinstance(n, dict) and n.get("note") and n.get("line_idx") is not None:
                    try:
                        by_id.setdefault(int(n["line_idx"]), []).append(n)
                    except (TypeError, ValueError):
                        continue
        for lid in ids_by_key[key]:
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row_by_id[lid]["zh_hash"]):
                counts["dropped_deleted" if ln is None else "flagged_source_changed"] += 1
                continue
            for n in by_id.get(lid, []):
                n["line_id"] = lid
                n["note_type"] = n.get("note_type") if n.get("note_type") in tguide.NOTE_TYPES else "cultural"
                notes.append(n)
                counts["applied"] += 1

    if notes:
        db.save_translation_notes(job["drama_id"], notes)
    return counts
