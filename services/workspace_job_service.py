"""
services/workspace_job_service.py -- the background-job runner functions
behind the Workspace and Library actions.

They are safe to run in a background thread because they touch only plain
Python objects, `background_jobs` for progress/result reporting, and the
database -- never any UI state.
"""

import logging
import os
import time

import adaptive_style
import db
import background_jobs
import ollama_unload
import translate_engines
import translation_guide as tguide
import bulk_translate
import emotion
import core as core_module
from core import transcribe_for_timing
from services import (auth_service, fixflag_transcribe, job_timing_service,
                      language_pack_service, library_restore_sql, line_provenance_service,
                      run_settings_service, settings_service)


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
                       summary_monthly_cap_usd=None, own_lines_only=False, thinking=None):
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

    target_ids: optional set of permanent line ids (set by the API
    start) -- only those lines are translated; None = every eligible line.

    own_lines_only (with target_ids): English is written only on the target
    lines and only where it still is what the job loaded -- a line edited
    meanwhile keeps the edit, with no Reflect note, substitution or flag
    from this run -- and the glossary's exact-term substitution touches
    only the target lines.
    """
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
    scene_aware = settings_service.get_preference("scene_aware_batches")
    # What produced each line, and per-stage timing.
    provenance = line_provenance_service.translate_run_tracker(
        drama_id, lines, engine, engine_choice, glossary_terms, locale=locale,
        style_preset=style_preset, reflect=bool(reflect), context_window=context_window,
        context_window_ahead=context_window_ahead, batch_size=batch_size,
        style_note=style_note or "", style_guidelines=style_guidelines or "",
        scene_aware_batches=scene_aware, thinking=thinking)

    if own_lines_only:
        _save, _notes = bulk_translate.own_lines_callbacks(drama_id, lines, provenance)
    else:
        def _save(ls):
            db.save_lines(drama_id, ls, fields=("en",))
            provenance(ls)

        def _notes(notes):
            db.save_translation_notes(drama_id, notes, id_by_idx=_id_by_idx(lines))

    job_timing_service.mark_stage(job_id, "Translate")
    _, errors = translate_engines.translate_lines_with_engine(
        lines, engine, drama_meta=drama_meta, style_note=style_note,
        novel_reference=novel_reference, force_retranslate=force_retranslate,
        locale=locale, glossary_terms=glossary_terms, style_guidelines=style_guidelines,
        context_window=context_window, context_window_ahead=context_window_ahead,
        batch_size=batch_size, character_names=character_names,
        ollama_num_ctx_override=ollama_num_ctx_override,
        reflect=reflect, target_ids=target_ids, scene_aware_batches=scene_aware,
        thinking=thinking, cost_cap_usd=cost_cap_usd,
        cap_cb=lambda spent: cap_reached.update(spent=spent),
        notes_cb=_notes,
        detail_cb=lambda frac, message: background_jobs.update_progress(
            job_id, frac, translate_engines.progress_message_with_rate_status(
                engine, frac, base=message)),
        # Translation owns `en` and nothing else -- a flag job, a merge or
        # the user's own edits can run alongside without being overwritten.
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
    # Shared with `cli.py translate`: glossary enforcement,
    # density flags, the version, persisted errors, and a "translated"
    # status only once nothing is left untranslated.
    if not bulk_translate.finish_translation_run(
            drama_id, lines, engine, engine_choice, style_preset, glossary_terms, errors,
            cancelled=background_jobs.is_cancel_requested(job_id),
            summary_engine=summary_engine, summary_engine_choice=summary_engine_choice,
            summary_monthly_cap_usd=summary_monthly_cap_usd,
            line_scoped=target_ids is not None,
            enforce_ids=set(target_ids) if own_lines_only and target_ids is not None else None,
            flags_needing_recheck=recheck):
        background_jobs.set_result(job_id, {"errors": errors, "lines_replaced": True,
                                            "cap_reached": cap_reached.get("spent"),
                                            **_fallback_result(engine)})
        return

    background_jobs.set_result(job_id, {"errors": errors, "cap_reached": cap_reached.get("spent"),
                                        **({"flags_needing_recheck": sorted(recheck)}
                                           if recheck else {}),
                                        **_fallback_result(engine)})


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
                on_gpu_fallback=lambda exc: gpu_fallback_msg.append(core_module.short_reason(exc)),
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Transcribing... {frac * 100:.0f}%"),
                fast_mode=fast_mode)
        except core_module.ModelDownloadError as exc:
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

    core_module.release_gpu_models()  # transcription stage done
    result = {
        "segments": segments,
        "gpu_fallback": gpu_fallback_msg[0] if gpu_fallback_msg else None,
        "word_align_error": word_align_error,
        **ollama_unload.take_notice_result(),
    }
    if gpu_fallback_msg:
        result["device_notice"] = core_module.gpu_fallback_notice("Transcription", gpu_fallback_msg[0])
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
    background_jobs.set_result(job_id, {"emotions": emap})


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
        core_module.release_gpu_models()
    sensevoice_tags.save_audio_tags(drama_dir, tags)
    background_jobs.set_result(job_id, {"tagged": len(tags)})


def _raise_if_cancelled(job_id):
    """Stops a review job between LLM batches once cancel is requested."""
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


def run_flag_job(job_id, drama_id, lines, engine, engine_choice):
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
    translate_engines.flag_uncertain_lines(
        lines, engine,
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
    background_jobs.set_result(job_id, {"flagged_count": flagged})


def run_consistency_job(job_id, drama_id, lines, engine, engine_choice):
    """
    Runs check_consistency_llm in a background thread. Backgrounding it,
    same as Review queue and Emotion detection, is what lets it run at the same time as those instead of forcing them
    to queue up one after another.
    """
    issues, failed_batches, total_batches = translate_engines.check_consistency_llm(
        lines, engine,
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_choice, getattr(engine, "model", engine_choice), "consistency_check",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)),
        cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    db.save_consistency_issues(drama_id, issues)
    background_jobs.set_result(job_id, {
        "issue_count": len(issues),
        "failed_batches": failed_batches,
        "total_batches": total_batches,
    })


def run_translation_notes_job(job_id, drama_id, lines, engine, engine_choice):
    """
    Runs generate_translation_notes_llm in a background thread -- same
    reasoning as run_consistency_job above.
    """
    found_notes = tguide.generate_translation_notes_llm(
        lines, engine,
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_choice, getattr(engine, "model", engine_choice), "translation_notes",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)),
        cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    if found_notes:
        db.save_translation_notes(drama_id, found_notes, id_by_idx=_id_by_idx(lines))
    background_jobs.set_result(job_id, {"note_count": len(found_notes)})


def run_fix_flagged_lines_job(job_id, drama_id, lines, audio_path, whisper_size, use_gpu,
                               source_language, engine, engine_choice, cost_cap_usd=None,
                               locale="en-US", include_genre_notes=None,
                               default_female_pronouns=None, style_note=""):
    """
    Bulk version of the single-line 🔧 tools in Review & edit: for every
    currently-flagged line, re-transcribes its own timing window from the
    original audio (skipped if there's no audio -- novel-narration has
    none to re-transcribe) and re-translates the result, clearing the
    flag on whichever lines that actually changed something for. Uses
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
    flagged = [ln for ln in lines if ln.flag]
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
    fixed_count = 0
    spent = 0.0
    cap_reached = None
    # Both stages below used to be able to lose every
    # already-fixed line, not just the one that failed. The re-transcribe
    # call had no `except` at all, so a real exception (e.g. a
    # model-download failure partway through) escaped the loop entirely --
    # and since db.save_lines() only ran once at the very end, that
    # aborted the whole job before anything got saved. The translate call
    # had an `except Exception: pass` that silently swallowed the error
    # (including an auth/quota failure that would repeat for every
    # remaining line), leaving the user with "Fixed 0 of N" and no
    # explanation. Now every per-line failure is caught, recorded, and
    # the line stays flagged, but the loop keeps going and the fixes made
    # so far are saved in a `finally` so a later failure can't erase them
    # (the cost-cap break below is a clean, expected stop, not a
    # failure, but the same `finally` covers it too).
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
        for i, ln in enumerate(flagged):
            # A cancel seen while hearing is not re-read: lines heard are fixed first.
            if not cancelled:
                _raise_if_cancelled(job_id)
            heard = heard_by_idx.get(ln.idx)
            if heard is not None:
                if heard.get("error"):
                    errors.append(heard["error"])
                elif heard["text"]:
                    ln.zh = heard["text"]
            if ln.zh.strip() and translate_engines.is_english_line(ln):
                ln.en = ln.zh
                ln.flag, ln.flag_note = None, ""
                fixed_count += 1
            elif ln.zh.strip() and not cancelled:
                try:
                    translated = engine.translate_batch(
                        [ln.zh], {**base_context,
                                  "speaker_labels": [character_names.get(ln.speaker)],
                                  "line_languages": translate_engines.tagged_line_languages(
                                      [ln], source_language)})[0]
                    if hasattr(engine, "last_usage"):
                        cost = translate_engines.estimate_cost_for_engine(
                            engine, engine.last_usage.get("input_tokens", 0),
                            engine.last_usage.get("output_tokens", 0))
                        spent += cost
                        db.log_usage(drama_id, engine_choice, getattr(engine, "model", engine_choice),
                                     "fix_flagged_line", engine.last_usage.get("input_tokens", 0),
                                     engine.last_usage.get("output_tokens", 0), cost)
                    if translated.strip():
                        ln.en = translated
                        ln.flag, ln.flag_note = None, ""
                        fixed_count += 1
                except Exception as e:
                    # leave the line flagged rather than lose the source fix silently
                    errors.append(translate_engines.redact_secrets(
                        f"line {ln.idx + 1} translation: {e}"))
            background_jobs.update_progress(job_id, (i + 1) / max(len(flagged), 1),
                                            f"Fixing flagged lines... {i + 1}/{len(flagged)}")
            if cost_cap_usd is not None and spent >= cost_cap_usd and i + 1 < len(flagged):
                cap_reached = spent
                break
    finally:
        if audio_path and os.path.exists(audio_path):
            core_module.release_gpu_models()  # re-transcription stage done
        db.save_lines(drama_id, lines, fields=("zh", "en", "flag", "flag_note"))
    if cancelled:
        raise background_jobs.JobCancelled(job_id)
    background_jobs.set_result(job_id, {"fixed_count": fixed_count, "total_flagged": total_flagged,
                                        "errors": errors[:20], "cap_reached": cap_reached})


