"""
services/workspace_job_service.py -- the translation job runners behind the
Workspace and Library actions: one drama's translate run, the bulk
series translate queue, and the style context both share with the review
jobs and the Live path.

They are safe to run in a background thread because they touch only plain
Python objects, `background_jobs` for progress/result reporting, and the
database -- never any UI state. The transcription and audio-tag jobs live
in `workspace_audio_job_service`, the LLM review jobs in
`workspace_review_job_service`.
"""

import time

import adaptive_style
import db
import background_jobs
from jobs import job_launch
import translate_engines
import translation_guide as tguide
from engine_backends.run_limits import FailureStreak
import translation_finish
import emotion
import core as core_module
from services import (job_timing_service, language_pack_service, line_provenance_service,
                      novel_files_service, ownership_service, profile_service, run_settings_service,
                      settings_service, timing_settings_service, vdb_names_service)
from services.service_errors import ConflictError, InvalidInputError


def resolve_style_toggles(drama, include_genre_notes=None, default_female_pronouns=None):
    """(include_genre_notes, default_female_pronouns) for one run: a value the
    caller passes wins, else what the owner saved on the title, else the API
    defaults (genre notes on, she/her off). Every path that builds a prompt
    goes through this, so a run that is not handed the toggles (retry, resume,
    glossary re-translate, line AI) uses the owner's choice, not a default."""
    saved_genre = (drama or {}).get("include_genre_notes")
    saved_female = (drama or {}).get("default_female_pronouns")
    if include_genre_notes is None:
        include_genre_notes = True if saved_genre is None else bool(saved_genre)
    if default_female_pronouns is None:
        default_female_pronouns = False if saved_female is None else bool(saved_female)
    return bool(include_genre_notes), bool(default_female_pronouns)


def build_run_style_context(drama_id, drama, lines, style_preset, with_emotions=True,
                            include_genre_notes=None, default_female_pronouns=None,
                            principal=None):
    """(glossary_terms, style_guidelines, character_names) for one drama,
    shared by the app's runs: series glossary, style
    profile, emotion guidance, gender hints, speaker labels and
    the title's language packs.
    include_genre_notes/default_female_pronouns are the Translate toggles;
    None means the title's saved choice (resolve_style_toggles).
    principal: whom the run is for; the learned style is left out unless
    they may see it (ownership_service.sees_learned_style). A job thread
    has no acting principal, so its starter passes the one captured at
    start (ownership_service.acting_principal)."""
    include_genre_notes, default_female_pronouns = resolve_style_toggles(
        drama, include_genre_notes, default_female_pronouns)
    series_id = (drama or {}).get("series_id")
    glossary_terms = db.list_glossary_terms(series_id) if series_id else None
    series_chars = db.list_series_characters(series_id) if series_id else []
    drama_chars = db.list_characters_with_series_names(drama_id)
    prof = db.get_style_profile(f"series:{series_id}" if series_id else "global")
    learned = ""
    if prof and ownership_service.sees_learned_style(principal, prof.get("profile")):
        learned = adaptive_style.profile_to_prompt_block(prof.get("profile", {}))
    emap = db.load_emotions(drama_id) if with_emotions else None
    emotion_block = emotion.build_emotion_guidance(emap, [ln.idx for ln in lines]) if emap else ""
    style_guidelines = tguide.build_style_guidelines(
        style_preset, glossary_terms=glossary_terms,
        include_genre_notes=include_genre_notes,
        default_female_pronouns=default_female_pronouns,
        custom_notes="\n\n".join(b for b in (
            learned, emotion_block,
            tguide.build_character_gender_hints(
                series_chars, drama_chars, default_female_pronouns)) if b))
    character_names = tguide.build_speaker_labels(drama_chars, series_chars)
    style_guidelines += language_pack_service.block_for(drama, lines, glossary_terms)
    style_guidelines += vdb_names_service.block_for(drama, lines, glossary_terms)
    return glossary_terms, style_guidelines, character_names


def _fallback_result(engine) -> dict:
    """{"fallbacks": [...]} when a FallbackEngine switched engines,
    else {} -- so the caller can show which engine actually did the work."""
    events = getattr(engine, "events", None)
    return {"fallbacks": list(events)} if isinstance(events, list) and events else {}


