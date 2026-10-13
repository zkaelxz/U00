"""
services/workspace_job_service.py -- the background-job runner functions
behind the Workspace and Library actions.

They are safe to run in a background thread because they touch only plain
Python objects, `background_jobs` for progress/result reporting, and the
database -- never any UI state.
"""

import os
import time

import adaptive_style
import db
import background_jobs
import ollama_unload
import translate_engines
import translation_guide as tguide
from engine_backends.run_limits import FailureStreak
import translation_finish
import emotion
import core as core_module
import whisper_models
from core import transcribe_for_timing
from services import (fixflag_transcribe, fixflag_translate, job_timing_service,
                      language_pack_service, line_provenance_service, paid_job_service,
                      run_settings_service, settings_service, timing_settings_service,
                      vdb_names_service)


CONSISTENCY_BATCH_SIZE = 60


def _id_by_idx(lines):
    """{line idx: permanent line id} as of when a job copied the lines, so
    a result keyed by position lands on the right line even if the user
    merged or split lines while the job ran. None if any line has no id
    yet (then db resolves positions against the lines as they are now)."""
    if any(getattr(ln, "id", None) is None for ln in lines):
        return None
    return {ln.idx: ln.id for ln in lines}


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
                            include_genre_notes=None, default_female_pronouns=None):
    """(glossary_terms, style_guidelines, character_names) for one drama,
    shared by the app's runs and `cli.py translate`: series glossary, style
    profile, emotion guidance, gender hints, speaker labels and
    the title's language packs.
    include_genre_notes/default_female_pronouns are the Translate toggles;
    None means the title's saved choice (resolve_style_toggles)."""
    include_genre_notes, default_female_pronouns = resolve_style_toggles(
        drama, include_genre_notes, default_female_pronouns)
    series_id = (drama or {}).get("series_id")
    glossary_terms = db.list_glossary_terms(series_id) if series_id else None
    series_chars = db.list_series_characters(series_id) if series_id else []
    drama_chars = db.list_characters_with_series_names(drama_id)
    prof = db.get_style_profile(f"series:{series_id}" if series_id else "global")
    learned = adaptive_style.profile_to_prompt_block(prof.get("profile", {})) if prof else ""
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
    # Shared with `cli.py translate`: glossary enforcement,
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