# Restore-a-backup guardrails -- generous, not tight, since a
# full library backup legitimately includes media (audio, video); they
# exist to catch a corrupted or accidentally-huge zip failing safely
# (before it fills the disk), not to defend against a malicious upload in
# this single-user app.
MAX_RESTORE_MEMBERS = 500_000
MAX_RESTORE_MEMBER_BYTES = 50 * 1024 ** 3  # 50 GiB, any one file
MAX_RESTORE_TOTAL_BYTES = 200 * 1024 ** 3  # 200 GiB, expanded total


# Top-level library entries a restore never takes from an upload and
# instead carries across from the current library: saved backups/exports,
# saved site sign-ins, approved source profiles and the browser-extension
# token, plus the temp folder of in-flight work. (Upload members under these names are skipped, so a planted
# backups/exports/x.zip can never become the "latest export".)
def restore_kept_names():
    import page_server
    import storage
    from sources import store as src_store
    return ("backups", src_store.BROWSER_PROFILES_DIRNAME, "source_profiles",
            page_server.TOKEN_FILENAME, storage.TEMP_DIRNAME)


# Who may sign in and what they may do: never from the upload (auth_service).
_RESTORE_KEPT_AUTH_TABLES = auth_service.RESTORE_KEPT_TABLES

# app_settings keys a restore takes from the current library, never from the
# upload: the automatic-backup identity (auto_backup_service.IDENTITY_KEY).
# A backup from another library must not bring that library's id (its
# copies in a shared folder would then look like this one's and be rotated
# out), and an older backup must not roll the copy sequence back.
_RESTORE_KEPT_APP_SETTINGS = ("auto_backup.identity",)