def run_translate_job(job_id, drama_id, lines, engine, drama_meta, style_note,
                       novel_reference, force_retranslate, locale, glossary_terms,
                       style_guidelines, engine_choice, style_preset, context_window=6,
                       ollama_num_ctx_override=None, reflect=False, cost_cap_usd=None,
                       context_window_ahead=3, batch_size=20, summary_engine=None,
                       summary_engine_choice=None, target_ids=None,
                       summary_monthly_cap_usd=None, own_lines_only=False, thinking=None,
                       summary_skip_reason=""):
    """
    The actual translation work, run inside a background thread. Touches
    only plain Python objects and the database, both of which are safe
    from a background thread. Progress goes through
    background_jobs.update_progress(); clients poll the job for it.

    reflect: the "High quality" Reflect mode -- three LLM passes per
    batch instead of one; the middle (reflection) pass's critique is
    saved as a translation note per line, via notes_cb below.

    cost_cap_usd: stop cleanly (every finished line kept) once this run's
    estimated spend reaches it -- the tighter of the per-job and monthly
    caps, resolved before the job starts. None = no cap.

    summary_engine/summary_engine_choice: the engine used for
    the once-per-episode running-summary call once this drama finishes
    translating, built by the caller (in the main thread, where Settings
    is readable) -- None if no summary engine is available/configured,
    which skips summary generation entirely rather than failing this job.
    summary_monthly_cap_usd: the monthly cap, re-checked right before a
    paid summary call (skipped once used up); None = no cap.
    summary_skip_reason: why the caller built no summary engine, for the
    result's "summary_skipped".

    target_ids: optional set of permanent line ids (set by the API
    start) -- only those lines are translated; None = every eligible line.

    English is written only where it still is what the job loaded: a line
    edited meanwhile keeps the edit, with no Reflect note, substitution or
    flag from this run, and is listed in the result's kept_your_edit.
    own_lines_only (with target_ids): the glossary's exact-term
    substitution touches only the target lines.

    force_retranslate: the lines are kept as an undo point first, here
    rather than at the request, so a start that loses the race leaves none.
    """
    if force_retranslate and any(ln.en for ln in lines):
        db.save_line_history_snapshot(drama_id, lines, "before force re-translate")
    en_before = {ln.id: ln.en for ln in lines} if target_ids is not None else None
    cap_reached = {}
    if isinstance(engine, translate_engines.FallbackEngine):
        # Tokens a provider reported for an attempt that then failed are
        # billed too: logged against the engine that spent them.
        engine.failed_usage_cb = lambda choice, eng, inp, out, cache_read=0, cache_write=0: \
            db.log_usage(drama_id, choice, getattr(eng, "model", choice), "translate", inp, out,
                         translate_engines.estimate_cost_for_engine(eng, inp, out, cache_read,
                                                                    cache_write),
                         cache_read_tokens=cache_read)
    # {speaker_label: "Name (pronouns)"}, named characters only: an unnamed
    # speaker is shown with no name, never the raw diarization label.
    _series_id = (db.get_drama(drama_id) or {}).get("series_id")
    character_names = tguide.build_speaker_labels(
        db.list_characters_with_series_names(drama_id),
        db.list_series_characters(_series_id) if _series_id else [])
    scene_aware = settings_service.get("scene_aware_batches")
    # What produced each line, and per-stage timing.
    provenance = line_provenance_service.translate_run_tracker(
        drama_id, lines, engine, engine_choice, glossary_terms, locale=locale,
        style_preset=style_preset, reflect=bool(reflect), context_window=context_window,
        context_window_ahead=context_window_ahead, batch_size=batch_size,
        style_note=style_note or "", style_guidelines=style_guidelines or "",
        scene_aware_batches=scene_aware, thinking=thinking)

    # A run takes minutes, and the user may type English meanwhile.
    _save, _notes, kept_edits = translation_finish.own_lines_callbacks(drama_id, lines, provenance)

    job_timing_service.mark_stage(job_id, "Translate")
    _, errors = translate_engines.translate_lines_with_engine(
        lines, engine, drama_meta=drama_meta, style_note=style_note,
        novel_reference=novel_reference, force_retranslate=force_retranslate,
        locale=locale, glossary_terms=glossary_terms, style_guidelines=style_guidelines,
        context_window=context_window, context_window_ahead=context_window_ahead,
        batch_size=batch_size, character_names=character_names,
        ollama_num_ctx_override=ollama_num_ctx_override,
        reflect=reflect, target_ids=target_ids, scene_aware_batches=scene_aware,
        **timing_settings_service.translate_limits(),
        check_replies=settings_service.reply_guard_override(),
        thinking=thinking, cost_cap_usd=cost_cap_usd,
        cap_cb=lambda spent: cap_reached.update(spent=spent),
        notes_cb=_notes,
        detail_cb=lambda frac, message: background_jobs.update_progress(
            job_id, frac, translate_engines.progress_message_with_rate_status(
                engine, frac, base=message)),
        save_cb=_save,
        cancel_check_cb=lambda: background_jobs.is_cancel_requested(job_id),
        usage_cb=lambda inp, out, cache_read=0, cache_write=0: db.log_usage(
            drama_id, (engine.active_choice if isinstance(engine, translate_engines.FallbackEngine)
                          else engine_choice),
            getattr(engine, "model", engine_choice), "translate",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out, cache_read, cache_write),
            cache_read_tokens=cache_read),
    )

    job_timing_service.mark_stage(job_id, "Finish (glossary checks, version, summary)")
    recheck = set()
    cancelled = background_jobs.is_cancel_requested(job_id)
    # Shared with the other translate paths: glossary enforcement,
    # density flags, the version, persisted errors, and a "translated"
    # status only once nothing is left untranslated.
    if not translation_finish.finish_translation_run(
            drama_id, lines, engine, engine_choice, style_preset, glossary_terms, errors,
            cancelled=cancelled,
            summary_engine=summary_engine, summary_engine_choice=summary_engine_choice,
            summary_monthly_cap_usd=summary_monthly_cap_usd,
            line_scoped=target_ids is not None,
            enforce_ids=(set(target_ids) if own_lines_only and target_ids is not None
                         else {ln.id for ln in lines if getattr(ln, "id", None) is not None}),
            flags_needing_recheck=recheck):
        background_jobs.set_result(job_id, {"errors": errors, "lines_replaced": True,
                                            "cap_reached": cap_reached.get("spent"),
                                            **_fallback_result(engine)})
    else:
        if target_ids is not None:
            # Imported here: it needs translate_run_service, which imports this module.
            from services import failed_lines_service
            failed_lines_service.prune_resolved(drama_id, lines, target_ids, en_before, errors)

        if summary_engine is not None and not summary_skip_reason:
            from services import episode_summary_service
            summary_skip_reason = episode_summary_service.missing_after_run_reason(
                drama_id, summary_engine_choice, cancelled)
        background_jobs.set_result(job_id, {"errors": errors,
                                            "cap_reached": cap_reached.get("spent"),
                                            **({"summary_skipped": summary_skip_reason}
                                               if summary_skip_reason else {}),
                                            **({"flags_needing_recheck": sorted(recheck)}
                                               if recheck else {}),
                                            **({"kept_your_edit": sorted(kept_edits)}
                                               if kept_edits else {}),
                                            **_fallback_result(engine)})
    if cancelled:
        # The engine stops between batches and returns normally, keeping the
        # lines already translated; returning here would end the job done at
        # 100% ("Finished."), so it ends cancelled with its real progress.
        raise background_jobs.JobCancelled(job_id)


