"""
tabs/workspace.py -- Workspace tab UI, extracted from the former monolithic app.py.
"""
import dataclasses

from common import *
import audio_preprocess
import raw_transcript
import resegment
import sensevoice_tags
import subtitle_formats
import bulk_translate

MEDIA_TYPE_OPTIONS = ["audio_drama", "video_drama", "novel", "manhwa", "manga", "manhua",
                       "asmr", "streamer_vod", "other"]


def _format_media_type(m):
    # .title() alone mangles acronyms (ASMR -> Asmr, streamer_vod -> Streamer Vod).
    special = {"asmr": "ASMR", "streamer_vod": "Streamer VOD"}
    return special.get(m, m.replace("_", " ").title())


# The same "remember the last pick in a global session_state key" pattern
# already used for the model dropdowns below (settings_claude_model etc.)
# -- applying a preset's engine_model just needs to write into whichever
# one matches the preset's own engine.
_ENGINE_MODEL_SESSION_KEY = {
    "claude": "settings_claude_model", "gemini": "settings_gemini_model",
    "ollama": "settings_ollama_model",
}


def apply_preset_to_session(preset: dict, drama_id):
    """Step 9c: copies a saved preset's captured fields onto session_state
    for the style/locale/pronoun-default/genre-notes widgets below --
    keyed per drama_id, same as `default_female_pronouns_{id}` already is
    -- plus the matching global model-memory key for its engine, if it
    saved one. translation_engine itself isn't handled here: it's
    persisted straight onto the drama row (db.update_drama, or a
    create_drama kwarg for a brand-new drama), the same place it's
    already stored, not through session_state.

    Every field is a plain default the widgets below read ONCE per
    rerun, still freely editable afterward -- applying a preset never
    locks anything, matching the roadmap's own requirement.
    """
    if preset.get("style_preset"):
        st.session_state[f"style_preset_{drama_id}"] = preset["style_preset"]
    if preset.get("locale"):
        st.session_state[f"locale_{drama_id}"] = preset["locale"]
    st.session_state[f"default_female_pronouns_{drama_id}"] = bool(
        preset.get("default_female_pronouns"))
    st.session_state[f"include_genre_notes_{drama_id}"] = bool(
        preset.get("include_genre_notes", True))
    _model_key = _ENGINE_MODEL_SESSION_KEY.get(preset.get("translation_engine"))
    if _model_key and preset.get("engine_model"):
        st.session_state[_model_key] = preset["engine_model"]


def _sanitize_filename(name: str, max_length: int = 80) -> str:
    """Strips characters Windows/macOS/Linux all disallow in a filename
    (a custom export name is free-typed text, not something to trust
    verbatim), collapses whitespace to underscores, and caps the length
    so a long drama title doesn't produce a path Windows itself refuses
    to write. Returns "" for input that's empty after cleaning, so
    callers can fall back to their own default rather than downloading
    a file literally named "_"."""
    name = (name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", "_", name).strip("_.")
    return name[:max_length]


_PRONOUN_CUSTOM = "Custom…"


def _pronoun_picker(label, current, key, unset_label="Unspecified (use the default)", help=None,
                    in_form=False):
    """she/her, he/him, they/them, or free-text Custom… -- returns the
    pronoun text to store ("" for unset). `current` may be a legacy
    "female"/"male" value; it's shown as its mapped preset. in_form: an
    st.form doesn't rerun when the selectbox changes, so the custom box
    has to be visible up front instead of appearing on "Custom…"."""
    current = tguide.normalize_pronouns(current)
    options = [""] + tguide.PRONOUN_PRESETS + [_PRONOUN_CUSTOM]
    index = options.index(current) if current in options else len(options) - 1
    picked = st.selectbox(label, options, index=index, key=key, help=help,
                          format_func=lambda p: unset_label if p == "" else p)
    if picked != _PRONOUN_CUSTOM and not in_form:
        return picked
    custom = st.text_input(
        f"{label} (custom)" if not in_form else "Custom pronouns (used when Custom… is picked)",
        key=f"{key}_custom", value=current if current not in options else "",
        placeholder="e.g. xe/xem")
    if picked != _PRONOUN_CUSTOM:
        return picked
    # Nothing typed yet: keep the saved value rather than clearing it the
    # moment someone opens the custom box.
    return custom.strip() or current


def _subtitle_style_controls(picked_id, lines, speaker_names):
    """Style controls for ASS export and burned-in video, with a live
    preview drawn from a real line of this drama. Widgets are keyed per
    preset, so picking a preset resets them to that preset's values."""
    sf = subtitle_formats
    preset = st.selectbox("Starting look", list(sf.ASS_PRESETS), key=f"sub_preset_{picked_id}",
                          help="A starting point to tune from, not a fixed look.")
    base = sf.ASS_PRESETS[preset]
    k = f"{picked_id}_{preset}"
    _other = "Other (type a font name)"
    c1, c2 = st.columns(2)
    font_choice = c1.selectbox("Font", sf.FONT_CHOICES + [_other],
                               index=sf.FONT_CHOICES.index(base["font"]), key=f"sub_font_{k}")
    font = (c1.text_input("Font name", key=f"sub_font_custom_{k}").strip() or base["font"]
            if font_choice == _other else font_choice)
    size = c2.slider("Size", 12, 60, base["size"], key=f"sub_size_{k}")
    outline_width = c2.slider("Outline width", 0, 10, base["outline_width"], key=f"sub_outline_w_{k}")
    c3, c4, c5, c6 = st.columns(4)
    bold = c3.checkbox("Bold", value=base["bold"], key=f"sub_bold_{k}")
    italic = c3.checkbox("Italic", value=base["italic"], key=f"sub_italic_{k}")
    primary = c4.color_picker("Text colour", base["primary"], key=f"sub_primary_{k}")
    outline = c5.color_picker("Outline colour", base["outline"], key=f"sub_outline_{k}")
    alignment = c6.selectbox("Position", list(sf.ALIGNMENTS), key=f"sub_align_{k}",
                             index=list(sf.ALIGNMENTS).index(base["alignment"]),
                             format_func=lambda a: a.replace("-", " "))
    st.caption("A font that isn't common must be installed on the computer doing the export or "
               "burn-in -- otherwise it silently falls back to a default font.")
    style = {"font": font, "size": size, "bold": bold, "italic": italic, "primary": primary,
             "outline": outline, "outline_width": outline_width, "alignment": alignment}

    speaker_colors = {}
    speakers = sorted({ln.speaker for ln in lines if ln.speaker})
    if speakers and st.checkbox("One colour per speaker (ASS only)", value=True,
                                key=f"sub_per_speaker_{picked_id}"):
        defaults = sf.default_speaker_colors(speakers)
        cols = st.columns(min(len(speakers), 4))
        for i, sp in enumerate(speakers):
            speaker_colors[sp] = cols[i % len(cols)].color_picker(
                speaker_names.get(sp) or sp, defaults[sp], key=f"sub_spcolor_{picked_id}_{sp}")

    sample = (next((ln for ln in lines if ln.en.strip()), None)
              or next((ln for ln in lines if ln.zh.strip()), None))
    if sample:
        st.markdown(sf.style_preview_html(sample.en or sample.zh, style,
                                          speaker_colors.get(sample.speaker)),
                    unsafe_allow_html=True)
        st.caption(f"Preview: line {sample.idx + 1}, updates as you change the controls.")
    return style, speaker_colors


def _jump_to_line_button(picked_id, line_idx, all_lines, key):
    """A button that lands on the right page of the Review & edit table
    for a specific line, instead of leaving a flagged-line list (pacing
    check, translation notes) as plain text with no way to act on it.
    Turns off both "show only" filters so the page number lines up
    against the full, unfiltered line list -- a line flagged as
    too-long-for-slot isn't necessarily also flagged for review, so
    leaving "Show flagged lines only" on could land on an empty page.
    """
    if st.button(f"↳ Jump to line {line_idx + 1} in Review & edit", key=key):
        st.session_state[f"flagged_only_{picked_id}"] = False
        st.session_state[f"untranslated_only_{picked_id}"] = False
        page_size = st.session_state.get("review_page_size", 40)
        position = next((i for i, ln in enumerate(all_lines) if ln.idx == line_idx), 0)
        st.session_state["review_page"] = (position // page_size) + 1
        st.rerun()


def run_translate_job(job_id, drama_id, lines, engine, drama_meta, style_note,
                       novel_reference, force_retranslate, locale, glossary_terms,
                       style_guidelines, engine_choice, style_preset, context_window=6,
                       ollama_num_ctx_override=None, reflect=False, cost_cap_usd=None):
    """
    The actual translation work, run inside a background thread by the
    Translate button. Deliberately touches nothing from Streamlit (no
    st.session_state, no widgets) -- only plain Python objects and the
    database, both of which are safe from a background thread. Progress
    goes through background_jobs.update_progress(); the main script polls
    that on its next rerun rather than this function updating any UI
    directly, which it structurally cannot do from here.

    reflect: Step 7's "High quality" Reflect mode -- three LLM passes per
    batch instead of one; the middle (reflection) pass's critique is
    saved as a translation note per line, via notes_cb below.

    cost_cap_usd: stop cleanly (every finished line kept) once this run's
    estimated spend reaches it -- the tighter of the per-job and monthly
    caps, resolved before the job starts. None = no cap.
    """
    cap_reached = {}
    # {speaker_label: "Name (pronouns)"}, named characters only -- a line
    # whose speaker has no name set is shown to the translator with no
    # name at all (see translate_lines_with_engine's own docstring),
    # never the raw diarization label, which isn't a name.
    _series_id = (db.get_drama(drama_id) or {}).get("series_id")
    character_names = tguide.build_speaker_labels(
        db.list_characters_with_series_names(drama_id),
        db.list_series_characters(_series_id) if _series_id else [])
    _, errors = translate_engines.translate_lines_with_engine(
        lines, engine, drama_meta=drama_meta, style_note=style_note,
        novel_reference=novel_reference, force_retranslate=force_retranslate,
        locale=locale, glossary_terms=glossary_terms, style_guidelines=style_guidelines,
        context_window=context_window, character_names=character_names,
        ollama_num_ctx_override=ollama_num_ctx_override,
        reflect=reflect,
        cost_cap_usd=cost_cap_usd,
        cap_cb=lambda spent: cap_reached.update(spent=spent),
        notes_cb=lambda notes: db.save_translation_notes(
            drama_id, notes, id_by_idx=_id_by_idx(lines)),
        progress_cb=lambda frac: background_jobs.update_progress(
            job_id, frac, translate_engines.progress_message_with_rate_status(engine, frac)),
        # Translation owns `en` and nothing else -- a flag job, a merge or
        # the user's own edits can run alongside without being overwritten.
        save_cb=lambda ls: db.save_lines(drama_id, ls, fields=("en",)),
        cancel_check_cb=lambda: background_jobs.is_cancel_requested(job_id),
        usage_cb=lambda inp, out, cache_read=0, cache_write=0: db.log_usage(
            drama_id, engine_choice, getattr(engine, "model", engine_choice), "translate",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out, cache_read, cache_write),
            cache_read_tokens=cache_read),
    )

    enforced = [t for t in (glossary_terms or []) if t.get("enforce_exact")]
    if enforced:
        for ln in lines:
            if ln.en:
                ln.en = tguide.apply_hard_term_substitutions(ln.en, enforced)
        db.save_lines(drama_id, lines, fields=("en",))

    # A translation too dense to read in the time it's on screen goes into
    # the review queue like any other flag (never replacing an existing one).
    if subtitle_formats.flag_dense_lines(lines):
        db.save_lines(drama_id, lines, fields=("flag", "flag_note"))

    _job_line_ids = [ln.id for ln in lines if getattr(ln, "id", None) is not None]
    if _job_line_ids and not db.line_ids_exist(drama_id, _job_line_ids):
        # Every line this job was translating has been replaced (e.g. a new
        # transcription finished meanwhile) -- its writes were no-ops, and
        # recording a version or a "translated" status would describe lines
        # that no longer exist.
        background_jobs.set_result(job_id, {"errors": errors, "lines_replaced": True,
                                            "cap_reached": cap_reached.get("spent")})
        return

    _version_label = f"{engine_choice} · {style_preset}"
    if (engine_choice in translate_engines.FREE_ENGINES
            or getattr(engine, "free_tier", False)):
        _version_label = f"[testing: {engine_choice}] {_version_label}"
    db.save_translation_version(
        drama_id, lines, label=_version_label,
        engine=engine_choice, model=getattr(engine, "model", ""), make_active=True)
    # Persisted, not just handed to the ephemeral job-status dict: if the app
    # restarts or the completion rerun is missed, the record of what failed
    # (and why some lines are untranslated) must not vanish with it.
    db.update_drama(drama_id, status="translated", translation_engine=engine_choice,
                     last_translate_errors=json.dumps(errors, ensure_ascii=False) if errors else None)

    background_jobs.set_result(job_id, {"errors": errors, "cap_reached": cap_reached.get("spent")})


def _bulk_engine_factory(engine_choice, model):
    """An engine built with the key currently in Settings, or None if
    there isn't one -- a pending bulk job can only be checked with a key,
    and keys are never stored on disk."""
    key = st.session_state.get(f"settings_{engine_choice}")
    if not key:
        return None
    try:
        return translate_engines.get_engine(engine_choice, key, model or None)
    except Exception:
        return None


def _start_bulk_translation(drama_id, drama, engine, engine_choice, novel_reference, glossary_terms,
                            style_guidelines, style_note, locale, style_preset, context_window,
                            force_retranslate, job_cap, monthly_cap, estimate):
    """Step 9's Bulk mode, from the Translate button. Returns (st method
    name, message). Lines come fresh from the database so each carries
    its permanent id and current English."""
    lines = db.load_line_objects(drama_id)
    translate_args = {"style_note": style_note, "locale": locale, "glossary_terms": glossary_terms,
                      "style_guidelines": style_guidelines, "style_preset": style_preset,
                      "context_window": context_window, "cost_cap_usd": job_cap or None}
    try:
        if engine_choice == "deepseek":
            translate_args["novel_reference"] = novel_reference
            bulk_id = bulk_translate.schedule_offpeak_translation(
                drama_id, lines, engine_choice, getattr(engine, "model", ""), translate_args,
                force_retranslate=force_retranslate)
            bulk_translate.start_poller(bulk_id, engine=engine, monthly_cap_usd=monthly_cap or None)
            job = db.get_bulk_job(bulk_id)
            return "success", (f"Scheduled for DeepSeek's off-peak window (from "
                               f"{job['scheduled_for'][:16].replace('T', ' ')} UTC). Track it under "
                               "🐢 Bulk jobs below; the app needs to be running then.")
        cap, refusal = translate_engines.resolve_cost_cap(
            job_cap, monthly_cap, db.get_month_spend() if monthly_cap else 0.0)
        if refusal:
            return "warning", refusal
        if cap is not None and estimate is not None and estimate > cap:
            return "warning", (f"Not submitted: a bulk batch can't be stopped part-way, and its "
                               f"estimate (~${estimate:.2f}) is above your ${cap:.2f} cap. Raise the "
                               "cap, or run it as a normal translation, which stops at the cap.")
        series_id = drama.get("series_id")
        character_names = tguide.build_speaker_labels(
            db.list_characters_with_series_names(drama_id),
            db.list_series_characters(series_id) if series_id else [])
        context = translate_engines.build_translation_context(
            engine, drama, style_note=style_note, novel_reference=novel_reference, locale=locale,
            glossary_terms=glossary_terms, style_guidelines=style_guidelines)
        provider = bulk_translate.make_provider(engine_choice, engine)
        bulk_id = bulk_translate.submit_bulk_translation(
            drama_id, lines, engine, engine_choice, context, provider=provider,
            translate_args={"glossary_terms": glossary_terms, "style_preset": style_preset},
            force_retranslate=force_retranslate, context_window=context_window,
            character_names=character_names)
        bulk_translate.start_poller(bulk_id, provider=provider, engine=engine)
        n = len(db.list_bulk_job_lines(bulk_id))
        return "success", (f"Submitted {n} line(s) as one bulk batch at half price. Most finish "
                           "within an hour (24 hours at most) -- track it under 🐢 Bulk jobs below.")
    except Exception as e:
        return "error", f"Bulk submission failed: {translate_engines.redact_secrets(str(e))}"


_BULK_GENERIC_SUBMIT = {
    "flag": bulk_translate.submit_bulk_flag,
    "consistency": bulk_translate.submit_bulk_consistency,
    "emotion": bulk_translate.submit_bulk_emotion,
    "translation_notes": bulk_translate.submit_bulk_translation_notes,
}
_BULK_GENERIC_LABELS = {
    "flag": "review-queue flagging", "consistency": "consistency check",
    "emotion": "emotion detection", "translation_notes": "translation notes",
}


def _start_bulk_generic(kind, drama_id, engine, engine_choice, **submit_kwargs):
    """Step 9d: submits a flag/consistency/emotion/translation-notes bulk
    job -- same "submit now, poll later" shape as _start_bulk_translation
    (Step 9), just for the four review/QA passes Step 9's own item 2
    named as in scope for bulk mode but didn't build. Returns (st method
    name, message)."""
    lines = db.load_line_objects(drama_id)
    try:
        provider = bulk_translate.make_provider(engine_choice, engine)
        bulk_id = _BULK_GENERIC_SUBMIT[kind](drama_id, lines, engine, engine_choice,
                                             provider=provider, **submit_kwargs)
        bulk_translate.start_poller(bulk_id, provider=provider, engine=engine)
        return "success", (f"Submitted the {_BULK_GENERIC_LABELS[kind]} as a bulk batch at half "
                           "price. Most finish within an hour (24 hours at most) -- track it "
                           "under 🐢 Bulk jobs below.")
    except Exception as e:
        return "error", f"Bulk submission failed: {translate_engines.redact_secrets(str(e))}"


def _start_bulk_reflect(drama_id, drama, engine, engine_choice, novel_reference, glossary_terms,
                        style_guidelines, style_note, locale, style_preset, force_retranslate):
    """Step 9d item 3: Reflect mode, bulk -- see bulk_translate.py's own
    module comment for why this is three sequential submissions instead
    of one. Only the FIRST (faithfulness) stage is submitted here; the
    other two follow automatically as each prior stage's own results
    come back (tracked under the same 🐢 Bulk jobs panel, one card per
    pipeline showing its current stage)."""
    lines = db.load_line_objects(drama_id)
    translate_args = {"style_note": style_note, "drama_meta": drama, "novel_reference": novel_reference,
                      "locale": locale, "glossary_terms": glossary_terms,
                      "style_guidelines": style_guidelines, "style_preset": style_preset}
    try:
        provider = bulk_translate.make_provider(engine_choice, engine)
        bulk_id = bulk_translate.submit_reflect_pipeline(
            drama_id, lines, engine, engine_choice, translate_args, provider=provider,
            force_retranslate=force_retranslate)
        bulk_translate.start_poller(bulk_id, provider=provider, engine=engine)
        return "success", ("Submitted the faithfulness pass as a bulk batch. Reflection and "
                           "expressiveness follow automatically once each pass's results are back -- "
                           "this takes roughly 3x longer than a single bulk pass, since each stage "
                           "waits on the provider before the next one can submit. Track it under "
                           "🐢 Bulk jobs below.")
    except Exception as e:
        return "error", f"Bulk submission failed: {translate_engines.redact_secrets(str(e))}"


_BULK_STATUS_LABELS = {
    "submitting": "Submitting", "submitted": "Waiting for results", "scheduled": "Scheduled",
    "running": "Translating (off-peak)", "applied": "Done", "cancelled": "Cancelled",
    "failed": "Failed", "auth_error": "Can't check -- key refused",
}
_BULK_SUMMARY_LABELS = {
    "applied": "applied", "translated": "translated", "dropped_deleted": "dropped (line deleted)",
    "flagged_source_changed": "flagged (source changed)", "kept_your_edit": "kept your edit",
    "skipped_changed": "skipped (edited meanwhile)", "missing": "missing",
    "failed_requests": "failed requests", "batch_errors": "failed batches",
}


_BULK_KIND_LABELS = {
    "translate": "Translation", "flag": "Review-queue flagging",
    "consistency": "Consistency check", "emotion": "Emotion detection",
    "translation_notes": "Translation notes", "reflect": "Reflect",
}
_REFLECT_STAGE_LABELS = {"faithful": "faithfulness pass", "reflect": "reflection pass",
                         "expressive": "expressiveness pass"}
_REFLECT_STAGE_NUMBER = {"faithful": 1, "reflect": 2, "expressive": 3}


def _bulk_job_title(job: dict) -> str:
    """Step 9d: a job's kind (and, for Reflect, its current stage) --
    the exit condition's own "not just 'pending'" requirement. A Reflect
    pipeline's card always shows the STAGE currently pending/applied,
    not a generic "Reflect" label, so it reads as a multi-stage job in
    progress rather than one opaque batch."""
    kind = job.get("kind") or "translate"
    if kind == "reflect" and job.get("stage"):
        stage = job["stage"]
        return (f"Bulk Reflect -- {_REFLECT_STAGE_LABELS.get(stage, stage)} "
               f"(stage {_REFLECT_STAGE_NUMBER.get(stage, '?')}/3)")
    return f"Bulk {_BULK_KIND_LABELS.get(kind, kind)}"


def _render_bulk_jobs_panel(drama_id, monthly_cap):
    """Pending and recent bulk jobs for this drama, with Check now and
    Cancel. Also restarts polling for pending jobs after an app restart.

    A Reflect pipeline's three stages are three separate bulk_jobs rows
    (see bulk_translate.py's own module comment) sharing one pipeline_id
    -- only the LATEST one is shown here (jobs come back newest-id-first,
    so the first row seen for a given pipeline_id already is the latest),
    so a pipeline reads as one card whose stage advances, not three
    separate opaque entries."""
    jobs = db.list_bulk_jobs(drama_id)
    if not jobs:
        return
    resumed = bulk_translate.resume_pending(drama_id, _bulk_engine_factory, monthly_cap or None)
    seen_key = f"bulk_applied_seen_{drama_id}"
    seen = st.session_state.setdefault(seen_key, {j["id"] for j in jobs if j["status"] == "applied"})
    if any(j["status"] == "applied" and j["id"] not in seen for j in jobs):
        # A poller finished in the background -- show its lines.
        st.session_state.lines = db.load_line_objects(drama_id)
        seen.update(j["id"] for j in jobs if j["status"] == "applied")
    pending = [j for j in jobs if j["status"] in ("submitting", "submitted", "scheduled",
                                                  "running", "auth_error")]
    seen_pipelines = set()
    displayed = []
    for job in jobs:
        pid = job.get("pipeline_id")
        if pid:
            if pid in seen_pipelines:
                continue
            seen_pipelines.add(pid)
        displayed.append(job)
    with st.expander(f"🐢 Bulk jobs ({len(pending)} pending)", expanded=bool(pending)):
        _note = st.session_state.pop(f"bulk_note_{drama_id}", None)
        if _note:
            st.info(_note)
        for job in displayed[:10]:
            with st.container(border=True):
                st.markdown(f"**#{job['id']}** · {_bulk_job_title(job)} · {job['engine']} "
                            f"({job['model'] or 'default'}) · "
                            f"submitted {(job['submitted_at'] or '')[:16].replace('T', ' ')} UTC · "
                            f"**{_BULK_STATUS_LABELS.get(job['status'], job['status'])}**")
                if job["status"] == "scheduled" and job.get("scheduled_for"):
                    st.caption(f"Starts at {job['scheduled_for'][:16].replace('T', ' ')} UTC "
                               "(DeepSeek off-peak), while the app is running.")
                if resumed.get(job["id"]) == "needs_key":
                    st.caption(f"Needs your {job['engine']} API key (Settings) to keep checking "
                               "this job.")
                if job["status"] == "auth_error":
                    st.error(f"Checking this batch failed: {job['last_error']} Update the key in "
                             "Settings, then Check now.")
                elif job.get("last_error"):
                    st.caption(f"Last problem: {job['last_error']}")
                if job.get("result_summary"):
                    parts = [f"{_BULK_SUMMARY_LABELS[k]} {v}" for k, v in job["result_summary"].items()
                             if k in _BULK_SUMMARY_LABELS and v]
                    if parts:
                        st.caption("Result: " + " · ".join(parts))
                c1, c2 = st.columns(2)
                if job["status"] in ("submitted", "auth_error") and c1.button(
                        "🔄 Check now", key=f"bulk_check_{job['id']}"):
                    engine = _bulk_engine_factory(job["engine"], job["model"])
                    if engine is None:
                        st.warning(f"Add your {job['engine']} API key in Settings first.")
                    else:
                        try:
                            bulk_translate.check_once(
                                job["id"], bulk_translate.make_provider(job["engine"], engine),
                                engine=engine)
                        except bulk_translate.BulkAuthError:
                            pass  # recorded on the job; shown after the rerun
                        except Exception as e:
                            db.update_bulk_job(job["id"], last_error=translate_engines.redact_secrets(str(e)))
                        st.rerun()
                if job["status"] in ("submitting", "submitted", "scheduled", "auth_error") and c2.button(
                        "✖ Cancel", key=f"bulk_cancel_{job['id']}"):
                    engine = _bulk_engine_factory(job["engine"], job["model"])
                    provider = bulk_translate.make_provider(job["engine"], engine) if engine else None
                    st.session_state[f"bulk_note_{drama_id}"] = bulk_translate.cancel_bulk_job(
                        job["id"], provider)
                    st.rerun()


def run_transcribe_job(job_id, audio_path, whisper_size, language, use_gpu,
                        local_model_path, hf_token, initial_prompt, beam_size,
                        min_silence_duration_ms, vad_threshold=0.5, separate_vocals_first=False,
                        realign_long_segments=False, chinese_script="simplified", fast_mode=False,
                        separation_backend="auto"):
    """
    Runs just the Whisper speech-recognition pass in a background thread,
    same reasoning as run_translate_job above: this is the step that
    dominates wall-clock time on a long-form file (3+ hours), so it's the
    one worth reporting progress on and not blocking the rest of the app
    for. The Qwen3-ASR text override, alignment, and diarization that can
    follow it stay synchronous, run from render_workspace_tab once this
    job's result is picked up on a later rerun -- those touch a lot of
    individual st.warning/st.success branches for their various fallback
    paths, which can't run from a thread (Streamlit widgets/session_state
    aren't thread-safe to write from here).

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

    gpu_fallback_msg = []
    try:
        segments = transcribe_for_timing(
            audio_path, whisper_size, language=language, use_gpu=use_gpu,
            local_model_path=local_model_path, hf_token=hf_token,
            initial_prompt=initial_prompt, beam_size=beam_size,
            min_silence_duration_ms=min_silence_duration_ms, vad_threshold=vad_threshold,
            on_gpu_fallback=lambda exc: gpu_fallback_msg.append(str(exc)),
            progress_cb=lambda frac: background_jobs.update_progress(
                job_id, frac, f"Transcribing... {frac * 100:.0f}%"),
            fast_mode=fast_mode)
    except core_module.ModelDownloadError as exc:
        background_jobs.set_result(job_id, {"failed_reason": "model_download", "detail": str(exc)})
        return

    if not segments:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return

    # Whisper's own pass has no cancel checkpoint of its own yet (out of
    # scope for this step -- see Step 4g) -- this is the next point a
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
                segments, audio_path, language, chinese_script=chinese_script)
        except word_align.WordAlignError as exc:
            # A missing dependency here must never cost the transcription
            # itself (the expensive part, already done) -- proceed with
            # the unmodified segments and just report the issue, the same
            # "never lose already-done work over an optional add-on
            # failing" rule this app follows everywhere else.
            word_align_error = str(exc)

    core_module.release_gpu_models()  # transcription stage done
    background_jobs.set_result(job_id, {
        "segments": segments,
        "gpu_fallback": gpu_fallback_msg[0] if gpu_fallback_msg else None,
        "word_align_error": word_align_error,
    })


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
        progress_cb=lambda frac: background_jobs.update_progress(
            job_id, frac, f"Reading captions from video... {frac * 100:.0f}%"))
    if not cues:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return
    background_jobs.set_result(job_id, {"segments": cues})


_LINE_WIDGET_KEY = re.compile(r"^(zh|en|start|end|speaker|rv_improved|rv_retrans)_\d+$")


def _clear_line_widget_state():
    """Review & edit's per-line widgets are keyed by position (zh_<idx>, ...)
    and keep showing what they held, not the line's new value -- after an
    operation that renumbers lines, a stale box would be read back on the
    next rerun as an edit and written over whichever line now sits at that
    position."""
    for key in [k for k in st.session_state.keys() if _LINE_WIDGET_KEY.match(str(k))]:
        del st.session_state[key]


def _drama_label(drama):
    return drama.get("title_en") or drama.get("title_zh") or f"drama #{drama['id']}"


def _job_start_message(job_id, running_message):
    """Step 5c: start_job() may have queued this job behind another
    GPU-touching one instead of actually starting it -- say so instead of
    claiming it's already running."""
    status = background_jobs.get_status(job_id)
    if status and status["status"] == "queued":
        return f"⏳ {status['message']}. It'll start automatically once the GPU is free."
    return running_message


def _diarization_estimate_caption(audio_duration_seconds):
    """pyannote's pipeline makes one call and only returns a result at the
    end -- no incremental progress callback exists in its public API, so
    unlike Whisper's segment-by-segment real progress bar, this is the best
    honest estimate available: diarization runtime scales roughly linearly
    with audio length, so a range scaled off the audio's own length (rather
    than a fixed number that ignores it) is truthful without pretending to
    more precision than a single st.spinner can back up."""
    if not audio_duration_seconds or audio_duration_seconds <= 0:
        return "Usually takes anywhere from under a minute to a few minutes, depending on audio length and hardware."

    def _mmss(seconds):
        m, s = divmod(int(round(seconds)), 60)
        return f"{m}:{s:02d}"

    return (f"For audio this long (~{_mmss(audio_duration_seconds)}), usually takes roughly "
            f"{_mmss(audio_duration_seconds)}–{_mmss(audio_duration_seconds * 2)}, depending on "
            f"your hardware -- there's no incremental progress to show here (pyannote's pipeline "
            f"doesn't expose one), just this spinner until it finishes.")


def _autotune_estimate_caption(audio_duration_seconds, num_candidates):
    """Step 6h: each candidate is its own full re-transcription (VAD
    segmentation happens inside faster-whisper's own decode pass, not a
    separable pre-step this app can hook into more cheaply) -- same
    "scale a range off the audio's own length" honesty as
    _diarization_estimate_caption above, here multiplied by how many
    candidates are actually queued."""
    if not audio_duration_seconds or audio_duration_seconds <= 0 or num_candidates <= 0:
        return ""

    def _mmss(seconds):
        m, s = divmod(int(round(seconds)), 60)
        return f"{m}:{s:02d}"

    return (f"{num_candidates} candidate(s) on audio this long (~{_mmss(audio_duration_seconds)}) "
            f"usually takes roughly {_mmss(audio_duration_seconds * num_candidates)}–"
            f"{_mmss(audio_duration_seconds * 2 * num_candidates)} total, depending on your "
            f"hardware -- each candidate is a full re-transcription, one after another.")


def _copy_lines(lines):
    """Independent copies for a background job (it mutates its own list
    while the page keeps editing st.session_state's). dataclasses.replace
    carries every field, including the permanent id and the `orig`
    baseline db.save_lines diffs against -- a hand-listed copy that forgets
    one (flag, speaker) is exactly how fields used to get silently wiped."""
    return [dataclasses.replace(ln, merged_ids=[]) for ln in lines]


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
    emap = emotion.detect_emotions(
        lines, engine, use_audio_cues=use_audio_cues,
        progress_cb=lambda frac: background_jobs.update_progress(
            job_id, frac, f"Reading tone... {frac * 100:.0f}%"),
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_choice, getattr(engine, "model", engine_choice), "emotion_detect",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)))
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
            progress_cb=lambda frac: background_jobs.update_progress(
                job_id, frac, f"Listening for emotion and sounds... {frac * 100:.0f}%"))
    finally:
        core_module.release_gpu_models()
    sensevoice_tags.save_audio_tags(drama_dir, tags)
    background_jobs.set_result(job_id, {"tagged": len(tags)})


