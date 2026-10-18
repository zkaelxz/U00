"""
bulk_reflect.py -- Reflect mode as a bulk pipeline: submitting its first stage,
applying each stage's results and submitting the next stage.
"""
import uuid

from bulk_apply import (_log_generic_usage, _parse_strict, _save_applied_english, _save_flags,
                        _stale_line)
from bulk_providers import make_provider
from bulk_translate import (_character_names, _copy_english_lines, _require_batch_engine, _target_lines,
                            request_key, zh_hash)
from engine_backends.medium_prompts import kind_for
import db
import source_change
import translate_engines
import translation_guide as tguide
from translation_finish import mark_translated_if_complete


# ---------------------------------------------------------------------------
# Reflect mode, bulk -- the same faithfulness -> reflection ->
# expressiveness pipeline as translate_engines.reflect_translate_batch,
# but as three SEQUENTIAL bulk submissions instead of three in-process
# calls. Confirmed directly against both Claude's Message Batches and
# Gemini's Batch API: neither has a way to submit pass 2 once pass 1's
# own results are back, all within one batch -- there is no such thing
# as a batch that depends on another batch's output. So this submits
# stage "faithful" now (submit_reflect_pipeline); once ITS results are
# in, apply_reflect_stage_results persists them and auto-submits stage
# "reflect", using the SAME pipeline_id; once THAT stage's results are
# in, it auto-submits stage "expressive", which is the only stage that
# actually writes anything to a line's .en (the other two only ever
# populate bulk_job_lines.result_text for the NEXT stage to read).
#
# A real, deliberate simplification versus the live path: every stage's
# prompt here omits recent_context/upcoming_lines (the surrounding-line
# steering text build_bulk_requests still gives plain bulk translation).
# Recomputing that accurately across three submissions that can each take
# up to 24h, on a drama whose OTHER lines may themselves be translated or
# edited in between, isn't worth the complexity for what's already a
# cheaper/slower tradeoff mode -- and it's genuinely supplementary
# context, not the actual cross-pass information a Reflect pass depends
# on (the faithfulness draft and reflection critique THEMSELVES, which
# this pipeline carries through exactly via bulk_job_lines.result_text,
# unaffected by this simplification).
# ---------------------------------------------------------------------------


def _reflect_prompt_parts(drama_id: int, translate_args: dict, engine, batch: list, names: dict):
    """(instructions, batch_ctx, source_by_id, speaker_by_id) for one batch,
    built the way reflect_translate_batch builds them, so a bulk stage sends
    the live prompt: novel-reference block, speaker names, and the she/her
    default's note and hidden 他/她."""
    args = translate_args or {}
    ctx = translate_engines.build_translation_context(
        engine, args.get("drama_meta") or db.get_drama(drama_id) or {},
        style_note=args.get("style_note", ""), novel_reference=args.get("novel_reference"),
        locale=args.get("locale", "en-US"), glossary_terms=args.get("glossary_terms"),
        style_guidelines=args.get("style_guidelines", ""))
    ids = [ln.id for ln in batch]
    ctx.update(speaker_labels=[names.get(ln.speaker) for ln in batch], line_ids=ids,
               batch_source_lines=[ln.zh for ln in batch])
    instructions, novel_block = translate_engines.build_stable_prompt(ctx)
    if novel_block:
        instructions = instructions + "\n\n" + novel_block
    lang = ctx["source_language"]
    sources = translate_engines.pronoun_neutral_texts(
        ctx, translate_engines.tagged_source_texts(batch, lang))
    return (instructions, translate_engines.pronoun_batch_note(ctx.get("style_guidelines"), lang),
            dict(zip(ids, sources)), dict(zip(ids, ctx["speaker_labels"])))


def _reflect_kind(drama_id: int, translate_args: dict) -> str:
    """The prompt kind (subtitle, novel, stream) the stage prompts are worded for."""
    return kind_for((translate_args or {}).get("drama_meta") or db.get_drama(drama_id) or {})


