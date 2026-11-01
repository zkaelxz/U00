"""
translation_finish.py -- what happens after a translation run writes its lines:
the untranslated count, the "aligned" -> "translated" status, and the shared
finish step shared by the translate jobs.
"""
import json
import logging

import db
import source_change
from engine_backends import engine_registry, pricing, llm_tasks
import translation_guide as tguide


def untranslated_line_count(drama_id: int) -> int:
    """Lines with source text but no translation yet, as saved right now."""
    return sum(1 for r in db.load_line_texts(drama_id)
               if (r.zh or "").strip() and not (r.en or "").strip())


def mark_translated_if_complete(drama_id: int) -> bool:
    """Moves a title from "aligned" to "translated" once every line with
    source text has a translation. Called after any write that can fill the
    last blank, so the status follows the lines whichever path wrote them.
    Only an exact "aligned" is promoted: "dubbed" and "exported" are later
    stages and must never go back, and a title with no source text has
    nothing translated."""
    drama = db.get_drama(drama_id)
    if not drama or drama.get("status") != "aligned":
        return False
    rows = db.load_line_texts(drama_id)
    if not any((r.zh or "").strip() for r in rows):
        return False
    if untranslated_line_count(drama_id) != 0:
        return False
    return db.set_status_if(drama_id, "aligned", "translated")


def repair_stale_aligned_statuses() -> int:
    """One-time, idempotent startup repair for titles left at "aligned" with
    every line already translated; touches no other status. Returns how
    many were corrected."""
    fixed = sum(1 for d in db.list_dramas(status="aligned")
                if mark_translated_if_complete(d["id"]))
    if fixed:
        logging.getLogger(__name__).info(
            "Corrected %d title(s) stuck at 'aligned' with every line translated.", fixed)
    return fixed


def _summary_engine_is_paid(summary_engine, summary_engine_choice) -> bool:
    name = summary_engine_choice or getattr(summary_engine, "name", "")
    return (name not in engine_registry.FREE_ENGINES
            and not getattr(summary_engine, "free_tier", False))


def own_lines_callbacks(drama_id: int, lines, on_saved):
    """(save_cb, notes_cb, skipped ids) for translate_lines_with_engine:
    `en` is written only where the database still holds what the run
    loaded, so an edit saved mid-run is kept. A Reflect critique arrives
    before its batch is saved, so it is held until that save and dropped
    for a line whose write was skipped: it describes text that never
    landed. on_saved(lines written) runs after each save (provenance)."""
    id_by_idx = {ln.idx: ln.id for ln in lines if getattr(ln, "id", None) is not None}
    skipped, pending = set(), []

    def save_cb(ls):
        skipped.update(db.save_lines(drama_id, ls, fields=("en",), only_if_unchanged=True) or ())
        notes = [n for n in pending if id_by_idx.get(n.get("line_idx")) not in skipped]
        pending.clear()
        if notes:
            db.save_translation_notes(drama_id, notes, id_by_idx=id_by_idx)
        on_saved([ln for ln in ls if getattr(ln, "id", None) not in skipped])

    return save_cb, pending.extend, skipped