def _raise_if_cancelled(job_id):
    """Stops a review job between LLM batches once cancel is requested."""
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


def run_bulk_series_translate_job(job_id, drama_ids, api_keys: dict, default_locale: str = "en-US",
                                  ollama_base_url: str = None, gemini_free_tier: bool = False,
                                  models: dict = None, monthly_cap: float = 0,
                                  expected_engines: dict = None, allow_paid_summary: bool = True,
                                  include_genre_notes: bool = None,
                                  default_female_pronouns: bool = None, principal=None,
                                  locale_is_explicit: bool = True):
    """Translates every drama in drama_ids that has no
    translation yet, queued ONE AT A TIME rather than all at once (same
    GPU/API-load reasoning as everywhere else in this app that queues
    rather than parallelizes). Each drama uses its own saved engine
    (`drama.translation_engine`) and, if it belongs to a series, that
    series' own glossary and style hints -- the same settings a single-drama
    run would use Skips (does not queue) a drama that already has a
    translate job running elsewhere, rather than racing it.

    Deliberately reuses the real per-drama job id ("translate_<id>")
    run_translate_job already uses -- if the user opens that drama's
    Workspace mid-run, they see the same real job, not a shadow copy.
    This coordinator job's own progress/message combine the queue
    position with that live per-drama progress into one line, for
    Library's combined status display.

    api_keys: {engine_name: api_key} gathered from Settings by the caller
    BEFORE starting this as a background job -- this function runs in a
    thread and must never touch UI state (background_jobs.py's hard
    rule). models: {engine_name: model_id}, same reasoning -- the
    Settings-configured default model for each engine, gathered by the
    caller before this starts, rather than falling back to each engine's
    own bare default. monthly_cap: Settings' monthly
    spending cap in USD, or 0/None for no cap -- re-checked against
    db.get_month_spend() before each drama, same as Workspace's
    per-run cap resolution. It is also
    checked before each paid episode-summary call. allow_paid_summary=False
    (a caller without engines.paid) skips a cloud summary engine picked in
    Settings, so only the engines the caller was authorized for run.
    include_genre_notes/default_female_pronouns: the Translate toggles,
    applied to every drama in the queue (defaults as the API).
    Each title is translated with what its last run used (model, style, style note,
    batch and context sizes, and the English variant unless locale_is_explicit:
    the caller named one for the whole batch), else the defaults.
    """
    summary_engine, summary_choice, default_style_note = _bulk_summary_setup(
        ollama_base_url, allow_paid_summary, monthly_cap)
    results = {"translated": [], "skipped_running": [], "skipped_no_key": [],
               "skipped_no_lines": [], "skipped_cap": [], "skipped_engine_changed": [],
               "errors": {}, "partial": {}, "cancelled": False}
    models = models or {}
    total = len(drama_ids) or 1
    # A broken key or an engine that is down fails every title the same way.
    failed_titles = FailureStreak()
    for i, did in enumerate(drama_ids):
        if background_jobs.is_cancel_requested(job_id):
            break
        drama = db.get_drama(did)
        title = (drama.get("title_en") or drama.get("title_zh") or f"title #{did}") if drama else f"title #{did}"
        per_job_id = f"translate_{did}"
        background_jobs.update_progress(job_id, i / total, f"Translating {i + 1}/{len(drama_ids)} -- {title} (0%)")
        if not drama:
            continue
        # A queued (GPU-waiting) job is the user's too: skip it rather than
        # adopt it below and possibly cancel it with this batch.
        if (background_jobs.get_status(per_job_id) or {}).get("status") in ("running", "queued"):
            results["skipped_running"].append(did)
            continue
        rows = db.load_lines(did)
        if not rows:
            results["skipped_no_lines"].append(did)
            continue

        plan = _plan_bulk_title(results, did, drama, api_keys, models, expected_engines,
                                gemini_free_tier, monthly_cap, ollama_base_url)
        if plan is None:
            continue
        started = _start_bulk_title(
            did, drama, rows, plan, title, per_job_id, default_locale, locale_is_explicit,
            default_style_note, summary_engine, summary_choice, monthly_cap,
            include_genre_notes, default_female_pronouns, principal)
        if not started:
            results["skipped_running"].append(did)
            continue

        _follow_bulk_title(job_id, per_job_id, i, total, len(drama_ids), title)
        if background_jobs.is_cancel_requested(job_id):
            break
        _record_bulk_outcome(results, did, per_job_id)
        # A title stopped by the cap says nothing about the engine.
        if failed_titles.record(failed=did in results["errors"] or (
                did in results["partial"] and results["partial"][did] == "batch errors"),
                succeeded=did in results["translated"]):
            results["stopped"] = f"Stopped: {failed_titles.count} titles in a row failed"
            break

    results["cancelled"] = background_jobs.is_cancel_requested(job_id)
    background_jobs.update_progress(job_id, 1.0, "Done")
    background_jobs.set_result(job_id, results)