def sibling_stage_job(pipeline_id: str, stage: str, skip_failed: bool = False):
    """The newest bulk_jobs row from the same Reflect pipeline for a given
    stage, or None if it doesn't exist (yet, or ever -- e.g. every line
    dropped out before reaching it). skip_failed: a next-stage submit that
    failed holds no results and must not block a retry; a parent stage
    that ended failed still holds the drafts its children build on."""
    for job in db.list_bulk_jobs(pipeline_id=pipeline_id):
        if job.get("stage") == stage and not (skip_failed and job.get("status") == "failed"):
            return job
    return None


def submit_reflect_stage(drama_id: int, stage: str, provider, batches: list, engine_choice: str,
                         model: str, pipeline_id: str, translate_args: dict = None,
                         en_at_submit_by_id: dict = None, max_tokens: int = 4000) -> int:
    """batches: [(id_zh_pairs, prompt_text), ...], id_zh_pairs = [(line_id,
    zh), ...] -- zh is carried alongside each id purely so this stage's
    own bulk_job_lines rows record a real zh_hash to compare against
    later, without needing a whole Line object here. en_at_submit_by_id:
    only ever populated for stage="expressive" (see
    apply_reflect_stage_results) -- every earlier stage still records
    whatever value is given (harmless; unused until expressive's own
    "kept your edit" check)."""
    requests_, rows = [], []
    for bi, (id_zh_pairs, prompt_text) in enumerate(batches):
        key = request_key(drama_id, bi)
        requests_.append(provider.build_prompt_request(key, prompt_text, max_tokens=max_tokens))
        for lid, zh in id_zh_pairs:
            en_at_submit = (en_at_submit_by_id or {}).get(lid)
            rows.append((lid, key, zh_hash(zh), en_at_submit))
    if not requests_:
        raise ValueError("Nothing to process -- no eligible lines.")
    bulk_job_id = db.create_bulk_job(drama_id, engine_choice, model, "submitting", rows,
                                     translate_args=translate_args, kind="reflect", stage=stage,
                                     pipeline_id=pipeline_id)
    try:
        batch_id = provider.submit(requests_)
    except Exception as e:
        db.update_bulk_job(bulk_job_id, status="failed",
                           last_error=translate_engines.redact_secrets(str(e)))
        raise
    db.update_bulk_job(bulk_job_id, status="submitted", provider_batch_id=batch_id)
    return bulk_job_id


def submit_reflect_pipeline(drama_id: int, lines: list, engine, engine_choice: str,
                            translate_args: dict, provider=None, batch_size=None,
                            force_retranslate: bool = False) -> int:
    """Submits stage "faithful", the pipeline's own first bulk job.
    Returns its bulk_job_id -- the Bulk jobs panel shows this one first;
    stages "reflect" and "expressive" only exist once the prior stage's
    own results have actually come back (see apply_reflect_stage_results)."""
    provider = _require_batch_engine(provider or make_provider(engine_choice, engine), engine_choice)
    targets = _copy_english_lines(drama_id, _target_lines(lines, force_retranslate))
    if not targets:
        raise ValueError("Nothing to translate -- every line already has a translation.")
    batch_size = batch_size or translate_engines.default_batch_size(engine)
    pipeline_id = f"reflect_{drama_id}_{uuid.uuid4().hex[:12]}"
    names = _character_names(drama_id)
    batches = []
    for start in range(0, len(targets), batch_size):
        batch = targets[start:start + batch_size]
        ids = [ln.id for ln in batch]
        instructions, batch_ctx, source_by_id, speaker_by_id = _reflect_prompt_parts(
            drama_id, translate_args, engine, batch, names)
        prompt = translate_engines.build_reflect_faithful_prompt(
            instructions, batch_ctx, ids, source_by_id, speaker_by_id,
            _reflect_kind(drama_id, translate_args))
        batches.append(([(ln.id, ln.zh) for ln in batch], prompt))
    en_at_submit_by_id = {ln.id: ln.en or "" for ln in targets}
    return submit_reflect_stage(drama_id, "faithful", provider, batches, engine_choice,
                                getattr(engine, "model", ""), pipeline_id,
                                translate_args=translate_args, en_at_submit_by_id=en_at_submit_by_id)


