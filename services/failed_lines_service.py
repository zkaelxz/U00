"""Retry the lines the last translate run failed on.

The run keeps its failed batches in dramas.last_translate_errors, each entry
naming its lines by permanent id ("line_ids") as well as by index. Only the ids
are used here: a merge or split renumbers indexes, so an index would point the
retry (which replaces English) at the wrong lines. A record from before ids
were kept still shows in the notice but offers no Retry.

A later "Re-translate existing" run leaves failed lines holding the earlier
engine's English, so the Translate button has nothing to do for them ("All
lines have English"). summarize() tells the notice which failed lines are empty
and which kept older English; start_retry() translates exactly those lines
(forced, for them only) through the normal run, so the engine, cap, usage log
and undo snapshot are the ones every run has; prune_resolved() drops the lines
a run did translate from the stored record.
"""
import json

import db
from services import translate_run_service
from services.service_errors import UnsupportedOperationError

# Words the run's own error text uses when a reply was cut off or came back
# short: a smaller batch fits the reply.
_CUT_OFF_MARKERS = ("cut off", "got no translation", "translation(s) for")


def _entries(drama: dict) -> list:
    errors = translate_run_service._parse_errors(drama.get("last_translate_errors"))
    return [e for e in errors if isinstance(e, dict)] if isinstance(errors, list) else []


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _failed_ids(drama: dict) -> set:
    return {i for e in _entries(drama) for i in (e.get("line_ids") or []) if _is_id(i)}


def _reply_was_cut_off(drama: dict) -> bool:
    return any(any(m in str(e.get("error") or "") for m in _CUT_OFF_MARKERS) for e in _entries(drama))


def _failed_rows(drama_id: int, drama: dict) -> list:
    wanted = _failed_ids(drama)
    return [r for r in db.load_lines(drama_id) if r["id"] in wanted and (r.get("zh") or "").strip()]


def summarize(drama_id: int) -> dict:
    """What the failed-batches notice shows: 1-based current line numbers of the
    failed lines still empty and of those that kept an earlier translation, and
    whether Retry is possible (the record names lines by id)."""
    rows = _failed_rows(drama_id, translate_run_service.require_drama(drama_id))
    return {"failed_untranslated_numbers": sorted(r["idx"] + 1 for r in rows if not (r.get("en") or "").strip()),
            "failed_kept_earlier_numbers": sorted(r["idx"] + 1 for r in rows if (r.get("en") or "").strip()),
            "failed_retryable": bool(rows)}


def retry_batch_size(drama: dict, engine_name, model, batch_size):
    """The form's batch size, halved when the failed reply was cut off."""
    size = batch_size
    if size is None:
        is_novel = drama.get("content_mode") == "novel_narration"
        size = translate_run_service.get_translate_config_defaults(
            is_novel, engine_name, model)["batch_size"]
    return max(1, size // 2) if _reply_was_cut_off(drama) else size


def start_retry(drama_id: int, engine_name: str = None, model: str = None,
                batch_size: int = None, **run_options) -> dict:
    """Starts a run over exactly the lines the record names by id, forced for
    them only. expected_en only closes the instant between reading the lines
    here and start_translate_run reading them again: a line edited in that gap
    is dropped. UnsupportedOperationError when nothing is left to retry or the
    record predates line ids; otherwise start_translate_run's own errors."""
    drama = translate_run_service.require_drama(drama_id)
    rows = _failed_rows(drama_id, drama)
    if not rows:
        raise UnsupportedOperationError(
            "The record of the failed lines is from an older version and does not name them, "
            "or they are gone. Use Translate again for the empty lines."
            if _entries(drama) else "There are no failed lines to retry.")
    engine_name = engine_name or drama.get("translation_engine")
    return translate_run_service.start_translate_run(
        drama_id, engine_name=engine_name, model=model, force_retranslate=True,
        batch_size=retry_batch_size(drama, engine_name, model, batch_size),
        line_ids=[r["id"] for r in rows], own_lines_only=True,
        expected_en={r["id"]: r.get("en") or "" for r in rows}, **run_options)


def prune_resolved(drama_id: int, lines, target_ids, en_before: dict, errors) -> None:
    """After a line-scoped run: forget the stored failures of target lines it
    translated, by id. A line counts as translated when its English changed and
    this run did not list it as failed (a cancelled or capped run leaves lines
    untouched). Entries without ids are left as they are. Held under the lock
    the dismiss route takes, so the two can't overwrite each other."""
    failed_now = {i for e in errors for i in (e.get("line_ids") or [])}
    done = {ln.id for ln in lines if ln.id in target_ids and ln.id not in failed_now
            and (ln.en or "").strip() and ln.en != en_before.get(ln.id)}
    if not done:
        return
    with translate_run_service.ERRORS_LOCK:
        stored = translate_run_service._parse_errors((db.get_drama(drama_id) or {}).get("last_translate_errors"))
        if not isinstance(stored, list) or not stored:
            return
        kept = []
        for entry in stored:
            ids = entry.get("line_ids") if isinstance(entry, dict) else None
            if not isinstance(ids, list) or not ids:
                kept.append(entry)
                continue
            keep = [n for n, i in enumerate(ids) if i not in done]
            if keep:
                idx = entry.get("lines")
                kept.append({**entry, "line_ids": [ids[n] for n in keep],
                             "lines": [idx[n] for n in keep] if isinstance(idx, list) and len(idx) == len(ids)
                             else idx})
        db.update_drama(drama_id, last_translate_errors=json.dumps(kept, ensure_ascii=False) if kept else None)