def _bulk_summary_setup(ollama_base_url, allow_paid_summary, monthly_cap):
    # Imported here: translate_run_service imports this module.
    from services import settings_service, translate_run_service
    # Same Settings episode-summary engine as a single run (None if it
    # can't be built), at this job's own Ollama URL.
    summary_engine, summary_choice = translate_run_service.pick_summary_engine(
        ollama_base_url, allow_paid=allow_paid_summary)
    # The Settings default the single-title form starts from, so a title
    # translated in bulk matches one translated alone.
    default_style_note = settings_service.get("default_style_note") or ""
    if (summary_engine is not None and not monthly_cap
            and translation_finish._summary_engine_is_paid(summary_engine, summary_choice)):
        summary_engine = None  # a paid summary is paid work nobody watches: it needs a cap
    return summary_engine, summary_choice, default_style_note


def _plan_bulk_title(results, did, drama, api_keys, models, expected_engines,
                     gemini_free_tier, monthly_cap, ollama_base_url):
    """Checks the engine, key and spend cap for one queued title and builds its
    engine. Records the skip or error in `results` and returns None when the
    title can't run; otherwise a dict of what the launch needs."""
    from services import settings_service, translate_run_service
    engine_choice = drama.get("translation_engine") or settings_service.get("default_engine")
    # expected_engines: what the caller was checked against; an engine
    # changed since then is skipped rather than used unchecked.
    if (engine_choice not in translate_engines.ENGINES
            or expected_engines is not None and expected_engines.get(did) != engine_choice):
        results["skipped_engine_changed"].append(did)
        return None
    needs_key = engine_choice not in translate_engines.KEYLESS_ENGINES
    api_key = api_keys.get(engine_choice)
    if needs_key and not api_key:
        results["skipped_no_key"].append(did)
        return None
    if not api_key:
        api_key = "local"

    # Same cap logic as Workspace's Translate button -- only the engines that bill per token and report
    # usage are subject to it, and it's re-resolved against the
    # month's spend-so-far right before each drama, not just once for
    # the whole batch, since earlier dramas in this same run add to
    # that spend too.
    cap_applies = translate_run_service.engine_cap_applies(engine_choice, gemini_free_tier)
    cost_cap = None
    if cap_applies and not monthly_cap:
        # Unattended spend needs a ceiling; the start refuses without one,
        # so this is a cap removed since.
        results["skipped_cap"].append(did)
        return None
    if cap_applies and monthly_cap:
        cost_cap, refusal = translate_engines.resolve_cost_cap(
            None, monthly_cap, db.get_month_spend())
        if refusal:
            results["skipped_cap"].append(did)
            return None

    # The engine is the title's own, so the model its last run chose is this engine's.
    last = profile_service.title_run_defaults(drama, engine_choice, engine_choice)
    model = models.get(engine_choice)
    if model is None and "model" in last:
        try:
            translate_run_service._require_offered_model(engine_choice, last["model"])
            model = last["model"]
        except InvalidInputError:
            pass    # no longer offered: the engine's default
    try:
        engine = translate_engines.get_engine(
            engine_choice, api_key, model,
            free_tier=engine_choice == "gemini" and gemini_free_tier,
            base_url=ollama_base_url if engine_choice == "ollama" else None)
    except Exception as e:
        results["errors"][did] = translate_engines.redact_secrets(str(e))
        return None
    return {"engine_choice": engine_choice, "engine": engine, "cost_cap": cost_cap, "last": last}