def run_transcribe_job(job_id, audio_path, whisper_size, language, use_gpu,
                        local_model_path, hf_token, initial_prompt, beam_size,
                        min_silence_duration_ms, vad_threshold=0.5, separate_vocals_first=False,
                        realign_long_segments=False, chinese_script="simplified", fast_mode=False,
                        separation_backend="auto", use_groq=False, groq_api_key=None):
    """
    Runs just the Whisper speech-recognition pass in a background thread,
    same reasoning as run_translate_job above: this is the step that
    dominates wall-clock time on a long-form file (3+ hours), so it's the
    one worth reporting progress on and not blocking the rest of the app
    for. The Qwen3-ASR text override, alignment, and diarization that can
    follow it are not part of this function (services/transcribe_service
    runs the full pipeline).

    separate_vocals_first: runs audio_preprocess.separate_vocals() on
    audio_path before transcribing, writing the vocals-only result
    alongside the original audio (as "vocals.wav" in the same folder) and
    transcribing THAT instead -- for content with a music bed under the
    dialogue, which Whisper otherwise has to fight through. Adds real
    processing time (a full Demucs pass over the whole file); a missing
    Demucs install or a separation failure is recorded the same way as a
    Whisper model-download failure below, not raised, since it's an
    expected/common outcome (an optional dependency the person hasn't
    installed) rather than a bug.

    use_groq: sends audio_path to Groq's hosted cloud Whisper
    API (core.transcribe_with_groq) instead of running local Whisper at
    all. whisper_size/beam_size/vad_threshold/fast_mode have no effect
    on this path -- Groq's own hosted model and its own VAD produce
    segments directly. A failure here (bad key, network, rate limit) is
    recorded via failed_reason="groq", the same "expected outcome, not a
    bug" treatment as a local model-download failure below.

    realign_long_segments: EXPERIMENTAL, off by default -- runs
    word_align.realign_oversized_segments() on the transcript afterward,
    splitting any oversized VAD-merged segment back into multiple
    correctly-timed lines using real word-level alignment (see
    word_align.py's own docstring for why MMS, and the real documented
    CJK failure mode it fails soft against). A missing torchaudio/uroman
    install is recorded the same way as a missing Demucs install above;
    a per-line alignment problem is NOT surfaced here at all, since
    word_align.py already degrades those individually and silently back
    to their original timing rather than raising.

    Model-download failure and "no audio detected" are expected, common
    outcomes here (a flaky connection, a silent/corrupt file), not bugs --
    both are recorded via a "failed_reason" on the result instead of
    raising, so the main script can show the same specific, actionable
    messages it always has instead of a generic error+traceback. Anything
    else that goes wrong is a real bug and is left to raise, same as
    run_translate_job -- background_jobs.start_job's own runner catches
    that and reports it as a job error.
    """
    if separate_vocals_first:
        import audio_preprocess
        background_jobs.update_progress(job_id, 0.0, "Separating vocals from background music...")
        vocals_path = os.path.join(os.path.dirname(audio_path), "vocals.wav")
        try:
            audio_path = audio_preprocess.separate_vocals(
                audio_path, vocals_path, backend=separation_backend,
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Removing background music... {frac * 100:.0f}%"),
                cancel_check_cb=lambda: background_jobs.is_cancel_requested(job_id))
        except audio_preprocess.VocalSeparationCancelled:
            background_jobs.set_result(job_id, {"failed_reason": "cancelled"})
            return
        except audio_preprocess.VocalSeparationError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "vocal_separation", "detail": str(exc)})
            return

    # separate_vocals()'s own cancel_check_cb only fires between
    # its internal chunks, so a cancel requested right at its tail (or,
    # when separation is off/skipped, a cancel requested before this
    # point is even reached) fell through this checkpoint-free gap and
    # let the expensive Whisper pass start anyway, uninterrupted, on
    # short files where separation has few or no chunk boundaries.
    if background_jobs.is_cancel_requested(job_id):
        background_jobs.set_result(job_id, {"failed_reason": "cancelled"})
        return

    gpu_fallback_msg = []
    if use_groq:
        background_jobs.update_progress(job_id, 0.0, "Transcribing via Groq's cloud API...")
        try:
            segments = core_module.transcribe_with_groq(
                audio_path, language, groq_api_key,
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Transcribing via Groq's cloud API... {frac * 100:.0f}%"))
        except core_module.GroqTranscriptionError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "groq", "detail": str(exc)})
            return
    else:
        try:
            segments = transcribe_for_timing(
                audio_path, whisper_size, language=language, use_gpu=use_gpu,
                local_model_path=local_model_path, hf_token=hf_token,
                initial_prompt=initial_prompt, beam_size=beam_size,
                min_silence_duration_ms=min_silence_duration_ms, vad_threshold=vad_threshold,
                on_gpu_fallback=lambda exc: gpu_fallback_msg.append(whisper_models.short_reason(exc)),
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Transcribing... {frac * 100:.0f}%"),
                fast_mode=fast_mode)
        except whisper_models.ModelDownloadError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "model_download", "detail": str(exc)})
            return

    if not segments:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return

    # Whisper's own pass has no cancel checkpoint of its own yet -- this is the next point a
    # cancel requested mid-transcription can actually be honored. The
    # expensive work is already done by here, so a cancel caught this
    # late just skips the optional realign step rather than discarding
    # the transcript -- never lose already-done work over a cancel, same
    # rule this app follows for an optional add-on failing outright.
    word_align_error = None
    if realign_long_segments and not background_jobs.is_cancel_requested(job_id):
        import word_align
        background_jobs.update_progress(job_id, 1.0, "Splitting long merged lines...")
        try:
            segments = word_align.realign_oversized_segments(
                segments, audio_path, language, chinese_script=chinese_script,
                progress_cb=lambda done, total: background_jobs.update_progress(
                    job_id, 1.0, f"Splitting long merged lines: {done} of {total}"),
                cancel_check=lambda: background_jobs.is_cancel_requested(job_id))
        except word_align.WordAlignError as exc:
            # A missing dependency here must never cost the transcription
            # itself (the expensive part, already done) -- proceed with
            # the unmodified segments and just report the issue, the same
            # "never lose already-done work over an optional add-on
            # failing" rule this app follows everywhere else.
            word_align_error = str(exc)

    whisper_models.release_gpu_models()  # transcription stage done
    result = {
        "segments": segments,
        "gpu_fallback": gpu_fallback_msg[0] if gpu_fallback_msg else None,
        "word_align_error": word_align_error,
        **ollama_unload.take_notice_result(),
    }
    if gpu_fallback_msg:
        result["device_notice"] = whisper_models.gpu_fallback_notice("Transcription", gpu_fallback_msg[0])
    background_jobs.set_result(job_id, result)