# SQLite side files a restore never extracts: the validated library.db /
# sources.db image must be exactly what goes live.
_RESTORE_SKIPPED_SIDECARS = tuple(f"{base}{ext}" for base in ("library.db", "sources.db")
                                  for ext in ("-wal", "-shm", "-journal"))


def _ro_uri(path: str, ro: bool = True) -> str:
    from urllib.parse import quote
    return f"file:{quote(os.path.abspath(path))}" + ("?mode=ro" if ro else "")


def _open_carry_conn(path: str):
    import sqlite3
    conn = sqlite3.connect(_ro_uri(path, ro=False), uri=True, isolation_level=None)
    conn.execute("PRAGMA trusted_schema = OFF")
    return conn


def _table_names(conn, schema: str) -> list:
    return [r[0] for r in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]


def _current_state_marker(library_dir: str):
    """(max audit_log id, sources settings rows) of the live library,
    compared right before the swap so a sign-in or settings change made
    while the restore was preparing isn't silently dropped.

    Known limits: a change landing between this last comparison and the
    rename is still lost (a small window, no cross-process lock), and
    only audited auth changes move the audit id -- a session created or
    revoked without an audit entry, or a sources.db table other than
    settings, isn't covered (sessions are revoked by the restore anyway)."""
    import sqlite3
    marker = []
    for name, sql in (("library.db", "SELECT MAX(id) FROM audit_log"),
                      ("sources.db", "SELECT key, value FROM settings ORDER BY key")):
        path = os.path.join(library_dir, name)
        if not os.path.isfile(path):
            marker.append(None)
            continue
        try:
            conn = sqlite3.connect(_ro_uri(path), uri=True)
            try:
                marker.append(tuple(tuple(r) for r in conn.execute(sql).fetchall()))
            finally:
                conn.close()
        except sqlite3.Error:
            marker.append(None)
    return tuple(marker)