def run_flag_job(job_id, drama_id, lines, engine, engine_choice):
    """
    Runs flag_uncertain_lines in a background thread -- same reasoning as
    the jobs above. Unlike emotion detection, the result IS persisted (via
    db.save_lines): the whole point of a review queue is coming back to it
    later, potentially after closing the app, not just within this
    session.
    """
    translate_engines.flag_uncertain_lines(
        lines, engine,
        progress_cb=lambda frac: background_jobs.update_progress(
            job_id, frac, f"Checking for lines that need a second look... {frac * 100:.0f}%"),
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_choice, getattr(engine, "model", engine_choice), "flag_review",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)))
    db.save_lines(drama_id, lines, fields=("flag", "flag_note"))
    background_jobs.set_result(job_id, {"flagged_count": sum(1 for ln in lines if ln.flag)})


def run_consistency_job(job_id, drama_id, lines, engine, engine_choice):
    """
    Runs check_consistency_llm in a background thread. Previously this ran
    synchronously (a blocking st.spinner), which meant it couldn't run
    alongside anything else -- the whole app was stuck until it finished.
    Backgrounding it, same as Review queue and Emotion detection, is what
    actually lets it run at the same time as those instead of forcing them
    to queue up one after another.
    """
    issues = translate_engines.check_consistency_llm(
        lines, engine,
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_choice, getattr(engine, "model", engine_choice), "consistency_check",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)))
    db.save_consistency_issues(drama_id, issues)
    background_jobs.set_result(job_id, {"issue_count": len(issues)})


def run_translation_notes_job(job_id, drama_id, lines, engine, engine_choice, source_language):
    """
    Runs generate_translation_notes_llm in a background thread -- same
    reasoning as run_consistency_job above.
    """
    found_notes = tguide.generate_translation_notes_llm(
        lines, engine, source_language=source_language,
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_choice, getattr(engine, "model", engine_choice), "translation_notes",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)))
    if found_notes:
        db.save_translation_notes(drama_id, found_notes, id_by_idx=_id_by_idx(lines))
    background_jobs.set_result(job_id, {"note_count": len(found_notes)})