def _drop_or_flag_stale(ln, counts: dict) -> bool:
    """Counts a stale line; True when it was newly flagged."""
    if ln is None:
        counts["dropped_deleted"] += 1
        return False
    counts["flagged_source_changed"] += 1
    if ln.flag:
        return False
    ln.flag = "bulk_source_changed"
    ln.flag_note = "Source text changed while a bulk Reflect job was pending"
    return True


def apply_reflect_stage_results(bulk_job_id: int, results) -> dict:
    """Dispatches to the right stage handler -- see the module's own
    Reflect-pipeline comment above for the three-stage shape. The first
    two stages only record their output; _advance_pipeline submits the
    next stage once this stage's results are safely recorded."""
    job = db.get_bulk_job(bulk_job_id)
    stage = job.get("stage")
    if stage == "faithful":
        return _apply_reflect_faithful(job, results)
    if stage == "reflect":
        return _apply_reflect_reflection(job, results)
    if stage == "expressive":
        return _apply_reflect_expressive(job, results)
    raise ValueError(f"Unknown Reflect stage: {stage}")


def _apply_reflect_faithful(job: dict, results) -> dict:
    job_lines = db.list_bulk_job_lines(job["id"])
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    counts = {"drafted": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "failed_requests": 0, "unknown_requests": 0}
    result_texts = {}
    changed_flag = False

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "reflect_faithful_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        expected_ids = [str(lid) for lid in ids_by_key[key]]
        draft_map = _parse_strict(text, expected_ids)
        for lid in ids_by_key[key]:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                changed_flag = _drop_or_flag_stale(ln, counts) or changed_flag
                continue
            draft = (draft_map.get(str(lid)) or "").strip()
            if not draft:
                continue  # no draft for this line -- it simply doesn't continue to the next stage
            result_texts[lid] = draft
            counts["drafted"] += 1

    if changed_flag:
        _save_flags(job, current, counts)
    db.set_bulk_job_line_result_texts(job["id"], result_texts)
    return counts


def _stage_chunks(job: dict, ids: list, engine):
    """(chunk_ids, zh_by_id, instructions, batch_ctx, source_by_id) per next-
    stage request, for the ids that still exist, in line order."""
    current = db.load_line_objects(job["drama_id"])
    wanted, names = set(ids), _character_names(job["drama_id"])
    alive = [ln for ln in current if ln.id in wanted]
    n = translate_engines.default_batch_size(engine)
    for start in range(0, len(alive), n):
        batch = alive[start:start + n]
        instructions, batch_ctx, source_by_id, _speakers = _reflect_prompt_parts(
            job["drama_id"], job.get("translate_args"), engine, batch, names)
        yield [ln.id for ln in batch], {ln.id: ln.zh for ln in batch}, instructions, batch_ctx, source_by_id


def _submit_next_stage(job: dict, stage: str, build_batches, ids: list, en_at_submit_by_id: dict,
                       provider, max_tokens: int):
    """None once submitted, else the (redacted) reason it wasn't. The
    prompts are built inside the try so a failure building them counts as a
    failed attempt (STAGE_SUBMIT_ATTEMPTS) instead of escaping the caller."""
    if provider is None:
        return f"{job['engine']} has no batch API"
    try:
        batches = build_batches()
        if not batches:
            return None
        submit_reflect_stage(job["drama_id"], stage, provider, batches, job["engine"],
                             job["model"], job["pipeline_id"], translate_args=job.get("translate_args"),
                             en_at_submit_by_id={lid: en_at_submit_by_id[lid] for lid in ids},
                             max_tokens=max_tokens)
        return None
    except Exception as exc:
        reason = translate_engines.redact_secrets(str(exc))
        # Only a provider.submit failure is recorded on the new job row by
        # submit_reflect_stage; failures before that (building requests,
        # creating the row) leave no trace, so log them.
        try:
            import applog
            applog.get_logger().warning(
                "reflect stage %s could not be submitted for drama %s: %s", stage, job["drama_id"],
                reason)
        except Exception:
            pass
        return reason