BAD_LIBRARY_DB = ("The backup's library.db is not a readable Baihe library "
                   "database.")
BAD_SOURCES_DB = "The backup's sources.db is not a readable database."

def _schema_sql_allowed(sql: str) -> bool:
    """True only for CREATE TABLE / CREATE [UNIQUE] INDEX text (comments
    and whitespace ignored, case-insensitive). Everything else -- triggers,
    views, virtual tables, TEMP objects -- is refused, whatever
    sqlite_master's `type` column claims (SQLite re-parses `sql` and
    ignores `type`, so a writable_schema-planted row can lie about it)."""
    import re
    text = re.sub(r"--[^\n]*|/\*.*?(\*/|$)", " ", sql, flags=re.S)
    text = " ".join(text.split()).upper()
    if not re.match(r"CREATE (TABLE|(UNIQUE )?INDEX)[ \"'`\[(]", text + " "):
        return False
    # Generated columns: quick_check and the row copy would evaluate them.
    return "GENERATED" not in text and not re.search(r"\bAS ?\(", text)


def _check_uploaded_db(db_path: str, message: str, need_table: str = None) -> None:
    """Raises ValueError(message) unless the uploaded file is SQLite,
    passes quick_check and every sqlite_master row is a plain table or
    index (see _schema_sql_allowed; NULL sql only for sqlite_autoindex_*).

    Why this is safe to open: read-only + immutable, trusted_schema OFF,
    and the only statements run are PRAGMA quick_check and a SELECT on
    sqlite_master. Loading the schema parses CREATE text but executes
    nothing: a trigger only runs on a write (none here) and a virtual
    table's module is only connected when that table is queried (never
    here). A schema SQLite can't parse fails the open -> refused.
    (Python 3.11's sqlite3 has no Connection.setconfig for
    SQLITE_DBCONFIG_DEFENSIVE, so that option isn't used.)"""
    import sqlite3
    if not os.path.isfile(db_path) or os.path.islink(db_path):
        raise ValueError(message)
    conn = None
    try:
        conn = sqlite3.connect(_ro_uri(db_path) + "&immutable=1", uri=True)
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.execute("PRAGMA ignore_check_constraints = ON")
        rows = conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        for _type, name, sql in rows:
            if sql is None:
                if not str(name).startswith("sqlite_autoindex_"):
                    raise ValueError(message)
            elif not _schema_sql_allowed(sql):
                raise ValueError(message)
        if [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()] != ["ok"]:
            raise ValueError(message)
        if need_table and not any(t == "table" and n == need_table for t, n, _ in rows):
            raise ValueError(message)
    except sqlite3.Error:
        raise ValueError(message) from None
    finally:
        if conn is not None:
            conn.close()