def finish_translation_run(drama_id: int, lines, engine, engine_choice: str, style_preset: str,
                           glossary_terms, errors, cancelled: bool = False,
                           summary_engine=None, summary_engine_choice: str = None,
                           line_scoped: bool = False,
                           summary_monthly_cap_usd: float = None,
                           flags_needing_recheck: set = None, *, enforce_ids) -> bool:
    """What happens after translate_pipeline.translate_lines_with_engine
    returns, shared by the translate jobs so they can't drift: applies
    enforce_exact glossary terms, flags reading-speed-dense lines, saves
    the run as the active translation version, and persists its batch
    failures (dramas.last_translate_errors).

    The drama is marked "translated" only once no line is left
    untranslated. A run stopped short -- batch failures, the cost cap, a
    cancel -- leaves the status as it was, so the drama stays in Library's
    untranslated selection. A cancelled run saves no version: it isn't a
    finished translation to compare against.

    summary_engine, if given, generates this episode's running
    summary ONCE, here -- only once the drama actually reaches "translated"
    and only for a non-cancelled run -- and stores it on the drama row for
    the next episode of the same series to read forward. None (the
    default) skips this entirely, e.g. when no engine could be built for
    it; a missing/unreachable summary engine must never fail the
    translation run itself. summary_monthly_cap_usd: Settings' monthly
    spending cap; a paid summary engine is skipped once it's used up
    (checked right before the call). None or 0 means no cap.

    line_scoped marks a run restricted to some lines (e.g. a retry
    of one content-blocked line on another engine): it must not replace
    the drama's recorded translation_engine, which describes the whole-
    drama run, nor its last_translate_errors, nor save a new active
    translation version.

    Returns False, recording nothing, if every line this run translated
    has since been replaced (e.g. a new transcription finished meanwhile)
    -- its writes were no-ops, and a version or status would describe
    lines that no longer exist.

    enforce_ids: the run's own line ids, the only English it may write:
    the exact-term substitution touches only those line ids, and
    only where the database still holds the English this run wrote. A
    line whose English differs (its write was skipped because it was
    edited mid-run, or it was edited since) is the user's: it gets no
    substitution, and no flag computed from this run's text. A flag is
    saved only while the line's English, timing and flag in the database
    are still what it was computed from (the flag can be a reading-speed
    or a content-blocked one); the ids of lines that changed during the
    run are added to flags_needing_recheck (a set, if given)."""
    # First: the exact-term substitution below changes `en` in the database,
    # which the compare-and-set on this run's English would then refuse.
    source_change.save_cleared_flags(drama_id, lines)
    enforced = [t for t in (glossary_terms or []) if t.get("enforce_exact")]
    substitute_terms = tguide.build_hard_term_substituter(enforced)
    # One load only: the substitution's compare-and-set guards against
    # edits after this read, so deciding which lines are still the run's
    # from a second, earlier read would let an edit in between through.
    run_en = {ln.id: (ln.en or "") for ln in lines if getattr(ln, "id", None) is not None}
    fresh = db.load_line_objects(drama_id)
    own_fresh = [ln for ln in fresh if ln.id in enforce_ids and ln.id in run_en
                 and (ln.en or "") == run_en[ln.id]]
    landed = {ln.id for ln in own_fresh}
    # The version saved below must hold an edit the run kept, or restoring
    # it later would bring this run's English back over the edit.
    kept = {ln.id: ln.en for ln in fresh if ln.id not in landed}
    for ln in lines:
        ln.en = kept.get(ln.id, ln.en)
    if enforced:
        substituted = {}
        for ln in own_fresh:
            if ln.en:
                before = ln.en
                ln.en = substitute_terms(ln.en)
                if ln.en != before:
                    substituted[ln.id] = (before, ln.en)
        # Compare-and-set: an edit saved since the load above is kept too.
        for lid in db.save_lines(drama_id, own_fresh, fields=("en",), only_if_unchanged=True):
            substituted.pop(lid, None)
        # The density check below reads the run's own copies: give them
        # the substituted English that is now in the database.
        for ln in lines:
            if ln.id in substituted:
                ln.en = substituted[ln.id][1]
        # The substitution is part of the machine translation: a line whose
        # provenance matched before still matches, so it isn't mistaken for
        # a hand-edited one.
        from services import line_provenance_service
        line_provenance_service.carry_forward(drama_id, substituted)

    # A translation too dense to read in the time it's on screen goes into
    # the review queue like any other flag (never replacing an existing one).
    # Unconditional (not just when flag_dense_lines finds something new):
    # translate_lines_with_engine may already have set a content_blocked flag
    # on some lines, and that has to reach the database too, or it
    # only ever exists on this run's in-memory copies.
    from services import reading_speed_dismissal_service as dismissals
    dismissals.flag_dense_lines(drama_id, lines)
    # Compare-and-set on the analysed text too: an edit saved after the
    # read above must not get a flag computed from this run's English.
    stale = db.save_lines(drama_id, [ln for ln in lines if ln.id in landed],
                          fields=("flag", "flag_note"), only_if_unchanged=True,
                          guard_fields=("en", "start", "end"))
    if flags_needing_recheck is not None:
        flags_needing_recheck.update(stale)

    line_ids = [ln.id for ln in lines if getattr(ln, "id", None) is not None]
    if line_ids and not db.line_ids_exist(drama_id, line_ids):
        return False

    # A line-scoped run (e.g. a one-line retry) touches only its own
    # lines, so it neither saves a new active version (which would be
    # labelled with the retry engine) nor replaces the drama's persisted
    # record of a whole run's failures.
    if not cancelled and not line_scoped:
        label = f"{engine_choice} · {style_preset}"
        if engine_choice in engine_registry.FREE_ENGINES or getattr(engine, "free_tier", False):
            label = f"[testing: {engine_choice}] {label}"
        db.save_translation_version(drama_id, lines, label=label, engine=engine_choice,
                                    model=getattr(engine, "model", ""), make_active=True)
    # Persisted, not just shown once: if the app restarts, the record of
    # what failed (and why some lines are untranslated) must not vanish.
    fields = {}
    if not line_scoped:
        fields["last_translate_errors"] = (json.dumps(errors, ensure_ascii=False)
                                           if errors else None)
        fields["translation_engine"] = engine_choice
    if untranslated_line_count(drama_id) == 0:
        fields["status"] = "translated"
    db.update_drama(drama_id, **fields)

    if (summary_engine is not None and summary_monthly_cap_usd
            and _summary_engine_is_paid(summary_engine, summary_engine_choice)):
        _cap, refusal = pricing.resolve_cost_cap(
            None, summary_monthly_cap_usd, db.get_month_spend())
        if refusal:
            summary_engine = None
    if fields.get("status") == "translated" and not cancelled and summary_engine is not None:
        _fresh_for_summary = db.load_line_objects(drama_id)
        summary = llm_tasks.generate_episode_summary(
            _fresh_for_summary, summary_engine,
            usage_cb=lambda inp, out, cache_read=0, cache_write=0: db.log_usage(
                drama_id, summary_engine_choice or getattr(summary_engine, "name", ""),
                getattr(summary_engine, "model", ""), "episode_summary", inp, out,
                pricing.estimate_cost_for_engine(
                    summary_engine, inp, out, cache_read, cache_write),
                cache_read_tokens=cache_read))
        if summary:
            db.update_drama(drama_id, episode_summary=summary)
    return True