def _start_bulk_title(did, drama, rows, plan, title, per_job_id, default_locale,
                      locale_is_explicit, default_style_note, summary_engine, summary_choice,
                      monthly_cap, include_genre_notes, default_female_pronouns, principal):
    """Builds one title's run context and starts its per-title translate job.
    Returns whether it started."""
    from services import settings_service, translate_run_service
    engine_choice, engine = plan["engine_choice"], plan["engine"]
    last = plan["last"]
    lines = core_module.lines_from_rows(rows)
    # Must not always be "audio_drama" (wrong for a novel-narration
    # drama): same per-content-mode default the shared
    # translate-finishing helper uses.
    is_novel = drama.get("content_mode") == "novel_narration"
    style_preset = last.get("style_preset") or ("novel" if is_novel else "audio_drama")
    style_note = last.get("style_note", default_style_note)
    locale = default_locale if locale_is_explicit else last.get("locale", default_locale)
    glossary_terms, style_guidelines, _ = build_run_style_context(
        did, drama, lines, style_preset, include_genre_notes=include_genre_notes,
        default_female_pronouns=default_female_pronouns, principal=principal)
    defs = translate_run_service.get_translate_config_defaults(is_novel, engine)
    defs = {k: last.get(k, v) for k, v in defs.items()}
    novel_reference = novel_files_service.read_reference_text(did, drama)

    # start_job returns False when someone else's translate_<id> run
    # started since the check in the caller: skip it rather than adopt (and
    # possibly cancel) that run.
    try:
        return job_launch.start_job(
            per_job_id, run_translate_job,
            per_job_id, did, lines, engine, drama, style_note, novel_reference, False,
            locale, glossary_terms, style_guidelines, engine_choice, style_preset,
            defs["context_window"], settings_service.get("ollama_num_ctx_override") or None,
            cost_cap_usd=plan["cost_cap"],
            context_window_ahead=defs["context_window_ahead"],
            batch_size=defs["batch_size"],
            summary_engine=summary_engine, summary_engine_choice=summary_choice,
            summary_monthly_cap_usd=monthly_cap,
            # Only a local Ollama model loads onto this PC's GPU; an Ollama
            # cloud tag is remote and must not queue behind GPU jobs. The
            # guard stops a local one running alongside another GPU job.
            gpu_touching=translate_engines.ollama_touches_local_gpu(
                engine_choice, getattr(engine, "model", None)),
            description=f"Ollama translation ({title})" if engine_choice == "ollama" else None,
            run_settings=run_settings_service.for_translate(
                drama, include_genre_notes, default_female_pronouns, glossary_terms,
                style_guidelines, style_note, engine=engine_choice,
                model=getattr(engine, "model", None), locale=locale,
                style_preset=style_preset, force_retranslate=False, **defs))
    except ConflictError:
        # The title is being deleted or cleaned up right now: its skip,
        # not the end of the whole run.
        return False