def validate_staged_library_db(db_path: str) -> None:
    """ValueError (fixed text, no path) unless an uploaded library.db is
    SQLite, passes quick_check, holds only plain tables/indexes and has a
    dramas table."""
    _check_uploaded_db(db_path, BAD_LIBRARY_DB, need_table="dramas")


def _rebuild_from_upload(fresh_path: str, upload_path: str, skip_tables=(),
                         live_path: str = None, live_tables=(), message: str = ""):
    """fresh_path already holds the app's own schema. Copies rows from the
    (validated) upload for every app table except skip_tables, and rows
    from the live database for live_tables, never taking a table
    definition from either. Raises ValueError(message) on any SQLite
    error."""
    import sqlite3
    try:
        conn = _open_carry_conn(fresh_path)
        try:
            conn.execute("ATTACH DATABASE ? AS up", (_ro_uri(upload_path) + "&immutable=1",))
            if live_path and os.path.isfile(live_path):
                conn.execute("ATTACH DATABASE ? AS cur", (_ro_uri(live_path),))
            else:
                live_path = None
            conn.execute("BEGIN")
            up_tables = set(_table_names(conn, "up"))
            cur_tables = set(_table_names(conn, "cur")) if live_path else set()
            for t in _table_names(conn, "main"):
                if t in live_tables:
                    if t in cur_tables:
                        library_restore_sql.copy_rows(conn, "cur", t)
                elif t not in skip_tables and t in up_tables:
                    library_restore_sql.copy_rows(conn, "up", t)
            conn.execute("COMMIT")
        finally:
            conn.close()
    except sqlite3.Error:
        raise ValueError(message) from None


def _prepare_scratch(path: str):
    """Drops sqlite_stat* tables (query-planner statistics the app never
    writes) from the scratch copy before anything is run on it."""
    conn = _open_carry_conn(path)
    try:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                    "AND name LIKE 'sqlite_stat%'").fetchall():
            conn.execute(f'DROP TABLE "{name}"')
    finally:
        conn.close()


def _checkpoint(path: str):
    conn = _open_carry_conn(path)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
    finally:
        conn.close()


def _carry_app_settings(conn, live_path: str):
    """Replaces the staged library's _RESTORE_KEPT_APP_SETTINGS rows with the
    live library's (none there = none in the restored one, so a new
    identity is made on first use)."""
    marks = ", ".join("?" for _ in _RESTORE_KEPT_APP_SETTINGS)
    conn.execute(f"DELETE FROM main.app_settings WHERE key IN ({marks})",
                 _RESTORE_KEPT_APP_SETTINGS)
    if not os.path.isfile(live_path):
        return
    conn.execute("ATTACH DATABASE ? AS cur", (_ro_uri(live_path),))
    try:
        if "app_settings" in _table_names(conn, "cur"):
            conn.execute(f"INSERT INTO main.app_settings (key, value) SELECT key, value "
                         f"FROM cur.app_settings WHERE key IN ({marks})",
                         _RESTORE_KEPT_APP_SETTINGS)
    finally:
        conn.execute("DETACH DATABASE cur")