def _advance_to_reflection_stage(job: dict, ids: list, draft_by_id: dict, en_at_submit_by_id: dict,
                                 engine):
    provider = make_provider(job["engine"], engine)

    kind = _reflect_kind(job["drama_id"], job.get("translate_args"))

    def batches():
        return [([(lid, zh_by_id[lid]) for lid in chunk], translate_engines.build_reflect_reflection_prompt(
                    instructions, batch_ctx, chunk, source_by_id, {lid: draft_by_id[lid] for lid in chunk}, kind))
                for chunk, zh_by_id, instructions, batch_ctx, source_by_id in _stage_chunks(job, ids, engine)]
    return _submit_next_stage(job, "reflect", batches, ids, en_at_submit_by_id, provider, 2000)


def _apply_reflect_reflection(job: dict, results) -> dict:
    job_lines = db.list_bulk_job_lines(job["id"])
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    counts = {"critiqued": 0, "no_critique_needed": 0, "dropped_deleted": 0,
              "flagged_source_changed": 0, "failed_requests": 0, "unknown_requests": 0}
    result_texts = {}
    changed_flag = False

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "reflect_reflection_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        expected_ids = [str(lid) for lid in ids_by_key[key]]
        critique_map = _parse_strict(text, expected_ids)
        for lid in ids_by_key[key]:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                changed_flag = _drop_or_flag_stale(ln, counts) or changed_flag
                continue
            critique = (critique_map.get(str(lid)) or "").strip()
            # A line the model chose not to critique is a normal, expected
            # outcome (see reflect_translate_batch's own docstring) -- it
            # still continues to expressiveness, so it is recorded as "",
            # which marks it as surviving this stage.
            result_texts[lid] = critique
            counts["critiqued" if critique else "no_critique_needed"] += 1

    if changed_flag:
        _save_flags(job, current, counts)
    db.set_bulk_job_line_result_texts(job["id"], result_texts)
    return counts


def _advance_to_expressive_stage(job: dict, ids: list, draft_by_id: dict, critique_by_id: dict,
                                 en_at_submit_by_id: dict, engine):
    provider = make_provider(job["engine"], engine)

    kind = _reflect_kind(job["drama_id"], job.get("translate_args"))

    def batches():
        return [([(lid, zh_by_id[lid]) for lid in chunk], translate_engines.build_reflect_expressive_prompt(
                    instructions, batch_ctx, chunk, source_by_id,
                    {lid: draft_by_id.get(lid, "") for lid in chunk},
                    {lid: critique_by_id.get(lid) for lid in chunk}, kind))
                for chunk, zh_by_id, instructions, batch_ctx, source_by_id in _stage_chunks(job, ids, engine)]
    return _submit_next_stage(job, "expressive", batches, ids, en_at_submit_by_id, provider, 4000)


STAGE_SUBMIT_ATTEMPTS = 5


def _stage_texts(job) -> dict:
    return {r["line_id"]: r["result_text"] for r in db.list_bulk_job_lines(job["id"])} if job else {}