def run_fix_flagged_lines_job(job_id, drama_id, lines, audio_path, whisper_size, use_gpu,
                               source_language, engine, engine_choice):
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
    """
    flagged = [ln for ln in lines if ln.flag]
    fixed_count = 0
    for i, ln in enumerate(flagged):
        if audio_path and os.path.exists(audio_path):
            slice_path = os.path.join(os.path.dirname(audio_path), f"_fixflag_slice_{ln.idx}.wav")
            try:
                core_module.extract_audio_slice(audio_path, ln.start, ln.end, slice_path)
                segments = core_module.transcribe_for_timing(
                    slice_path, model_size=whisper_size, language=source_language, use_gpu=use_gpu)
                new_zh = " ".join(s["text"] for s in segments).strip()
                if new_zh:
                    ln.zh = new_zh
            finally:
                if os.path.exists(slice_path):
                    os.remove(slice_path)
        if ln.zh.strip():
            try:
                translated = engine.translate_batch([ln.zh], {"source_language": source_language})[0]
                if hasattr(engine, "last_usage"):
                    db.log_usage(drama_id, engine_choice, getattr(engine, "model", engine_choice),
                                 "fix_flagged_line", engine.last_usage.get("input_tokens", 0),
                                 engine.last_usage.get("output_tokens", 0),
                                 translate_engines.estimate_cost_for_engine(
                                     engine,
                                     engine.last_usage.get("input_tokens", 0),
                                     engine.last_usage.get("output_tokens", 0)))
                if translated.strip():
                    ln.en = translated
                    ln.flag, ln.flag_note = None, ""
                    fixed_count += 1
            except Exception:
                pass  # leave the line flagged rather than lose the source fix silently
        background_jobs.update_progress(job_id, (i + 1) / max(len(flagged), 1),
                                        f"Fixing flagged lines... {i + 1}/{len(flagged)}")
    if audio_path and os.path.exists(audio_path):
        core_module.release_gpu_models()  # re-transcription stage done
    db.save_lines(drama_id, lines, fields=("zh", "en", "flag", "flag_note"))
    background_jobs.set_result(job_id, {"fixed_count": fixed_count, "total_flagged": len(flagged)})


def _apply_speaker_turns(drama_id, turns, overwrite_manual):
    """Re-merges stored diarization turns onto the drama's existing lines
    -- speakers only, never text or timing, and no ASR."""
    import diarize
    lines = db.load_line_objects(drama_id)
    result = diarize.merge_speakers(lines, turns, overwrite_manual=overwrite_manual)
    for label in sorted({ln.speaker for ln in lines if ln.speaker}):
        db.upsert_character(drama_id, label)
    db.save_lines(drama_id, lines, fields=("speaker", "speaker_manual"))
    st.session_state.lines = db.load_line_objects(drama_id)
    # Review & edit's speaker boxes would otherwise keep showing (and, on
    # the next rerun, read back as a manual edit) the old labels.
    for ln in lines:
        st.session_state.pop(f"speaker_{ln.idx}", None)
    return result


def _apply_diarization_job_result(picked_id, ddir, expected_speakers, job, pending_key):
    """Step 4d: once the diarize_<id> subprocess job reports 'done', apply
    its result the exact same way a same-script-run diarization used to
    -- save the turns, then merge speaker labels onto whatever lines are
    currently saved (which may have been saved, by a caller elsewhere,
    AFTER this job was started -- diarize.merge_speakers matches by time
    overlap, not by being the same in-memory list, so that's fine)."""
    import diarize
    result = job.get("result") or {}
    turns, model, embeddings = result.get("segments"), result.get("model"), result.get("embeddings", {})
    if turns is not None:
        diarize.save_turns(ddir, turns, num_speakers=expected_speakers or None, model=model,
                           embeddings=embeddings)
        st.session_state[f"speaker_segments_{picked_id}"] = turns
        conflicts = diarize.manual_lines_that_would_change(db.load_line_objects(picked_id), turns)
        if conflicts:
            st.session_state[pending_key] = len(conflicts)
        else:
            res = _apply_speaker_turns(picked_id, turns, overwrite_manual=False)
            st.success(f"Speakers re-detected ({model}): {res['changed']} line(s) relabelled.")
    background_jobs.clear_job(f"diarize_{picked_id}")


def _render_speaker_rerun(picked_id, ddir, audio_path, hf_token, expected_speakers):
    import diarize
    st.caption("Already transcribed? Re-detect who speaks each line from the stored audio. "
               "The transcript text and timing aren't touched and nothing is re-transcribed. "
               "Uses the expected number of speakers above.")
    pending_key = f"speaker_rerun_pending_{picked_id}"
    job_id = f"diarize_{picked_id}"
    job = background_jobs.get_status(job_id)
    _job_active = bool(job and job["status"] in ("running", "queued"))

    _existing_lines = db.load_line_objects(picked_id)
    _audio_duration = max((ln.end for ln in _existing_lines), default=0)
    if _audio_duration:
        st.caption(_diarization_estimate_caption(_audio_duration))

    if st.button("🔁 Re-run speaker detection", disabled=not hf_token or _job_active,
                 key=f"rerun_speakers_{picked_id}"):
        st.session_state.pop(pending_key, None)
        started = background_jobs.start_process_job(
            job_id, diarize.diarize_subprocess_worker,
            args=(audio_path, hf_token, expected_speakers or None),
            gpu_touching=True, description=f"Diarization (drama #{picked_id})")
        if started:
            st.rerun()

    if job:
        if job["status"] == "queued":
            st.info(job.get("message") or "Waiting for the GPU...")
        elif job["status"] == "running":
            st.info("Detecting speakers... (first run downloads the model)")
            st.caption("Running in the background as its own process -- safe to switch tabs or "
                      "use other dramas. Cancel below genuinely stops it (not just the display), "
                      "unlike every other job's cooperative cancel in this app.")
            jc1, jc2 = st.columns(2)
            if jc1.button("🔄 Refresh progress", key=f"refresh_diarize_{picked_id}"):
                st.rerun()
            if jc2.button("✖ Cancel", key=f"cancel_diarize_{picked_id}"):
                background_jobs.request_cancel(job_id)
                st.rerun()
        elif job["status"] == "cancelled":
            st.warning("Speaker detection was stopped. Nothing was changed.")
            background_jobs.clear_job(job_id)
        elif job["status"] == "error":
            st.error(f"Speaker detection failed ({job['error']}). Check your Hugging Face token "
                     "and pyannote.audio install. Nothing was changed.")
            background_jobs.clear_job(job_id)
        elif job["status"] == "done":
            _apply_diarization_job_result(picked_id, ddir, expected_speakers, job, pending_key)

    n_conflicts = st.session_state.get(pending_key)
    if n_conflicts:
        st.warning(f"{n_conflicts} line(s) have a speaker you corrected by hand, and the new "
                   "detection would change them. Nothing has been applied yet.")
        k1, k2 = st.columns(2)
        keep = k1.button(f"Apply, keeping my {n_conflicts} correction(s)",
                         key=f"rerun_keep_{picked_id}", type="primary")
        overwrite = k2.button(f"Apply and overwrite my {n_conflicts} correction(s)",
                              key=f"rerun_overwrite_{picked_id}")
        if keep or overwrite:
            res = _apply_speaker_turns(picked_id, diarize.load_turns(ddir), overwrite_manual=overwrite)
            st.session_state.pop(pending_key, None)
            st.success(f"Speakers re-detected: {res['changed']} line(s) relabelled"
                       + (f", {res['kept_manual']} hand-corrected line(s) kept." if keep else "."))


def render_workspace_tab():
    _gemini_free_tier = st.session_state.get("gemini_free_tier", False)
    # Threaded into every get_engine(...) call below via base_url=; without
    # this, OllamaEngine always fell back to its own http://localhost:11434
    # default no matter what was configured in Settings.
    _ollama_base_url = st.session_state.get("settings_ollama_url") or None
    st.subheader("1. Choose a drama")
    all_dramas = db.list_dramas()
    options = {"➕ New drama": None}
    options.update({f"#{d['id']} — {d['title_en'] or d['title_zh']}": d["id"] for d in all_dramas})
    default_label = "➕ New drama"
    if st.session_state.active_drama_id:
        for label, did in options.items():
            if did == st.session_state.active_drama_id:
                default_label = label
    picked_label = st.selectbox("Drama", list(options.keys()),
                                 index=list(options.keys()).index(default_label))
    picked_id = options[picked_label]

    if picked_id is None:
        st.markdown("**New drama metadata**")
        with st.expander("🔍 Auto-fill from a public listing page (optional)"):
            st.caption("Paste a link to a public listing/info page. Only bibliographic metadata "
                      "(title, author, cast, synopsis) is extracted -- never the actual chapters/"
                      "episodes. Always review before saving.")
            import known_sites
            with st.popover("📋 Known official platforms"):
                for s in known_sites.KNOWN_SITES:
                    st.markdown(f"**[{s['name']}]({s['url']})** — {', '.join(s['content_types'])}")
            lookup_url = st.text_input("URL")
            lookup_engine = st.selectbox("Engine", [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference], key="lookup_engine")
            lookup_key = synced_api_key_input("API key for lookup", lookup_engine, "lookup_key")
            if st.button("Fetch & extract metadata") and lookup_url and lookup_key:
                import metadata_lookup
                with st.spinner("Fetching page and extracting metadata..."):
                    engine = translate_engines.get_engine(lookup_engine, lookup_key)
                    found, status = metadata_lookup.lookup_metadata(lookup_url, engine)
                if not status["ok"]:
                    st.warning(status["message"])
                    if status.get("needs_manual"):
                        st.session_state["show_manual_meta_paste"] = True
                if found:
                    st.session_state["autofill_metadata"] = found
                    st.success(f"Found: {', '.join(found.keys())}. Fields below are pre-filled -- review before saving.")
            if st.session_state.get("show_manual_meta_paste"):
                st.caption("**Manual fallback** — open the page in your browser, select all "
                          "(Ctrl+A), copy, and paste here. This always works.")
                pasted_page = st.text_area("Pasted page text", height=140, key="manual_meta_paste")
                if st.button("Extract from pasted text") and pasted_page.strip() and lookup_key:
                    engine_m = translate_engines.get_engine(lookup_engine, lookup_key)
                    found_m, status_m = metadata_lookup.lookup_metadata_from_text(pasted_page, engine_m)
                    if found_m:
                        st.session_state["autofill_metadata"] = found_m
                        st.success(f"Found: {', '.join(found_m.keys())}.")
                        st.rerun()
                    else:
                        st.warning(status_m["message"])

        prefill = st.session_state.get("autofill_metadata", {})
        c1, c2 = st.columns(2)
        title_en = c1.text_input("Title (English)", value=prefill.get("title_en", ""))
        title_zh = c2.text_input("Title (Chinese)", value=prefill.get("title_zh", ""))
        author = c1.text_input("Author", value=prefill.get("author", ""))
        studio = c2.text_input("Studio", value=prefill.get("studio", ""))
        director = c1.text_input("Director", value=prefill.get("director", ""))
        voice_actors = c2.text_input("Voice actors (comma-separated)", value=prefill.get("voice_actors", ""))
        summary = st.text_area("Summary", value=prefill.get("summary", ""), height=100)
        media_type = st.selectbox("Content type", MEDIA_TYPE_OPTIONS,
                                   format_func=_format_media_type)

        _all_presets = db.list_presets()
        _preset_options = {"-- none --": None}
        _preset_options.update({p["name"]: p for p in _all_presets})
        _preset_choice = st.selectbox(
            "Apply a preset (optional)", list(_preset_options.keys()),
            help="Fills in the engine/model, translation style, English variant, pronoun "
                 "default and genre-guidance fields below from a saved Workspace configuration "
                 "(📚 Library → 🎛️ Presets to manage them) -- still freely editable afterward.")
        _picked_preset = _preset_options[_preset_choice]

        if st.button("Create drama"):
            new_id = db.create_drama(
                title_en=title_en, title_zh=title_zh, author=author, studio=studio,
                director=director, voice_actors=voice_actors, summary=summary,
                media_type=media_type,
                **({"translation_engine": _picked_preset["translation_engine"]}
                   if _picked_preset and _picked_preset.get("translation_engine") else {}))
            if _picked_preset:
                apply_preset_to_session(_picked_preset, new_id)
            st.session_state.pop("autofill_metadata", None)
            st.session_state.active_drama_id = new_id
            st.session_state.lines = None
            st.rerun()
        # A plain return, not st.stop() -- st.stop() halts the ENTIRE
        # script (every tab after Workspace in app.py's render order,
        # currently just Diagnostics), not only this function. That left
        # the Diagnostics tab completely blank -- no error, just empty --
        # any time no drama was active, which includes every fresh launch.
        return

    drama = db.get_drama(picked_id)
    st.session_state.active_drama_id = picked_id
    ddir = db.drama_dir(picked_id)

    # Defined here, once, right where `drama` first becomes available --
    # not down in "Content source" where it used to live. Two separate
    # sections above that point (Romanize credits, Raw novel upload) both
    # need it, and each read it before that later definition ran.
    source_language = drama.get("source_language") or "zh"

    with st.expander("✏️ Edit metadata", expanded=False):
        c1, c2 = st.columns(2)
        title_en = c1.text_input("Title (English)", value=drama["title_en"] or "")
        title_zh = c2.text_input("Title (Chinese)", value=drama["title_zh"] or "")
        source_url = st.text_input(
            "Source URL (original YouTube/stream link, optional)",
            value=drama.get("source_url") or "",
            help="For a Streamer/VOD drama, this is a place to keep the original link -- "
                 "Title (English)/(Chinese) above already double as the stream's "
                 "translated/untranslated name, so this is just the remaining piece: "
                 "where it came from.")
        author = c1.text_input("Author", value=drama["author"] or "")
        studio = c2.text_input("Studio", value=drama["studio"] or "")
        director = c1.text_input("Director", value=drama["director"] or "")
        voice_actors = c2.text_input("Voice actors (comma-separated)", value=drama["voice_actors"] or "")
        summary = st.text_area("Summary", value=drama["summary"] or "", height=100)
        media_type = c1.selectbox(
            "Content type", MEDIA_TYPE_OPTIONS,
            index=MEDIA_TYPE_OPTIONS.index(drama.get("media_type") or "audio_drama")
                  if (drama.get("media_type") or "audio_drama") in MEDIA_TYPE_OPTIONS else 0,
            format_func=_format_media_type)
        genre = c2.text_input("Genre", value=drama.get("genre") or "",
                               placeholder="historical, modern, fantasy...")
        status_opts = ["unknown", "ongoing", "completed", "hiatus"]
        pub_status = c1.selectbox("Publication status", status_opts,
                                   index=status_opts.index(drama.get("publication_status") or "unknown"))
        chapter_count = c2.number_input("Chapter/episode count", value=int(drama.get("chapter_count") or 0),
                                         min_value=0, step=1)
        custom_tags = st.text_input("Custom tags (comma-separated)", value=drama.get("custom_tags") or "",
                                     placeholder="favorite, slow burn, rec to friends")
        personal_notes = st.text_area("Personal notes (private, yours only)",
                                       value=drama.get("personal_notes") or "", height=80)
        if st.button("🔤 Romanize credits", key=f"roman_{picked_id}"):
            _rk = st.session_state.get(f"settings_{drama.get('translation_engine') or 'claude'}", "")
            if not _rk:
                st.warning("Set an API key in the ⚙️ Settings sidebar first.")
            else:
                _eng_r = translate_engines.get_engine(
                    drama.get("translation_engine") or "claude", _rk)
                with st.spinner("Romanizing credits..."):
                    _rom = tguide.romanize_metadata(
                        {"author": author, "studio": studio, "director": director,
                         "voice_actors": voice_actors},
                        _eng_r, source_language=source_language)
                if _rom:
                    db.update_drama(
                        picked_id,
                        author_romanized=_rom.get("author"),
                        studio_romanized=_rom.get("studio"),
                        director_romanized=_rom.get("director"),
                        voice_actors_romanized=_rom.get("voice_actors"))
                    st.success("Credits romanized — originals kept alongside.")
                    st.rerun()
                else:
                    st.warning("Couldn't romanize those credits.")

        for _f, _label in [("author", "Author"), ("studio", "Studio"),
                            ("director", "Director"), ("voice_actors", "Cast")]:
            _rv = drama.get(f"{_f}_romanized")
            if _rv:
                st.caption(f"**{_label}:** "
                          + tguide.format_bilingual_credit(drama.get(_f), _rv))

        cover_file = st.file_uploader("Cover art", type=["png", "jpg", "jpeg", "webp"], key=f"cover_{picked_id}")
        if drama.get("cover_art_filename"):
            _cp = os.path.join(ddir, drama["cover_art_filename"])
            if os.path.exists(_cp):
                st.image(_cp, width=140, caption="Current cover")
        if st.button("Save metadata"):
            if cover_file is not None:
                _ext = os.path.splitext(cover_file.name)[1]
                with open(os.path.join(ddir, f"cover{_ext}"), "wb") as _f:
                    _f.write(cover_file.getbuffer())
                db.update_drama(picked_id, cover_art_filename=f"cover{_ext}")
            db.update_drama(picked_id, title_en=title_en, title_zh=title_zh, author=author,
                             studio=studio, director=director, voice_actors=voice_actors,
                             summary=summary, media_type=media_type, genre=genre,
                             publication_status=pub_status,
                             chapter_count=int(chapter_count) if chapter_count else None,
                             custom_tags=custom_tags, personal_notes=personal_notes,
                             source_url=source_url)
            st.success("Saved.")
            st.rerun()
        if st.button("🗑️ Delete this drama", type="secondary"):
            db.delete_drama(picked_id)
            st.session_state.active_drama_id = None
            st.session_state.lines = None
            st.rerun()

    st.divider()
    with st.expander("2. 📥 Content source", expanded=False):

        source_language = st.selectbox(
            "Source language",
            ["zh", "ja", "ko"],
            index=["zh", "ja", "ko"].index(source_language),
            format_func=lambda l: {"zh": "🇨🇳 Chinese", "ja": "🇯🇵 Japanese", "ko": "🇰🇷 Korean"}[l],
        )
        if source_language != drama.get("source_language"):
            db.update_drama(picked_id, source_language=source_language)

        chinese_script = "simplified"
        if source_language == "zh":
            _script_options = ["simplified", "traditional"]
            _saved_script = drama.get("chinese_script") or "simplified"
            chinese_script = st.radio(
                "Chinese script", _script_options,
                index=_script_options.index(_saved_script) if _saved_script in _script_options else 0,
                format_func=lambda s: "Simplified (Mainland)" if s == "simplified"
                                       else "Traditional (Taiwan, Hong Kong)",
                horizontal=True,
                help="Whisper transcription and translation work the same either way -- this only "
                     "affects OCR (picks Tesseract's chi_sim vs chi_tra language pack) and the "
                     "Reader's word segmentation, both of which need to know which script they're "
                     "looking at to work well.")
            if chinese_script != drama.get("chinese_script"):
                db.update_drama(picked_id, chinese_script=chinese_script)

        content_mode_options = ["audio_drama", "streamer_vod", "novel_narration"]
        content_mode = st.radio(
            "What are you working from?",
            content_mode_options,
            index=content_mode_options.index(drama.get("content_mode") or "audio_drama")
                  if (drama.get("content_mode") or "audio_drama") in content_mode_options else 0,
            format_func=lambda m: {
                "audio_drama": "🎧 Audio drama (I have the audio, + transcript)",
                "streamer_vod": "🎥 Streamer/VOD (long-form, multiple speakers, no transcript)",
                "novel_narration": "📖 Novel only (no audio -- generate a full AI narration)",
            }[m],
            horizontal=False,
        )
        if content_mode != drama.get("content_mode"):
            db.update_drama(picked_id, content_mode=content_mode)
            # Reflect the choice in the library-facing "Content type" field too --
            # only for streamer_vod, which has no other way to be represented
            # there. audio_drama/novel_narration aren't force-synced since
            # Content type already draws finer distinctions under them (e.g.
            # media_type=asmr with content_mode=audio_drama is a valid,
            # deliberate combination that shouldn't be silently overwritten).
            if content_mode == "streamer_vod" and drama.get("media_type") != "streamer_vod":
                db.update_drama(picked_id, media_type="streamer_vod")

        # Streamer/VOD is audio-bearing just like audio_drama -- everything
        # gated on "does this drama have real audio to run through Whisper/
        # diarization/translation" applies to both; only novel_narration
        # (no audio at all) is excluded.
        has_audio_pipeline = content_mode in ("audio_drama", "streamer_vod")

        # Only audio_drama can be adapted from an original novel in a way this
        # app can use: streamer_vod has no source text to be "adapted from",
        # and novel_narration's own novel text area (below) already IS the
        # novel -- no separate ASR pass exists in that mode to prime. Off by
        # default (it's optional) unless a raw novel was already saved for
        # this drama, so existing configuration doesn't disappear on its own.
        _raw_novel_path = os.path.join(ddir, "raw_novel_context.txt")
        _has_raw_novel = os.path.exists(_raw_novel_path)
        if content_mode == "audio_drama":
            _show_raw_novel = st.checkbox(
                "📕 I have the original novel this is adapted from",
                value=_has_raw_novel, key=f"raw_novel_toggle_{picked_id}",
                help="Optional. Priming Whisper with the original-language novel's text "
                     "helps it guess the right proper nouns and phrasing instead of the "
                     "nearest-sounding word.")
            if _show_raw_novel:
                with st.expander(f"📕 Raw {source_language.upper()} novel (optional -- helps transcription)"):
                    st.caption(
                        "Different from the reference translation below: this is the ORIGINAL-language "
                        "novel, used as context for speech recognition, not for translation. Whisper "
                        "primes on a short excerpt plus your glossary's names, which meaningfully helps it "
                        "guess the right proper nouns and phrasing instead of the nearest-sounding word."
                    )
                    raw_novel_file = st.file_uploader(
                        f"Upload the raw {source_language.upper()} novel (.txt/.md/.epub)",
                        type=["txt", "md", "epub"], key=f"raw_novel_{picked_id}")
                    if raw_novel_file is not None:
                        try:
                            _raw_text = core_module.load_novel_text_for_context(
                                raw_novel_file.getvalue(), raw_novel_file.name)
                            with open(_raw_novel_path, "w", encoding="utf-8") as f:
                                f.write(_raw_text)
                            st.success(f"Loaded {len(_raw_text):,} characters.")
                            _has_raw_novel = True
                        except ImportError as e:
                            st.error(str(e))
                    if _has_raw_novel:
                        st.caption(f"✅ Raw novel context saved (~{os.path.getsize(_raw_novel_path):,} bytes).")
                        if st.button("🗑️ Remove raw novel context", key=f"rmraw_{picked_id}"):
                            os.remove(_raw_novel_path)
                            st.rerun()

        audio_file = None
        transcript_text = ""
        novel_narration_text = ""
        existing_audio = None

        if has_audio_pipeline:
            if drama["audio_filename"]:
                p = os.path.join(ddir, drama["audio_filename"])
                if os.path.exists(p):
                    existing_audio = p

            if existing_audio:
                st.caption(f"✅ Current audio: `{drama['audio_filename']}` "
                          f"({storage.format_bytes(os.path.getsize(existing_audio))})"
                          + (f" + video `{drama['source_video_filename']}`"
                             if drama.get("source_video_filename") else ""))
                if st.button("🗑️ Remove current audio/video", key=f"rm_audio_{picked_id}"):
                    for _fname_field in ("audio_filename", "source_video_filename"):
                        _fname = drama.get(_fname_field)
                        if _fname:
                            _fpath = os.path.join(ddir, _fname)
                            if os.path.exists(_fpath):
                                os.remove(_fpath)
                    db.update_drama(picked_id, audio_filename=None, source_video_filename=None)
                    st.success("Removed. Upload or download something to replace it -- your "
                              "transcript/lines below (if any) are untouched.")
                    st.rerun()

            # A segmented choice instead of "uploader always visible, download
            # option buried in a collapsed expander below it" -- the expander
            # version was easy to scroll past entirely, which is exactly why
            # people couldn't find the download option.
            import_method = st.radio(
                "Add audio/video" + (" — already added" if existing_audio else ""),
                ["upload", "url"],
                format_func=lambda m: "📁 Upload a file" if m == "upload"
                                       else "🔗 Download from a URL (yt-dlp)",
                horizontal=True, key=f"import_method_{picked_id}")

            if import_method == "upload":
                audio_file = st.file_uploader(
                    "Audio or video file *(required)*",
                    type=["mp3", "wav", "m4a", "flac", "ogg", "mp4", "mkv", "mov", "webm"],
                    key=f"audio_up_{picked_id}")
            else:
                st.caption(
                    "Fetches audio/video directly via yt-dlp (YouTube and many other sites) -- "
                    "no need to run it on the command line and upload the result yourself. Only "
                    "use this for content you actually have the right to use. Needs "
                    "`pip install yt-dlp`."
                )
                dl_url = st.text_input("Video URL", key=f"dl_url_{picked_id}")
                dl_audio_only = st.checkbox(
                    "Audio only (recommended -- smaller, and this is all the pipeline needs "
                    "unless you also want the video for hardsub/dub export later)",
                    value=True, key=f"dl_audio_only_{picked_id}")
                if st.button("⬇️ Download", disabled=not dl_url.strip()):
                    progress_bar = st.progress(0.0)
                    status = st.empty()
                    try:
                        import video_download
                        # Auto-fills the untranslated title from yt-dlp's own metadata
                        # -- only when nothing's there yet, so it never overwrites a
                        # title someone already typed or fixed by hand.
                        _fetched_title = {}
                        downloaded_path = video_download.download(
                            dl_url.strip(), ddir, audio_only=dl_audio_only,
                            progress_cb=lambda frac, msg: (progress_bar.progress(frac), status.caption(msg)),
                            title_cb=lambda t: _fetched_title.setdefault("title", t),
                            cookies_browser=st.session_state.get("settings_cookies_browser"),
                            cookies_file=st.session_state.get("settings_cookies_file") or None)
                        _title_update = {}
                        if _fetched_title.get("title") and not (drama.get("title_en") or drama.get("title_zh")):
                            _title_update["title_zh"] = _fetched_title["title"]
                        if dl_audio_only:
                            db.update_drama(picked_id, audio_filename=os.path.basename(downloaded_path),
                                             source_url=dl_url.strip(), **_title_update)
                        else:
                            audio_out = os.path.join(ddir, "audio.wav")
                            with st.spinner("Extracting audio from downloaded video..."):
                                core_module.extract_audio_from_video(downloaded_path, audio_out)
                            db.update_drama(picked_id, audio_filename="audio.wav",
                                             source_video_filename=os.path.basename(downloaded_path),
                                             source_url=dl_url.strip(), **_title_update)
                        st.success("Downloaded."
                                   + (f" Title filled in from the source: \"{_title_update['title_zh']}\" "
                                      "-- edit it below if you want to translate or adjust it."
                                      if _title_update else ""))
                        st.rerun()
                    except ImportError as exc:
                        st.error(str(exc))
                    except video_download.DownloadError as exc:
                        st.error(str(exc))

            st.markdown("**Transcript**")
            _video_exts = (".mp4", ".mkv", ".mov", ".webm")
            _has_video_source = bool(drama.get("source_video_filename")) or (
                audio_file is not None
                and os.path.splitext(audio_file.name)[1].lower() in _video_exts)
            _transcript_mode_options = ["have_transcript", "whisper"]
            if _has_video_source:
                _transcript_mode_options.append("hardsub_ocr")
            transcript_mode = st.radio(
                "Where does the transcript come from?",
                _transcript_mode_options,
                index=_transcript_mode_options.index(drama.get("transcript_mode") or "have_transcript")
                      if (drama.get("transcript_mode") or "have_transcript") in _transcript_mode_options
                      else 0,
                format_func=lambda m: {
                    "have_transcript": "I have the transcript (most accurate)",
                    "whisper": "I don't have one -- let Whisper transcribe the audio",
                    "hardsub_ocr": "The video already has captions burned in -- read those instead (OCR, experimental)",
                }[m],
                key=f"tmode_{picked_id}", horizontal=False)
            if transcript_mode != drama.get("transcript_mode"):
                db.update_drama(picked_id, transcript_mode=transcript_mode)

            if transcript_mode == "have_transcript":
                st.caption("Paste the official or fan transcript. Using a real transcript is "
                          "meaningfully better than speech recognition -- Whisper misreads names "
                          "and uncommon terms, and those errors carry straight into the translation.")
                transcript_text = st.text_area("Transcript *(required)*", height=180,
                                                key=f"transcript_{picked_id}")
            elif transcript_mode == "hardsub_ocr":
                st.caption("Samples frames from the video, auto-finds the caption band, and reads "
                          "its text with OCR instead of transcribing the audio -- for clips where "
                          "the caption is what should be translated, not necessarily whatever's "
                          "spoken (compilations, variety shows, or audio that doesn't match the "
                          "caption). Experimental: auto-detection can miss unusual caption "
                          "placement/styling, and only reads ONE caption region even if the video "
                          "has captions in two places at once (e.g. a header AND a bottom caption).")
                _hardsub_backend_options = ["tesseract", "paddle"] if source_language == "zh" else ["tesseract"]
                ocr_backend_choice = st.selectbox(
                    "OCR engine", _hardsub_backend_options,
                    index=(1 if source_language == "zh" else 0),
                    format_func=lambda b: "Tesseract (general-purpose)" if b == "tesseract"
                                            else "PaddleOCR (higher accuracy for Chinese, heavier install)",
                    help="Defaults to PaddleOCR for Chinese -- confirmed more accurate on stylized/"
                         "small hardsub captions than Tesseract, at the cost of a heavier install "
                         "(`pip install paddleocr paddlepaddle`). Switch to Tesseract if PaddleOCR "
                         "isn't installed and you'd rather not install it.",
                    key=f"hardsub_ocr_backend_{picked_id}")
                sample_interval = st.slider(
                    "Sample every N seconds", 0.5, 3.0, 1.0, step=0.5,
                    key=f"hardsub_interval_{picked_id}",
                    help="Lower catches short-lived captions more reliably but takes longer to run.")
                transcript_text = ""
            else:
                st.caption("Whisper will produce the transcript from the audio itself. Expect errors "
                          "on names, sect terms, and anything homophone-heavy -- you can correct them "
                          "in the review table before translating. Larger model = fewer mistakes.")
                transcript_text = ""
        else:
            st.caption(
                "Paste the novel text (Chinese). It'll be chunked into narration lines, "
                "speaker-tagged automatically (dialogue vs. narrator), translated, and "
                "synthesized into a full AI narration track -- no source audio needed."
            )
            with st.expander("📷 Or extract text from chapter/page scan images (OCR)"):
                st.caption(
                    "For chapters served as images instead of selectable text. "
                    "Backend options depend on source language -- see README for install steps."
                )
                ocr_images = st.file_uploader("Upload page images (in reading order)",
                                               type=["png", "jpg", "jpeg"], accept_multiple_files=True)
                ocr_backend_options = ["tesseract", "paddle"] if source_language == "zh" else (
                    ["manga_ocr", "tesseract"] if source_language == "ja" else ["tesseract"])
                ocr_backend = st.radio("OCR backend", ocr_backend_options, horizontal=True,
                                        help="manga_ocr: best for JP speech-bubble crops. "
                                             "paddle: higher accuracy for Chinese, heavier install.")
                if ocr_images and st.button("Extract text from images"):
                    import ocr as ocr_module
                    img_paths = []
                    for img in ocr_images:
                        p = os.path.join(ddir, f"ocr_{img.name}")
                        with open(p, "wb") as f:
                            f.write(img.getbuffer())
                        img_paths.append(p)
                    with st.spinner("Running OCR..."):
                        extracted = ocr_module.extract_text_from_images(
                            img_paths, backend=ocr_backend, source_language=source_language,
                            chinese_script=chinese_script,
                            tesseract_cmd=st.session_state.get("settings_tesseract_cmd") or None)
                    st.session_state[f"ocr_text_{picked_id}"] = extracted
                    st.success(f"Extracted {len(extracted):,} characters. Review below before using.")

            ocr_default = st.session_state.get(f"ocr_text_{picked_id}", "")

            with st.expander("📚 Or import from an EPUB you own"):
                st.caption("Requires `pip install ebooklib beautifulsoup4`.")
                epub_file = st.file_uploader("Upload .epub", type=["epub"], key="epub_upload")
                if epub_file:
                    epub_path = os.path.join(ddir, "source.epub")
                    with open(epub_path, "wb") as f:
                        f.write(epub_file.getbuffer())
                    import epub_io
                    try:
                        n_chapters = epub_io.get_epub_chapter_count(epub_path)
                        ec1, ec2 = st.columns(2)
                        ch_start = ec1.number_input("From chapter", value=1, min_value=1, max_value=n_chapters)
                        ch_end = ec2.number_input("To chapter", value=min(5, n_chapters), min_value=1, max_value=n_chapters)
                        if st.button("Import chapters from EPUB"):
                            with st.spinner("Extracting text..."):
                                extracted = epub_io.import_epub_text(epub_path, chapter_range=(ch_start - 1, ch_end))
                            st.session_state[f"ocr_text_{picked_id}"] = extracted
                            st.success(f"Imported {len(extracted):,} characters from chapters {ch_start}-{ch_end}.")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Couldn't read that EPUB: {e}")

            novel_narration_text = st.text_area("Novel text *(required)*", value=ocr_default, height=220)

    with st.expander("3. 📚 Reference novel (optional) & recognition settings", expanded=False):
        st.caption("If this drama already has an official/fan English translation elsewhere, "
                   "paste it here to keep terminology consistent -- separate from the novel "
                   "narration text above, which is what actually gets read aloud.")
        existing_novel_text = None
        novel_path = os.path.join(ddir, drama["novel_reference_filename"]) if drama["novel_reference_filename"] else None
        if novel_path and os.path.exists(novel_path):
            with open(novel_path, "r", encoding="utf-8") as f:
                existing_novel_text = f.read()
            st.caption(f"Novel reference already saved (~{len(existing_novel_text):,} chars).")
        novel_file = st.file_uploader("Upload novel translation (.txt/.md)", type=["txt", "md"], key="novel_up")
        novel_pasted = st.text_area("...or paste it here", height=100, key="novel_paste")

        # ---- Build a glossary from the novel ----------------------------------
        with st.expander("📕 Build a glossary from this novel"):
            st.caption(
                "The novel is usually the better source for terminology than the drama's "
                "dialogue -- it's longer and introduces more names, sects and places. Terms are "
                "sampled from across the whole text, not just the opening chapters, so later "
                "introductions aren't missed. Only terminology is extracted; no passages are stored."
            )
            _gl_series = drama.get("series_id")
            if not _gl_series:
                st.info("Assign this drama to a series first (under Translation below) -- "
                       "glossaries are shared across a series so every book and season stays "
                       "consistent.")
            else:
                _novel_src = (existing_novel_text or "")
                if novel_file is not None:
                    _novel_src = novel_file.getvalue().decode("utf-8", errors="ignore")
                elif novel_pasted.strip():
                    _novel_src = novel_pasted

                # Reuses the raw novel uploaded above (Content source -> "Raw novel") rather
                # than asking for it a second time -- one upload now feeds both transcription
                # priming and this pairing, instead of needing the same file twice.
                _raw_context_path = os.path.join(ddir, "raw_novel_context.txt")
                if os.path.exists(_raw_context_path):
                    with open(_raw_context_path, "r", encoding="utf-8") as f:
                        _orig_src = f.read()
                    st.caption(f"✅ Using the raw {source_language.upper()} novel uploaded above "
                              f"(~{len(_orig_src):,} chars) as the paired original-language source. "
                              f"With both, terms are extracted as matched pairs -- capturing how "
                              f"each was actually rendered rather than inventing new wording.")
                else:
                    st.caption(f"Optionally provide the ORIGINAL {source_language.upper()} novel too. "
                              f"With both, terms are extracted as matched pairs -- capturing how "
                              f"each was actually rendered rather than inventing new wording. "
                              f"(Uploading it here also saves it for transcription priming above.)")
                    orig_novel_file = st.file_uploader(
                        f"Original {source_language.upper()} novel", type=["txt", "md", "epub"],
                        key="orig_novel_up")
                    _orig_src = ""
                    if orig_novel_file is not None:
                        try:
                            _orig_src = core_module.load_novel_text_for_context(
                                orig_novel_file.getvalue(), orig_novel_file.name)
                            with open(_raw_context_path, "w", encoding="utf-8") as f:
                                f.write(_orig_src)
                            st.success(f"Saved -- also now feeding transcription priming above.")
                        except ImportError as e:
                            st.error(str(e))

                gl_key = st.session_state.get(f"settings_{drama.get('translation_engine') or 'claude'}", "")
                if st.button("📖 Extract glossary from novel"):
                    if not (_novel_src.strip() or _orig_src.strip()):
                        st.warning("Upload or paste a novel first.")
                    elif not gl_key:
                        st.warning("Set an API key in the ⚙️ Settings sidebar first.")
                    else:
                        _gl_engine_name = drama.get("translation_engine") or "claude"
                        eng_gl = translate_engines.get_engine(
                            _gl_engine_name, gl_key,
                            free_tier=_gl_engine_name == "gemini" and _gemini_free_tier,
                            base_url=_ollama_base_url if _gl_engine_name == "ollama" else None)
                        # If both are supplied the original is the source and the
                        # existing translation shows the established rendering.
                        src_text = _orig_src if _orig_src.strip() else _novel_src
                        en_text = _novel_src if _orig_src.strip() else ""
                        bar = st.progress(0.0, text="Reading the novel...")
                        try:
                            proposed_gl = tguide.extract_glossary_from_novel(
                                src_text, eng_gl, source_language=source_language,
                                english_translation=en_text,
                                known_terms=db.list_glossary_terms(_gl_series),
                                progress_cb=lambda f: bar.progress(f, text=f"Reading... {f*100:.0f}%"),
                                usage_cb=lambda inp, out: db.log_usage(
                                    picked_id, _gl_engine_name, getattr(eng_gl, "model", _gl_engine_name),
                                    "glossary_from_novel", inp, out,
                                    translate_engines.estimate_cost_for_engine(eng_gl, inp, out)))
                            bar.empty()
                            st.session_state[f"novel_glossary_{picked_id}"] = proposed_gl
                            st.success(f"Proposed {len(proposed_gl)} term(s). Review below.")
                        except Exception as e:
                            bar.empty()
                            st.error(f"Extraction failed: {e}")

                _proposed_gl = st.session_state.get(f"novel_glossary_{picked_id}", [])
                if _proposed_gl:
                    st.caption(f"Review {len(_proposed_gl)} proposed term(s) -- uncheck anything wrong:")
                    gl_df = pd.DataFrame(_proposed_gl)
                    gl_df.insert(0, "Add", True)
                    edited_gl = st.data_editor(gl_df, width='stretch', hide_index=True,
                                                key=f"gl_editor_{picked_id}")
                    if st.button("➕ Add these terms to the series glossary"):
                        n_added = 0
                        for row in edited_gl[edited_gl["Add"]].to_dict("records"):
                            db.upsert_glossary_term(
                                _gl_series, row.get("term", ""), row.get("suggested_translation", ""),
                                notes=row.get("reason", ""), category=row.get("category"),
                                policy=row.get("policy"))
                            n_added += 1
                        st.session_state[f"novel_glossary_{picked_id}"] = []
                        st.success(f"Added {n_added} term(s) to the glossary.")
                        st.rerun()

                st.divider()
                st.caption("**Or import a glossary file you already have** (CSV, TSV, or JSON). "
                          "A plain two-column term/translation sheet works.")
                gl_file = st.file_uploader("Glossary file", type=["csv", "tsv", "json"], key="gl_file_up")
                if gl_file is not None and st.button("📥 Import glossary file"):
                    raw = gl_file.getvalue().decode("utf-8", errors="ignore")
                    imported, warns = tguide.parse_glossary_file(raw, gl_file.name)
                    for w in warns[:10]:
                        st.caption(f"⚠️ {w}")
                    if imported:
                        for t in imported:
                            db.upsert_glossary_term(
                                _gl_series, t["term_original"], t["term_translation"],
                                notes=t["notes"], category=t["category"],
                                policy=t["policy"], enforce_exact=t["enforce_exact"])
                        st.success(f"Imported {len(imported)} term(s).")
                        st.rerun()
                    else:
                        st.error("Nothing could be imported from that file.")

                _current_gl = db.list_glossary_terms(_gl_series)
                if _current_gl:
                    st.download_button(
                        f"📤 Export glossary ({len(_current_gl)} terms) as CSV",
                        tguide.glossary_to_csv(_current_gl),
                        file_name="glossary.csv")

        _whisper_size_options = list(core_module.WHISPER_MODELS)
        _default_whisper_size = drama.get("whisper_size") or core_module.DEFAULT_WHISPER_SIZE
        whisper_size = st.selectbox(
            "Speech recognition model", _whisper_size_options,
            index=_whisper_size_options.index(_default_whisper_size)
                  if _default_whisper_size in _whisper_size_options
                  else _whisper_size_options.index(core_module.DEFAULT_WHISPER_SIZE),
            format_func=lambda m: core_module.WHISPER_MODELS[m],
            disabled=content_mode == "novel_narration",
            key=f"whisper_size_{picked_id}",
            help="large-v3 is markedly better on Chinese names and homophones. It's free, "
                 "just slower and ~3GB to download — and much faster with GPU enabled.")
        if whisper_size != drama.get("whisper_size"):
            db.update_drama(picked_id, whisper_size=whisper_size)
        _model_warning = core_module.whisper_model_warning(whisper_size, source_language)
        if _model_warning and content_mode != "novel_narration":
            st.warning(_model_warning)
        st.checkbox(
            "⚡ Fast mode (batched decoding, ~4× faster on a GPU)", value=False,
            key=f"whisper_fast_mode_{picked_id}", disabled=content_mode == "novel_narration",
            help="Runs several stretches of speech through the model at once. Same model and "
                 "settings, just quicker -- it needs more GPU memory while it runs, so turn it "
                 "off if transcription runs out of memory.")

        with st.expander("🎯 Recognition accuracy (free — worth doing)"):
            st.caption(
                "Whisper mishears proper nouns constantly in Chinese, because a wrong guess is "
                "usually still a real word — nothing looks broken until you read the translation. "
                "Priming it with the names it should expect fixes a lot of that at no cost."
            )
            _gl_terms = (db.list_glossary_terms(drama["series_id"])
                         if drama.get("series_id") else [])
            _auto_prompt = core_module.build_initial_prompt(_gl_terms)
            if _auto_prompt:
                st.caption(f"From this series' glossary ({len(_gl_terms)} terms): `{_auto_prompt[:120]}`")
            else:
                st.caption("No glossary terms yet. Build one from the novel above, or type names "
                          "below — either feeds recognition.")
            _manual_prompt = st.text_input(
                "Extra names to expect (、 or comma separated)",
                key=f"initprompt_{picked_id}",
                placeholder="沈清疑、云隐宗、神机营")
            _name_prompt = "、".join(x for x in [_auto_prompt.rstrip("。"),
                                                  _manual_prompt.strip()] if x)
            if _name_prompt:
                _name_prompt += "。"

            _raw_novel_path = os.path.join(ddir, "raw_novel_context.txt")
            if os.path.exists(_raw_novel_path):
                with open(_raw_novel_path, "r", encoding="utf-8") as f:
                    _novel_excerpt = core_module.extract_novel_excerpt_for_prompt(f.read())
                initial_prompt = core_module.combine_initial_prompt(_name_prompt, _novel_excerpt)
                st.caption(f"Including an excerpt from the raw novel you uploaded above "
                          f"({len(_novel_excerpt):,} of its characters, names take priority).")
            else:
                initial_prompt = _name_prompt

            beam_size = st.slider("Search width (beam size)", 1, 10, 5,
                                   help="Higher considers more alternatives before committing. "
                                        "8-10 helps on difficult audio; it costs time, not money.")

            _default_min_silence_ms = st.session_state.get(f"min_silence_ms_{picked_id}", 300)
            min_silence_ms = st.slider(
                "Speech-splitting sensitivity (ms of silence to start a new line)", 300, 3000,
                _default_min_silence_ms, 100,
                help="The default (300ms) starts a new line at almost any real pause, so "
                     "back-to-back dialogue, internal-monologue narration and quick exchanges "
                     "each get their own line instead of several being merged into one "
                     "oversized block with only the first sentence kept as its text. Raise it "
                     "(1000ms+) if a drama has genuinely long natural pauses and lines are "
                     "splitting mid-thought -- there's no universally correct value.")
            st.session_state[f"min_silence_ms_{picked_id}"] = min_silence_ms
            if min_silence_ms > 300:
                st.caption(f"Set to {min_silence_ms}ms -- fewer, longer lines than the default; "
                          f"re-run 'Check line coverage' below after aligning to see the effect.")

            vad_threshold = st.slider(
                "Speech detection sensitivity", 0.1, 0.9, 0.5, 0.05,
                help="How confident the voice-activity detector must be that a stretch of audio "
                     "is actually speech before keeping it (this IS Silero VAD -- faster-whisper "
                     "uses it internally already, this just exposes its own sensitivity knob). "
                     "Lower it (0.3-0.4) if quiet or distant dialogue is going missing entirely. "
                     "Raise it (0.6-0.7) if a noisy or music-heavy source is producing phantom "
                     "lines from non-speech. Same tradeoff shape as the setting above -- no value "
                     "is strictly better for every source.")

            if existing_audio and os.path.exists(existing_audio):
                _autotune_key = f"autotune_{picked_id}"
                _autotune_job_id = f"autotune_{picked_id}"
                _autotune = st.session_state.get(_autotune_key)
                _autotune_job = background_jobs.get_status(_autotune_job_id)
                with st.expander("🪄 Auto-tune this value (tries a few candidates, you pick)"):
                    st.caption(
                        "Re-transcribes this drama's audio once per candidate value below and "
                        "shows how many long/merged lines each produces -- an honest way to pick "
                        "a value without guessing, at the real cost of one full re-transcription "
                        "per candidate. Never applies anything by itself -- you pick from the "
                        "results, the same as setting the slider above by hand.")
                    _candidates = list(core_module.DEFAULT_AUTOTUNE_CANDIDATES_MS)

                    def _start_autotune_candidate(candidate_ms):
                        background_jobs.start_process_job(
                            _autotune_job_id, core_module.autotune_subprocess_worker,
                            args=(existing_audio, whisper_size, source_language,
                                  st.session_state.get("use_gpu", False), None,
                                  st.session_state.get("settings_hf_token", "") or None,
                                  initial_prompt, beam_size, candidate_ms, vad_threshold,
                                  st.session_state.get(f"whisper_fast_mode_{picked_id}", False)),
                            gpu_touching=True, description=f"Auto-tuning ({_drama_label(drama)})")

                    if not _autotune and not _autotune_job:
                        try:
                            import video_export
                            _audio_duration = video_export.probe_duration_seconds(existing_audio)
                        except Exception:
                            _audio_duration = 0
                        _eta = _autotune_estimate_caption(_audio_duration, len(_candidates))
                        if _eta:
                            st.caption(_eta)
                        if st.button("🪄 Auto-tune", key=f"autotune_btn_{picked_id}"):
                            st.session_state[_autotune_key] = {
                                "candidates": _candidates, "results": [], "cancelled": False}
                            _start_autotune_candidate(_candidates[0])
                            st.rerun()

                    if _autotune_job:
                        if _autotune_job["status"] == "queued":
                            st.info(_autotune_job.get("message") or "Waiting for the GPU...")
                        elif _autotune_job["status"] == "running":
                            _tested_n = len(_autotune["results"]) if _autotune else 0
                            _current_ms = _candidates[_tested_n] if _tested_n < len(_candidates) else "?"
                            st.info(f"Testing candidate {_tested_n + 1} of {len(_candidates)} "
                                    f"({_current_ms}ms)...")
                            ac1, ac2 = st.columns(2)
                            if ac1.button("🔄 Refresh progress", key=f"refresh_autotune_{picked_id}"):
                                st.rerun()
                            if ac2.button("✖ Cancel", key=f"cancel_autotune_{picked_id}"):
                                background_jobs.request_cancel(_autotune_job_id)
                                if _autotune:
                                    _autotune["cancelled"] = True
                                st.rerun()
                        elif _autotune_job["status"] == "cancelled":
                            st.warning("Auto-tune was stopped. Results for whichever candidate(s) "
                                      "already finished are shown below, if any.")
                            background_jobs.clear_job(_autotune_job_id)
                        elif _autotune_job["status"] == "error":
                            st.error(f"Auto-tune failed: {_autotune_job['error']}")
                            background_jobs.clear_job(_autotune_job_id)
                            st.session_state.pop(_autotune_key, None)
                        elif _autotune_job["status"] == "done":
                            _result = _autotune_job.get("result") or {}
                            _segments = _result.get("segments") or []
                            _cand_lines = [Line(idx=i, start=s["start"], end=s["end"], zh=s["text"])
                                          for i, s in enumerate(_segments) if s["text"].strip()]
                            _coverage = core_module.diagnose_line_coverage(_cand_lines)
                            _autotune["results"].append({
                                "candidate_ms": _result.get("candidate_ms"),
                                "long_lines": len(_coverage["long_lines"]),
                                "total_lines": len(_cand_lines),
                            })
                            background_jobs.clear_job(_autotune_job_id)
                            _tested = {r["candidate_ms"] for r in _autotune["results"]}
                            _remaining = [c for c in _autotune["candidates"] if c not in _tested]
                            if _remaining and not _autotune.get("cancelled"):
                                _start_autotune_candidate(_remaining[0])
                            st.rerun()

                    if _autotune and _autotune["results"] and not _autotune_job:
                        st.markdown("**Results** (fewer long/merged lines is generally better -- "
                                  "watch total line count too, since splitting into far more lines "
                                  "isn't automatically an improvement)")
                        _best = min(_autotune["results"], key=lambda r: r["long_lines"])
                        for r in sorted(_autotune["results"], key=lambda r: r["candidate_ms"]):
                            rc1, rc2, rc3, rc4 = st.columns([2, 2, 2, 2])
                            rc1.metric(f"{r['candidate_ms']}ms" + (" ⭐" if r is _best else ""), "")
                            rc2.metric("Long/merged lines", r["long_lines"])
                            rc3.metric("Total lines", r["total_lines"])
                            if rc4.button(f"Use {r['candidate_ms']}ms",
                                         key=f"autotune_use_{picked_id}_{r['candidate_ms']}"):
                                st.session_state[f"min_silence_ms_{picked_id}"] = r["candidate_ms"]
                                st.session_state.pop(_autotune_key, None)
                                st.success(f"Speech-splitting sensitivity set to {r['candidate_ms']}ms. "
                                          "Re-run \"Transcribe & Align\" above to apply it.")
                                st.rerun()
                        if st.button("Discard results", key=f"autotune_discard_{picked_id}"):
                            st.session_state.pop(_autotune_key, None)
                            st.rerun()

            separate_vocals_first = st.checkbox(
                "🎵 Remove background music before transcribing (slower)",
                value=False,
                help="Runs a real music-source-separation model (not a generic noise filter) "
                     "over the whole file first and transcribes only its vocals -- for a music "
                     "bed under the dialogue that's confusing Whisper (phantom lines from "
                     "lyrics, or real dialogue getting missed under the mix). Adds a full extra "
                     "pass over the audio (roughly as long as transcription itself) and "
                     "downloads its own model on first use. Skip this for already-clean "
                     "dialogue -- there's nothing for it to separate out. Needs "
                     "`pip install audio-separator` (preferred) or `pip install demucs`.")
            separation_backend = st.selectbox(
                "Music-removal model", list(audio_preprocess.SEPARATION_BACKENDS),
                format_func=lambda b: audio_preprocess.SEPARATION_BACKENDS[b],
                disabled=not separate_vocals_first, key=f"separation_backend_{picked_id}",
                help="Mel-Band RoFormer usually leaves cleaner vocals on content with background "
                     "music. Demucs is the older fallback (its project is no longer maintained).")

            realign_long_segments = st.checkbox(
                "🧪 Split long merged lines using word-level alignment (experimental)",
                value=False,
                help="For a line that's still one oversized block after tuning the sensitivity "
                     "sliders above (the VAD-merge problem -- one 'line' spanning several minutes "
                     "with only its first sentence's text) -- re-aligns that line's own text "
                     "against its own audio to find its REAL internal pauses and splits it back "
                     "into multiple correctly-timed lines. Can't recover text Whisper didn't "
                     "already transcribe, only re-time what's already there. Not verified against "
                     "real speech in development -- if a line looks wrong afterward, turn this "
                     "off and re-transcribe; nothing about your audio or existing settings is "
                     "affected either way. Needs `pip install torchaudio uroman` (first use also "
                     "downloads a ~1.1GB model).")

            if not st.session_state.get("use_gpu"):
                st.caption("💡 GPU is off. On your card, enabling it under Settings → Performance "
                          "makes large-v3 practical rather than painfully slow.")

        _alignment_method_options = ["whisper_diff", "qwen3_forced_align"]
        alignment_method = st.selectbox(
            "Timing method (when you have a real transcript)",
            _alignment_method_options,
            index=_alignment_method_options.index(drama.get("alignment_method") or "whisper_diff")
                  if (drama.get("alignment_method") or "whisper_diff") in _alignment_method_options
                  else 0,
            format_func=lambda m: ("Whisper + character-diff (current default)"
                                    if m == "whisper_diff" else
                                    "Qwen3-ForcedAligner (experimental -- true forced alignment)"),
            disabled=content_mode == "novel_narration",
            key=f"alignment_method_{picked_id}",
            help="The default runs Whisper for timing, then fuzzy-matches your real transcript "
                 "against Whisper's (often wrong) text character-by-character, guessing each "
                 "match's timestamp as an even split across its Whisper segment. Qwen3-ForcedAligner "
                 "instead aligns your ACTUAL transcript text directly against the audio -- no "
                 "guessing, no fuzzy-matching against ASR errors -- but needs `pip install "
                 "qwen-asr torch` and is unverified on this project's content. Only applies when "
                 "you supplied a real transcript above; Whisper's own text has nothing to align "
                 "against. Falls back to the default automatically if qwen-asr isn't installed.")
        if alignment_method != drama.get("alignment_method"):
            db.update_drama(picked_id, alignment_method=alignment_method)

        _asr_backend_options = ["whisper", "qwen3_asr"]
        asr_backend_choice = st.selectbox(
            "Transcription model (when Whisper is doing the transcript, not just timing)",
            _asr_backend_options,
            index=_asr_backend_options.index(drama.get("asr_backend_choice") or "whisper")
                  if (drama.get("asr_backend_choice") or "whisper") in _asr_backend_options else 0,
            format_func=lambda m: ("Whisper (current default)" if m == "whisper" else
                                    "Qwen3-ASR (experimental -- purpose-built for zh/ja/ko)"),
            disabled=content_mode == "novel_narration",
            key=f"asr_backend_choice_{picked_id}",
            help="Only applies when you picked 'let Whisper transcribe the audio' above -- if you "
                 "supplied a real transcript, this has no effect (nothing to transcribe). Public "
                 "benchmarks show Qwen3-ASR well ahead of Whisper on Mandarin, especially under "
                 "noise; no direct Japanese comparison was found, so this is unverified on that "
                 "language specifically. Needs `pip install qwen-asr torch`, re-transcribes each "
                 "of Whisper's segments individually (so it's slower than one Whisper pass), and "
                 "keeps Whisper's own segment timing either way -- only the transcribed text "
                 "changes. Falls back to Whisper automatically if qwen-asr isn't installed.")
        if asr_backend_choice != drama.get("asr_backend_choice"):
            db.update_drama(picked_id, asr_backend_choice=asr_backend_choice)

    with st.expander("4. 🎙️ Speaker diarization", expanded=False):
        if has_audio_pipeline:
            st.caption(
                "Distinguishes different voices/characters in the audio, so lines can be grouped "
                "by character and dubbed with different voices (or cloned). Needs `pyannote.audio` "
                "installed and a free Hugging Face token -- see README."
            )
            hf_token = synced_api_key_input(
                "Hugging Face token (for diarization)", "hf_token", "workspace_hf_token_input")
            run_diarize = st.checkbox("Run speaker diarization during alignment",
                                       value=(content_mode == "streamer_vod"),
                                       disabled=not hf_token)
            import diarize
            _default_expected_speakers = st.session_state.get(
                f"expected_speakers_{picked_id}",
                diarize.load_last_speaker_count(ddir) or 0)
            expected_speakers = st.number_input(
                "Expected number of speakers (0 = auto-detect)", min_value=0, max_value=20,
                value=_default_expected_speakers, disabled=not hf_token,
                help="Telling the diarizer how many speakers to expect is usually more reliable "
                     "than auto-detection, especially on long or noisy audio -- particularly "
                     "relevant for Streamer/VOD content with several people talking.")
            st.session_state[f"expected_speakers_{picked_id}"] = expected_speakers
            _speaker_audio = (os.path.join(ddir, drama["audio_filename"])
                              if drama.get("audio_filename") else None)
            if (_speaker_audio and os.path.exists(_speaker_audio)
                    and (st.session_state.lines or db.load_lines(picked_id))):
                _render_speaker_rerun(picked_id, ddir, _speaker_audio, hf_token, expected_speakers)
        else:
            st.caption("For novel narration, speaker attribution is done by the translation LLM "
                       "(who's speaking each line) instead of audio diarization -- no audio to analyze.")
            hf_token, run_diarize, expected_speakers = None, False, 0

    with st.expander("5. 🌐 Translation", expanded=False):

        if drama.get("last_translate_errors"):
            try:
                _persisted_errors = json.loads(drama["last_translate_errors"])
            except (json.JSONDecodeError, TypeError):
                _persisted_errors = []
            if _persisted_errors:
                _failed_nums = sorted({i + 1 for e in _persisted_errors for i in e.get("lines", [])})
                st.warning(
                    f"⚠️ The last translation run had {len(_persisted_errors)} batch failure(s) -- "
                    f"line(s) {_failed_nums} are still untranslated. This is why some lines have "
                    f"no English text; it's not a display bug. Click **Translate all lines** below "
                    f"to retry just the missing ones (already-translated lines are skipped "
                    f"automatically, so this won't re-cost anything already done).")
                if st.button("Dismiss this notice", key=f"dismiss_tr_err_{picked_id}"):
                    db.update_drama(picked_id, last_translate_errors=None)
                    st.rerun()

        _all_presets = db.list_presets()
        if _all_presets:
            _preset_options = {"-- none --": None}
            _preset_options.update({p["name"]: p for p in _all_presets})
            _pc1, _pc2 = st.columns([3, 1])
            _preset_choice = _pc1.selectbox(
                "Apply a preset", list(_preset_options.keys()),
                key=f"apply_preset_choice_{picked_id}",
                help="Fills in the fields below from a saved Workspace configuration "
                     "(📚 Library → 🎛️ Presets to manage them) -- still freely editable "
                     "afterward, never locked.")
            if _pc2.button("Apply", key=f"apply_preset_btn_{picked_id}",
                          disabled=_preset_options[_preset_choice] is None):
                _picked_preset = _preset_options[_preset_choice]
                if _picked_preset.get("translation_engine"):
                    db.update_drama(picked_id, translation_engine=_picked_preset["translation_engine"])
                apply_preset_to_session(_picked_preset, picked_id)
                st.rerun()

        # tguide is already available here via `from common import *` (common.py
        # imports it at module level) -- a redundant local `import ... as tguide`
        # used to sit here, which makes Python treat `tguide` as local to this
        # entire function (imports are assignments), breaking the EARLIER use of
        # `tguide` in the Romanize-credits handler above with an UnboundLocalError,
        # since that use executes before this line does.
        _style_keys = list(tguide.STYLE_PRESETS.keys())
        _default_style = "novel" if content_mode == "novel_narration" else "audio_drama"
        _saved_style = st.session_state.get(f"style_preset_{picked_id}", _default_style)
        if _saved_style not in _style_keys:
            _saved_style = _default_style
        style_preset = st.selectbox(
            "Translation style", _style_keys, index=_style_keys.index(_saved_style),
            format_func=lambda k: tguide.STYLE_PRESETS[k]["label"],
            help="Changes register and pacing guidance -- spoken dialogue reads very "
                 "differently from prose or bubble text.")
        st.session_state[f"style_preset_{picked_id}"] = style_preset
        with st.expander("ℹ️ What this style asks the translator for"):
            st.caption(tguide.STYLE_PRESETS[style_preset]["guidance"])
        # "Glossary scope" in Step 9c's preset (the roadmap's own term):
        # whether the baihe/GL genre-guidance glossary block below is
        # included by default -- the only reusable, series-independent
        # glossary-related toggle here. A specific series' actual glossary
        # terms (glossary_terms table) can't be a preset field: presets
        # are explicitly meant to work "across unrelated series/projects"
        # (see the roadmap item's own text), so binding one to one
        # particular series_id would contradict that.
        include_genre_notes = st.checkbox(
            "Include baihe/GL genre guidance (pronoun clarity, kinship-term nuance, "
            "don't soften romantic content)",
            value=st.session_state.get(f"include_genre_notes_{picked_id}", True))
        st.session_state[f"include_genre_notes_{picked_id}"] = include_genre_notes
        st.session_state[f"default_female_pronouns_{picked_id}"] = st.checkbox(
            "Default ambiguous pronouns to she/her",
            value=st.session_state.get(f"default_female_pronouns_{picked_id}", False),
            help="Spoken Mandarin doesn't distinguish 他/她/它 (all pronounced \"tā\"), so "
                 "Whisper's transcribed character for a pronoun isn't a reliable gender signal "
                 "-- for an all/mostly-female cast, this tells the translator to default an "
                 "ambiguous reference to female instead of guessing from that character. A "
                 "specific character's pronouns always win over this default -- set them under "
                 "📖 Series glossary → 👥 People & pronouns (for a series), or per drama in "
                 "6. Name your characters.")
        custom_guide_notes = st.text_area(
            "Project-specific translation notes (optional)", height=68,
            placeholder="e.g. this character always speaks formally; keep the narrator distant")

        with st.expander("📖 Series glossary & term handling", expanded=False):
            existing_series = db.list_series()
            series_options = ["-- none --"] + [s["name"] for s in existing_series] + ["+ New series..."]
            current_series_name = next((s["name"] for s in existing_series if s["id"] == drama.get("series_id")), "-- none --")
            series_pick = st.selectbox("Series", series_options,
                                        index=series_options.index(current_series_name) if current_series_name in series_options else 0)
            if series_pick == "+ New series...":
                new_series_name = st.text_input("New series name")
                if new_series_name and st.button("Create & assign series"):
                    sid = db.get_or_create_series(new_series_name)
                    db.update_drama(picked_id, series_id=sid)
                    st.success(f"Assigned to series '{new_series_name}'.")
                    st.rerun()
            elif series_pick != "-- none --":
                sid = next(s["id"] for s in existing_series if s["name"] == series_pick)
                if sid != drama.get("series_id"):
                    db.update_drama(picked_id, series_id=sid)
                    st.rerun()

                st.markdown("**Auto-extract terms from the source text**")
                st.caption("Scans for names, sects, titles, honorifics, and concepts needing "
                          "consistent handling, and proposes a policy for each. Always review "
                          "before adding -- these are judgment calls.")
                _extract_key = st.session_state.get(f"settings_{drama.get('translation_engine') or 'claude'}", "")
                if st.button("🔍 Extract terms"):
                    source_lines = [ln.zh for ln in (st.session_state.lines or [])]
                    if not source_lines:
                        st.warning("No source lines yet -- align or chunk the text first.")
                    elif not _extract_key:
                        st.warning("Set an API key in the ⚙️ Settings sidebar first.")
                    else:
                        _ext_engine_name = drama.get("translation_engine") or "claude"
                        engine_x = translate_engines.get_engine(
                            _ext_engine_name, _extract_key,
                            free_tier=_ext_engine_name == "gemini" and _gemini_free_tier,
                            base_url=_ollama_base_url if _ext_engine_name == "ollama" else None)
                        with st.spinner("Scanning for terms..."):
                            proposed = tguide.extract_terms_llm(
                                source_lines, engine_x, source_language=source_language,
                                known_terms=db.list_glossary_terms(sid),
                                usage_cb=lambda inp, out: db.log_usage(
                                    picked_id, _ext_engine_name,
                                    getattr(engine_x, "model", _ext_engine_name),
                                    "extract_terms", inp, out,
                                    translate_engines.estimate_cost_for_engine(engine_x, inp, out)))
                        st.session_state[f"proposed_terms_{picked_id}"] = proposed
                        st.success(f"Proposed {len(proposed)} term(s) for review.")

                proposed = st.session_state.get(f"proposed_terms_{picked_id}", [])
                if proposed:
                    st.caption(f"Review {len(proposed)} proposed term(s):")
                    prop_df = pd.DataFrame(proposed)
                    prop_df.insert(0, "Add", True)
                    edited_prop = st.data_editor(
                        prop_df, width='stretch', hide_index=True,
                        key=f"prop_editor_{picked_id}")
                    if st.button("➕ Add selected terms to glossary"):
                        added = 0
                        for row in edited_prop[edited_prop["Add"]].to_dict("records"):
                            db.upsert_glossary_term(
                                sid, row.get("term", ""), row.get("suggested_translation", ""),
                                notes=row.get("reason", ""), category=row.get("category"),
                                policy=row.get("policy"))
                            added += 1
                        st.session_state[f"proposed_terms_{picked_id}"] = []
                        st.success(f"Added {added} term(s).")
                        st.rerun()

                st.markdown("**Current glossary**")
                terms = db.list_glossary_terms(sid)
                if terms:
                    for t in terms:
                        _editing_key = f"editing_glossary_{t['id']}"
                        pol_label = tguide.TERM_POLICIES.get(t.get("policy") or "keep_pinyin", {}).get("label", "")
                        lock = " 🔒" if t.get("enforce_exact") else ""
                        gtc1, gtc2, gtc3 = st.columns([6, 0.6, 0.6])
                        gtc1.caption(f"**{t['term_original']}** → {t['term_translation']}{lock} "
                                    f"_{pol_label}_" + (f" — {t['notes']}" if t.get("notes") else ""))
                        if gtc2.button("✏️", key=f"editglo_btn_{t['id']}"):
                            st.session_state[_editing_key] = not st.session_state.get(_editing_key, False)
                        if gtc3.button("🗑️", key=f"delglo_{t['id']}"):
                            db.delete_glossary_term(t["id"])
                            st.rerun()

                        if st.session_state.get(_editing_key):
                            with st.container(border=True):
                                egc1, egc2 = st.columns(2)
                                e_orig = egc1.text_input("Original term", value=t["term_original"],
                                                          key=f"eglo_orig_{t['id']}")
                                e_trans = egc2.text_input("Translation", value=t["term_translation"],
                                                           key=f"eglo_trans_{t['id']}")
                                egc3, egc4 = st.columns(2)
                                _cat_keys = list(tguide.TERM_CATEGORIES.keys())
                                _cur_cat = t.get("category") or "other"
                                e_cat = egc3.selectbox(
                                    "Category", _cat_keys,
                                    index=_cat_keys.index(_cur_cat) if _cur_cat in _cat_keys else 0,
                                    format_func=lambda k: tguide.TERM_CATEGORIES[k],
                                    key=f"eglo_cat_{t['id']}")
                                _pol_keys = list(tguide.TERM_POLICIES.keys())
                                _cur_pol = t.get("policy") or "keep_pinyin"
                                e_pol = egc4.selectbox(
                                    "Handling policy", _pol_keys,
                                    index=_pol_keys.index(_cur_pol) if _cur_pol in _pol_keys else 0,
                                    format_func=lambda k: tguide.TERM_POLICIES[k]["label"],
                                    key=f"eglo_pol_{t['id']}")
                                e_notes = st.text_input("Notes / known wrong variants",
                                                         value=t.get("notes") or "",
                                                         key=f"eglo_notes_{t['id']}")
                                e_enforce = st.checkbox("🔒 Enforce exactly",
                                                         value=bool(t.get("enforce_exact")),
                                                         key=f"eglo_enforce_{t['id']}")
                                esc1, esc2 = st.columns(2)
                                if esc1.button("💾 Save", key=f"eglo_save_{t['id']}"):
                                    db.update_glossary_term(
                                        t["id"], e_orig, e_trans, e_notes, e_cat, e_pol, e_enforce)
                                    st.session_state[_editing_key] = False
                                    st.success("Saved.")
                                    st.rerun()
                                if esc2.button("Cancel", key=f"eglo_cancel_{t['id']}"):
                                    st.session_state[_editing_key] = False
                                    st.rerun()
                else:
                    st.caption("No terms yet.")

                with st.form(f"add_glossary_{sid}", clear_on_submit=True):
                    gc1, gc2 = st.columns(2)
                    g_orig = gc1.text_input("Original term")
                    g_trans = gc2.text_input("Translation")
                    gc3, gc4 = st.columns(2)
                    g_cat = gc3.selectbox("Category", list(tguide.TERM_CATEGORIES.keys()),
                                           format_func=lambda k: tguide.TERM_CATEGORIES[k])
                    g_pol = gc4.selectbox("Handling policy", list(tguide.TERM_POLICIES.keys()),
                                           format_func=lambda k: f"{tguide.TERM_POLICIES[k]['label']} "
                                                                 f"({tguide.TERM_POLICIES[k]['example']})")
                    g_notes = st.text_input("Notes / known wrong variants (pipe-separated)",
                                             help="For enforced terms, list variants to auto-correct, "
                                                  "e.g. Shen Qing Yi|Chen Qingyi")
                    g_enforce = st.checkbox("🔒 Enforce exactly (hard find-replace after translation)")
                    if st.form_submit_button("Add term") and g_orig and g_trans:
                        db.upsert_glossary_term(sid, g_orig, g_trans, g_notes, g_cat, g_pol, g_enforce)
                        st.success("Added.")
                        st.rerun()

                st.markdown("**👥 People & pronouns**")
                st.caption("A recurring cast (a streamer's regulars, or a book series' main "
                          "characters) that persists across every drama in this series -- once "
                          "someone's added here, section 6 below lets you pick them by name for "
                          "any drama instead of retyping and re-spelling it each time. Their "
                          "pronouns here are the default for every drama; section 6 can override "
                          "them for one drama.")
                series_chars = db.list_series_characters(sid)
                if series_chars:
                    for sc in series_chars:
                        with st.container(border=True):
                            scc1, scc2 = st.columns([3, 1])
                            new_sc_name = scc1.text_input(
                                "name", value=sc["character_name"], label_visibility="collapsed",
                                key=f"scname_{sc['id']}")
                            if scc2.button("🗑️ Remove", key=f"scdel_{sc['id']}"):
                                db.delete_series_character(sc["id"])
                                st.rerun()
                            if new_sc_name and new_sc_name != sc["character_name"]:
                                # A rename, not a delete-and-recreate -- every drama's
                                # characters row already linked to this id (see section 6)
                                # picks up the corrected name automatically.
                                db.rename_series_character(sc["id"], new_sc_name)
                                st.rerun()
                            sc_notes = st.text_input(
                                "Nicknames, speaking style, relationships (optional)",
                                value=sc["notes"] or "", key=f"scnotes_{sc['id']}",
                                label_visibility="collapsed",
                                placeholder="Nicknames, speaking style, relationships (optional)")
                            _cur_pronouns = tguide.normalize_pronouns(sc.get("gender"))
                            sc_pronouns = _pronoun_picker(
                                "Pronouns", _cur_pronouns, key=f"scgender_{sc['id']}",
                                help="Fixes this character's pronouns in translation, overriding "
                                     "both Whisper's transcribed 他/她/它 (unreliable -- they're "
                                     "homophones in speech) and the \"default to she/her\" toggle "
                                     "above.")
                            if sc_notes != (sc["notes"] or "") or sc_pronouns != _cur_pronouns:
                                db.upsert_series_character(sid, sc["character_name"],
                                                            aliases=sc["aliases"] or "", notes=sc_notes,
                                                            gender=sc_pronouns)
                else:
                    st.caption("None yet -- add someone below, or link an existing per-drama "
                              "character to a new series character in section 6.")
                with st.form(f"add_series_char_{sid}", clear_on_submit=True):
                    new_char_name = st.text_input("Add a known character")
                    new_char_pronouns = _pronoun_picker(
                        "Pronouns", "", key=f"add_sc_pronouns_{sid}", in_form=True)
                    if st.form_submit_button("Add") and new_char_name:
                        db.upsert_series_character(sid, new_char_name, gender=new_char_pronouns)
                        st.success(f"Added '{new_char_name}'.")
                        st.rerun()


        default_engine_list = list(translate_engines.ENGINES.keys())
        saved_engine = drama.get("translation_engine") or st.session_state.get("settings_default_engine", "claude")
        engine_choice = st.selectbox(
            "Translation engine",
            default_engine_list,
            index=default_engine_list.index(saved_engine) if saved_engine in default_engine_list else 0,
            format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _gemini_free_tier)}",
        )
        engine_model = None
        if engine_choice == "claude":
            _model_keys = list(translate_engines.CLAUDE_MODELS.keys())
            _saved_model = st.session_state.get(f"settings_claude_model", _model_keys[0])
            engine_model = st.selectbox(
                "Claude model", _model_keys,
                index=_model_keys.index(_saved_model) if _saved_model in _model_keys else 0,
                format_func=lambda m: translate_engines.CLAUDE_MODELS[m],
                help="Anthropic updates this lineup periodically. If a model here starts "
                     "erroring, check console.anthropic.com for what's currently available.")
            st.session_state["settings_claude_model"] = engine_model
        elif engine_choice == "gemini":
            _model_keys = list(translate_engines.GEMINI_MODELS.keys())
            _saved_model = st.session_state.get(f"settings_gemini_model", _model_keys[0])
            engine_model = st.selectbox(
                "Gemini model", _model_keys,
                index=_model_keys.index(_saved_model) if _saved_model in _model_keys else 0,
                format_func=lambda m: translate_engines.GEMINI_MODELS[m],
                help="Google updates this lineup periodically. If a model here starts "
                     "erroring, check ai.google.dev/gemini-api/docs/models for what's "
                     "currently available.")
            st.session_state["settings_gemini_model"] = engine_model
            if _gemini_free_tier and engine_model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS:
                st.error("⚠️ Pro isn't available on the Gemini free tier anymore (removed "
                         "April 2026) -- pick Flash or Flash-Lite above, or turn off \"My "
                         "Gemini key is free-tier\" in Settings.")
        elif engine_choice == "ollama":
            _model_keys = list(translate_engines.OLLAMA_MODELS.keys())
            _saved_model = st.session_state.get("settings_ollama_model", _model_keys[0])
            engine_model = st.selectbox(
                "Ollama model", _model_keys,
                index=_model_keys.index(_saved_model) if _saved_model in _model_keys else 0,
                format_func=lambda m: translate_engines.OLLAMA_MODELS[m],
                help="Pull it first: `ollama pull <name>`. 8B is the practical default on an "
                     "8 GB GPU; 14B is a bit better but spills onto the CPU there and runs "
                     "much slower.")
            st.session_state["settings_ollama_model"] = engine_model
        elif engine_choice == "nllb":
            _model_keys = list(translate_engines.NLLB_MODELS.keys())
            engine_model = st.selectbox(
                "NLLB model size", _model_keys,
                format_func=lambda m: translate_engines.NLLB_MODELS[m],
                help="Downloads once, then runs fully offline -- no API key, no per-line cost. "
                     "600M is the practical default on CPU; 1.3B is a real quality step up if "
                     "you have the RAM/disk/patience for the heavier download and slower runs.")

        _gemini_free_tier_pro_blocked = (
            engine_choice == "gemini" and _gemini_free_tier
            and engine_model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS)

        _needs_key = engine_choice not in ("test_offline", "ollama", "libretranslate", "nllb")
        if engine_choice == "test_offline":
            st.success("Dry-run mode: no API key, no network, no cost. Produces obvious [TEST] "
                      "placeholder text so you can confirm the pipeline works end to end before "
                      "spending anything.")
            api_key = "offline"
        else:
            api_key = synced_api_key_input(
                f"{engine_choice} API key" + (" *(required)*" if _needs_key else " (optional)"),
                engine_choice, f"workspace_api_key_input_{engine_choice}",
                help="Claude keys come from console.anthropic.com and are billed separately "
                     "from any Claude.ai subscription.")
            if _needs_key and not api_key:
                st.caption("⚠️ Required — set it here or in the ⚙️ Settings sidebar. "
                          "To try the pipeline for free first, choose `test_offline` above.")
            elif not _needs_key:
                api_key = api_key or "local"

        # DeepL/Google/NLLB/LibreTranslate are pure machine translation --
        # no instruction-following ability at all, so picking one of them
        # for any feature below used to silently produce nothing (each
        # feature's own supports_reference guard declines quietly).
        # call_llm_json now raises instead of pretending to have worked;
        # this is the UI-level equivalent, disabling the button up front
        # with a clear reason rather than surfacing that error mid-job.
        _translation_only_engine = engine_choice in translate_engines.TRANSLATION_ONLY_ENGINES
        _translation_only_message = (
            f"{engine_choice} is translation-only and can't run this -- pick Claude, DeepSeek, "
            "Gemini, Ollama, or Test mode above.")

        style_note = st.text_input("Optional style notes",
                                    value=st.session_state.get("settings_default_style_note", ""))
        locale_options = ["en-US", "en-GB", "en-AU"]
        default_locale = st.session_state.get(
            f"locale_{picked_id}", st.session_state.get("settings_default_locale", "en-US"))
        locale = st.selectbox("English variant", locale_options,
                               index=locale_options.index(default_locale) if default_locale in locale_options else 0,
                               format_func=lambda l: {"en-US": "American English", "en-GB": "British English",
                                                       "en-AU": "Australian English"}[l])
        st.session_state[f"locale_{picked_id}"] = locale

        st.caption("💾 **Save current settings as a preset** — captures engine + model, "
                  "translation style, English variant, the pronoun default and "
                  "genre-guidance toggle above -- reusable on any other drama, not just "
                  "this series (📚 Library → 🎛️ Presets to manage saved ones).")
        _new_preset_name = st.text_input("Preset name", key=f"new_preset_name_{picked_id}")
        if st.button("Save as preset", key=f"save_preset_btn_{picked_id}",
                    disabled=not _new_preset_name.strip()):
            db.save_preset(
                _new_preset_name.strip(), translation_engine=engine_choice,
                engine_model=engine_model, style_preset=style_preset, locale=locale,
                default_female_pronouns=st.session_state.get(
                    f"default_female_pronouns_{picked_id}", False),
                include_genre_notes=include_genre_notes)
            st.success(f"Saved preset \"{_new_preset_name.strip()}\".")
        context_window = st.slider(
            "Context lines shown from before each batch", 0, 20, 6,
            help="Shows the model how the immediately preceding lines were already "
                 "translated, so a pronoun or someone referred to only by relation "
                 "('her', 'that guy') has something to resolve against instead of "
                 "being guessed fresh every batch. 0 turns this off.")

        b1, b2 = st.columns(2)
        if has_audio_pipeline:
            _has_audio = bool(audio_file or existing_audio)
            _tmode = st.session_state.get(f"tmode_{picked_id}")
            _whisper_mode = _tmode == "whisper"
            _hardsub_mode = _tmode == "hardsub_ocr"
            _has_transcript = bool(transcript_text.strip()) or _whisper_mode or _hardsub_mode
            can_prep = (_has_video_source if _hardsub_mode else _has_audio) and _has_transcript
            prep_label = ("▶ Read Captions from Video" if _hardsub_mode else
                          "▶ Transcribe with Whisper" if _whisper_mode else
                          "▶ Transcribe & Align")

            # A disabled button with no explanation is a dead end -- say what's missing.
            if not can_prep:
                _missing = []
                if _hardsub_mode and not _has_video_source:
                    _missing.append("a video file (audio-only won't have captions to read)")
                elif not _hardsub_mode and not _has_audio:
                    _missing.append("an audio or video file")
                if not _has_transcript:
                    _missing.append("a transcript (paste one, or switch to Whisper/caption-OCR above)")
                st.info("Still needed before this can run: " + " and ".join(_missing) + ".")
        else:
            can_prep = bool(novel_narration_text.strip())
            prep_label = "▶ Chunk & Tag Speakers"
        if has_audio_pipeline and can_prep:
            if not core_module.is_whisper_model_cached(whisper_size):
                st.caption(f"ℹ️ The '{whisper_size}' model isn't downloaded yet — first run will "
                          f"fetch it from Hugging Face (a few hundred MB to ~3GB). Needs a working "
                          f"internet connection; it's cached afterwards.")

        run_prep = b1.button(prep_label, type="primary", disabled=not can_prep)

        # Ollama is exempted from the API-key check above, so with nothing
        # in its place, clicking Translate against a stopped local server
        # used to start a background job that only failed once
        # translate_batch's own 300s request timeout expired. Checked here,
        # before the button, rather than left to fail inside the job.
        _ollama_unreachable = False
        if engine_choice == "ollama":
            _ollama_check_url = _ollama_base_url or "http://localhost:11434"
            if not translate_engines.check_ollama_reachable(_ollama_check_url):
                _ollama_unreachable = True
                st.warning(f"⚠️ Can't reach Ollama at `{_ollama_check_url}` — is it running?")

        # Step 9: spending caps apply to the engines that bill per token
        # and report usage; a free-tier Gemini key costs nothing.
        _cap_applies = (engine_choice in ("claude", "deepseek", "gemini")
                        and not (engine_choice == "gemini" and _gemini_free_tier))
        _monthly_cap = st.session_state.get("settings_monthly_cap_usd") or 0
        _month_spend = db.get_month_spend() if (_cap_applies and _monthly_cap) else 0.0
        _monthly_refusal = None
        if _cap_applies:
            _, _monthly_refusal = translate_engines.resolve_cost_cap(None, _monthly_cap, _month_spend)
            if _monthly_refusal:
                st.warning(_monthly_refusal)

        run_translate = b2.button("🌐 Translate all lines",
                                   disabled=st.session_state.lines is None or not api_key
                                            or _ollama_unreachable or bool(_monthly_refusal)
                                            or _gemini_free_tier_pro_blocked)
        force_retranslate = b2.checkbox(
            "Force re-translate everything (redoes lines that already have a "
            "translation too, not just what's missing)",
            value=False, key="force_retranslate",
            help="Unchecked (default): Translate only skips lines that don't have a "
                 "translation yet, leaving existing ones untouched -- the normal way to "
                 "pick up where you left off. Checked: every line gets re-translated from "
                 "scratch, overwriting anything already there -- use this after changing "
                 "the engine, style, or glossary and wanting the whole drama redone "
                 "consistently.")

        reflect_mode = False
        if not _translation_only_engine:
            reflect_mode = b2.checkbox(
                "✨ High quality (Reflect mode)", value=False, key=f"reflect_mode_{picked_id}",
                help="Three separate passes instead of one: a literal translation, then the "
                     "same engine critiques that specific translation (saved as a translation "
                     "note you can review), then a final rewrite using that critique. Costs "
                     "about 3x as much as a normal translation run.")

        bulk_mode = False
        if (engine_choice in bulk_translate.BULK_ENGINES
                and not (engine_choice == "gemini" and _gemini_free_tier)):
            bulk_mode = b2.checkbox(
                "🐢 Bulk (cheaper, slower)", value=False, key=f"bulk_mode_{picked_id}",
                help="Half price, for work nobody is waiting on. Claude and Gemini: every batch "
                     "is sent at once through their batch APIs -- most finish within an hour, "
                     "some take up to 24 hours. DeepSeek: waits for its next off-peak window "
                     "(half price) and runs then. Results are applied by line id, so you can keep "
                     "editing meanwhile -- a line whose source text changes before its result "
                     "arrives is flagged for review instead of overwritten.")
            if bulk_mode and reflect_mode and engine_choice == "deepseek":
                st.info("Bulk Reflect needs Claude or Gemini's own batch API -- DeepSeek only "
                        "has an off-peak discount, which Reflect's three dependent passes can't "
                        "use the same way a normal translation can.")
                bulk_mode = False
            elif bulk_mode and reflect_mode:
                st.caption("🐢✨ Bulk Reflect: the faithfulness pass goes out first; reflection "
                          "and expressiveness each submit automatically once the pass before them "
                          "comes back, so this takes roughly 3x as long as a normal bulk batch on "
                          "top of Bulk's own turnaround (still tracked as one entry under 🐢 Bulk "
                          "jobs below).")
            elif bulk_mode and engine_choice == "deepseek":
                _window = bulk_translate.next_deepseek_offpeak_start(bulk_translate._utcnow())
                st.caption("🐢 Runs in DeepSeek's off-peak window (half price) -- "
                           + ("starting right away." if bulk_translate.is_deepseek_offpeak(
                               bulk_translate._utcnow()) else f"from {_window:%H:%M} UTC today."))
            elif bulk_mode:
                st.caption("🐢 Bulk trade-off: every batch is sent at the same time, so the "
                           "look-back context can only use translations that already exist -- "
                           "not ones from earlier batches of this same run. Slightly less "
                           "consistent than a normal run; a consistency check afterwards helps.")

        _est_targets = []
        if st.session_state.lines and api_key and (reflect_mode or _cap_applies):
            _est_targets = (st.session_state.lines if force_retranslate
                            else [ln for ln in st.session_state.lines if not ln.en.strip()])
        _estimate = None
        if _est_targets:
            try:
                _est_engine = translate_engines.get_engine(
                    engine_choice, api_key, engine_model,
                    free_tier=engine_choice == "gemini" and _gemini_free_tier,
                    base_url=_ollama_base_url if engine_choice == "ollama" else None)
                _est_fn = (translate_engines.estimate_reflect_mode_cost if reflect_mode
                           else translate_engines.estimate_translation_cost)
                _estimate = _est_fn(_est_engine, [ln.zh for ln in _est_targets])
            except Exception:
                _estimate = None
        if _estimate is not None and reflect_mode:
            st.caption(f"💰 Estimated Reflect-mode cost for {len(_est_targets)} "
                       f"line(s): ~${_estimate:.2f} (roughly 3x a normal "
                       "translation run -- a rough estimate, not a precise bill).")
        elif _estimate is not None and _cap_applies and bulk_mode:
            _estimate *= bulk_translate.BATCH_PRICE_FACTOR
            st.caption(f"💰 Estimated bulk cost for {len(_est_targets)} line(s): ~${_estimate:.2f} "
                       "(half price -- a rough estimate, not a precise bill).")
        elif _estimate is not None and _cap_applies:
            st.caption(f"💰 Estimated cost for {len(_est_targets)} line(s): ~${_estimate:.2f} "
                       "(a rough estimate, not a precise bill).")

        _job_cap = 0.0
        if _cap_applies:
            _job_cap = b2.number_input(
                "Stop this job if it costs more than (USD, 0 = no cap)", min_value=0.0,
                step=0.5, value=0.0, key=f"cost_cap_{picked_id}",
                help="Spend is added up from each batch's real reported usage while the job "
                     "runs; once it reaches this, the job stops cleanly and keeps every "
                     "finished line. The batch that crosses the cap still completes, so it can "
                     "go over by at most one batch. Your monthly cap (Settings) applies too.")
            if _job_cap and _estimate is not None and _estimate > _job_cap:
                st.warning(f"The estimate (~${_estimate:.2f}) is above your ${_job_cap:.2f} cap -- "
                           "the job will stop once it reaches the cap, keeping what's finished.")

        _transcribe_job_id = f"transcribe_{picked_id}"
        _tjob = background_jobs.get_status(_transcribe_job_id)

        if run_prep and has_audio_pipeline:
            audio_path = existing_audio
            if audio_file is not None:
                ext = os.path.splitext(audio_file.name)[1]
                saved_path = os.path.join(ddir, f"source{ext}")
                with open(saved_path, "wb") as f:
                    f.write(audio_file.getbuffer())
                if ext.lower() in (".mp4", ".mkv", ".mov", ".webm"):
                    audio_path = os.path.join(ddir, "audio.wav")
                    with st.spinner("Extracting audio from video..."):
                        extract_audio_from_video(saved_path, audio_path)
                    db.update_drama(picked_id, audio_filename="audio.wav",
                                     source_video_filename=f"source{ext}")
                else:
                    audio_path = saved_path
                    db.update_drama(picked_id, audio_filename=f"source{ext}")

            novel_reference = existing_novel_text
            if novel_file is not None:
                novel_reference = novel_file.read().decode("utf-8", errors="ignore")
            elif novel_pasted.strip():
                novel_reference = novel_pasted
            if novel_reference:
                with open(os.path.join(ddir, "novel_reference.txt"), "w", encoding="utf-8") as f:
                    f.write(novel_reference)
                db.update_drama(picked_id, novel_reference_filename="novel_reference.txt")

            with open(os.path.join(ddir, "transcript.txt"), "w", encoding="utf-8") as f:
                f.write(transcript_text)

            if _hardsub_mode:
                video_filename = drama.get("source_video_filename")
                video_path = os.path.join(ddir, video_filename) if video_filename else None
                started = background_jobs.start_job(
                    _transcribe_job_id, run_hardsub_ocr_job,
                    _transcribe_job_id, video_path, source_language,
                    st.session_state.get(f"hardsub_interval_{picked_id}", 1.0),
                    st.session_state.get(f"hardsub_ocr_backend_{picked_id}", "tesseract"),
                    chinese_script, st.session_state.get("settings_tesseract_cmd") or None,
                    gpu_touching=True, description=f"Reading captions ({_drama_label(drama)})")
                if started:
                    st.info(_job_start_message(
                        _transcribe_job_id,
                        "Reading captions from the video in the background -- it keeps running "
                        "even if you switch tabs or close this browser tab. Come back here any "
                        "time to see progress; it'll pick up right where it is."))
                    st.rerun()
                else:
                    st.warning("A caption-reading job is already running for this drama.")
            else:
                _local_model = st.session_state.get("settings_whisper_model_path", "").strip() or None
                started = background_jobs.start_job(
                    _transcribe_job_id, run_transcribe_job,
                    _transcribe_job_id, audio_path, whisper_size, source_language,
                    st.session_state.get("use_gpu", False), _local_model,
                    st.session_state.get("settings_hf_token", "") or None,
                    initial_prompt, beam_size, min_silence_ms, vad_threshold, separate_vocals_first,
                    realign_long_segments, chinese_script,
                    st.session_state.get(f"whisper_fast_mode_{picked_id}", False),
                    st.session_state.get(f"separation_backend_{picked_id}", "auto"),
                    gpu_touching=True, description=f"Transcription ({_drama_label(drama)})")
                if started:
                    st.info(_job_start_message(
                        _transcribe_job_id,
                        "Transcription started in the background -- it keeps running even if you "
                        "switch tabs or close this browser tab. Come back here any time to see "
                        "progress; it'll pick up right where it is."))
                    st.rerun()
                else:
                    st.warning("A transcription is already running for this drama.")

        elif run_prep and content_mode == "novel_narration":
            with open(os.path.join(ddir, "novel_narration_source.txt"), "w", encoding="utf-8") as f:
                f.write(novel_narration_text)
            with st.spinner("Chunking novel text..."):
                chunks = chunk_novel_text(novel_narration_text)
                lines = [Line(idx=i, start=float(i), end=float(i) + 1.0, zh=c) for i, c in enumerate(chunks)]
            if api_key and not _translation_only_engine:
                with st.spinner("Tagging speakers with the translation LLM..."):
                    engine = translate_engines.get_engine(
                        engine_choice, api_key, engine_model,
                        free_tier=engine_choice == "gemini" and _gemini_free_tier,
                        base_url=_ollama_base_url if engine_choice == "ollama" else None)
                    known_chars = [c["character_name"] for c in db.list_characters(picked_id) if c["character_name"]]
                    speakers = translate_engines.tag_speakers_llm(
                        [ln.zh for ln in lines], engine, known_chars,
                        usage_cb=lambda inp, out: db.log_usage(
                            picked_id, engine_choice, getattr(engine, "model", engine_choice),
                            "tag_speakers", inp, out,
                            translate_engines.estimate_cost_for_engine(engine, inp, out)))
                    for ln, sp in zip(lines, speakers):
                        ln.speaker = sp
                    for label in sorted(set(speakers)):
                        db.upsert_character(picked_id, label, character_name=label)
            else:
                if _translation_only_engine:
                    st.warning(f"{_translation_only_message} All lines marked 'Narrator' for "
                               "now; switch engines above and re-run to tag them.")
                else:
                    st.warning("No API key yet -- skipped speaker tagging (needs an LLM engine). "
                               "All lines marked 'Narrator' for now; add a key and re-run to tag them.")
                for ln in lines:
                    ln.speaker = "Narrator"
                db.upsert_character(picked_id, "Narrator", character_name="Narrator")

            st.session_state.lines = lines
            background_jobs.cancel_line_jobs(picked_id)
            db.save_lines(picked_id, lines)
            db.update_drama(picked_id, status="aligned")
            st.success(f"Prepared {len(lines)} narration chunks.")

        # Not gated on run_prep -- this has to keep checking on every rerun
        # while the job above is still going, not just the one where the
        # button was clicked.
        if _tjob:
            if _tjob["status"] == "running":
                st.progress(_tjob["progress"], text=(_tjob.get("message") or "Transcribing...") + background_jobs.eta_text(_tjob))
                st.caption("Running in the background -- safe to switch tabs, use other dramas, "
                          "or close the browser tab. Come back and this will show current progress.")
                tc1, tc2 = st.columns(2)
                if tc1.button("🔄 Refresh progress", key=f"refresh_tc_{picked_id}"):
                    st.rerun()
                if tc2.button("✖ Cancel", key=f"cancel_tc_{picked_id}"):
                    background_jobs.request_cancel(_transcribe_job_id)
                    st.rerun()
                st.caption("Cancelling during vocal separation stops it right away; cancelling once "
                          "the actual speech recognition has started only takes effect once that "
                          "pass finishes (it has no mid-run checkpoint of its own yet) and just "
                          "skips the optional \"Split long merged lines\" step -- the transcript "
                          "itself is never discarded.")
            elif _tjob["status"] == "done":
                _tresult = _tjob.get("result") or {}
                if _tresult.get("failed_reason") == "cancelled":
                    st.warning("Transcription was cancelled before it produced a transcript. "
                              "Nothing was lost -- your audio and settings are saved. Press the "
                              "button again to start over.")
                elif _tresult.get("failed_reason") == "model_download":
                    st.error("Speech recognition model couldn't be downloaded.")
                    st.code(_tresult.get("detail", ""), language="text")
                    st.caption("Nothing was lost -- your audio, transcript and settings are saved. "
                              "Fix the connection and press the button again.")
                elif _tresult.get("failed_reason") == "empty":
                    st.error("Speech recognition returned nothing. Check the file actually contains "
                             "audio, and that ffmpeg is installed (see the Diagnostics tab).")
                elif _tresult.get("failed_reason") == "vocal_separation":
                    st.error("Removing background music failed before transcription could even start.")
                    st.code(_tresult.get("detail", ""), language="text")
                    st.caption("Nothing was lost -- your audio, transcript and settings are saved. "
                              "Turn off \"Remove background music\" above to transcribe the original "
                              "audio instead, or fix the reported issue (often a missing "
                              "`pip install audio-separator` or `pip install demucs`) and press the button again.")
                else:
                    segments = _tresult["segments"]
                    audio_path = existing_audio
                    if _tresult.get("gpu_fallback"):
                        st.warning(
                            "GPU was requested but failed at the actual transcription step, so this "
                            "ran on CPU instead (slower, but it completed). This is a CUDA/driver "
                            "problem on this machine, not something wrong with your audio.\n\n"
                            f"Error: {_tresult['gpu_fallback']}\n\n"
                            "Common cause: PyTorch/ctranslate2 installed without CUDA support, or a "
                            "CUDA toolkit version that doesn't match your driver. Turn GPU off in "
                            "Settings → Performance if you'd rather not see this each time, or "
                            "reinstall the CUDA-enabled build matching your driver version.")
                    if _tresult.get("word_align_error"):
                        st.warning(
                            "Transcription completed normally, but \"Split long merged lines\" "
                            "couldn't run, so long merged lines weren't split this time.\n\n"
                            f"Error: {_tresult['word_align_error']}\n\n"
                            "Often a missing `pip install torchaudio uroman`. Nothing was lost -- "
                            "your transcript below is exactly as complete as it would be with this "
                            "experimental setting off.")

                    _result_tmode = st.session_state.get(f"tmode_{picked_id}")
                    # What actually produced the text, for raw_transcript.json.
                    _raw_backend, _raw_model = "whisper", whisper_size
                    if _result_tmode == "hardsub_ocr":
                        _raw_backend = "hardsub_ocr"
                        _raw_model = st.session_state.get(f"hardsub_ocr_backend_{picked_id}", "tesseract")
                        # OCR already produced real per-cue timing straight from
                        # the video -- no separate alignment step needed, same
                        # reasoning as the Whisper-text-override branch below,
                        # just sourced from captions instead of speech.
                        lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"])
                                 for i, seg in enumerate(segments) if seg["text"].strip()]
                        transcript_text = "\n".join(ln.zh for ln in lines)
                        st.warning("This transcript came from OCR on the video's burned-in "
                                  "captions, so expect occasional misreads (especially on "
                                  "stylized fonts or busy backgrounds) -- correct them in the "
                                  "review table below **before** translating.")
                    elif _result_tmode == "whisper":
                        # No supplied transcript: use a transcription model's own text.
                        # Segment TIMING always comes from Whisper's VAD (segments, above)
                        # -- asr_backend_choice only affects which model's TEXT fills
                        # those segments. See asr_backend.py's module docstring for why
                        # that split is deliberate.
                        if asr_backend_choice == "qwen3_asr":
                            try:
                                import asr_backend
                                segments = asr_backend.Qwen3ASRBackend().transcribe(
                                    audio_path, source_language, whisper_segments=segments,
                                    use_gpu=st.session_state.get("use_gpu", False))
                                _raw_backend, _raw_model = "qwen3_asr", "Qwen3-ASR"
                            except ImportError:
                                st.warning("Qwen3-ASR needs `pip install qwen-asr torch` -- using Whisper's "
                                          "own transcription for this run.")
                            except core_module.ModelDownloadError as exc:
                                st.warning(f"Qwen3-ASR couldn't be downloaded ({exc}) -- using Whisper's "
                                          "own transcription for this run.")
                        lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"])
                                 for i, seg in enumerate(segments) if seg["text"].strip()]
                        transcript_text = "\n".join(ln.zh for ln in lines)
                        st.warning("This transcript came from speech recognition, so expect errors on "
                                  "names and uncommon terms. Correct them in the review table below "
                                  "**before** translating -- mistakes here carry into the translation.")
                    else:
                        with st.spinner("Aligning transcript to timing..."):
                            user_lines = split_user_transcript(transcript_text)
                            if alignment_method == "qwen3_forced_align":
                                try:
                                    import forced_align
                                    lines = forced_align.align_with_qwen3(
                                        audio_path, user_lines, segments, language=source_language,
                                        use_gpu=st.session_state.get("use_gpu", False))
                                except ImportError:
                                    st.warning("Qwen3 forced alignment needs `pip install qwen-asr torch` -- "
                                              "using the default character-alignment method for this run.")
                                    lines = align_transcript_to_timing(user_lines, segments)
                                except core_module.ModelDownloadError as exc:
                                    st.warning(f"Qwen3-ForcedAligner couldn't be downloaded ({exc}) -- "
                                              "using the default character-alignment method for this run.")
                                    lines = align_transcript_to_timing(user_lines, segments)
                                except ValueError as exc:
                                    st.warning(f"Qwen3 forced alignment couldn't run ({exc}) -- using the "
                                              "default character-alignment method for this run.")
                                    lines = align_transcript_to_timing(user_lines, segments)
                            else:
                                lines = align_transcript_to_timing(user_lines, segments)

                    core_module.release_gpu_models()  # text/alignment stage done
                    _start_diarization_after_align = run_diarize and hf_token

                    st.session_state.lines = lines
                    # A brand-new set of lines: a translate/flag job still
                    # running on the old ones would only be spending money
                    # on lines that no longer exist.
                    background_jobs.cancel_line_jobs(picked_id)
                    db.save_lines(picked_id, lines)
                    # After the save, so each line's permanent id is recorded.
                    # Written once and never touched again -- a later run gets
                    # its own timestamped file.
                    raw_transcript.write_raw_transcript(
                        ddir, segments, lines, backend=_raw_backend, model=_raw_model,
                        language=source_language,
                        mode=_result_tmode if _result_tmode in ("hardsub_ocr", "whisper")
                        else "aligned_transcript")
                    db.update_drama(picked_id, status="aligned")
                    st.success(f"Aligned {len(lines)} lines.")
                    if _start_diarization_after_align:
                        import diarize
                        background_jobs.start_process_job(
                            f"diarize_{picked_id}", diarize.diarize_subprocess_worker,
                            args=(audio_path, hf_token, expected_speakers or None),
                            gpu_touching=True, description=f"Diarization ({_drama_label(drama)})")
                        st.info("Speaker detection started in the background -- see "
                               "'4. 🎙️ Speaker diarization' above for progress, or to cancel it.")
                background_jobs.clear_job(_transcribe_job_id)
            elif _tjob["status"] == "error":
                # This job slot is shared between the Whisper transcription
                # path and the hardsub-OCR path (both use _transcribe_job_id,
                # since only one can run at a time for a given drama) -- label
                # the failure by which one actually ran, not always "Transcription".
                _failed_step = "Reading captions from video" if _hardsub_mode else "Transcription"
                st.error(f"{_failed_step} failed: {_tjob['error']}")
                with st.expander("Details"):
                    st.code(_tjob.get("traceback", ""), language="text")
                background_jobs.clear_job(_transcribe_job_id)

        if st.session_state.lines is None:
            saved = db.load_lines(picked_id)
            if saved:
                st.session_state.lines = core_module.lines_from_rows(saved)

        _translate_job_id = f"translate_{picked_id}"
        _job = background_jobs.get_status(_translate_job_id)

        if run_translate and st.session_state.lines:
            novel_reference = existing_novel_text
            if has_audio_pipeline:
                if novel_file is not None:
                    novel_reference = novel_file.read().decode("utf-8", errors="ignore")
                elif novel_pasted.strip():
                    novel_reference = novel_pasted
            engine = translate_engines.get_engine(
                engine_choice, api_key, engine_model,
                free_tier=engine_choice == "gemini" and _gemini_free_tier,
                base_url=_ollama_base_url if engine_choice == "ollama" else None)
            if force_retranslate and any(ln.en for ln in st.session_state.lines):
                db.save_line_history_snapshot(picked_id, st.session_state.lines,
                                               "before force re-translate")
            glossary_terms = db.list_glossary_terms(drama["series_id"]) if drama.get("series_id") else None
            _scope = f"series:{drama['series_id']}" if drama.get("series_id") else "global"
            _prof = db.get_style_profile(_scope)
            _learned = ""
            if _prof and st.session_state.get("apply_style_profile", True):
                _learned = adaptive_style.profile_to_prompt_block(_prof.get("profile", {}))
            _emap = db.load_emotions(picked_id)
            _emotion_block = emotion.build_emotion_guidance(
                _emap, [ln.idx for ln in st.session_state.lines]) if _emap else ""
            _gender_block = tguide.build_character_gender_hints(
                db.list_series_characters(drama["series_id"]) if drama.get("series_id") else [],
                db.list_characters_with_series_names(picked_id))
            style_guidelines = tguide.build_style_guidelines(
                style_preset, glossary_terms=glossary_terms,
                include_genre_notes=include_genre_notes,
                default_female_pronouns=st.session_state.get(f"default_female_pronouns_{picked_id}", False),
                custom_notes=(custom_guide_notes
                               + ("\n\n" + _learned if _learned else "")
                               + ("\n\n" + _emotion_block if _emotion_block else "")
                               + ("\n\n" + _gender_block if _gender_block else "")))

            if bulk_mode and reflect_mode:
                _kind, _msg = _start_bulk_reflect(
                    picked_id, drama, engine, engine_choice, novel_reference, glossary_terms,
                    style_guidelines, style_note, locale, style_preset, force_retranslate)
                getattr(st, _kind)(_msg)
            elif bulk_mode:
                _kind, _msg = _start_bulk_translation(
                    picked_id, drama, engine, engine_choice, novel_reference, glossary_terms,
                    style_guidelines, style_note, locale, style_preset, context_window,
                    force_retranslate, _job_cap, _monthly_cap, _estimate)
                getattr(st, _kind)(_msg)
            else:
                # A copy, not the live list -- the background thread mutates its own
                # lines and saves through the database; the main script reloads from
                # there once the job is visible again, rather than two threads
                # touching the same objects st.session_state also holds.
                _lines_copy = _copy_lines(st.session_state.lines)

                _cost_cap = None
                if _cap_applies:
                    _cost_cap, _ = translate_engines.resolve_cost_cap(
                        _job_cap, _monthly_cap, db.get_month_spend() if _monthly_cap else 0.0)

                started = background_jobs.start_job(
                    _translate_job_id, run_translate_job,
                    _translate_job_id, picked_id, _lines_copy, engine, drama, style_note,
                    novel_reference, force_retranslate, locale, glossary_terms, style_guidelines,
                    engine_choice, style_preset, context_window,
                    st.session_state.get("settings_ollama_num_ctx_override") or None,
                    reflect=reflect_mode,
                    cost_cap_usd=_cost_cap,
                    gpu_touching=engine_choice == "ollama",
                    description=(f"Ollama Reflect-mode translation ({_drama_label(drama)})"
                                if reflect_mode and engine_choice == "ollama" else
                                f"Ollama translation ({_drama_label(drama)})"))
                if started:
                    st.info(_job_start_message(
                        _translate_job_id,
                        "Translation started in the background -- it keeps running even if you "
                        "switch tabs or close this one. Come back here any time to see progress; "
                        "it'll pick up right where it is."))
                    st.rerun()
                else:
                    st.warning("A translation is already running for this drama.")

        if _job:
            if _job["status"] == "running":
                st.progress(_job["progress"], text=(_job.get("message") or "Translating...") + background_jobs.eta_text(_job))
                st.caption("Running in the background -- safe to switch tabs, use other dramas, "
                          "or close the browser tab. Come back and this will show current progress.")
                if st.button("🔄 Refresh progress", key=f"refresh_tr_{picked_id}"):
                    st.rerun()
            elif _job["status"] == "done":
                st.session_state.lines = db.load_line_objects(picked_id)
                _errors = (_job.get("result") or {}).get("errors", [])
                _cap_spent = (_job.get("result") or {}).get("cap_reached")
                if _cap_spent is not None:
                    _left = sum(1 for ln in st.session_state.lines if not ln.en.strip())
                    st.warning(f"Stopped at your spending cap after about ${_cap_spent:.2f} -- "
                               f"every finished line was kept. {_left} line(s) are still "
                               "untranslated; raise the cap and click Translate to continue.")
                elif _errors:
                    failed_line_nums = [i + 1 for e in _errors for i in e["lines"]]
                    st.warning(f"Translated with {len(_errors)} batch failure(s) -- lines "
                              f"{failed_line_nums} are still untranslated, but everything else was "
                              f"saved. Click Translate again to retry just the missing lines.")
                else:
                    st.success("Translation complete.")
                background_jobs.clear_job(_translate_job_id)
            elif _job["status"] == "error":
                st.error(f"Translation failed: {_job['error']}")
                with st.expander("Details"):
                    st.code(_job.get("traceback", ""), language="text")
                background_jobs.clear_job(_translate_job_id)

        _render_bulk_jobs_panel(picked_id, _monthly_cap)

    # ---------------------------------------------------- Character naming
    characters = db.list_characters_with_series_names(picked_id)
    if characters:
        st.divider()
        with st.expander("6. 🎭 Name your characters & set up voice cloning", expanded=False):
            st.caption("Map speaker labels to character names, and optionally attach a reference "
                       "voice clip per character for cloning (instead of the free TTS pool).")
            speaker_segments = st.session_state.get(f"speaker_segments_{picked_id}")
            if speaker_segments is None:
                import diarize as _diarize_turns
                speaker_segments = _diarize_turns.load_turns(ddir)
            can_auto_extract = has_audio_pipeline and speaker_segments is not None

            if can_auto_extract and st.button("🎯 Auto-extract reference clips from this audio"):
                audio_path = os.path.join(ddir, drama["audio_filename"]) if drama["audio_filename"] else None
                if audio_path and os.path.exists(audio_path):
                    clips, skipped = dub_module.extract_reference_clips(
                        audio_path, st.session_state.lines, speaker_segments, ddir)
                    _ref_text_match_failed = set()
                    for label, info in clips.items():
                        matching_zh = next((ln.zh for ln in st.session_state.lines
                                             if ln.speaker == label and info["start"] <= ln.start <= info["end"] + 1), "")
                        db.upsert_character(picked_id, label,
                                             ref_audio_filename=os.path.relpath(info["path"], ddir))
                        if matching_zh:
                            db.upsert_character(picked_id, label, ref_text=matching_zh)
                            # The ref_text text_input below is bound to this same
                            # key -- without updating it too, its stale
                            # (pre-auto-extract) widget value would win over the
                            # `value=` we just changed on the very next rerun,
                            # and the ref_text_input != c["ref_text"] check at
                            # the bottom of the character loop would read that
                            # as a user edit and immediately overwrite the
                            # ref_text we just saved back to "".
                            st.session_state[f"reftext_{label}"] = matching_zh
                        else:
                            # Don't silently save "" -- indistinguishable from the
                            # field never having been touched. Leave whatever
                            # ref_text was already there and explain the gap
                            # instead (Step 8b item 3).
                            _ref_text_match_failed.add(label)
                    st.session_state[f"clip_skip_reasons_{picked_id}"] = skipped
                    st.session_state[f"ref_text_match_failed_{picked_id}"] = _ref_text_match_failed
                    st.success(f"Extracted {len(clips)} reference clip(s).")
                    st.rerun()

            _series_chars = db.list_series_characters(drama["series_id"]) if drama.get("series_id") else []

            # Step 8: recurring-voice suggestions -- experimental (Phase 1
            # §3.4: nobody has shown this works reliably across different
            # recordings). Purely a suggestion the user confirms or
            # dismisses; nothing here ever names a speaker on its own.
            if _series_chars:
                import diarize as _diarize_embeddings
                import voice_id as _voice_id
                _voice_embeddings = _diarize_embeddings.load_embeddings(ddir)
                if _voice_embeddings:
                    _already_named = {c["speaker_label"] for c in characters if c["character_name"]}
                    _dismissed_suggestions = db.list_dismissed_voice_suggestions(picked_id)
                    _voice_suggestions = _voice_id.suggest_speaker_matches(
                        _voice_embeddings, _series_chars, already_named=_already_named,
                        dismissed=_dismissed_suggestions)
                    for sug in _voice_suggestions:
                        with st.container(border=True):
                            vc1, vc2, vc3 = st.columns([4, 1, 1])
                            vc1.caption(f"🔊 **{sug['speaker_label']}** sounds like "
                                       f"**{sug['character_name']}** (similarity "
                                       f"{sug['similarity']:.2f}) -- experimental, please confirm.")
                            if vc2.button("✅ Accept", key=f"voiceaccept_{sug['speaker_label']}_"
                                                          f"{sug['series_character_id']}"):
                                db.upsert_character(
                                    picked_id, sug["speaker_label"],
                                    character_name=sug["character_name"],
                                    series_character_id=sug["series_character_id"])
                                db.update_series_character_voice_fingerprint(
                                    sug["series_character_id"],
                                    _voice_embeddings[sug["speaker_label"]])
                                st.success(f"{sug['speaker_label']} set to {sug['character_name']}.")
                                st.rerun()
                            if vc3.button("❌ Reject", key=f"voicereject_{sug['speaker_label']}_"
                                                          f"{sug['series_character_id']}"):
                                db.dismiss_voice_suggestion(
                                    picked_id, sug["speaker_label"], sug["series_character_id"])
                                st.rerun()

            for c in characters:
                with st.container(border=True):
                    if _series_chars:
                        # Hands-off path: pick a person who's already known in this
                        # series (a streamer's regulars, a book series' cast) instead
                        # of retyping and re-spelling their name for every new drama.
                        _known_options = ["-- type a new name below --"] + [sc["character_name"] for sc in _series_chars]
                        _current = next((sc["character_name"] for sc in _series_chars
                                          if sc["id"] == c.get("series_character_id")), _known_options[0])
                        picked_known = st.selectbox(
                            f"Known characters in this series ({c['speaker_label']})", _known_options,
                            index=_known_options.index(_current) if _current in _known_options else 0,
                            key=f"cknown_{c['speaker_label']}")
                        if picked_known != "-- type a new name below --":
                            _sc = next(sc for sc in _series_chars if sc["character_name"] == picked_known)
                            if c.get("series_character_id") != _sc["id"]:
                                db.upsert_character(picked_id, c["speaker_label"],
                                                     character_name=_sc["character_name"],
                                                     series_character_id=_sc["id"])
                                st.rerun()

                    # Step 8b item 1: a real sample of what this speaker actually
                    # said, pulled straight from the transcript -- without this
                    # there's no way to tell who SPEAKER_00 vs SPEAKER_01 is
                    # without leaving this section to cross-reference Review & edit.
                    _speaker_lines = [ln.zh for ln in st.session_state.lines
                                     if ln.speaker == c["speaker_label"] and ln.zh.strip()]
                    if _speaker_lines:
                        _samples = [_speaker_lines[0]]
                        if len(_speaker_lines) > 1:
                            _samples.append(_speaker_lines[len(_speaker_lines) // 2])
                        st.caption("💬 " + "  /  ".join(_samples))
                    else:
                        st.caption("No lines attributed to this speaker yet.")

                    cc1, cc2, cc3, cc4 = st.columns([1, 2, 2, 2])
                    cc1.write(c["speaker_label"])
                    name = cc2.text_input("name", value=c["character_name"] or "",
                                           label_visibility="collapsed", key=f"cname_{c['speaker_label']}")
                    va = cc3.text_input("voice actor", value=c["voice_actor"] or "",
                                         placeholder="voice actor", label_visibility="collapsed",
                                         key=f"cva_{c['speaker_label']}")
                    voice = cc4.selectbox("tts voice (fallback)", dub_module.DEFAULT_VOICE_POOL,
                                           index=dub_module.DEFAULT_VOICE_POOL.index(c["tts_voice"])
                                           if c["tts_voice"] in dub_module.DEFAULT_VOICE_POOL else 0,
                                           label_visibility="collapsed", key=f"cvoice_{c['speaker_label']}")
                    if name != (c["character_name"] or "") or va != (c["voice_actor"] or "") or voice != c["tts_voice"]:
                        db.upsert_character(picked_id, c["speaker_label"], character_name=name,
                                             voice_actor=va, tts_voice=voice)
                    _series_default = tguide.normalize_pronouns(c.get("series_pronouns"))
                    _shown_pronouns = tguide.normalize_pronouns(c.get("pronouns")) or _series_default
                    c_pronouns = _pronoun_picker(
                        f"Pronouns ({name or c['speaker_label']})", _shown_pronouns,
                        key=f"cpronouns_{c['speaker_label']}",
                        help=("Defaults to this person's pronouns under People & pronouns "
                              f"({_series_default}); setting it here overrides that for this "
                              "drama only." if _series_default else
                              "Fixes this character's pronouns in translation for this drama."))
                    if c_pronouns != _shown_pronouns:
                        db.upsert_character(picked_id, c["speaker_label"], pronouns=c_pronouns)
                    if (drama.get("series_id") and name.strip()
                            and name.strip() not in [sc["character_name"] for sc in _series_chars]):
                        # Deliberately opt-in, not automatic on every keystroke --
                        # auto-saving every typed name (including mid-typo) would
                        # clutter the series' cast list with one-off junk. This is
                        # the one moment a person decides "yes, remember them".
                        if st.checkbox(f"💾 Remember '{name.strip()}' as a known character in this series",
                                       key=f"cremember_{c['speaker_label']}"):
                            db.upsert_series_character(drama["series_id"], name.strip())
                            _sc = next(sc for sc in db.list_series_characters(drama["series_id"])
                                       if sc["character_name"] == name.strip())
                            db.upsert_character(picked_id, c["speaker_label"], series_character_id=_sc["id"])
                            st.success(f"'{name.strip()}' will be pickable for every future drama in this series.")
                            st.rerun()

                    rc1, rc2 = st.columns([1, 2])
                    if c["ref_audio_filename"]:
                        rc1.caption(f"✅ Clone ref: {c['ref_audio_filename']}")
                    else:
                        rc1.caption("No clone reference set")
                        # Step 8b item 2: a missing clone ref isn't a bug, but
                        # silence about WHY is -- name the specific reason
                        # auto-extract found no eligible segment for this
                        # speaker, instead of leaving this indistinguishable
                        # from "auto-extract was never run."
                        _skip_reason = st.session_state.get(
                            f"clip_skip_reasons_{picked_id}", {}).get(c["speaker_label"])
                        if _skip_reason:
                            _bound = ("shorter than the 3s minimum" if _skip_reason["reason"] == "too_short"
                                     else "longer than the 12s maximum")
                            rc1.caption(f"Closest available clip was {_skip_reason['closest_duration']:.1f}s "
                                       f"-- {_bound} for a clean reference.")
                    ref_upload = rc2.file_uploader(f"Upload clone reference for {name or c['speaker_label']}",
                                                    type=["wav", "mp3", "m4a"], key=f"refup_{c['speaker_label']}",
                                                    label_visibility="collapsed")
                    ref_text_input = st.text_input(
                        f"What's said in that clip (original language, for {name or c['speaker_label']})",
                        value=c["ref_text"] or "", key=f"reftext_{c['speaker_label']}")
                    if (not ref_text_input.strip() and c["speaker_label"] in
                            st.session_state.get(f"ref_text_match_failed_{picked_id}", set())):
                        # Step 8b item 3: same "silent empty result" shape as
                        # item 2, in extract_reference_clips's own caller this
                        # time -- a reference clip WAS found, but no transcript
                        # line's speaker tag matched its time window.
                        st.caption("A reference clip was found, but no transcript line's speaker "
                                  "tag matched it -- try re-running speaker detection or "
                                  "auto-extract again, or type the words said in the clip above.")
                    if ref_upload is not None:
                        ref_filename = f"clone_ref_{c['speaker_label']}{os.path.splitext(ref_upload.name)[1]}"
                        with open(os.path.join(ddir, ref_filename), "wb") as f:
                            f.write(ref_upload.getbuffer())
                        db.upsert_character(picked_id, c["speaker_label"], ref_audio_filename=ref_filename)
                    if ref_text_input != (c["ref_text"] or ""):
                        db.upsert_character(picked_id, c["speaker_label"], ref_text=ref_text_input)

            with st.expander("☁️ Or use ElevenLabs cloning instead (hosted, no GPU needed)", expanded=False):
                st.caption("Paid API with a limited free tier. Simpler to get working than F5-TTS since "
                          "there's no local model to install -- worth trying first if F5-TTS gives you trouble.")
                el_key = synced_api_key_input("ElevenLabs API key", "elevenlabs", f"el_key_{picked_id}")
                for c in characters:
                    if not c["ref_audio_filename"]:
                        continue
                    ec1, ec2 = st.columns([2, 1])
                    label = c['character_name'] or c['speaker_label']
                    if c.get("elevenlabs_voice_id"):
                        ec1.caption(f"{label} — ✅ cloned (voice ID: {c['elevenlabs_voice_id']})")
                    else:
                        ec1.caption(label)
                    if ec2.button(f"Clone via ElevenLabs", key=f"elclone_{c['speaker_label']}", disabled=not el_key):
                        voice_id = dub_module.clone_voice_elevenlabs(
                            el_key, c["character_name"] or c["speaker_label"],
                            os.path.join(ddir, c["ref_audio_filename"]))
                        db.upsert_character(picked_id, c["speaker_label"], elevenlabs_voice_id=voice_id)
                        st.success(f"Cloned and saved. Voice ID: {voice_id}")
                        st.rerun()

    # ---------------------------------------------------- Review & edit
    if st.session_state.lines:
        st.divider()
        with st.expander("7. 📝 Review & edit", expanded=False):

            # A background job further down (Review queue) can finish and save
            # its flags to the database mid-render -- if that just happened,
            # pick it up here BEFORE computing the flagged count below. Without
            # this, "Show flagged lines only" showed a stale (often zero,
            # permanently disabled) count for the one render where completion
            # was first detected, since Streamlit doesn't re-render a widget
            # emitted earlier in the same script run after later code changes
            # session_state -- confirmed directly: the checkbox stayed
            # disabled at "(0)" after a real "281 flagged" run until some
            # unrelated click forced a second rerun.
            _early_flag_job = background_jobs.get_status(f"flag_{picked_id}")
            if _early_flag_job and _early_flag_job["status"] == "done":
                st.session_state.lines = db.load_line_objects(picked_id)

            all_lines = st.session_state.lines


            _n_flagged_total = sum(1 for ln in all_lines if ln.flag)
            _n_untranslated_total = sum(1 for ln in all_lines if ln.zh.strip() and not ln.en.strip())
            fc1, fc2 = st.columns(2)
            show_flagged_only = fc1.checkbox(
                f"Show flagged lines only ({_n_flagged_total})",
                value=False, disabled=not _n_flagged_total, key=f"flagged_only_{picked_id}")
            show_untranslated_only = fc2.checkbox(
                f"Show untranslated lines only ({_n_untranslated_total})",
                value=False, disabled=not _n_untranslated_total,
                key=f"untranslated_only_{picked_id}",
                help="For finding the handful of blank lines Export subtitles warns about "
                     "in a long drama, without scrolling through every page.")
            if show_flagged_only:
                visible_lines = [ln for ln in all_lines if ln.flag]
            elif show_untranslated_only:
                visible_lines = [ln for ln in all_lines if ln.zh.strip() and not ln.en.strip()]
            else:
                visible_lines = all_lines

            review_page_size = st.number_input("Lines per page", value=40, min_value=10, max_value=200,
                                                step=10, key="review_page_size")
            n_review_pages = max(1, (len(visible_lines) + review_page_size - 1) // review_page_size)
            review_page = st.number_input(f"Page (1-{n_review_pages})", value=1, min_value=1,
                                           max_value=n_review_pages, step=1, key="review_page")
            page_start = (review_page - 1) * review_page_size
            page_slice = visible_lines[page_start: page_start + review_page_size]

            edited_page_rows = []
            _raw = raw_transcript.load_latest(ddir)
            for ln in page_slice:
                if ln.flag:
                    fc1, fc2 = st.columns([5, 1])
                    fc1.warning(f"⚠️ **{translate_engines.flag_reason_label(ln.flag)}**"
                               + (f" — {ln.flag_note}" if ln.flag_note else ""))
                    if fc2.button("✅ Dismiss", key=f"dismiss_flag_{ln.idx}"):
                        ln.flag, ln.flag_note = None, ""
                        db.save_lines(picked_id, all_lines)
                        st.rerun()
                cols = st.columns([1, 1, 1, 3, 3, 0.5, 0.5])
                start = cols[0].number_input("start", value=round(ln.start, 2), step=0.1,
                                              label_visibility="collapsed", key=f"start_{ln.idx}")
                end = cols[1].number_input("end", value=round(ln.end, 2), step=0.1,
                                            label_visibility="collapsed", key=f"end_{ln.idx}")
                speaker = cols[2].text_input(
                    "speaker", value=ln.speaker or "", label_visibility="collapsed",
                    key=f"speaker_{ln.idx}", placeholder="—",
                    help="Who says this line. Changing it by hand marks it as a correction that "
                         "re-running speaker detection won't overwrite without asking.").strip()
                _speaker_changed = speaker != (ln.speaker or "")
                zh = cols[3].text_area("zh", value=ln.zh, height=68, label_visibility="collapsed", key=f"zh_{ln.idx}")
                en = cols[4].text_area("en", value=ln.en, height=68, label_visibility="collapsed", key=f"en_{ln.idx}")
                cols[5].write(f"#{ln.idx + 1}")
                with cols[6].popover("🔧"):
                    st.caption("Fix just this line -- cheaper and faster than redoing the "
                              "whole drama for one mistake.")
                    if api_key and st.button("✏️ Improve translation", key=f"rvimprove_{ln.idx}"):
                        eng_imp = translate_engines.get_engine(
                            engine_choice, api_key, engine_model,
                            free_tier=engine_choice == "gemini" and _gemini_free_tier,
                            base_url=_ollama_base_url if engine_choice == "ollama" else None)
                        with st.spinner("Rewriting..."):
                            improved = line_tools.improve_line(zh, en, eng_imp,
                                                                source_language=source_language)
                        st.session_state[f"rv_improved_{ln.idx}"] = improved
                    _improved = st.session_state.get(f"rv_improved_{ln.idx}")
                    if _improved:
                        st.success(_improved)
                        if st.button("Use this", key=f"rvuseimproved_{ln.idx}"):
                            db.record_edit_sample(picked_id, zh, en, _improved)
                            for _r in all_lines:
                                if _r.idx == ln.idx:
                                    _r.en = _improved
                            db.save_lines(picked_id, all_lines)
                            st.session_state[f"rv_improved_{ln.idx}"] = None
                            # Same reason as re-transcribe's zh_<idx> pop below: the
                            # en box would otherwise read its old text back over the
                            # just-accepted translation on the next rerun.
                            st.session_state.pop(f"en_{ln.idx}", None)
                            st.rerun()
                    if has_audio_pipeline and drama.get("audio_filename"):
                        if st.button("🎙️ Re-transcribe", key=f"rvretrans_{ln.idx}"):
                            _audio_path = os.path.join(ddir, drama["audio_filename"])
                            _slice_path = os.path.join(ddir, "_retranscribe_slice.wav")
                            with st.spinner("Re-transcribing..."):
                                core_module.extract_audio_slice(_audio_path, ln.start, ln.end, _slice_path)
                                try:
                                    _segs = core_module.transcribe_for_timing(
                                        _slice_path, model_size=drama.get("whisper_size") or core_module.DEFAULT_WHISPER_SIZE,
                                        language=source_language,
                                        use_gpu=st.session_state.get("use_gpu", False))
                                    st.session_state[f"rv_retrans_{ln.idx}"] = " ".join(
                                        s["text"] for s in _segs).strip()
                                finally:
                                    if os.path.exists(_slice_path):
                                        os.remove(_slice_path)
                        _retrans = st.session_state.get(f"rv_retrans_{ln.idx}")
                        if _retrans is not None:
                            if _retrans:
                                st.success(_retrans)
                                if st.button("Use this", key=f"rvuseretrans_{ln.idx}"):
                                    for _r in all_lines:
                                        if _r.idx == ln.idx:
                                            _r.zh = _retrans
                                    db.save_lines(picked_id, all_lines)
                                    st.session_state[f"rv_retrans_{ln.idx}"] = None
                                    # The zh box below is a widget keyed on zh_<idx>, which
                                    # (like every keyed widget) keeps showing whatever it
                                    # already had rather than the line's new text on the next
                                    # rerun -- without popping it, edited_page_rows further
                                    # down reads that stale value back and overwrites the
                                    # just-accepted retranscription in st.session_state.lines.
                                    st.session_state.pop(f"zh_{ln.idx}", None)
                                    st.rerun()
                            else:
                                st.warning("No speech found in this line's timing window.")
                    _orig_text = raw_transcript.original_text_for_line(_raw, ln)
                    if _orig_text is not None:
                        st.markdown("**📜 Compare with original**")
                        st.caption(_orig_text or "(empty)")
                        if _orig_text.strip() == zh.strip():
                            st.caption("Unchanged from the original transcript.")
                        elif st.button("↩️ Restore original text for this line",
                                       key=f"rvrestore_{ln.idx}"):
                            for _r in all_lines:
                                if _r.idx == ln.idx:
                                    _r.zh = _orig_text
                            db.save_lines(picked_id, all_lines)
                            # The text box keeps what was typed in it across a
                            # rerun unless its state is dropped.
                            st.session_state.pop(f"zh_{ln.idx}", None)
                            st.rerun()
                # Editing a flagged line's translation is treated as addressing
                # it -- clears automatically rather than needing a separate
                # "mark reviewed" click on top of the fix itself. Merely
                # looking at it (no change) leaves the flag in place.
                _still_flag, _still_note = (ln.flag, ln.flag_note) if en.strip() == ln.en.strip() else (None, "")
                edited_page_rows.append(Line(idx=ln.idx, start=start, end=end, zh=zh, en=en,
                                              speaker=(speaker or None) if _speaker_changed else ln.speaker,
                                              speaker_manual=ln.speaker_manual or _speaker_changed,
                                              dub_filename=ln.dub_filename,
                                              flag=_still_flag, flag_note=_still_note,
                                              id=ln.id, orig=ln.orig, merged_ids=ln.merged_ids))

            # Splice the edited page back into the full list -- lines outside
            # this page stay untouched rather than being re-rendered/re-edited.
            edited_rows = list(all_lines)
            _idx_to_pos = {ln.idx: i for i, ln in enumerate(edited_rows)}
            for ln in edited_page_rows:
                edited_rows[_idx_to_pos[ln.idx]] = ln
            st.session_state.lines = edited_rows

            if st.button("💾 Save edits (this page)"):
                # Capture what you actually changed, so the style profile can learn
                # from real edits rather than guesswork.
                _prev = {r["idx"]: r.get("en") or "" for r in db.load_lines(picked_id)}
                for ln in edited_page_rows:
                    before = _prev.get(ln.idx, "")
                    if before and ln.en and before.strip() != ln.en.strip():
                        db.record_edit_sample(picked_id, ln.zh, before, ln.en)
                db.save_lines(picked_id, edited_rows)
                st.success("Saved.")

            with st.expander("🔍 Check line coverage (do this before translating)"):
                st.caption(
                    "Scans for the patterns that usually mean real dialogue got missed or merged "
                    "during transcription -- worth running before spending on translation, since "
                    "fixing timing after is free and fixing it after translating means re-doing "
                    "the translation too."
                )
                if st.button("Run coverage check"):
                    st.session_state[f"coverage_report_{picked_id}"] = core_module.diagnose_line_coverage(
                        edited_rows)
                report = st.session_state.get(f"coverage_report_{picked_id}")
                if report:
                    cc1, cc2, cc3, cc4 = st.columns(4)
                    cc1.metric("Long/merged lines", len(report["long_lines"]))
                    cc2.metric("Large silent gaps", len(report["large_gaps"]))
                    cc3.metric("No source text", len(report["blank_zh"]))
                    cc4.metric("Untranslated", len(report["blank_en"]))

                    if report["long_lines"]:
                        st.markdown("**Suspiciously long lines** (likely several merged into one -- "
                                  "lower the speech-splitting sensitivity above and re-align to fix)")
                        for l in report["long_lines"][:15]:
                            st.caption(f"Line {l['idx']+1} ({fmt_ts(l['start'])}–{fmt_ts(l['end'])}): "
                                      f"{l['note']} — \"{l['zh'][:40]}\"")
                    if report["large_gaps"]:
                        st.markdown("**Large silent gaps** (real silence, or quiet dialogue the "
                                  "detector missed -- worth a quick listen)")
                        for g in report["large_gaps"][:15]:
                            st.caption(f"{g['gap_seconds']:.1f}s gap between line {g['after_idx']+1} "
                                      f"and {g['before_idx']+1} ({fmt_ts(g['gap_start'])}–"
                                      f"{fmt_ts(g['gap_end'])})")
                    if not report["long_lines"] and not report["large_gaps"]:
                        st.success("No obvious coverage problems found.")

            with st.expander("⏱️ Check dubbing pacing (optional)"):
                st.caption(
                    "Flags lines that are too long to say naturally within their time slot, "
                    "or oddly short relative to it. Doesn't change anything by itself."
                )
                if st.button("Check pacing"):
                    flags = translate_engines.smart_segment_lines(edited_rows)
                    if flags:
                        st.session_state[f"pacing_flags_{picked_id}"] = flags
                        st.warning(f"{len(flags)} line(s) flagged.")
                    else:
                        st.success("No pacing issues detected.")
                flags = st.session_state.get(f"pacing_flags_{picked_id}", [])
                if flags:
                    for f in flags:
                        pc1, pc2 = st.columns([4, 1])
                        pc1.caption(f"Line #{f['idx']+1} ({f['issue']}): {f['detail']}")
                        with pc2:
                            _jump_to_line_button(picked_id, f["idx"], all_lines,
                                                  key=f"jump_pacing_{f['idx']}")
                    too_long_idxs = {f["idx"] for f in flags if f["issue"] == "too_long_for_slot"}
                    if too_long_idxs and _translation_only_engine:
                        st.caption(f"⚠️ {_translation_only_message}")
                    if too_long_idxs and api_key and st.button(
                            "✂️ Auto-shorten overlong lines with LLM",
                            disabled=_translation_only_engine):
                        engine = translate_engines.get_engine(
                            engine_choice, api_key, engine_model,
                            free_tier=engine_choice == "gemini" and _gemini_free_tier,
                            base_url=_ollama_base_url if engine_choice == "ollama" else None)
                        to_fix = [ln for ln in edited_rows if ln.idx in too_long_idxs]
                        translate_engines.rewrite_for_pacing_llm(
                            to_fix, engine,
                            usage_cb=lambda inp, out: db.log_usage(
                                picked_id, engine_choice, getattr(engine, "model", engine_choice),
                                "pacing_shorten", inp, out,
                                translate_engines.estimate_cost_for_engine(engine, inp, out)))
                        db.save_lines(picked_id, edited_rows)
                        st.session_state.lines = edited_rows
                        st.session_state[f"pacing_flags_{picked_id}"] = []
                        st.success(f"Shortened {len(to_fix)} line(s). Review below.")
                        st.rerun()

            with st.expander("🔍 Check translation consistency (optional)"):
                st.caption(
                    "Flags the same Chinese name/term translated differently in different lines "
                    "(e.g. a character's name spelled two ways). Doesn't change anything by itself. "
                    "Runs in the background, same as Review queue and Emotion detection -- safe to "
                    "run any of them at the same time."
                )
                _consistency_job_id = f"consistency_{picked_id}"
                _cjob = background_jobs.get_status(_consistency_job_id)
                if _translation_only_engine:
                    st.caption(f"⚠️ {_translation_only_message}")
                _consistency_bulk = False
                if engine_choice in ("claude", "gemini") and not (engine_choice == "gemini"
                                                                  and _gemini_free_tier):
                    _consistency_bulk = st.checkbox(
                        "🐢 Bulk (cheaper, slower)", key=f"bulk_consistency_{picked_id}",
                        help="Half price via Claude/Gemini's own batch API -- most finish within "
                             "an hour, some up to 24 hours. Tracked under 🐢 Bulk jobs below "
                             "instead of here.")
                if st.button("Check consistency",
                             disabled=_translation_only_engine or _gemini_free_tier_pro_blocked) and api_key:
                    engine = translate_engines.get_engine(
                        engine_choice, api_key, engine_model,
                        free_tier=engine_choice == "gemini" and _gemini_free_tier,
                        base_url=_ollama_base_url if engine_choice == "ollama" else None)
                    if _consistency_bulk:
                        _kind, _msg = _start_bulk_generic("consistency", picked_id, engine, engine_choice)
                        getattr(st, _kind)(_msg)
                    else:
                        _lines_copy = _copy_lines(edited_rows)
                        started = background_jobs.start_job(
                            _consistency_job_id, run_consistency_job,
                            _consistency_job_id, picked_id, _lines_copy, engine, engine_choice,
                            gpu_touching=engine_choice == "ollama",
                            description=f"Ollama consistency check ({_drama_label(drama)})")
                        if started:
                            st.info(_job_start_message(
                                _consistency_job_id,
                                "Checking in the background -- safe to switch tabs or run another "
                                "check while this runs."))
                            st.rerun()
                        else:
                            st.warning("Already checking for this drama.")

                if _cjob:
                    if _cjob["status"] == "running":
                        st.progress(0.5, text="Checking...")
                        if st.button("🔄 Refresh progress", key=f"refresh_cc_{picked_id}"):
                            st.rerun()
                    elif _cjob["status"] == "done":
                        _count = (_cjob.get("result") or {}).get("issue_count", 0)
                        if _count:
                            st.warning(f"{_count} consistency issue(s) found.")
                        else:
                            st.success("No consistency issues detected.")
                        background_jobs.clear_job(_consistency_job_id)
                    elif _cjob["status"] == "error":
                        st.error(f"Consistency check failed: {_cjob['error']}")
                        with st.expander("Details"):
                            st.code(_cjob.get("traceback", ""), language="text")
                        background_jobs.clear_job(_consistency_job_id)

                issues = db.load_consistency_issues(picked_id)
                for issue in issues:
                    st.caption(f"**{issue.get('term')}**: {', '.join(issue.get('variants', []))} "
                              f"— {issue.get('note', '')}")

            with st.expander("⚠️ Review queue (flag lines that need a second look)"):
                st.caption(
                    "Instead of scanning a whole multi-hour transcript, flag just the handful of "
                    "lines worth a second look -- a possible mistranslation, an unresolved pronoun, "
                    "an uncertain name, slang that may not have translated well. Most lines get no "
                    "flag at all; this doesn't change anything by itself."
                )
                _flag_job_id = f"flag_{picked_id}"
                _fjob = background_jobs.get_status(_flag_job_id)
                if _translation_only_engine:
                    st.caption(f"⚠️ {_translation_only_message}")
                _flag_bulk = False
                if engine_choice in ("claude", "gemini") and not (engine_choice == "gemini"
                                                                  and _gemini_free_tier):
                    _flag_bulk = st.checkbox(
                        "🐢 Bulk (cheaper, slower)", key=f"bulk_flag_{picked_id}",
                        help="Half price via Claude/Gemini's own batch API -- most finish within "
                             "an hour, some up to 24 hours. Tracked under 🐢 Bulk jobs below "
                             "instead of here.")
                if st.button("Find lines to flag",
                             disabled=_translation_only_engine or _gemini_free_tier_pro_blocked) and api_key:
                    engine_f = translate_engines.get_engine(
                        engine_choice, api_key, engine_model,
                        free_tier=engine_choice == "gemini" and _gemini_free_tier,
                        base_url=_ollama_base_url if engine_choice == "ollama" else None)
                    if _flag_bulk:
                        _kind, _msg = _start_bulk_generic("flag", picked_id, engine_f, engine_choice)
                        getattr(st, _kind)(_msg)
                    else:
                        _lines_copy = _copy_lines(edited_rows)
                        started = background_jobs.start_job(
                            _flag_job_id, run_flag_job,
                            _flag_job_id, picked_id, _lines_copy, engine_f, engine_choice,
                            gpu_touching=engine_choice == "ollama",
                            description=f"Ollama flagging ({_drama_label(drama)})")
                        if started:
                            st.info(_job_start_message(
                                _flag_job_id,
                                "Checking in the background -- safe to switch tabs while this runs."))
                            st.rerun()
                        else:
                            st.warning("Already checking for this drama.")

                if _fjob:
                    if _fjob["status"] == "running":
                        st.progress(_fjob["progress"], text=(_fjob.get("message") or "Checking...") + background_jobs.eta_text(_fjob))
                        if st.button("🔄 Refresh progress", key=f"refresh_fl_{picked_id}"):
                            st.rerun()
                    elif _fjob["status"] == "done":
                        _count = (_fjob.get("result") or {}).get("flagged_count", 0)
                        st.session_state.lines = db.load_line_objects(picked_id)
                        edited_rows = st.session_state.lines
                        if _count:
                            st.warning(f"{_count} line(s) flagged -- see the review table below, or "
                                      "turn on \"Show flagged lines only\" to jump straight to them.")
                        else:
                            st.success("Nothing flagged.")
                        background_jobs.clear_job(_flag_job_id)
                    elif _fjob["status"] == "error":
                        st.error(f"Flagging failed: {_fjob['error']}")
                        with st.expander("Details"):
                            st.code(_fjob.get("traceback", ""), language="text")
                        background_jobs.clear_job(_flag_job_id)

                _n_flagged = sum(1 for ln in edited_rows if ln.flag)
                if _n_flagged:
                    st.caption(f"{_n_flagged} line(s) currently flagged.")

                    st.markdown("**Fix flagged lines in bulk**")
                    _fixflag_job_id = f"fixflag_{picked_id}"
                    _ffjob = background_jobs.get_status(_fixflag_job_id)
                    _fixflag_audio_path = (os.path.join(ddir, drama["audio_filename"])
                                            if drama.get("audio_filename") else None)
                    st.caption(
                        f"Re-transcribes each flagged line's audio with the '{whisper_size}' "
                        f"speech recognition model (set above in 3.) and re-translates it with "
                        f"{engine_choice} / {engine_model or 'default model'} (set below in 5.) -- "
                        "change either of those pickers first if you want this to use something "
                        "else. Lines with no audio to re-transcribe (novel narration) are just "
                        "re-translated. Clears the flag on any line this actually changes."
                        if _fixflag_audio_path else
                        f"Re-translates each flagged line with {engine_choice} / "
                        f"{engine_model or 'default model'} (set below in 5.) -- change that "
                        "picker first if you want a different engine or model. There's no audio "
                        "on this drama to re-transcribe, so only re-translation runs. Clears the "
                        "flag on any line this actually changes.")
                    if st.button("🔁 Re-transcribe + re-translate flagged lines") and api_key:
                        engine_ff = translate_engines.get_engine(
                            engine_choice, api_key, engine_model,
                            free_tier=engine_choice == "gemini" and _gemini_free_tier,
                            base_url=_ollama_base_url if engine_choice == "ollama" else None)
                        _lines_copy_ff = _copy_lines(edited_rows)
                        started = background_jobs.start_job(
                            _fixflag_job_id, run_fix_flagged_lines_job,
                            _fixflag_job_id, picked_id, _lines_copy_ff, _fixflag_audio_path,
                            whisper_size, st.session_state.get("use_gpu", False), source_language,
                            engine_ff, engine_choice,
                            gpu_touching=bool(_fixflag_audio_path) or engine_choice == "ollama",
                            description=f"Fixing flagged lines ({_drama_label(drama)})")
                        if started:
                            st.info(_job_start_message(
                                _fixflag_job_id,
                                "Fixing flagged lines in the background -- safe to switch tabs "
                                "while this runs."))
                            st.rerun()
                        else:
                            st.warning("Already fixing flagged lines for this drama.")

                    if _ffjob:
                        if _ffjob["status"] == "running":
                            st.progress(_ffjob["progress"], text=(_ffjob.get("message") or "Fixing...") + background_jobs.eta_text(_ffjob))
                            if st.button("🔄 Refresh progress", key=f"refresh_ff_{picked_id}"):
                                st.rerun()
                        elif _ffjob["status"] == "done":
                            st.session_state.lines = db.load_line_objects(picked_id)
                            edited_rows = st.session_state.lines
                            _ff_result = _ffjob.get("result") or {}
                            st.success(f"Fixed {_ff_result.get('fixed_count', 0)} of "
                                      f"{_ff_result.get('total_flagged', 0)} flagged line(s).")
                            background_jobs.clear_job(_fixflag_job_id)
                        elif _ffjob["status"] == "error":
                            st.error(f"Fixing flagged lines failed: {_ffjob['error']}")
                            with st.expander("Details"):
                                st.code(_ffjob.get("traceback", ""), language="text")
                            background_jobs.clear_job(_fixflag_job_id)

            with st.expander("🎭 Emotional register (sarcasm, humour, anger)"):
                st.caption(
                    "Tags each line's emotional charge so translation preserves it. Sarcasm read "
                    "as sincerity, or suppressed anger read as calm, breaks a scene even when the "
                    "words are technically correct -- these are the registers most often flattened."
                )
                emap = db.load_emotions(picked_id)
                if _translation_only_engine:
                    st.caption(f"⚠️ {_translation_only_message}")
                ec1, ec2 = st.columns([1, 1])
                use_cues = ec2.checkbox("Use audio delivery cues", value=(has_audio_pipeline),
                                         help="Uses pacing and pauses from the original timing as "
                                              "weak evidence for emotional register.")
                _emotion_job_id = f"emotion_{picked_id}"
                _ejob = background_jobs.get_status(_emotion_job_id)
                _emotion_bulk = False
                if engine_choice in ("claude", "gemini") and not (engine_choice == "gemini"
                                                                  and _gemini_free_tier):
                    _emotion_bulk = st.checkbox(
                        "🐢 Bulk (cheaper, slower)", key=f"bulk_emotion_{picked_id}",
                        help="Half price via Claude/Gemini's own batch API -- most finish within "
                             "an hour, some up to 24 hours. Tracked under 🐢 Bulk jobs below "
                             "instead of here.")
                if ec1.button("Detect emotional register", disabled=_translation_only_engine) and api_key:
                    eng_e = translate_engines.get_engine(
                        engine_choice, api_key, engine_model,
                        free_tier=engine_choice == "gemini" and _gemini_free_tier,
                        base_url=_ollama_base_url if engine_choice == "ollama" else None)
                    if _emotion_bulk:
                        _kind, _msg = _start_bulk_generic("emotion", picked_id, eng_e, engine_choice,
                                                          use_audio_cues=use_cues)
                        getattr(st, _kind)(_msg)
                    else:
                        # A copy, not the live list -- same reasoning as the Translate
                        # button's _lines_copy: this runs in a background thread, and
                        # edited_rows is tied to the review table's current widget state.
                        _lines_copy = _copy_lines(edited_rows)
                        started = background_jobs.start_job(
                            _emotion_job_id, run_emotion_job,
                            _emotion_job_id, picked_id, _lines_copy, eng_e, use_cues, engine_choice,
                            gpu_touching=engine_choice == "ollama",
                            description=f"Ollama emotion detection ({_drama_label(drama)})")
                        if started:
                            st.info(_job_start_message(
                                _emotion_job_id,
                                "Reading tone in the background -- safe to switch tabs while this "
                                "runs."))
                            st.rerun()
                        else:
                            st.warning("Already detecting emotional register for this drama.")

                if _ejob:
                    if _ejob["status"] == "running":
                        st.progress(_ejob["progress"], text=(_ejob.get("message") or "Reading tone...") + background_jobs.eta_text(_ejob))
                        if st.button("🔄 Refresh progress", key=f"refresh_em_{picked_id}"):
                            st.rerun()
                    elif _ejob["status"] == "done":
                        # Saved to the database inside run_emotion_job itself (see
                        # its docstring for why) -- reload from there rather than
                        # trusting the job's own ephemeral result dict, the same
                        # "persisted state wins" pattern the flag job uses.
                        emap = db.load_emotions(picked_id)
                        background_jobs.clear_job(_emotion_job_id)
                        st.rerun()
                    elif _ejob["status"] == "error":
                        st.error(f"Emotion detection failed: {_ejob['error']}")
                        with st.expander("Details"):
                            st.code(_ejob.get("traceback", ""), language="text")
                        background_jobs.clear_job(_emotion_job_id)

                if emap:
                    summ = emotion.emotion_summary(emap)
                    sc1, sc2 = st.columns(2)
                    sc1.metric("Lines tagged", summ["total"])
                    sc2.metric("High-risk register", summ["high_risk"],
                                help="Sarcasm, dry humour, suppressed anger, flirtation, evasion -- "
                                     "the registers most likely to be lost in translation.")
                    st.caption(" · ".join(f"{k}: {v}" for k, v in
                                           sorted(summ["by_emotion"].items(), key=lambda x: -x[1])))
                    st.caption("These tags are applied automatically on the next translation run.")

                if has_audio_pipeline:
                    st.markdown("**🔊 From the audio (SenseVoice, optional)**")
                    st.caption("A second opinion from how each line actually sounds -- emotion "
                               "(happy, sad, angry, neutral, fearful, disgusted, surprised) plus "
                               "sounds like laughter, crying or background music. Shown next to "
                               "the text-based tags above, never merged into them: the two can "
                               "disagree, and only the text-based tags feed translation. Needs "
                               "`pip install funasr`. " + sensevoice_tags.LICENSE_NOTE)
                    _sv_audio = (os.path.join(ddir, drama["audio_filename"])
                                 if drama.get("audio_filename") else None)
                    _sv_job_id = f"sensevoice_{picked_id}"
                    _svjob = background_jobs.get_status(_sv_job_id)
                    if st.button("🔊 Tag emotion & sounds from the audio",
                                 disabled=not (_sv_audio and os.path.exists(_sv_audio)),
                                 key=f"sensevoice_run_{picked_id}"):
                        started = background_jobs.start_job(
                            _sv_job_id, run_sensevoice_job, _sv_job_id, picked_id,
                            _copy_lines(edited_rows), _sv_audio, ddir,
                            st.session_state.get("use_gpu", False),
                            gpu_touching=True,
                            description=f"SenseVoice audio tagging ({_drama_label(drama)})")
                        if started:
                            st.rerun()
                        else:
                            st.warning("Already tagging this drama's audio.")
                    if _svjob:
                        if _svjob["status"] == "queued":
                            st.info(_svjob.get("message") or "Waiting -- GPU busy.")
                            if st.button("🔄 Refresh progress", key=f"refresh_sv_queued_{picked_id}"):
                                st.rerun()
                        elif _svjob["status"] == "running":
                            st.progress(_svjob["progress"], text=(_svjob.get("message") or "Listening...") + background_jobs.eta_text(_svjob))
                            if st.button("🔄 Refresh progress", key=f"refresh_sv_{picked_id}"):
                                st.rerun()
                        elif _svjob["status"] == "done":
                            background_jobs.clear_job(_sv_job_id)
                            st.rerun()
                        elif _svjob["status"] == "error":
                            st.error(f"Audio tagging failed: {_svjob['error']}")
                            background_jobs.clear_job(_sv_job_id)
                    _audio_tags = sensevoice_tags.load_audio_tags(ddir)
                    if _audio_tags:
                        _rows = sensevoice_tags.side_by_side(edited_rows, emap, _audio_tags)
                        _n_disagree = sum(r["disagree"] for r in _rows)
                        st.caption(f"{len(_audio_tags)} line(s) tagged from the audio. "
                                   f"{_n_disagree} where the audio and the text-based read "
                                   "disagree -- worth a listen.")
                        st.dataframe(
                            pd.DataFrame(_rows).rename(columns={
                                "line": "#", "text": "Line", "text_emotion": "Text-based",
                                "audio_emotion": "Audio emotion", "audio_events": "Sounds",
                                "disagree": "Disagree?"}),
                            hide_index=True, width="stretch")

            with st.expander("🎯 Adaptive style (learns from your edits)"):
                st.caption(
                    "Every line you rewrite is recorded. Once enough accumulate, they're analyzed "
                    "for consistent patterns and folded into future translation prompts. "
                    "Conservative by design -- it only reports preferences it can see repeatedly."
                )
                samples = db.list_edit_samples(picked_id)
                tend = adaptive_style.summarize_edit_tendencies(samples)
                if tend["total"]:
                    tc1, tc2, tc3, tc4 = st.columns(4)
                    tc1.metric("Edits recorded", tend["total"])
                    tc2.metric("Shortened", tend["shortened"])
                    tc3.metric("Expanded", tend["expanded"])
                    tc4.metric("Avg word change", f"{tend['avg_word_delta']:+.1f}")
                else:
                    st.caption("No edits recorded yet -- rewrite some lines above and save.")

                scope = f"series:{drama['series_id']}" if drama.get("series_id") else "global"
                existing_profile = db.get_style_profile(scope)
                if st.button("🧠 Learn my style from these edits") and api_key:
                    eng_a = translate_engines.get_engine(
                        engine_choice, api_key, engine_model,
                        free_tier=engine_choice == "gemini" and _gemini_free_tier,
                        base_url=_ollama_base_url if engine_choice == "ollama" else None)
                    all_samples = db.list_edit_samples()
                    with st.spinner("Analyzing your edits..."):
                        result = adaptive_style.analyze_edit_patterns(
                            all_samples, eng_a,
                            existing_profile=(existing_profile or {}).get("profile"),
                            # drama_id=None: this analyzes edits across every drama
                            # (or every drama in a series), not just this one.
                            usage_cb=lambda inp, out: db.log_usage(
                                None, engine_choice, getattr(eng_a, "model", engine_choice),
                                "adaptive_style", inp, out,
                                translate_engines.estimate_cost_for_engine(eng_a, inp, out)))
                    if result.get("preferences"):
                        db.save_style_profile(scope, result, sample_count=len(all_samples))
                        st.success(f"Learned {len(result['preferences'])} preference(s).")
                        st.rerun()
                    else:
                        st.info(result.get("summary", "No clear patterns found yet."))

                if existing_profile and existing_profile["profile"].get("preferences"):
                    pr = existing_profile["profile"]
                    st.caption(f"**Learned profile** (confidence: {pr.get('confidence','?')}, "
                              f"from {existing_profile['sample_count']} edits)")
                    if pr.get("summary"):
                        st.caption(f"_{pr['summary']}_")
                    for p_ in pr["preferences"]:
                        st.caption(f"• {p_}")
                    st.session_state["apply_style_profile"] = st.checkbox(
                        "Apply this profile to future translations",
                        value=st.session_state.get("apply_style_profile", True))
                    if st.button("Reset learned style"):
                        db.save_style_profile(scope, {"preferences": []}, 0)
                        st.rerun()

            with st.expander("🔀 Translation versions (compare models)"):
                st.caption(
                    "Every translation run is saved as a version, so re-translating with a "
                    "different model never destroys the previous attempt. Compare them "
                    "side-by-side and activate whichever reads better."
                )
                versions = db.list_translation_versions(picked_id)
                if not versions:
                    st.caption("No saved versions yet -- run a translation first.")
                else:
                    for v in versions:
                        vc1, vc2, vc3 = st.columns([3, 1, 1])
                        active = " ✅ **active**" if v["is_active"] else ""
                        when = v["created_at"][:16].replace("T", " ") if v["created_at"] else "?"
                        vc1.caption(f"**{v['label']}**{active} — {v['model'] or v['engine']} · {when}")
                        if not v["is_active"] and vc2.button("Activate", key=f"actv_{v['id']}"):
                            full = db.get_translation_version(v["id"])
                            if full:
                                db.save_line_history_snapshot(picked_id, st.session_state.lines,
                                                               "before switching version")
                                restored = core_module.adopt_ids(
                                    [Line(idx=r["idx"], start=r["start"], end=r["end"],
                                          zh=r["zh"], en=r["en"], speaker=r.get("speaker"),
                                          id=r.get("id"))
                                     for r in full["lines"]],
                                    st.session_state.lines)
                                db.save_lines(picked_id, restored)
                                db.set_active_translation_version(picked_id, v["id"])
                                st.session_state.lines = restored
                                st.success(f"Activated '{v['label']}'.")
                                st.rerun()
                        if vc3.button("🗑️", key=f"delv_{v['id']}"):
                            db.delete_translation_version(v["id"])
                            st.rerun()

                    if len(versions) >= 2:
                        st.markdown("**Compare two versions**")
                        vlabels = {f"{v['label']} ({v['created_at'][:10]})": v["id"] for v in versions}
                        cc1, cc2 = st.columns(2)
                        left = cc1.selectbox("Left", list(vlabels.keys()), index=0, key="cmp_left")
                        right = cc2.selectbox("Right", list(vlabels.keys()),
                                               index=min(1, len(vlabels) - 1), key="cmp_right")
                        if st.button("Show differences"):
                            lv = db.get_translation_version(vlabels[left])
                            rv = db.get_translation_version(vlabels[right])
                            rmap = {r["idx"]: r["en"] for r in rv["lines"]}
                            diffs = [(r["idx"], r["zh"], r["en"], rmap.get(r["idx"], ""))
                                     for r in lv["lines"] if rmap.get(r["idx"], "") != r["en"]]
                            st.caption(f"{len(diffs)} line(s) differ out of {len(lv['lines'])}.")
                            for idx, zh, l_en, r_en in diffs[:60]:
                                st.markdown(f"**Line {idx+1}** · {zh}")
                                dc1, dc2 = st.columns(2)
                                dc1.info(l_en or "_(empty)_")
                                dc2.warning(r_en or "_(empty)_")

            with st.expander("📝 Translation notes (idioms, wordplay, meaningful names)"):
                st.caption(
                    "Reviews the translation for things that lost something crossing languages -- "
                    "四字成语 and set phrases, puns, names whose characters carry meaning, literary "
                    "allusions, and honorifics whose nuance doesn't survive a direct rendering. "
                    "Produces notes for readers; doesn't change any line. Runs in the background, "
                    "same as Review queue and Emotion detection -- safe to run any of them at the "
                    "same time."
                )
                _notes_job_id = f"notes_{picked_id}"
                _njob = background_jobs.get_status(_notes_job_id)
                if _translation_only_engine:
                    st.caption(f"⚠️ {_translation_only_message}")
                _notes_bulk = False
                if engine_choice in ("claude", "gemini") and not (engine_choice == "gemini"
                                                                  and _gemini_free_tier):
                    _notes_bulk = st.checkbox(
                        "🐢 Bulk (cheaper, slower)", key=f"bulk_notes_{picked_id}",
                        help="Half price via Claude/Gemini's own batch API -- most finish within "
                             "an hour, some up to 24 hours. Tracked under 🐢 Bulk jobs below "
                             "instead of here.")
                if st.button("Generate translation notes",
                             disabled=_translation_only_engine or _gemini_free_tier_pro_blocked) and api_key:
                    engine_n = translate_engines.get_engine(
                        engine_choice, api_key, engine_model,
                        free_tier=engine_choice == "gemini" and _gemini_free_tier,
                        base_url=_ollama_base_url if engine_choice == "ollama" else None)
                    if _notes_bulk:
                        _kind, _msg = _start_bulk_generic("translation_notes", picked_id, engine_n,
                                                          engine_choice)
                        getattr(st, _kind)(_msg)
                    else:
                        _lines_copy = _copy_lines(edited_rows)
                        started = background_jobs.start_job(
                            _notes_job_id, run_translation_notes_job,
                            _notes_job_id, picked_id, _lines_copy, engine_n, engine_choice,
                            source_language,
                            gpu_touching=engine_choice == "ollama",
                            description=f"Ollama translation notes ({_drama_label(drama)})")
                        if started:
                            st.info(_job_start_message(
                                _notes_job_id,
                                "Reviewing in the background -- safe to switch tabs or run another "
                                "check while this runs."))
                            st.rerun()
                        else:
                            st.warning("Already generating notes for this drama.")

                if _njob:
                    if _njob["status"] == "running":
                        st.progress(0.5, text="Reviewing for idioms, wordplay, and allusions...")
                        if st.button("🔄 Refresh progress", key=f"refresh_nt_{picked_id}"):
                            st.rerun()
                    elif _njob["status"] == "done":
                        _count = (_njob.get("result") or {}).get("note_count", 0)
                        if _count:
                            st.success(f"Found {_count} note(s).")
                        else:
                            st.info("Nothing flagged as needing a note.")
                        background_jobs.clear_job(_notes_job_id)
                    elif _njob["status"] == "error":
                        st.error(f"Note generation failed: {_njob['error']}")
                        with st.expander("Details"):
                            st.code(_njob.get("traceback", ""), language="text")
                        background_jobs.clear_job(_notes_job_id)

                existing_notes = db.list_translation_notes(picked_id)
                if existing_notes:
                    st.caption(f"{len(existing_notes)} note(s) recorded:")
                    for n in existing_notes:
                        nc1, nc2, nc3 = st.columns([4, 1.3, 0.5])
                        line_ref = f"Line {n['line_idx'] + 1}" if n.get("line_idx") is not None else "—"
                        nc1.caption(f"**{n['term']}** ({n['note_type']}, {line_ref}): {n['note']}")
                        with nc2:
                            if n.get("line_idx") is not None:
                                _jump_to_line_button(picked_id, n["line_idx"], all_lines,
                                                      key=f"jump_note_{n['id']}")
                        if nc3.button("🗑️", key=f"delnote_{n['id']}"):
                            db.delete_translation_note(n["id"])
                            st.rerun()

                    notes_md = tguide.format_notes_as_markdown(
                        existing_notes, drama["title_en"] or drama["title_zh"] or "")
                    st.download_button("📄 Download notes as Markdown", notes_md,
                                        file_name="translation_notes.md")

                    with st.form(f"add_note_{picked_id}", clear_on_submit=True):
                        st.caption("Add your own note:")
                        anc1, anc2 = st.columns(2)
                        an_term = anc1.text_input("Term / phrase")
                        an_type = anc2.selectbox("Type", list(tguide.NOTE_TYPES.keys()),
                                                  format_func=lambda k: tguide.NOTE_TYPES[k])
                        an_line = st.number_input("Line number (1-based)", value=1, min_value=1)
                        an_text = st.text_area("Note", height=68)
                        if st.form_submit_button("Add note") and an_term and an_text:
                            db.save_translation_notes(picked_id, [{
                                "line_idx": an_line - 1, "term": an_term,
                                "note_type": an_type, "note": an_text}])
                            st.success("Added.")
                            st.rerun()

            with st.expander("🔗 Merge short adjacent lines (optional)"):
                st.caption(
                    "Combines consecutive short lines from the same speaker into one natural "
                    "subtitle, when they're close enough in time that the split was probably just "
                    "an artifact of the source transcript's line breaks. This changes your line "
                    "count -- review the result before saving."
                )
                if st.button("Preview merge"):
                    # merge_adjacent_short_lines mutates the Line objects it merges
                    # in place (and renumbers every line's .idx) -- list(edited_rows)
                    # only copies the outer list, not the Line objects inside it, so
                    # without _copy_lines a preview silently corrupted the live,
                    # unsaved st.session_state.lines before "Apply merge" was ever
                    # clicked.
                    merged_preview = merge_adjacent_short_lines(_copy_lines(edited_rows))
                    st.session_state[f"merge_preview_{picked_id}"] = merged_preview
                    st.info(f"{len(edited_rows)} lines -> {len(merged_preview)} lines after merging.")
                merge_preview = st.session_state.get(f"merge_preview_{picked_id}")
                if merge_preview:
                    if st.button("✅ Apply merge"):
                        db.save_line_history_snapshot(picked_id, edited_rows, "before merge")
                        db.save_lines(picked_id, merge_preview)
                        st.session_state.lines = merge_preview
                        st.session_state[f"merge_preview_{picked_id}"] = None
                        _clear_line_widget_state()
                        st.success("Merged and saved. (Previous version saved to history -- "
                                  "see 'Version history' below if you want it back.)")
                        st.rerun()

            with st.expander("✂️ Re-segment long lines by meaning (optional)"):
                _reseg_max = resegment.max_line_chars(source_language)
                st.caption(
                    "Speech recognition ends a line wherever the speaker pauses, not where a "
                    "thought ends -- so a line can run several sentences together. This splits "
                    f"only lines too long for a two-line subtitle (over {_reseg_max} characters), at "
                    "sentence ends first, then commas, then clause-joining words (但是, 所以, でも, "
                    "그리고...) -- never inside a word, never reworded. A line with no sensible "
                    "place to split is left whole. Changes your line count -- review the preview "
                    "before applying."
                )
                _reseg_llm_ok = bool(api_key) and not _translation_only_engine
                _reseg_use_llm = st.checkbox(
                    f"Ask {engine_choice} where to split lines the rules can't",
                    value=_reseg_llm_ok, disabled=not _reseg_llm_ok,
                    key=f"reseg_use_llm_{picked_id}",
                    help="The model can only suggest WHERE to break. Its answer is matched back "
                         "to the original text and thrown away if it changed any wording, so it "
                         "can't alter what a line says. One short request per line that still "
                         "needs it (retried up to 3 times on an unusable answer).")
                _reseg_key = f"reseg_preview_{picked_id}"
                _reseg_job_id = f"resegment_{picked_id}"
                _reseg_job = background_jobs.get_status(_reseg_job_id)
                _reseg_job_active = bool(_reseg_job and _reseg_job["status"] in ("running", "queued"))
                if st.button("Preview re-segmentation", key=f"reseg_preview_btn_{picked_id}",
                             disabled=_reseg_job_active):
                    # Re-fetch fresh from the database right before computing the
                    # preview, rather than relying on edited_rows/st.session_state.lines
                    # (which can go stale relative to the database between renders --
                    # e.g. a background job's own field-scoped save landing in between).
                    # Apply later commits this exact snapshot as a full line-list
                    # replacement -- a stale id set here is exactly what caused real,
                    # confirmed duplicate/orphaned rows (Step 6f).
                    _reseg_source_lines = db.load_line_objects(picked_id)
                    _reseg_source_ids = {ln.id for ln in _reseg_source_lines if ln.id is not None}
                    _reseg_engine = None
                    if _reseg_use_llm and _reseg_llm_ok:
                        _reseg_engine = translate_engines.get_engine(
                            engine_choice, api_key, engine_model,
                            free_tier=engine_choice == "gemini" and _gemini_free_tier,
                            base_url=_ollama_base_url if engine_choice == "ollama" else None)
                    _raw = raw_transcript.load_latest(ddir)
                    # Only the local-Ollama LLM pass is GPU-touching (and worth a
                    # real mid-run stop for) -- rule-only and cloud-engine runs
                    # stay synchronous, same as before Step 4e.
                    if _reseg_engine is not None and engine_choice == "ollama":
                        background_jobs.start_process_job(
                            _reseg_job_id, resegment.resegment_subprocess_worker,
                            args=(_copy_lines(_reseg_source_lines), source_language, _reseg_engine,
                                  (_raw or {}).get("segments"), chinese_script),
                            gpu_touching=True, description=f"Re-segmenting ({_drama_label(drama)})")
                        st.session_state[f"{_reseg_key}_source_ids"] = _reseg_source_ids
                        st.info("Finding meaningful split points in the background -- come back "
                                "here for the preview once it's done, or Cancel below.")
                        st.rerun()
                    else:
                        with st.spinner("Finding meaningful split points..."):
                            _new_lines, _changed = resegment.resegment_lines(
                                _copy_lines(_reseg_source_lines), source_language, engine=_reseg_engine,
                                segments=(_raw or {}).get("segments"), chinese_script=chinese_script,
                                usage_cb=lambda inp, out: db.log_usage(
                                    picked_id, engine_choice,
                                    getattr(_reseg_engine, "model", engine_choice), "resegment",
                                    inp, out, translate_engines.estimate_cost_for_engine(
                                        _reseg_engine, inp, out)))
                        st.session_state[_reseg_key] = {
                            "lines": _new_lines,
                            "changed": [(ln.id, ln.idx, ln.zh, pieces) for ln, pieces in _changed],
                            "source_ids": _reseg_source_ids,
                        }
                if _reseg_job:
                    if _reseg_job["status"] == "queued":
                        st.info(_reseg_job.get("message") or "Waiting for the GPU...")
                    elif _reseg_job["status"] == "running":
                        st.info("Finding meaningful split points in the background -- safe to "
                                "switch tabs. Cancel below genuinely stops it.")
                        rgc1, rgc2 = st.columns(2)
                        if rgc1.button("🔄 Refresh progress", key=f"refresh_reseg_{picked_id}"):
                            st.rerun()
                        if rgc2.button("✖ Cancel", key=f"cancel_reseg_{picked_id}"):
                            background_jobs.request_cancel(_reseg_job_id)
                            st.rerun()
                    elif _reseg_job["status"] == "cancelled":
                        st.warning("Re-segmentation preview was stopped. Nothing was changed.")
                        background_jobs.clear_job(_reseg_job_id)
                    elif _reseg_job["status"] == "error":
                        st.error(f"Re-segmentation failed ({_reseg_job['error']}).")
                        background_jobs.clear_job(_reseg_job_id)
                    elif _reseg_job["status"] == "done":
                        _reseg_result = _reseg_job.get("result") or {}
                        _cost_engine = translate_engines.get_engine(
                            engine_choice, api_key, engine_model,
                            free_tier=engine_choice == "gemini" and _gemini_free_tier,
                            base_url=_ollama_base_url if engine_choice == "ollama" else None)
                        for _inp, _out in _reseg_result.get("usage_calls", []):
                            db.log_usage(picked_id, engine_choice,
                                        getattr(_cost_engine, "model", engine_choice), "resegment",
                                        _inp, _out, translate_engines.estimate_cost_for_engine(
                                            _cost_engine, _inp, _out))
                        st.session_state[_reseg_key] = {
                            "lines": _reseg_result.get("lines"),
                            "changed": _reseg_result.get("changed"),
                            "source_ids": st.session_state.pop(f"{_reseg_key}_source_ids", set()),
                        }
                        background_jobs.clear_job(_reseg_job_id)
                _reseg = st.session_state.get(_reseg_key)
                if _reseg is not None:
                    if not _reseg["changed"]:
                        st.info("Nothing to re-segment: every line either fits in a two-line "
                                "subtitle already or has no meaningful place to split.")
                    else:
                        st.info(f"{len(edited_rows)} lines -> {len(_reseg['lines'])} lines: "
                                f"{len(_reseg['changed'])} long line(s) would be split.")
                        for _lid, _idx, _zh, _pieces in _reseg["changed"][:10]:
                            st.caption(f"**#{_idx + 1}** {_zh}  \n→ " + "  ·  ".join(_pieces))
                        if len(_reseg["changed"]) > 10:
                            st.caption(f"...and {len(_reseg['changed']) - 10} more.")

                        # Guardrail: splitting a line orphans its translation, flag,
                        # notes and emotion tag (they described the old, longer line),
                        # so those are cleared -- but only on the lines being split.
                        _changed_ids = {c[0] for c in _reseg["changed"]}
                        _by_id = {ln.id: ln for ln in edited_rows}
                        _n_translated = sum(1 for i in _changed_ids
                                            if i in _by_id and _by_id[i].en.strip())
                        _n_flagged = sum(1 for i in _changed_ids if i in _by_id and _by_id[i].flag)
                        _id_by_idx = {ln.idx: ln.id for ln in edited_rows}
                        _n_notes = sum(1 for n in db.list_translation_notes(picked_id)
                                       if _id_by_idx.get(n.get("line_idx")) in _changed_ids)
                        _needs_confirm = bool(_n_translated or _n_flagged or _n_notes)
                        if _needs_confirm or any(ln.en.strip() for ln in edited_rows):
                            st.warning(
                                f"⚠️ {_n_translated} of the lines being split "
                                f"{'is' if _n_translated == 1 else 'are'} already translated -- "
                                "re-segmenting will require re-translating the affected lines. "
                                "Their translation"
                                + (f", {_n_flagged} flag(s)" if _n_flagged else "")
                                + (f", {_n_notes} note(s)" if _n_notes else "")
                                + " and emotion tags will be cleared. Every other line keeps its "
                                "translation, notes and flags untouched. (A snapshot is saved to "
                                "Version history first.)")
                        _confirmed = (not _needs_confirm) or st.checkbox(
                            "I understand -- clear translations, flags and notes on the lines being split",
                            key=f"reseg_confirm_{picked_id}")
                        if st.button("✅ Apply re-segmentation", disabled=not _confirmed,
                                     key=f"reseg_apply_{picked_id}"):
                            # Real safety check (Step 6f): _reseg["lines"] is about to
                            # fully replace this drama's line set, computed from a
                            # snapshot taken back when Preview ran -- if the database's
                            # actual current id set has since diverged (another edit, a
                            # background job finishing, etc.), committing it anyway is
                            # exactly what silently orphaned/duplicated rows before.
                            # Refuse and ask for a fresh Preview instead of guessing.
                            if _reseg.get("source_ids") != db.load_line_ids(picked_id):
                                st.error("This drama's lines changed since this preview was "
                                        "computed -- re-run \"Preview re-segmentation\" before "
                                        "applying, so nothing gets silently corrupted.")
                            else:
                                db.save_line_history_snapshot(
                                    picked_id, db.load_line_objects(picked_id), "before re-segment")
                                db.save_lines(picked_id, _reseg["lines"])
                                st.session_state.lines = db.load_line_objects(picked_id)
                                st.session_state.pop(_reseg_key, None)
                                st.session_state.pop(f"reseg_confirm_{picked_id}", None)
                                _clear_line_widget_state()
                                st.success(f"Re-segmented {len(_reseg['changed'])} line(s) and saved. "
                                           "(Previous version saved to history -- see 'Version "
                                           "history' below if you want it back.)")
                                st.rerun()

            with st.expander("🕓 Version history / undo"):
                st.caption(
                    "Snapshots are taken automatically before operations that discard work "
                    "(force re-translate, merge). Restoring replaces the current lines with the "
                    "saved version -- and takes its own snapshot first, so you can undo the undo."
                )
                history = db.list_line_history(picked_id)
                if not history:
                    st.caption("No snapshots yet for this drama.")
                else:
                    for h in history:
                        hc1, hc2 = st.columns([3, 1])
                        when = h["created_at"][:16].replace("T", " ") if h["created_at"] else "?"
                        hc1.caption(f"**{h['label']}** — {when}")
                        if hc2.button("Restore", key=f"restore_{h['id']}"):
                            snapshot = db.get_line_history_snapshot(h["id"])
                            if snapshot:
                                db.save_line_history_snapshot(picked_id, st.session_state.lines,
                                                               "before restore")
                                restored = core_module.adopt_ids(
                                    [Line(**s) for s in snapshot], st.session_state.lines)
                                db.save_lines(picked_id, restored)
                                st.session_state.lines = restored
                                _clear_line_widget_state()
                                st.success(f"Restored '{h['label']}'.")
                                st.rerun()
                            else:
                                st.error("That snapshot could not be read.")

        with st.expander("8. 🎙️ AI dub / narration", expanded=False):
            st.caption(
                "Uses each character's cloning reference clip if set, otherwise falls back to "
                "the TTS engine chosen below. Voice cloning needs `f5-tts` installed locally. "
                "Requires ffmpeg on PATH."
            )
            tts_engine = st.radio(
                "Fallback TTS engine (used where no clone reference is set)",
                ["edge_tts", "offline"],
                format_func=lambda e: "🌐 edge-tts (free, online, more natural)"
                             if e == "edge_tts" else
                             "📴 Offline / Piper (fully local, no internet, lower quality)",
                horizontal=False,
            )
            voice_pool = dub_module.DEFAULT_VOICE_POOL if tts_engine == "edge_tts" else dub_module.DEFAULT_OFFLINE_VOICE_POOL
            dub_button_label = "🎙️ Generate narration track" if content_mode == "novel_narration" else "🎙️ Generate dub track"
            _dub_job_id = f"dub_{picked_id}"
            _dub_job = background_jobs.get_status(_dub_job_id)
            _dub_job_active = bool(_dub_job and _dub_job["status"] in ("running", "queued"))
            if st.button(dub_button_label, disabled=_dub_job_active):
                chars = db.list_characters(picked_id)
                voice_map = {c["speaker_label"]: c["tts_voice"] for c in chars if c["tts_voice"]}
                clone_map = {}
                el_key_for_dub = st.session_state.get(f"el_key_{picked_id}", "") or st.session_state.get("settings_elevenlabs", "")
                for c in chars:
                    if c.get("elevenlabs_voice_id"):
                        clone_map[c["speaker_label"]] = {
                            "engine": "elevenlabs", "voice_id": c["elevenlabs_voice_id"],
                            "api_key": el_key_for_dub,
                        }
                    elif c["ref_audio_filename"]:
                        clone_map[c["speaker_label"]] = {
                            "ref_audio": os.path.join(ddir, c["ref_audio_filename"]),
                            "ref_text": c["ref_text"] or "",
                        }
                # F5-TTS (voice cloning without an ElevenLabs voice_id) is the
                # only locally-run, GPU-touching path here -- edge_tts is a free
                # online service and "offline" fallback TTS is CPU-only, so
                # this only takes a GPU slot when it's actually needed.
                _uses_f5tts = any("engine" not in v for v in clone_map.values())
                background_jobs.start_process_job(
                    _dub_job_id, dub_module.build_track_subprocess_worker,
                    args=(_copy_lines(st.session_state.lines), ddir, voice_map, "en-US-AvaNeural",
                          clone_map, tts_engine, content_mode == "novel_narration"),
                    gpu_touching=_uses_f5tts, description=f"Dub generation ({_drama_label(drama)})")
                st.info("Generating in the background -- come back here for progress or to "
                        "Cancel. Safe to switch tabs or use other dramas meanwhile.")
                st.rerun()

            if _dub_job:
                if _dub_job["status"] == "queued":
                    st.info(_dub_job.get("message") or "Waiting for the GPU...")
                elif _dub_job["status"] == "running":
                    st.info("Generating... (no live progress while this runs as its own "
                            "process, but Cancel below genuinely stops it)")
                    dc1, dc2 = st.columns(2)
                    if dc1.button("🔄 Refresh progress", key=f"refresh_dub_{picked_id}"):
                        st.rerun()
                    if dc2.button("✖ Cancel", key=f"cancel_dub_{picked_id}"):
                        background_jobs.request_cancel(_dub_job_id)
                        st.rerun()
                    st.caption("Cancelling keeps whatever line clips already finished writing -- "
                              "the next run reuses them instead of starting over.")
                elif _dub_job["status"] == "cancelled":
                    st.warning("Generation was stopped. Already-generated clips were kept -- "
                              "click Generate again to pick up where it left off.")
                    background_jobs.clear_job(_dub_job_id)
                elif _dub_job["status"] == "error":
                    st.error(f"Generation failed: {_dub_job['error']}. Check ffmpeg / edge-tts / "
                             "piper-tts / f5-tts install (see README).")
                    background_jobs.clear_job(_dub_job_id)
                elif _dub_job["status"] == "done":
                    _dub_result = _dub_job.get("result") or {}
                    _dub_fields = (("dub_filename", "start", "end") if content_mode == "novel_narration"
                                  else ("dub_filename",))
                    db.save_lines(picked_id, _dub_result["lines"], fields=_dub_fields)
                    st.session_state.lines = db.load_line_objects(picked_id)
                    db.update_drama(picked_id, status="dubbed")
                    dub_errors = _dub_result.get("errors") or []
                    if dub_errors:
                        failed_nums = [e["line_idx"] + 1 for e in dub_errors]
                        st.warning(f"Generated with {len(dub_errors)} line failure(s) -- lines "
                                  f"{failed_nums} are silent in the track. Already-generated clips were "
                                  f"kept; click Generate again to retry just the missing ones.")
                    else:
                        st.success("Track generated." + (" Line timings updated to match narration audio -- "
                                   "re-download the .srt below to stay in sync." if content_mode == "novel_narration" else ""))
                    _dub_out_path = _dub_result["out_path"]
                    with open(_dub_out_path, "rb") as f:
                        st.download_button(f"Download {os.path.basename(_dub_out_path)}", f.read(),
                                            file_name=os.path.basename(_dub_out_path),
                                            key=f"dub_download_{picked_id}")
                    background_jobs.clear_job(_dub_job_id)

        with st.expander("9. 💾 Export subtitles", expanded=False):
            _total_lines = len(st.session_state.lines)
            _zh_filled = sum(1 for ln in st.session_state.lines if ln.zh.strip())
            _en_filled = sum(1 for ln in st.session_state.lines if ln.en.strip())

            # drama["translation_engine"] is whichever engine most recently
            # produced this drama's current lines (set alongside them in
            # run_translate_job) -- not a per-line record, but the same
            # signal the rest of the app already uses as "this drama's
            # engine" (e.g. the picker's own default above).
            _test_mode_output = drama.get("translation_engine") == "test_offline" and _en_filled > 0
            if _test_mode_output:
                st.warning("🧪 Some lines were produced by **Test mode** -- fake placeholder text, "
                          "not a real translation. Don't ship these subtitles; re-translate with a "
                          "real engine first.")

            # A timed-but-textless .srt is technically valid and gives no error --
            # it just looks broken when you open it. Say so before the download
            # happens rather than after someone's confused by an empty file.
            if _en_filled == 0:
                st.warning(f"⚠️ No lines are translated yet (0/{_total_lines}). The English and "
                          f"bilingual exports below will have correct timing but blank text. "
                          f"Run **Translate all lines** above first — or use the free "
                          f"`test_offline` engine to check the export pipeline without spending "
                          f"anything.")
            elif _en_filled < _total_lines:
                st.caption(f"ℹ️ {_total_lines - _en_filled} of {_total_lines} lines aren't "
                          f"translated yet — those will export with blank English text.")
            if _zh_filled == 0:
                st.error(f"⚠️ No source text on any line (0/{_total_lines}) — alignment may not "
                        f"have completed. Chinese/bilingual exports will be entirely blank.")

            _existing_notes = db.list_translation_notes(picked_id)
            _include_notes_inline = st.checkbox(
                "Include translation notes inline (idioms, wordplay, meaningful names)",
                value=False, disabled=not _existing_notes,
                help="Appends each note (e.g. \"Qijutang: lit. 'Hall of Sitting Together', used "
                     "as a joke\") in brackets on the subtitle line it's about -- for a note to "
                     "reach someone watching the exported video, not just the in-app Reader. "
                     "Generate notes first under Translation notes above."
                     if _existing_notes else
                     "No translation notes recorded yet -- generate some under Translation notes "
                     "above (section 5) to enable this.")
            _notes_by_idx = tguide.group_notes_by_line(_existing_notes) if _include_notes_inline else None

            _default_base_name = _sanitize_filename(drama.get("title_en") or drama.get("title_zh") or "export")
            _base_name = st.text_input(
                "Base filename (optional)", value="", placeholder=_default_base_name,
                help="Used for every download below, e.g. \"my_title_english.srt\". Leave blank "
                     "to use the drama's title.", key=f"export_base_name_{picked_id}")
            _base_name = _sanitize_filename(_base_name) or _default_base_name

            # Never export an overlapping (invalid) cue: trimmed in the export
            # copies. Detecting this is read-only and safe to do on every
            # render, but writing the flag isn't -- this section renders on
            # every page load, so a write here happened unconditionally,
            # with no user action, straight from whatever st.session_state
            # .lines held at that moment. That's exactly how a stale-lines
            # bug elsewhere (e.g. the ones Step 6d just fixed) would reach
            # the database as bogus flags before anyone noticed. Flagging
            # for review now needs an explicit click.
            _export_lines, _overlaps = subtitle_formats.clamp_overlaps(st.session_state.lines)
            if _overlaps:
                _next_start = {a.idx: b.start for a, b in zip(st.session_state.lines,
                                                               st.session_state.lines[1:])}
                _unflagged = [ln for ln in st.session_state.lines
                              if ln.idx in _overlaps and not ln.flag]
                st.warning(f"⚠️ {len(_overlaps)} line(s) overlap the next one. The export trims them "
                           "so no player gets an invalid cue." +
                           (f" {len(_unflagged)} of them aren't flagged for review yet."
                            if _unflagged else
                            " They're flagged in Review & edit so you can fix the timing."))
                if _unflagged and st.button("🚩 Flag overlapping lines for review",
                                            key=f"flag_overlaps_{picked_id}"):
                    for ln in _unflagged:
                        ln.flag = subtitle_formats.OVERLAP_FLAG
                        ln.flag_note = subtitle_formats.overlap_note(ln, _next_start[ln.idx])
                    db.save_lines(picked_id, st.session_state.lines, fields=("flag", "flag_note"))
                    st.rerun()

            _dense = subtitle_formats.dense_lines(st.session_state.lines)
            if _dense:
                st.warning(f"⚠️ {len(_dense)} translated line(s) have more text than can comfortably "
                           "be read in the time they're on screen: "
                           + ", ".join(f"#{ln.idx + 1} ({cps:.0f}/s)" for ln, cps, _ in _dense[:8])
                           + (" …" if len(_dense) > 8 else "") + ".")
                if st.button("🚩 Flag these for review", key=f"flag_dense_{picked_id}"):
                    if subtitle_formats.flag_dense_lines(st.session_state.lines):
                        db.save_lines(picked_id, st.session_state.lines, fields=("flag", "flag_note"))
                    st.rerun()

            _sub_format = st.radio(
                "Format", ["SRT", "VTT", "ASS"], horizontal=True, key=f"sub_format_{picked_id}",
                help="SRT plays everywhere. VTT is for web players. ASS carries the style below "
                     "(font, colours, one colour per speaker) -- for styled subtitles in players "
                     "like mpv/VLC, or burned into the video.")
            _wrap_chars = None
            if st.checkbox("Split long lines", value=False, key=f"sub_wrap_{picked_id}",
                           help="Breaks a long subtitle onto several lines at a sentence or clause "
                                "break (or a space) -- never mid-word."):
                w1, w2 = st.columns(2)
                _wrap_chars = {
                    "en": w1.number_input("Max characters per line (English)", 10, 80,
                                          subtitle_formats.line_char_limit("en"),
                                          key=f"sub_wrap_en_{picked_id}"),
                    "zh": w2.number_input("Max characters per line (original)", 6, 60,
                                          subtitle_formats.line_char_limit(source_language),
                                          key=f"sub_wrap_zh_{picked_id}"),
                }

            st.markdown("**🎨 Subtitle style** — used by .ass files and burned-in video")
            _speaker_names = {c["speaker_label"]: c["character_name"]
                              for c in db.list_characters_with_series_names(picked_id)
                              if c.get("character_name")}
            _sub_style, _speaker_colors = _subtitle_style_controls(
                picked_id, st.session_state.lines, _speaker_names)

            def _subtitle_text(field):
                if _sub_format == "VTT":
                    return subtitle_formats.lines_to_vtt(_export_lines, field, _notes_by_idx, _wrap_chars)
                if _sub_format == "ASS":
                    return subtitle_formats.lines_to_ass(
                        _export_lines, _sub_style, field, _notes_by_idx,
                        speaker_colors=_speaker_colors, speaker_names=_speaker_names,
                        wrap_chars=_wrap_chars, title=drama.get("title_en") or drama.get("title_zh") or "")
                _src = subtitle_formats.wrap_lines(_export_lines, _wrap_chars)
                if field == "bilingual":
                    return lines_to_bilingual_srt(_src, notes_by_idx=_notes_by_idx)
                return lines_to_srt(_src, field, notes_by_idx=_notes_by_idx)

            _ext = _sub_format.lower()
            c1, c2, c3 = st.columns(3)
            c1.download_button(f"Download English .{_ext}", _subtitle_text("en"),
                                file_name=f"{_base_name}_english.{_ext}", disabled=(_en_filled == 0))
            c2.download_button(f"Download Chinese .{_ext}", _subtitle_text("zh"),
                                file_name=f"{_base_name}_chinese.{_ext}", disabled=(_zh_filled == 0))
            c3.download_button(f"Download Bilingual .{_ext}", _subtitle_text("bilingual"),
                                file_name=f"{_base_name}_bilingual.{_ext}",
                                disabled=(_zh_filled == 0 and _en_filled == 0))

            if content_mode == "novel_narration":
                st.caption("Novel/narration content -- also export as an EPUB for reading in any e-reader app.")
                if st.button("📚 Generate EPUB"):
                    import epub_io
                    try:
                        epub_path = os.path.join(ddir, "translated.epub")
                        epub_io.export_epub(st.session_state.lines, drama["title_en"] or drama["title_zh"] or "Untitled",
                                             drama.get("author", ""), epub_path, field="en")
                        with open(epub_path, "rb") as f:
                            st.download_button("Download .epub", f.read(), file_name=f"{_base_name}.epub")
                    except Exception as e:
                        st.error(f"EPUB export failed: {e}. Check `pip install ebooklib`.")

        source_video_path = None
        if drama.get("source_video_filename"):
            p = os.path.join(ddir, drama["source_video_filename"])
            if os.path.exists(p):
                source_video_path = p

        if source_video_path:
            with st.expander("10. 🎬 Export full subtitled episode", expanded=False):
                st.caption("Uses the original video you uploaded + your reviewed English subtitles.")
                if _test_mode_output:
                    st.warning("🧪 Some lines were produced by **Test mode** -- fake placeholder "
                              "text, not a real translation. Don't ship this video; re-translate "
                              "with a real engine first.")
                sub_style = st.radio(
                    "Subtitle style",
                    ["hardsub", "softsub"],
                    format_func=lambda s: "🔥 Burn-in (always visible, plays everywhere)"
                                 if s == "hardsub" else
                                 "🎚️ Soft subtitles (toggleable track, needs a compatible player)",
                    horizontal=False,
                )
                sub_language = st.selectbox("Which subtitles to export on video", ["English", "Bilingual", "Chinese"])
                _field_for = {"English": "en", "Bilingual": "bilingual", "Chinese": "zh"}
                _srt_src = subtitle_formats.wrap_lines(_export_lines, _wrap_chars)
                sub_text_map = {"English": lines_to_srt(_srt_src, "en", notes_by_idx=_notes_by_idx),
                                 "Bilingual": lines_to_bilingual_srt(_srt_src, notes_by_idx=_notes_by_idx),
                                 "Chinese": lines_to_srt(_srt_src, "zh", notes_by_idx=_notes_by_idx)}
                if sub_style == "hardsub":
                    st.caption("Burned in with the style from 9. Export subtitles above"
                               + (" -- as ASS, so each speaker keeps their own colour."
                                  if _sub_format == "ASS" else
                                  " (pick ASS there for one colour per speaker)."))

                if st.button("🎬 Generate subtitled episode"):
                    import video_export
                    out_ext = os.path.splitext(source_video_path)[1]
                    out_path = os.path.join(ddir, f"subtitled_episode{out_ext}")
                    try:
                        with st.spinner("Rendering subtitled video... this can take a while for long episodes."):
                            if sub_style == "hardsub" and _sub_format == "ASS":
                                video_export.burn_ass(source_video_path,
                                                      _subtitle_text(_field_for[sub_language]), out_path)
                            elif sub_style == "hardsub":
                                video_export.burn_subtitles(
                                    source_video_path, sub_text_map[sub_language], out_path,
                                    font_size=_sub_style["size"], font_color=_sub_style["primary"],
                                    outline_color=_sub_style["outline"], font_name=_sub_style["font"],
                                    bold=_sub_style["bold"], italic=_sub_style["italic"],
                                    outline_width=_sub_style["outline_width"],
                                    alignment=subtitle_formats.ALIGNMENTS[_sub_style["alignment"]])
                            else:
                                if out_ext.lower() not in (".mp4", ".mkv"):
                                    out_path = os.path.splitext(out_path)[0] + ".mp4"
                                video_export.mux_soft_subtitles(source_video_path, sub_text_map[sub_language], out_path)
                        st.success("Subtitled episode ready.")
                        _dl_name = f"{_base_name}_subtitled{os.path.splitext(out_path)[1]}"
                        with open(out_path, "rb") as f:
                            st.download_button(f"Download {_dl_name}", f.read(), file_name=_dl_name)
                    except Exception as e:
                        st.error(f"Video export failed: {e}. Check that ffmpeg (with libass for hardsub) is installed.")

                dub_track_path = os.path.join(ddir, "dub_track.wav")
                if os.path.exists(dub_track_path):
                    st.caption("An AI dub track exists for this drama -- you can also replace/mix the video's "
                              "audio with it below.")
                    keep_orig = st.checkbox("Mix original audio in quietly underneath (instead of full replace)")
                    if st.button("🔊 Export video with dub audio"):
                        import video_export
                        out_path = os.path.join(ddir, f"dubbed_episode{os.path.splitext(source_video_path)[1]}")
                        try:
                            with st.spinner("Rendering dubbed video..."):
                                video_export.replace_audio_with_dub(
                                    source_video_path, dub_track_path, out_path,
                                    keep_original_at_db=-20.0 if keep_orig else None,
                                )
                            st.success("Dubbed episode ready.")
                            _dl_name = f"{_base_name}_dubbed{os.path.splitext(out_path)[1]}"
                            with open(out_path, "rb") as f:
                                st.download_button(f"Download {_dl_name}", f.read(), file_name=_dl_name)
                        except Exception as e:
                            st.error(f"Video export failed: {e}")

            with st.expander("10b. 📱 Vertical/shorts export", expanded=False):
                st.caption(
                    "Renders a 9:16 vertical clip -- centre-cropped from the original video, with "
                    "your subtitle style from 9. Export subtitles burned in -- for shorts/reels/"
                    "TikTok. Additive: this doesn't replace the horizontal export above."
                )
                import video_export
                try:
                    _v_duration = video_export.probe_duration_seconds(source_video_path)
                except Exception as e:
                    _v_duration = None
                    st.warning(f"Couldn't read this video's duration ({e}) -- check that ffmpeg "
                               "is installed and the file isn't corrupted.")
                if _v_duration:
                    _clip_start, _clip_end = st.slider(
                        "Clip range (seconds)", 0.0, float(_v_duration),
                        value=(0.0, float(_v_duration)), key=f"vshort_range_{picked_id}",
                        help="Defaults to the whole episode -- narrow it to a single scene/moment "
                             "for an actual short-form clip.")
                    _crop_position = st.slider(
                        "Crop position (left ↔ right)", 0.0, 1.0, 0.5,
                        key=f"vshort_crop_{picked_id}",
                        help="Which part of the frame to keep after cropping to 9:16 -- 0.5 is "
                             "centred. No face/subject auto-detection here; adjust by eye.")
                    _clip_duration = max(_clip_end - _clip_start, 0.0)
                    if _clip_duration <= 0:
                        st.warning("Pick a range with a positive length.")
                    else:
                        _v_est = video_export.estimate_vertical_export(_clip_duration)
                        st.caption(_v_est["time_note"])
                        st.caption(_v_est["size_note"])
                        if _v_est["is_long"]:
                            st.warning(
                                f"⚠️ {video_export._fmt_mmss(_clip_duration)} is a long selection "
                                "for a vertical clip -- re-encoding a stretch this long is slow and "
                                "heavy. Consider narrowing the range to a shorter moment, or use "
                                "the horizontal export above for the full episode.")
                        if st.button("📱 Generate vertical clip", key=f"vshort_generate_{picked_id}"):
                            _clip_lines = subtitle_formats.lines_for_clip(
                                _export_lines, _clip_start, _clip_end)
                            _clip_ass = subtitle_formats.lines_to_ass(
                                _clip_lines, _sub_style, "en", speaker_colors=_speaker_colors,
                                speaker_names=_speaker_names, wrap_chars=_wrap_chars,
                                title=drama.get("title_en") or drama.get("title_zh") or "")
                            out_path = os.path.join(
                                ddir, f"vertical_clip{os.path.splitext(source_video_path)[1]}")
                            try:
                                with st.spinner("Rendering vertical clip... this can take a while."):
                                    video_export.render_vertical_clip(
                                        source_video_path, _clip_ass, out_path,
                                        start=_clip_start, end=_clip_end,
                                        crop_position=_crop_position)
                                st.success("Vertical clip ready.")
                                _dl_name = f"{_base_name}_vertical{os.path.splitext(out_path)[1]}"
                                with open(out_path, "rb") as f:
                                    st.download_button(f"Download {_dl_name}", f.read(), file_name=_dl_name)
                            except Exception as e:
                                st.error(f"Vertical export failed: {e}. Check that ffmpeg (with "
                                         "libass) is installed.")

        st.divider()
        with st.expander("📦 Export this drama as a package", expanded=False):
            st.caption(
                "Bundles everything for this one title -- metadata, all subtitle formats, the "
                "original audio/video, any dub/narration track, and the reference novel -- into a "
                "single zip. For archiving a finished drama or handing it off, without exporting "
                "your whole library."
            )
            if _test_mode_output:
                st.warning("🧪 Some lines were produced by **Test mode** -- fake placeholder text, "
                          "not a real translation. Don't ship this package; re-translate with a "
                          "real engine first.")
            if st.button("Build export package"):
                import export_package
                try:
                    pkg_path = os.path.join(ddir, "export_package.zip")
                    with st.spinner("Building package..."):
                        _, manifest = export_package.build_drama_export_package(
                            db, picked_id, pkg_path, lines_to_srt, lines_to_bilingual_srt, Line)
                    st.success(f"Package built with {len(manifest)} item(s).")
                    for m in manifest:
                        st.caption(f"• {m}")
                    with open(pkg_path, "rb") as f:
                        safe_title = re.sub(r"[^\w\- ]", "", drama["title_en"] or drama["title_zh"] or str(picked_id))
                        st.download_button("Download package .zip", f.read(),
                                            file_name=f"{safe_title}_package.zip")
                except Exception as e:
                    st.error(f"Package build failed: {e}")

        if st.button("Mark as exported"):
            db.update_drama(picked_id, status="exported")
            st.success("Marked exported.")
    else:
        st.info("Run **Transcribe & Align** above to get started on this drama.")

