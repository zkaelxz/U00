"""
services/workspace_review_job_service.py -- the background-job runners for
the LLM review passes in Review & edit: emotion tags, flag uncertain
lines, consistency check, translation notes and the bulk "fix flagged
lines" re-translate.

Thread-safe in the same way as `workspace_job_service`: plain Python
objects, `background_jobs` and the database only.
"""

import os

import db
import background_jobs
from engine_backends import llm_tasks, translate_pipeline
from engine_backends import paid_calls
import translation_guide as tguide
import emotion
import whisper_models
from services import fixflag_transcribe, fixflag_translate, paid_job_service, transcribe_options_service
from services.workspace_job_service import _raise_if_cancelled, build_run_style_context


CONSISTENCY_BATCH_SIZE = 60


def _id_by_idx(lines):
    """{line idx: permanent line id} as of when a job copied the lines, so
    a result keyed by position lands on the right line even if the user
    merged or split lines while the job ran. None if any line has no id
    yet (then db resolves positions against the lines as they are now)."""
    if any(getattr(ln, "id", None) is None for ln in lines):
        return None
    return {ln.idx: ln.id for ln in lines}


def run_emotion_job(job_id, drama_id, lines, engine, use_audio_cues, engine_choice):
    """
    Runs emotion.detect_emotions in a background thread, same reasoning as
    the jobs above: a full stream's worth of lines is enough LLM batches
    that a static spinner looks stuck, and there's no reason to freeze the
    rest of the app for it. Unlike translation this has no db.save_lines
    step -- detect_emotions only returns a result, it doesn't mutate
    anything -- so the result is just handed back whole.

    Persists to the database (db.save_emotions), not just the ephemeral
    job-status dict -- this is a whole-drama LLM batch job, same scale as
    translation itself, and losing its result to a page refresh (session
    state doesn't survive one) meant re-running it and re-paying for it.
    """
    with paid_job_service.job_calls(job_id, drama_id, engine, engine_choice, "emotion_detect") as limits:
        emap = emotion.detect_emotions(
            lines, engine, use_audio_cues=use_audio_cues,
            progress_cb=lambda frac: background_jobs.update_progress(
                job_id, frac, f"Reading tone... {frac * 100:.0f}%"),
            usage_cb=paid_calls.usage_logger(drama_id, engine_choice, engine, "emotion_detect"),
            cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    db.save_emotions(drama_id, emap, id_by_idx=_id_by_idx(lines))
    background_jobs.set_result(job_id, {"emotions": emap, **paid_job_service.stop_fields(limits)})


def run_flag_job(job_id, drama_id, lines, engine, engine_choice, lang=None):
    """
    Runs flag_uncertain_lines in a background thread -- same reasoning as
    the jobs above. Unlike emotion detection, the result IS persisted (via
    db.save_lines): the whole point of a review queue is coming back to it
    later, potentially after closing the app, not just within this
    session.

    Compare-and-set: each line's flag/flag_note is written by one
    conditional UPDATE keyed on the value the job started with
    (db.update_lines_fields_if_many), so a line the user changed while the
    job ran -- even between the job's last read and its write -- keeps the
    user's value; the job's result for that line is dropped. A line whose
    English was corrected meanwhile is dropped too: the flag judged the old text.
    """
    started_with = {ln.id: (ln.flag or "", ln.flag_note or "") for ln in lines}
    english_at_start = {ln.id: ln.en for ln in lines}
    stats = {}
    with paid_job_service.job_calls(job_id, drama_id, engine, engine_choice, "flag_review") as limits:
        llm_tasks.flag_uncertain_lines(
            lines, engine, source_language=lang, stats=stats,
            progress_cb=lambda frac: background_jobs.update_progress(
                job_id, frac, f"Checking for lines that need a second look... {frac * 100:.0f}%"),
            usage_cb=paid_calls.usage_logger(drama_id, engine_choice, engine, "flag_review"),
            cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    items = []
    for ln in lines:
        if ln.id is None:
            continue
        before = started_with[ln.id]
        if (ln.flag or "", ln.flag_note or "") != before:
            items.append((ln.id, {"flag": ln.flag or None, "flag_note": ln.flag_note or ""},
                          {"flag": before[0], "flag_note": before[1],
                           "en": english_at_start[ln.id]}))
    db.update_lines_fields_if_many(drama_id, items)
    # Counted from the saved rows, so flags the user set or cleared by hand
    # while the job ran are reflected (read-only; nothing is written here).
    saved = {r["id"]: bool(r["flag"]) for r in db.load_lines(drama_id)}
    flagged = sum(saved.get(ln.id, False) if ln.id is not None else bool(ln.flag)
                  for ln in lines)
    background_jobs.set_result(job_id, {"flagged_count": flagged, **stats,
                                        **paid_job_service.stop_fields(limits)})


def run_consistency_job(job_id, drama_id, lines, engine, engine_choice, lang=None):
    """
    Runs check_consistency_llm in a background thread. Backgrounding it,
    same as Review queue and Emotion detection, is what lets it run at the same time as those instead of forcing them
    to queue up one after another.
    """
    with paid_job_service.job_calls(job_id, drama_id, engine, engine_choice,
                                    "consistency_check") as limits:
        issues, failed_batches, total_batches = llm_tasks.check_consistency_llm(
            lines, engine, batch_size=CONSISTENCY_BATCH_SIZE, source_language=lang,
            usage_cb=paid_calls.usage_logger(drama_id, engine_choice, engine, "consistency_check"),
            cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    if limits.stop_reason:
        # Batches never sent count as failed, and a partial result must not
        # replace (or wipe) the issues an earlier run saved.
        wanted = -(-sum(1 for ln in lines if ln.en.strip()) // CONSISTENCY_BATCH_SIZE)
        # (the batch it stopped at was counted but never sent)
        failed_batches = wanted - (total_batches - failed_batches - 1)
        total_batches = wanted
    if issues or not limits.stop_reason:
        db.save_consistency_issues(drama_id, issues)
    background_jobs.set_result(job_id, {
        "issue_count": len(issues),
        "failed_batches": failed_batches,
        "total_batches": total_batches,
        **paid_job_service.stop_fields(limits),
    })


def run_translation_notes_job(job_id, drama_id, lines, engine, engine_choice):
    """
    Runs generate_translation_notes_llm in a background thread -- same
    reasoning as run_consistency_job above.
    """
    with paid_job_service.job_calls(job_id, drama_id, engine, engine_choice,
                                    "translation_notes") as limits:
        found_notes = tguide.generate_translation_notes_llm(
            lines, engine,
            usage_cb=paid_calls.usage_logger(drama_id, engine_choice, engine, "translation_notes"),
            cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    if found_notes:
        db.save_translation_notes(drama_id, found_notes, id_by_idx=_id_by_idx(lines))
    background_jobs.set_result(job_id, {"note_count": len(found_notes),
                                        **paid_job_service.stop_fields(limits)})


def run_fix_flagged_lines_job(job_id, drama_id, lines, audio_path, whisper_size, use_gpu,
                               source_language, engine, engine_choice, cost_cap_usd=None,
                               locale="en-US", include_genre_notes=None,
                               default_female_pronouns=None, style_note="", principal=None):
    """
    Bulk version of the single-line 🔧 tools in Review & edit: for every
    currently-flagged line, re-transcribes its own timing window from the
    original audio (skipped if there's no audio -- novel-narration has
    none to re-transcribe) and re-translates the result, clearing the
    flag on whichever lines that actually changed something for (a reply
    identical to the current English leaves the line flagged). Uses
    whatever Whisper model size and translation engine/model are
    currently selected in Workspace (3. Recognition accuracy / 5.
    Translation) -- the same ones "Transcribe" and "Translate all lines"
    themselves would use, not a separate hidden choice.

    cost_cap_usd: same cap "Translate all lines" enforces (the tighter of
    the per-job and monthly caps, resolved before the job starts) -- stop
    cleanly once this run's real logged spend reaches it, leaving whatever
    is still flagged untouched. This loop checks the cap so it can't spend without
    limit regardless of a configured monthly cap.

    Each re-translation gets the same context a translate run uses
    (glossary, style guidelines, locale, the line's character name).
    """
    flagged, timing_skipped = fixflag_translate.split_timing_flags(
        [ln for ln in lines if ln.flag])
    drama = db.get_drama(drama_id) or {}
    style_preset = "novel" if drama.get("content_mode") == "novel_narration" else "audio_drama"
    glossary_terms, style_guidelines, character_names = build_run_style_context(
        drama_id, drama, lines, style_preset, with_emotions=False,
        include_genre_notes=include_genre_notes,
        default_female_pronouns=default_female_pronouns, principal=principal)
    base_context = translate_pipeline.build_translation_context(
        engine, drama, style_note=style_note or "", locale=locale,
        glossary_terms=glossary_terms, style_guidelines=style_guidelines)
    base_context["source_language"] = source_language
    outcome = {"fixed": 0, "unchanged": 0, "cap_reached": None}
    # Per-line failures are recorded and the line stays flagged, and the
    # fixes made so far are saved in the `finally`, so a later failure (or the
    # cost-cap stop, which is expected) can't erase them.
    errors = []
    # Lines heard before a timeout are fixed and saved; after a Cancel their
    # text is saved but not translated (no paid calls), so they stay flagged.
    total_flagged = len(flagged)
    heard_entries, cancelled, hearing_error = [], False, None
    if flagged and audio_path and os.path.exists(audio_path):
        heard_entries, cancelled, hearing_error = fixflag_transcribe.hear_flagged(
            job_id, flagged, transcribe_options_service.rehear_audio_path(drama, audio_path),
            fixflag_transcribe.hearing_settings(
                drama_id, drama, whisper_size, source_language, use_gpu))
        if hearing_error:
            errors.append(hearing_error)
    heard_by_idx = {entry["idx"]: entry for entry in heard_entries}
    if cancelled or hearing_error:
        flagged = [ln for ln in flagged if ln.idx in heard_by_idx]
    try:
        with paid_job_service.job_calls(job_id, drama_id, engine, engine_choice,
                                        "fix_flagged_line") as limits:
            outcome = fixflag_translate.fix_lines(
                flagged, engine=engine, engine_choice=engine_choice, drama_id=drama_id,
                base_context=base_context, character_names=character_names,
                source_language=source_language, heard_by_idx=heard_by_idx,
                translate=not cancelled, cost_cap_usd=cost_cap_usd, errors=errors, all_lines=lines,
                check_cancel=lambda: None if cancelled else _raise_if_cancelled(job_id),
                on_progress=lambda done, total: background_jobs.update_progress(
                    job_id, done / max(total, 1), f"Fixing flagged lines... {done}/{total}"))
    finally:
        if audio_path and os.path.exists(audio_path):
            whisper_models.release_gpu_models()  # re-transcription stage done
        kept = fixflag_transcribe.save_fixed(drama_id, lines)
    if cancelled:
        raise background_jobs.JobCancelled(job_id)
    background_jobs.set_result(job_id, {
        "fixed_count": outcome["fixed"], "total_flagged": total_flagged, "errors": errors[:20],
        "cap_reached": outcome["cap_reached"], **paid_job_service.stop_fields(limits),
        **({"timing_skipped_count": timing_skipped} if timing_skipped else {}),
        **({"unchanged_count": outcome["unchanged"]} if outcome["unchanged"] else {}), **kept})