def _follow_bulk_title(job_id, per_job_id, i, total, n_titles, title):
    """Mirrors the per-title job's progress into the coordinator job until it
    stops, forwarding a cancel of the coordinator to it."""
    while True:
        # An Ollama drama can be queued behind
        # the GPU guard (see gpu_touching= in _start_bulk_title) instead of
        # starting immediately -- is_running() alone would miss that
        # state entirely and fall straight through to the "finished"
        # check while the job was still only queued.
        per_status = background_jobs.get_status(per_job_id) or {}
        if per_status.get("status") not in ("running", "queued"):
            break
        if background_jobs.is_cancel_requested(job_id):
            if per_status.get("status") == "queued":
                background_jobs.cancel_queued(per_job_id)
            else:
                background_jobs.request_cancel(per_job_id)
        background_jobs.update_progress(
            job_id, (i + (per_status.get("progress") or 0.0)) / total,
            f"Translating {i + 1}/{n_titles} -- {title} -- "
            + ("waiting for the GPU" if per_status.get("status") == "queued"
               else f"{(per_status.get('progress') or 0.0) * 100:.0f}%"))
        time.sleep(0.5)


def _record_bulk_outcome(results, did, per_job_id):
    per_status = background_jobs.get_status(per_job_id) or {}
    per_result = per_status.get("result") if isinstance(per_status.get("result"), dict) else {}
    if per_status.get("status") == "error":
        results["errors"][did] = per_status.get("error")
    elif (not per_result.get("errors") and per_result.get("cap_reached") is None) \
            or (db.get_drama(did) or {}).get("status") == "translated":
        results["translated"].append(did)
    else:
        # run_translate_job catches batch errors, so a revoked key still
        # ends "done": only a clean run (or a fully translated drama) counts.
        results["partial"][did] = ("cost cap reached" if per_result.get("cap_reached") is not None
                                   else "batch errors")