def _advance_pipeline(job: dict, engine, monthly_cap_usd, summary: dict, cancel_check=None) -> str:
    """Submits the next Reflect stage (once per pipeline), returning job's new
    status. A failed submit holds "needs_resubmit"; past the cap or
    STAGE_SUBMIT_ATTEMPTS the paid drafts are written. cancel_check: the
    polling job's own Cancel, which is only in memory until this records it."""
    nxt = "reflect" if job["stage"] == "faithful" else "expressive"
    rows = db.list_bulk_job_lines(job["id"])
    survivors = [r["line_id"] for r in rows
                 if (r["result_text"] if nxt == "reflect" else r["result_text"] is not None)]
    if sibling_stage_job(job["pipeline_id"], nxt, skip_failed=True) is not None:
        return _leave_applying(job["id"], "applied", result_summary=summary, last_error=None)
    if nxt == "expressive" and "drafts_applied" not in summary:
        # Failed reflections skip the next stage; keep their drafts.
        failed = {r["line_id"] for r in rows if r["result_text"] is None}
        summary["drafts_applied"] = summary.get("drafts_applied", 0) + (
            _apply_reflect_drafts(job, failed) if failed else 0)
    if not survivors:
        return _leave_applying(job["id"], "applied", result_summary=summary, last_error=None)
    if engine is None:
        return _leave_applying(job["id"], "needs_resubmit", result_summary=summary,
                               last_error="Waiting for an API key to send the next Reflect stage.")
    _cap, error = translate_engines.resolve_cost_cap(
        None, monthly_cap_usd, db.get_month_spend() if monthly_cap_usd else 0.0)
    if _cancelled(job["id"], cancel_check):
        # Cancelled while this stage's results were being applied: the next
        # paid stage is never sent.
        return db.get_bulk_job(job["id"])["status"]
    if not error:
        en_at_submit = {r["line_id"]: r["en_at_submit"] for r in rows}
        texts = _stage_texts(job)
        if nxt == "reflect":
            error = _advance_to_reflection_stage(job, survivors, texts, en_at_submit, engine)
        else:
            drafts = _stage_texts(sibling_stage_job(job["pipeline_id"], "faithful"))
            error = _advance_to_expressive_stage(job, survivors, drafts, texts, en_at_submit, engine)
        if error is None:
            status = (db.get_bulk_job(job["id"])["status"] if _cancelled(job["id"], cancel_check)
                      else _leave_applying(job["id"], "applied", result_summary=summary, last_error=None))
            if status != "applied":
                _cancel_orphaned_stage(job, nxt, engine)
            return status
        summary["next_stage_attempts"] = summary.get("next_stage_attempts", 0) + 1
        if summary["next_stage_attempts"] < STAGE_SUBMIT_ATTEMPTS:
            return _leave_applying(job["id"], "needs_resubmit", result_summary=summary,
                                   last_error=f"The next Reflect stage couldn't be sent yet: {error}")
    summary["drafts_applied"] = summary.get("drafts_applied", 0) + _apply_reflect_drafts(job, set(survivors))
    return _leave_applying(job["id"], "applied", result_summary=summary,
                           last_error=f"The next Reflect stage wasn't sent ({error}); "
                                      "the first-pass drafts were used instead.")


def _cancelled(job_id: int, cancel_check) -> bool:
    """True once this applying job was cancelled, in the database or by its
    polling job (recorded here, so the Cancel wins over _leave_applying)."""
    if cancel_check is not None and cancel_check():
        db.set_bulk_job_status_if(job_id, ("applying",), "cancelled")
    return db.get_bulk_job(job_id)["status"] != "applying"


def _cancel_orphaned_stage(job: dict, stage: str, engine):
    """A Cancel that landed while `stage` was being submitted left a paid
    batch under a cancelled parent, which nothing would poll or cancel:
    cancel it here and at the provider."""
    child = sibling_stage_job(job["pipeline_id"], stage, skip_failed=True)
    if child is None or not db.set_bulk_job_status_if(child["id"], ("submitted",), "cancelled"):
        return
    provider = make_provider(job["engine"], engine)
    try:
        provider.cancel(child["provider_batch_id"])
        note = "Cancelled at the provider and here, with the stage before it."
    except Exception as e:
        note = ("Cancelled here with the stage before it, but the provider's own cancel call "
                f"failed -- results that still arrive will be ignored. "
                f"({translate_engines.redact_secrets(str(e))})")
    db.update_bulk_job(child["id"], last_error=note)


def _leave_applying(job_id: int, status: str, **fields) -> str:
    # A Cancel that landed while the next paid stage was being sent wins.
    if not db.set_bulk_job_status_if(job_id, ("applying",), status):
        return db.get_bulk_job(job_id)["status"]
    db.update_bulk_job(job_id, **fields)
    return status


