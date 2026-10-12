"""Retry the lines the last translate run failed on.

The run keeps its failed batches in dramas.last_translate_errors by line
index. A later "Re-translate existing" run leaves those lines holding the
earlier engine's English, so the Translate button has nothing to do for them
("All lines have English"). summarize() tells the notice which failed lines
are empty and which kept older English; start_retry() translates exactly those
lines (forced, for them only) through the normal run, so the engine, cap, usage
log and undo snapshot are the ones every run has; prune_resolved() drops the
lines a run did translate from the stored record.
"""
import json

import db
from services import translate_run_service
from services.service_errors import UnsupportedOperationError

# Words the run's own error text uses when a reply was cut off or came back
# short: a smaller batch fits the reply.
_CUT_OFF_MARKERS = ("cut off", "got no translation", "translation(s) for")


def _failed_indexes(drama: dict) -> set:
    errors = translate_run_service._parse_errors(drama.get("last_translate_errors"))
    found = set()
    for entry in errors if isinstance(errors, list) else []:
        for i in (entry.get("lines") if isinstance(entry, dict) else None) or []:
            if isinstance(i, int) and not isinstance(i, bool) and i >= 0:
                found.add(i)
    return found


def _reply_was_cut_off(drama: dict) -> bool:
    errors = translate_run_service._parse_errors(drama.get("last_translate_errors"))
    return any(isinstance(e, dict) and any(m in str(e.get("error") or "") for m in _CUT_OFF_MARKERS)
               for e in errors if isinstance(errors, list))


def _failed_rows(drama_id: int, drama: dict) -> list:
    wanted = _failed_indexes(drama)
    return [r for r in db.load_lines(drama_id) if r["idx"] in wanted and (r.get("zh") or "").strip()]


def summarize(drama_id: int) -> dict:
    """What the failed-batches notice shows, as 1-based line numbers: the failed
    lines still empty, and those that kept an earlier translation."""
    rows = _failed_rows(drama_id, translate_run_service.require_drama(drama_id))
    return {"failed_untranslated_numbers": sorted(r["idx"] + 1 for r in rows if not (r.get("en") or "").strip()),
            "failed_kept_earlier_numbers": sorted(r["idx"] + 1 for r in rows if (r.get("en") or "").strip())}


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
    """Starts a run over exactly the failed lines, forced for them only. A line
    edited since the page loaded keeps its edit (own_lines_only). UnsupportedOperationError
    when nothing is left to retry; otherwise start_translate_run's own errors."""
    drama = translate_run_service.require_drama(drama_id)
    rows = _failed_rows(drama_id, drama)
    if not rows:
        raise UnsupportedOperationError("There are no failed lines to retry.")
    engine_name = engine_name or drama.get("translation_engine")
    return translate_run_service.start_translate_run(
        drama_id, engine_name=engine_name, model=model, force_retranslate=True,
        batch_size=retry_batch_size(drama, engine_name, model, batch_size),
        line_ids=[r["id"] for r in rows], own_lines_only=True,
        expected_en={r["id"]: r.get("en") or "" for r in rows}, **run_options)


def prune_resolved(drama_id: int, lines, target_ids, en_before: dict, errors) -> None:
    """After a line-scoped run: forget the stored failures of target lines it
    translated. A line counts as translated when its English changed and this
    run did not list it as failed (a cancelled or capped run leaves lines
    untouched)."""
    drama = db.get_drama(drama_id) or {}
    stored = translate_run_service._parse_errors(drama.get("last_translate_errors"))
    if not isinstance(stored, list) or not stored:
        return
    failed_now = {i for e in errors for i in e.get("lines", [])}
    done = {ln.idx for ln in lines if ln.id in target_ids and ln.idx not in failed_now
            and (ln.en or "").strip() and ln.en != en_before.get(ln.id)}
    if not done:
        return
    kept = []
    for entry in stored:
        if not isinstance(entry, dict) or not isinstance(entry.get("lines"), list) or not entry["lines"]:
            kept.append(entry)
            continue
        left = [i for i in entry["lines"] if i not in done]
        if left:
            kept.append({**entry, "lines": left})
    db.update_drama(drama_id, last_translate_errors=json.dumps(kept, ensure_ascii=False) if kept else None)