def run_hardsub_ocr_job(job_id, video_path, language, sample_interval, ocr_backend,
                         chinese_script="simplified", tesseract_cmd=None):
    """
    Runs hardsub_ocr's sample-frames -> auto-detect-caption-band -> OCR ->
    dedupe pipeline in a background thread. Same reasoning as
    run_transcribe_job above: OCR-ing every sampled frame of a long video
    is slow, and it needs to report real progress and not block the rest
    of the app while it runs. The result already carries real per-cue
    timing from the OCR pass itself, so unlike a raw Whisper transcript it
    needs no separate alignment step -- the polling code on the other end
    builds Lines directly from it, the same way Whisper's own
    speech-to-text override (rather than an aligned user transcript) does.
    """
    import hardsub_ocr
    cues = hardsub_ocr.extract_hardsub_subtitles(
        video_path, language=language, sample_interval=sample_interval,
        ocr_backend=ocr_backend, chinese_script=chinese_script, tesseract_cmd=tesseract_cmd,
        job_id=job_id, cancel_check=lambda: _raise_if_cancelled(job_id),
        progress_cb=lambda frac: background_jobs.update_progress(
            job_id, frac, f"Reading captions from video... {frac * 100:.0f}%"))
    if not cues:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return
    background_jobs.set_result(job_id, {"segments": cues})


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
            usage_cb=lambda inp, out: db.log_usage(
                drama_id, engine_choice, getattr(engine, "model", engine_choice), "emotion_detect",
                inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)),
            cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    db.save_emotions(drama_id, emap, id_by_idx=_id_by_idx(lines))
    background_jobs.set_result(job_id, {"emotions": emap, **paid_job_service.stop_fields(limits)})


def run_sensevoice_job(job_id, drama_id, lines, audio_path, drama_dir, use_gpu):
    """SenseVoice audio emotion/event tags per line, in the background --
    stored in their own file (sensevoice_tags.AUDIO_TAGS_FILE), never
    merged into the text-based emotions that feed translation."""
    import sensevoice_tags
    try:
        tags = sensevoice_tags.tag_lines(
            audio_path, lines, use_gpu=use_gpu,
            cancel_check=lambda: _raise_if_cancelled(job_id),
            progress_cb=lambda frac: background_jobs.update_progress(
                job_id, frac, f"Listening for emotion and sounds... {frac * 100:.0f}%"))
    finally:
        whisper_models.release_gpu_models()
    sensevoice_tags.save_audio_tags(drama_dir, tags)
    background_jobs.set_result(job_id, {"tagged": len(tags)})