def _fail_keeping_drafts(job: dict, message: str, summary: dict = None) -> str:
    if job.get("kind") == "reflect" and job.get("stage") in ("reflect", "expressive"):
        # Paid drafts survive an unusable batch, for its own lines only: an
        # earlier-written draft may since have been reverted.
        own = {r["line_id"] for r in db.list_bulk_job_lines(job["id"])}
        summary = {**(summary or {}), "drafts_applied": _apply_reflect_drafts(job, own)}
        message += " First-pass drafts were used."
    return _leave_applying(job["id"], "failed", last_error=message, result_summary=summary)


def _apply_reflect_drafts(job: dict, only_ids=None) -> int:
    faithful = job if job["stage"] == "faithful" else sibling_stage_job(job["pipeline_id"], "faithful")
    rows = {r["line_id"]: r for r in db.list_bulk_job_lines(faithful["id"])
            if r["result_text"] and (only_ids is None or r["line_id"] in only_ids)}
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0, "kept_your_edit": 0}
    _write_reflect_english(job, rows, {lid: r["result_text"] for lid, r in rows.items()}, {}, counts,
                           f"{job['engine']} bulk reflect (first-pass drafts)")
    return counts["applied"]


def _apply_reflect_expressive(job: dict, results) -> dict:
    """The only Reflect stage that writes to a line (en_at_submit dates from
    the faithful submission) and saves the critique as a note."""
    job_lines = db.list_bulk_job_lines(job["id"])
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    critique_by_id = {lid: t for lid, t in _stage_texts(
        sibling_stage_job(job["pipeline_id"], "reflect")).items() if t}
    draft_by_id = _stage_texts(sibling_stage_job(job["pipeline_id"], "faithful"))

    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "kept_your_edit": 0, "failed_requests": 0, "unknown_requests": 0}
    final_by_id = {}
    succeeded = set()
    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "reflect_expressive_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        request_ids = ids_by_key[key]
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        succeeded.add(key)
        final_map = _parse_strict(text, [str(lid) for lid in request_ids])
        for lid in request_ids:
            final_by_id[lid] = final_map.get(str(lid)) or draft_by_id.get(lid) or ""
    for key, request_ids in ids_by_key.items():
        if key not in succeeded:
            # Failed/unanswered: the paid draft stands in, critique dropped.
            for lid in request_ids:
                final_by_id[lid] = draft_by_id.get(lid) or ""
                critique_by_id.pop(lid, None)
    return _write_reflect_english(job, row_by_id, final_by_id, critique_by_id, counts,
                                  f"{job['engine']} bulk reflect")


def _write_reflect_english(job: dict, row_by_id: dict, text_by_id: dict, critique_by_id: dict,
                           counts: dict, label: str) -> dict:
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    args = job.get("translate_args") or {}
    enforced = [t for t in (args.get("glossary_terms") or []) if t.get("enforce_exact")]
    substitute_terms = tguide.build_hard_term_substituter(enforced)
    notes = []
    for lid, text in text_by_id.items():
        row = row_by_id[lid]
        ln = cur_by_id.get(lid)
        if _stale_line(ln, row["zh_hash"]):
            _drop_or_flag_stale(ln, counts)
            continue
        if (ln.en or "") != (row["en_at_submit"] or ""):
            counts["kept_your_edit"] += 1
            continue
        translation = (text or "").strip()
        if not translation:
            continue
        if enforced:
            translation = substitute_terms(translation)
        ln.en = translation
        source_change.clear_for_new_english(ln)
        counts["applied"] += 1
        critique = critique_by_id.get(lid)
        if critique:
            notes.append({"line_id": lid, "term": "", "note_type": "reflection", "note": critique})

    kept = _save_applied_english(job, current, counts, label)
    mark_translated_if_complete(job["drama_id"])
    # A critique of English that never landed would describe the wrong text.
    notes = [n for n in notes if n["line_id"] not in kept]
    if notes:
        db.save_translation_notes(job["drama_id"], notes)
    return counts