def _build_staged_databases(staging_dir: str, library_dir: str) -> None:
    """Replaces the uploaded library.db / sources.db in staging with fresh
    files built from the app's own schema (db.migrate_database_file /
    sources.store._SCHEMA) and filled with the upload's rows -- except
    the auth tables (current rows, sessions emptied: every session is
    revoked) and the sources settings table (current rows). Running and
    queued job_records rows are marked cancelled and gpu_lock is cleared
    (a backup can only hold stale ones). The uploaded files never become
    a live schema: they are only ATTACHed read-only after
    _check_uploaded_db."""
    import shutil
    import sqlite3
    from jobs import gpu_slots
    from sources import store as src_store
    staged = os.path.join(staging_dir, "library.db")
    upload = os.path.join(staging_dir, ".uploaded_library.db")
    os.replace(staged, upload)
    # The app's data migrations (line refs -> line ids, profiles)
    # run on a scratch COPY of the validated upload -- safe because the
    # allowlist leaves only plain tables/indexes; its DDL is discarded, only
    # its rows are copied into the fresh file below.
    scratch = os.path.join(staging_dir, ".migrated_upload.db")
    try:
        shutil.copyfile(upload, scratch)
        try:
            _prepare_scratch(scratch)
            db.migrate_database_file(scratch)
            _checkpoint(scratch)
            db.migrate_database_file(staged)
        except Exception:
            logging.getLogger(__name__).warning("Restore: staged database migration failed",
                                                exc_info=True)
            raise ValueError(BAD_LIBRARY_DB) from None
        _rebuild_from_upload(staged, scratch, skip_tables=_RESTORE_KEPT_AUTH_TABLES,
                             live_path=os.path.join(library_dir, "library.db"),
                             live_tables=auth_service.RESTORE_LIVE_TABLES,
                             message=BAD_LIBRARY_DB)
        try:
            conn = _open_carry_conn(staged)
            try:
                conn.execute("UPDATE job_records SET status = 'cancelled', finished_at = ?, "
                             "cancel_requested = 0 WHERE status IN ('queued', 'running')",
                             (time.time(),))
                gpu_slots.clear(conn)
                _carry_app_settings(conn, os.path.join(library_dir, "library.db"))
                conn.execute("PRAGMA journal_mode = DELETE")
            finally:
                conn.close()
        except sqlite3.Error:
            raise ValueError(BAD_LIBRARY_DB) from None
    finally:
        for path in (upload, scratch, scratch + "-wal", scratch + "-shm"):
            if os.path.lexists(path):
                os.remove(path)

    staged_src = os.path.join(staging_dir, "sources.db")
    if not os.path.lexists(staged_src):
        return
    upload_src = os.path.join(staging_dir, ".uploaded_sources.db")
    os.replace(staged_src, upload_src)
    try:
        try:
            conn = _open_carry_conn(staged_src)
            try:
                conn.executescript(src_store.SCHEMA)
            finally:
                conn.close()
        except sqlite3.Error:
            raise ValueError(BAD_SOURCES_DB) from None
        _rebuild_from_upload(staged_src, upload_src, skip_tables=("settings",),
                             live_path=os.path.join(library_dir, "sources.db"),
                             live_tables=("settings",), message=BAD_SOURCES_DB)
    finally:
        os.remove(upload_src)