def _raise_if_cancelled(job_id):
    """Stops a review job between LLM batches once cancel is requested."""
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


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
    user's value; the job's result for that line is dropped.
    """
    started_with = {ln.id: (ln.flag or "", ln.flag_note or "") for ln in lines}
    stats = {}
    with paid_job_service.job_calls(job_id, drama_id, engine, engine_choice, "flag_review") as limits:
        translate_engines.flag_uncertain_lines(
            lines, engine, source_language=lang, stats=stats,
            progress_cb=lambda frac: background_jobs.update_progress(
                job_id, frac, f"Checking for lines that need a second look... {frac * 100:.0f}%"),
            usage_cb=lambda inp, out: db.log_usage(
                drama_id, engine_choice, getattr(engine, "model", engine_choice), "flag_review",
                inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)),
            cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    items = []
    for ln in lines:
        if ln.id is None:
            continue
        before = started_with[ln.id]
        if (ln.flag or "", ln.flag_note or "") != before:
            items.append((ln.id, {"flag": ln.flag or None, "flag_note": ln.flag_note or ""},
                          {"flag": before[0], "flag_note": before[1]}))
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
        issues, failed_batches, total_batches = translate_engines.check_consistency_llm(
            lines, engine, batch_size=CONSISTENCY_BATCH_SIZE, source_language=lang,
            usage_cb=lambda inp, out: db.log_usage(
                drama_id, engine_choice, getattr(engine, "model", engine_choice), "consistency_check",
                inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)),
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
            usage_cb=lambda inp, out: db.log_usage(
                drama_id, engine_choice, getattr(engine, "model", engine_choice), "translation_notes",
                inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)),
            cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    if found_notes:
        db.save_translation_notes(drama_id, found_notes, id_by_idx=_id_by_idx(lines))
    background_jobs.set_result(job_id, {"note_count": len(found_notes),
                                        **paid_job_service.stop_fields(limits)})


def run_fix_flagged_lines_job(job_id, drama_id, lines, audio_path, whisper_size, use_gpu,
                               source_language, engine, engine_choice, cost_cap_usd=None,
                               locale="en-US", include_genre_notes=None,
                               default_female_pronouns=None, style_note=""):
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
        default_female_pronouns=default_female_pronouns)
    base_context = translate_engines.build_translation_context(
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
            job_id, flagged, audio_path, fixflag_transcribe.hearing_settings(
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


def run_bulk_series_translate_job(job_id, drama_ids, api_keys: dict, default_locale: str = "en-US",
                                  ollama_base_url: str = None, gemini_free_tier: bool = False,
                                  models: dict = None, monthly_cap: float = 0,
                                  expected_engines: dict = None, allow_paid_summary: bool = True,
                                  include_genre_notes: bool = None,
                                  default_female_pronouns: bool = None):
    """Translates every drama in drama_ids that has no
    translation yet, queued ONE AT A TIME rather than all at once (same
    GPU/API-load reasoning as everywhere else in this app that queues
    rather than parallelizes). Each drama uses its own saved engine
    (`drama.translation_engine`) and, if it belongs to a series, that
    series' own glossary and style hints -- the same settings a single-drama
    run would use (mirrors cli.cmd_translate). Skips (does not queue) a drama that already has a
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
    db.get_month_spend() before each drama, same as Workspace's and
    cli.py translate's own per-run cap resolution, since this was the one
    translate path in the app that didn't enforce it at all. It is also
    checked before each paid episode-summary call. allow_paid_summary=False
    (a caller without engines.paid) skips a cloud summary engine picked in
    Settings, so only the engines the caller was authorized for run.
    include_genre_notes/default_female_pronouns: the Translate toggles,
    applied to every drama in the queue (defaults as the API).
    """
    # Imported here: translate_run_service imports this module.
    from services import settings_service, translate_run_service
    # Same Settings episode-summary engine as a single run (None if it
    # can't be built), at this job's own Ollama URL.
    summary_engine, summary_choice = translate_run_service.pick_summary_engine(
        ollama_base_url, allow_paid=allow_paid_summary)
    # The Settings default the single-title form starts from, so a title
    # translated in bulk matches one translated alone.
    style_note = settings_service.get("default_style_note") or ""
    if (summary_engine is not None and not monthly_cap
            and translation_finish._summary_engine_is_paid(summary_engine, summary_choice)):
        summary_engine = None  # a paid summary is paid work nobody watches: it needs a cap
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

        engine_choice = drama.get("translation_engine") or settings_service.get("default_engine")
        # expected_engines: what the caller was checked against; an engine
        # changed since then is skipped rather than used unchecked.
        if (engine_choice not in translate_engines.ENGINES
                or expected_engines is not None and expected_engines.get(did) != engine_choice):
            results["skipped_engine_changed"].append(did)
            continue
        needs_key = engine_choice not in translate_engines.KEYLESS_ENGINES
        api_key = api_keys.get(engine_choice)
        if needs_key and not api_key:
            results["skipped_no_key"].append(did)
            continue
        if not api_key:
            api_key = "local"

        # Same cap logic as Workspace's Translate button and `cli.py
        # translate` -- only the engines that bill per token and report
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
            continue
        if cap_applies and monthly_cap:
            cost_cap, refusal = translate_engines.resolve_cost_cap(
                None, monthly_cap, db.get_month_spend())
            if refusal:
                results["skipped_cap"].append(did)
                continue

        try:
            engine = translate_engines.get_engine(
                engine_choice, api_key, models.get(engine_choice),
                free_tier=engine_choice == "gemini" and gemini_free_tier,
                base_url=ollama_base_url if engine_choice == "ollama" else None)
        except Exception as e:
            results["errors"][did] = translate_engines.redact_secrets(str(e))
            continue

        lines = core_module.lines_from_rows(rows)
        # Must not always be "audio_drama" (wrong for a novel-narration
        # drama): same per-content-mode default the shared
        # translate-finishing helper and `cli.py translate` use.
        is_novel = drama.get("content_mode") == "novel_narration"
        style_preset = "novel" if is_novel else "audio_drama"
        glossary_terms, style_guidelines, _ = build_run_style_context(
            did, drama, lines, style_preset, include_genre_notes=include_genre_notes,
            default_female_pronouns=default_female_pronouns)
        defs = translate_run_service.get_translate_config_defaults(is_novel, engine)
        novel_reference = None
        if drama.get("novel_reference_filename"):
            novel_path = os.path.join(db.drama_dir(did), drama["novel_reference_filename"])
            if os.path.exists(novel_path):
                with open(novel_path, encoding="utf-8") as f:
                    novel_reference = f.read()

        # start_job returns False when someone else's translate_<id> run
        # started since the check above: skip it rather than adopt (and
        # possibly cancel) that run below.
        started = background_jobs.start_job(
            per_job_id, run_translate_job,
            per_job_id, did, lines, engine, drama, style_note, novel_reference, False,
            default_locale, glossary_terms, style_guidelines, engine_choice, style_preset,
            defs["context_window"], settings_service.get("ollama_num_ctx_override") or None,
            cost_cap_usd=cost_cap,
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
                model=getattr(engine, "model", None), locale=default_locale,
                style_preset=style_preset, force_retranslate=False, **defs))
        if not started:
            results["skipped_running"].append(did)
            continue

        while True:
            # An Ollama drama can be queued behind
            # the GPU guard (see gpu_touching= above) instead of
            # starting immediately -- is_running() alone would miss that
            # state entirely and fall straight through to the "finished"
            # check below while the job was still only queued.
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
                f"Translating {i + 1}/{len(drama_ids)} -- {title} -- "
                + ("waiting for the GPU" if per_status.get("status") == "queued"
                   else f"{(per_status.get('progress') or 0.0) * 100:.0f}%"))
            time.sleep(0.5)

        if background_jobs.is_cancel_requested(job_id):
            break
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
        # A title stopped by the cap says nothing about the engine.
        if failed_titles.record(failed=did in results["errors"] or (
                did in results["partial"] and results["partial"][did] == "batch errors"),
                succeeded=did in results["translated"]):
            results["stopped"] = f"Stopped: {failed_titles.count} titles in a row failed"
            break

    results["cancelled"] = background_jobs.is_cancel_requested(job_id)
    background_jobs.update_progress(job_id, 1.0, "Done")
    background_jobs.set_result(job_id, results)