def _remove_entry(path: str):
    import shutil
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def restore_library_backup(zip_bytes: bytes, library_dir: str, before_swap=None) -> None:
    """Validate an uploaded backup zip and swap it in for
    library_dir, without ever destroying the existing library if the
    upload turns out to be invalid.

    Order, all before library_dir is touched: size/member checks; extract
    to a staging directory, skipping kept names and SQLite side files,
    then deleting anything the filesystem resolves to a kept name (case,
    trailing dots); check the uploaded library.db (and sources.db) with
    SQLite (quick_check, only plain tables/indexes, dramas table); build
    FRESH databases from the app's own schema and copy the upload's rows
    into them (_build_staged_databases -- the upload never becomes a live
    schema, and auth rows and sources settings come from the current
    library), so an unusable upload is refused here rather than after the
    swap; then before_swap() and a check that the live
    auth/settings state didn't change meanwhile. Only then is library_dir
    renamed aside and the staging directory renamed in (the original is
    renamed back on failure). The current kept entries (backups/, browser
    profiles, source profiles, extension token) are then moved into the
    restored library; if one can't be moved, the previous library is left
    beside the restored one instead of being deleted. Raises (ValueError,
    zipfile.BadZipFile, OSError, ServiceError, ...) with nothing changed
    if any check fails.

    before_swap: optional callable run right before the rename; if it
    raises, nothing is changed and the exception propagates.
    """
    import io
    import shutil
    import sqlite3
    import tempfile
    import zipfile

    from services.service_errors import ConflictError

    kept = restore_kept_names()
    staging_dir = None
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            if "library.db" not in zf.namelist():
                raise ValueError("This doesn't look like a Baihe library "
                                  "backup (no library.db found inside the zip).")

            infos = zf.infolist()
            if len(infos) > MAX_RESTORE_MEMBERS:
                raise ValueError(
                    f"Backup zip contains {len(infos):,} files, more than the "
                    f"{MAX_RESTORE_MEMBERS:,}-file limit -- this looks corrupted "
                    "or unsafe to extract.")
            total_size = 0
            for info in infos:
                if info.file_size > MAX_RESTORE_MEMBER_BYTES:
                    raise ValueError(
                        f"Backup zip contains a file ({info.filename}) that would "
                        f"expand to {info.file_size / 1024 ** 3:.1f} GiB, more than "
                        f"the {MAX_RESTORE_MEMBER_BYTES / 1024 ** 3:.0f} GiB "
                        "per-file limit -- this looks corrupted or unsafe to extract.")
                total_size += info.file_size
                if total_size > MAX_RESTORE_TOTAL_BYTES:
                    raise ValueError(
                        f"Backup zip would expand to more than "
                        f"{MAX_RESTORE_TOTAL_BYTES / 1024 ** 3:.0f} GiB total -- "
                        "this looks corrupted or unsafe to extract.")

            if zf.testzip() is not None:
                raise ValueError("Backup zip is corrupted.")
            parent_dir = os.path.dirname(os.path.abspath(library_dir)) or "."
            staging_dir = tempfile.mkdtemp(prefix=".restore_staging_", dir=parent_dir)
            for info in infos:
                norm = info.filename.replace("\\", "/")
                top = norm.lstrip("/").split("/", 1)[0]
                if top in kept or norm in _RESTORE_SKIPPED_SIDECARS:
                    continue
                zf.extract(info, staging_dir)
        # Whatever the filesystem maps to a kept name (./backups, BACKUPS,
        # "extension_token.txt." on Windows) is removed from staging.
        for name in kept + _RESTORE_SKIPPED_SIDECARS:
            path = os.path.join(staging_dir, name)
            if os.path.lexists(path):
                _remove_entry(path)

        validate_staged_library_db(os.path.join(staging_dir, "library.db"))
        staged_src = os.path.join(staging_dir, "sources.db")
        if os.path.lexists(staged_src):
            _check_uploaded_db(staged_src, BAD_SOURCES_DB)
        marker = _current_state_marker(library_dir)
        _build_staged_databases(staging_dir, library_dir)
        if before_swap is not None:
            before_swap()
        if _current_state_marker(library_dir) != marker:
            raise ConflictError("Sign-in or source settings changed while the restore was "
                                "being prepared; nothing was changed -- try again.")

        old_dir = None
        if os.path.exists(library_dir):
            old_dir = f"{library_dir}.pre_restore_{int(time.time())}"
            os.rename(library_dir, old_dir)
        try:
            os.rename(staging_dir, library_dir)
        except Exception:
            if old_dir is not None:
                os.rename(old_dir, library_dir)
            raise
        staging_dir = None
        if old_dir is not None:
            carried = True
            for name in kept:
                src = os.path.join(old_dir, name)
                if not os.path.lexists(src):
                    continue
                try:
                    os.rename(src, os.path.join(library_dir, name))
                except OSError:
                    carried = False
                    logging.getLogger(__name__).warning(
                        "Restore: could not carry a kept entry across; the previous "
                        "library was left in place beside the restored one.")
            if carried:
                shutil.rmtree(old_dir, ignore_errors=True)
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


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
    style_note = settings_service.get_preference("default_style_note") or ""
    results = {"translated": [], "skipped_running": [], "skipped_no_key": [],
               "skipped_no_lines": [], "skipped_cap": [], "skipped_engine_changed": [],
               "errors": {}, "partial": {}, "cancelled": False}
    models = models or {}
    total = len(drama_ids) or 1
    for i, did in enumerate(drama_ids):
        if background_jobs.is_cancel_requested(job_id):
            break
        drama = db.get_drama(did)
        title = (drama.get("title_en") or drama.get("title_zh") or f"drama #{did}") if drama else f"drama #{did}"
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

        engine_choice = drama.get("translation_engine") or settings_service.get_default_engine()
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
        defaults = translate_run_service.get_translate_config_defaults(is_novel)
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
            defaults["context_window"], settings_service.get_ollama_num_ctx_override() or None,
            cost_cap_usd=cost_cap,
            context_window_ahead=defaults["context_window_ahead"],
            batch_size=defaults["batch_size"],
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
                style_preset=style_preset, force_retranslate=False, **defaults))
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

    results["cancelled"] = background_jobs.is_cancel_requested(job_id)
    background_jobs.update_progress(job_id, 1.0, "Done")
    background_jobs.set_result(job_id, results)
