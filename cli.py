"""
cli.py -- headless batch driver. Runs align + translate (+ optionally
dub) across your whole library, or a filtered subset, without opening
the Streamlit GUI. Meant for unattended overnight/background runs
across 50-100+ dramas.

Reliability: every per-drama step in a batch is isolated -- if one
drama fails (corrupt file, API error, whatever), it's logged and the
run continues to the next drama rather than crashing the whole batch.
Each command prints a summary at the end (N succeeded, M failed) and
lists which drama IDs failed, so you can re-run just those.

Examples:

  # Translate every drama that's been aligned but not yet translated
  python cli.py translate --status aligned --engine claude --api-key $ANTHROPIC_API_KEY

  # Align + translate a specific drama by id
  python cli.py run --id 12 --engine deepseek --api-key $DEEPSEEK_API_KEY

  # Generate AI dub tracks for every translated drama
  python cli.py dub --status translated

  # Re-detect speakers for one drama with 3 voices (no re-transcription;
  # lines you corrected by hand are kept unless --overwrite-manual)
  python cli.py diarize --id 12 --num-speakers 3 --hf-token $HF_TOKEN

  # List what's in the library and its status
  python cli.py list
"""

# Must run before any other import in this file -- see portable.py's own
# docstring (app.py does the same, as the literal first thing it does).
import portable
portable.activate_portable_mode()

import argparse
import contextlib
import os
import sys
import time
import traceback

import audio_preprocess
import db
import diagnostics
from core import (
    Line, split_user_transcript, transcribe_for_timing, align_transcript_to_timing,
    chunk_novel_text, lines_from_rows, release_gpu_models, WHISPER_MODELS,
    DEFAULT_WHISPER_SIZE, build_initial_prompt, combine_initial_prompt,
    extract_novel_excerpt_for_prompt, ModelDownloadError,
)
import translate_engines
import translation_guide as tguide
import bulk_translate
import raw_transcript
import adaptive_style
import emotion
import dub as dub_module


@contextlib.contextmanager
def _gpu_lock(description: str, poll_interval: float = 5.0):
    """Step 25w: cross-process "one GPU job at a time" guard, shared with
    the live Streamlit UI's background_jobs.py through the gpu_lock table
    in the shared library.db (see db.try_acquire_gpu_lock's own
    docstring) -- this module never imports background_jobs.py at all, so
    its own in-process guard (Step 5c) never covered a CLI run, and an
    overnight CLI batch could run concurrently with a GPU-touching job
    started from the live UI, competing for the same VRAM. Waits and
    retries rather than failing outright, matching this module's own
    "built for unattended overnight runs" framing -- yields the holder id
    a caller running a multi-drama batch under this lock can use to send
    its own periodic heartbeat_gpu_lock() calls, so a long batch doesn't
    look abandoned partway through. poll_interval is a test-only knob;
    real callers use the 5-second default."""
    holder = f"cli:{os.getpid()}"
    waited = False
    while not db.try_acquire_gpu_lock(holder, description):
        if not waited:
            _busy_with = db.gpu_lock_status()[1] or "another job"
            print(f"Waiting for the GPU -- busy with: {_busy_with}")
            waited = True
        time.sleep(poll_interval)
    try:
        yield holder
    finally:
        db.release_gpu_lock(holder)


def _run_batch(dramas, step_fn, label: str):
    """Runs step_fn(drama) for each drama, isolating failures so one
    bad drama doesn't stop the rest. Prints a summary at the end."""
    succeeded, failed = [], []
    for d in dramas:
        try:
            step_fn(d)
            succeeded.append(d["id"])
        except Exception as e:
            failed.append((d["id"], str(e)))
            print(f"\n#{d['id']} FAILED during {label}: {e}", file=sys.stderr)
            if os.environ.get("BAIHE_CLI_DEBUG"):
                traceback.print_exc()

    print(f"\n--- {label} summary: {len(succeeded)} succeeded, {len(failed)} failed ---")
    if failed:
        print("Failed drama IDs (re-run this command to retry just these, with --id):")
        for fid, err in failed:
            print(f"  #{fid}: {err}")
    return succeeded, failed


def cmd_narrate_prep(args):
    """Chunk + speaker-tag a novel-narration drama's text (no audio)."""
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status="not started")
    dramas = [d for d in dramas if d.get("content_mode") == "novel_narration"]
    engine = translate_engines.get_engine(
        args.engine, args.api_key, args.model,
        base_url=getattr(args, "ollama_url", None)) if args.api_key else None

    def step(d):
        ddir = db.drama_dir(d["id"])
        src = os.path.join(ddir, "novel_narration_source.txt")
        if not os.path.exists(src):
            print(f"#{d['id']} skipped: no novel_narration_source.txt found in {ddir}")
            return
        with open(src, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = chunk_novel_text(text)
        lines = [Line(idx=i, start=float(i), end=float(i) + 1.0, zh=c) for i, c in enumerate(chunks)]
        if engine:
            known = [c["character_name"] for c in db.list_characters(d["id"]) if c["character_name"]]
            # Labels come back keyed by each chunk's idx; a chunk with no
            # label defaults to "Narrator" -- never paired by list position.
            by_idx = translate_engines.tag_speakers_by_id({ln.idx: ln.zh for ln in lines}, engine, known)
            for ln in lines:
                ln.speaker = (by_idx.get(ln.idx) or "").strip() or "Narrator"
            for label in sorted({ln.speaker for ln in lines}):
                db.upsert_character(d["id"], label, character_name=label)
        else:
            for ln in lines:
                ln.speaker = "Narrator"
            db.upsert_character(d["id"], "Narrator", character_name="Narrator")
        db.save_lines(d["id"], lines)
        db.update_drama(d["id"], status="aligned")
        print(f"#{d['id']} prepared {len(lines)} narration chunks.")

    _run_batch(dramas, step, "narrate-prep")


def cmd_export_video(args):
    import video_export
    import subtitle_formats
    from core import lines_to_srt, lines_to_bilingual_srt

    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status="translated")

    def step(d):
        ddir = db.drama_dir(d["id"])
        if not d.get("source_video_filename"):
            print(f"#{d['id']} skipped: no source video on file.")
            return
        video_path = os.path.join(ddir, d["source_video_filename"])
        if not os.path.exists(video_path):
            print(f"#{d['id']} skipped: source video file missing on disk.")
            return
        rows = db.load_lines(d["id"])
        lines = [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"], en=r.get("en") or "",
                      sfx=bool(r.get("sfx"))) for r in rows]
        # Step 25d item 6: same clamp Workspace's own export already applies --
        # never burn in an overlapping (invalid) cue.
        lines, _ = subtitle_formats.clamp_overlaps(lines)

        # A timed-but-textless subtitle track burns in fine and produces no
        # error -- it just looks broken in the finished video. Refuse rather
        # than silently ship that.
        field_for_track = {"english": "en", "bilingual": "zh", "chinese": "zh"}[args.subs]
        filled = sum(1 for ln in lines if getattr(ln, field_for_track).strip())
        if filled == 0:
            print(f"#{d['id']} skipped: no {field_for_track} text on any line -- "
                  f"{'translate first' if field_for_track == 'en' else 'alignment may not have completed'}.")
            return
        if filled < len(lines):
            print(f"#{d['id']} warning: {len(lines) - filled}/{len(lines)} lines have no "
                  f"{field_for_track} text and will appear blank in the burned-in subtitles.")

        srt_text = {"english": lines_to_srt(lines, "en"), "bilingual": lines_to_bilingual_srt(lines),
                    "chinese": lines_to_srt(lines, "zh")}[args.subs]

        out_ext = os.path.splitext(video_path)[1]
        out_path = os.path.join(ddir, f"subtitled_episode{out_ext}")
        print(f"#{d['id']} rendering {args.style} video...")
        if args.style == "hardsub":
            video_export.burn_subtitles(video_path, srt_text, out_path)
        else:
            if out_ext.lower() not in (".mp4", ".mkv"):
                out_path = os.path.splitext(out_path)[0] + ".mp4"
            video_export.mux_soft_subtitles(video_path, srt_text, out_path)
        print(f"#{d['id']} exported: {out_path}")

    _run_batch(dramas, step, "export-video")


def cmd_list(args):
    dramas = db.list_dramas(status=args.status or "")
    for d in dramas:
        print(f"#{d['id']:<4} [{d['status']:<10}] {d['title_en'] or d['title_zh']}")
    print(f"\n{len(dramas)} drama(s)")


def _load_novel_reference(drama):
    ddir = db.drama_dir(drama["id"])
    if drama.get("novel_reference_filename"):
        p = os.path.join(ddir, drama["novel_reference_filename"])
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return None


def cmd_diarize(args):
    """Re-runs speaker detection on each drama's stored audio and re-labels
    its existing lines -- text and timing untouched, no ASR. Same as the
    Workspace's "Re-run speaker detection" button: hand-corrected speakers
    are kept unless --overwrite-manual is given."""
    import diarize
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas()
    hf_token = args.hf_token or os.environ.get("HF_TOKEN") or os.environ.get("BAIHE_HF_TOKEN")
    if not hf_token:
        print("Needs a Hugging Face token: --hf-token or the HF_TOKEN environment variable.")
        return

    def step(d):
        ddir = db.drama_dir(d["id"])
        audio_path = os.path.join(ddir, d["audio_filename"]) if d.get("audio_filename") else None
        if not audio_path or not os.path.exists(audio_path):
            print(f"#{d['id']} skipped: no audio file found in {ddir}")
            return
        lines = db.load_line_objects(d["id"])
        if not lines:
            print(f"#{d['id']} skipped: no lines yet (transcribe first).")
            return
        print(f"#{d['id']} detecting speakers...")
        db.heartbeat_gpu_lock(_gpu_holder)
        try:
            turns, model, embeddings = diarize.diarize(
                audio_path, hf_token, num_speakers=args.num_speakers or None,
                return_model=True, return_embeddings=True)
        finally:
            release_gpu_models()
        diarize.save_turns(ddir, turns, num_speakers=args.num_speakers or None, model=model,
                          embeddings=embeddings)
        result = diarize.merge_speakers(lines, turns, overwrite_manual=args.overwrite_manual)
        for label in sorted({ln.speaker for ln in lines if ln.speaker}):
            db.upsert_character(d["id"], label)
        db.save_lines(d["id"], lines, fields=("speaker", "speaker_manual"))
        print(f"#{d['id']} {result['changed']} line(s) relabelled with {model}"
              + (f"; kept {result['kept_manual']} hand-corrected line(s) "
                 f"(--overwrite-manual to replace them)." if result["kept_manual"] else "."))

    with _gpu_lock(f"CLI diarize ({len(dramas)} drama(s))") as _gpu_holder:
        _run_batch(dramas, step, "diarize")


def cmd_align(args):
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status="not started")

    def step(d):
        ddir = db.drama_dir(d["id"])
        audio_path = os.path.join(ddir, d["audio_filename"]) if d["audio_filename"] else None
        transcript_path = os.path.join(ddir, "transcript.txt")
        if not audio_path or not os.path.exists(audio_path):
            print(f"#{d['id']} skipped: no audio file found in {ddir}")
            return
        if not os.path.exists(transcript_path):
            print(f"#{d['id']} skipped: no transcript.txt found in {ddir} "
                  f"(place your Chinese transcript there)")
            return
        with open(transcript_path, "r", encoding="utf-8") as f:
            transcript_text = f.read()
        # UI parity (Step 25d item 10): this command used to always use
        # args.whisper_size (or its own hardcoded default), plain
        # character-diff alignment, and no recognition priming at all --
        # ignoring the drama's own saved Whisper size / alignment method
        # (Workspace's own "3. Recognition accuracy" section) and its
        # series glossary. (asr_backend_choice, the other setting in that
        # same section, only affects transcripts with no user-supplied
        # script -- this command always requires transcript.txt, so it
        # never applies here and there's nothing to read for it.)
        whisper_size = args.whisper_size or d.get("whisper_size") or DEFAULT_WHISPER_SIZE
        alignment_method = d.get("alignment_method") or "whisper_diff"
        glossary_terms = db.list_glossary_terms(d["series_id"]) if d.get("series_id") else []
        initial_prompt = build_initial_prompt(glossary_terms)
        raw_novel_path = os.path.join(ddir, "raw_novel_context.txt")
        if os.path.exists(raw_novel_path):
            with open(raw_novel_path, "r", encoding="utf-8") as f:
                initial_prompt = combine_initial_prompt(
                    initial_prompt, extract_novel_excerpt_for_prompt(f.read()))
        print(f"#{d['id']} aligning ({d['title_en'] or d['title_zh']})...")
        db.heartbeat_gpu_lock(_gpu_holder)
        segments = transcribe_for_timing(audio_path, whisper_size, language=d.get("source_language") or "zh",
                                         fast_mode=getattr(args, "fast", False), initial_prompt=initial_prompt)
        user_lines = split_user_transcript(transcript_text)
        if alignment_method == "qwen3_forced_align":
            try:
                import forced_align
                lines = forced_align.align_with_qwen3(
                    audio_path, user_lines, segments, language=d.get("source_language") or "zh")
            except (ImportError, ModelDownloadError, ValueError) as exc:
                print(f"#{d['id']} Qwen3 forced alignment unavailable ({exc}) -- using the "
                      "default character-alignment method for this run.")
                lines = align_transcript_to_timing(user_lines, segments)
        else:
            lines = align_transcript_to_timing(user_lines, segments)
        release_gpu_models()
        db.save_lines(d["id"], lines)
        # Same untouched-output record the Workspace transcription writes.
        raw_transcript.write_raw_transcript(
            ddir, segments, lines, backend="whisper", model=whisper_size,
            language=d.get("source_language") or "zh", mode="aligned_transcript")
        db.update_drama(d["id"], status="aligned")
        print(f"#{d['id']} aligned {len(lines)} lines.")

    with _gpu_lock(f"CLI align ({len(dramas)} drama(s))") as _gpu_holder:
        _run_batch(dramas, step, "align")


def cmd_translate(args):
    query_status = args.status or "aligned"
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status=query_status)
    engine = translate_engines.get_engine(
        args.engine, args.api_key, args.model, base_url=getattr(args, "ollama_url", None))
    # Step 74: UI parity -- Workspace's own Translate button builds this
    # same optional summary_engine before starting the job (defaulting to
    # local Ollama); a missing/unreachable one just skips the summary
    # rather than failing the translate command.
    summary_engine_choice = getattr(args, "episode_summary_engine", None) or "ollama"
    try:
        summary_engine = translate_engines.get_engine(
            summary_engine_choice, getattr(args, "episode_summary_api_key", None),
            base_url=getattr(args, "ollama_url", None) if summary_engine_choice == "ollama" else None)
    except Exception:
        summary_engine = None

    def step(d):
        rows = db.load_lines(d["id"])
        if not rows:
            print(f"#{d['id']} skipped: no aligned lines yet.")
            return
        lines = lines_from_rows(rows)
        novel_reference = _load_novel_reference(d)
        # UI parity: without these, a CLI-run translation skipped the
        # series glossary, craft/style guidelines, and locale entirely --
        # a real, confirmed gap between what the Workspace Translate
        # button sends and what this command sent for the same drama.
        glossary_terms = db.list_glossary_terms(d["series_id"]) if d.get("series_id") else None
        series_chars = db.list_series_characters(d["series_id"]) if d.get("series_id") else []
        drama_chars = db.list_characters_with_series_names(d["id"])
        # Step 25r: Workspace's own translate path also folds in the learned
        # style profile and per-line emotion guidance -- both DB-backed, so
        # (unlike the pronoun-default/genre-notes toggles, which only ever
        # live in browser session state) there's no structural reason for the
        # CLI to leave them out.
        _scope = f"series:{d['series_id']}" if d.get("series_id") else "global"
        _prof = db.get_style_profile(_scope)
        _learned = adaptive_style.profile_to_prompt_block(_prof["profile"]) if _prof else ""
        _emap = db.load_emotions(d["id"])
        _emotion_block = emotion.build_emotion_guidance(
            _emap, [ln.idx for ln in lines]) if _emap else ""
        style_preset = args.style_preset or (
            "novel" if d.get("content_mode") == "novel_narration" else "audio_drama")
        style_guidelines = tguide.build_style_guidelines(
            style_preset=style_preset, glossary_terms=glossary_terms,
            custom_notes="\n\n".join(b for b in (
                _learned, _emotion_block,
                tguide.build_character_gender_hints(series_chars, drama_chars)) if b))
        character_names = tguide.build_speaker_labels(drama_chars, series_chars)
        print(f"#{d['id']} translating {len(lines)} lines with {args.engine}"
              + (" (+ novel reference)" if novel_reference else "") + "...")
        _id_by_idx = {ln.idx: ln.id for ln in lines if getattr(ln, "id", None) is not None}
        # Same caps as the Workspace Translate job: per job (--cost-cap)
        # and per calendar month (--monthly-cap, or BAIHE_MONTHLY_CAP_USD).
        monthly_cap = getattr(args, "monthly_cap", None)
        cost_cap, refusal = translate_engines.resolve_cost_cap(
            getattr(args, "cost_cap", None), monthly_cap,
            db.get_month_spend() if monthly_cap else 0.0)
        if refusal:
            raise RuntimeError(refusal)
        cap_reached = {}
        def _progress(frac, did=d["id"]):
            if _gpu_holder:
                # Step 25w: --engine ollama holds the cross-process GPU
                # lock for this whole batch (see below) -- refreshed here,
                # on every batch's own progress tick, so a long run doesn't
                # look abandoned to another process before it's done.
                db.heartbeat_gpu_lock(_gpu_holder)
            print(f"  #{did}: {frac*100:.0f}%", end="\r")

        _, batch_errors = translate_engines.translate_lines_with_engine(
            lines, engine, drama_meta=d, style_note=args.style_note or "",
            novel_reference=novel_reference, force_retranslate=args.force,
            locale=args.locale, glossary_terms=glossary_terms,
            style_guidelines=style_guidelines, character_names=character_names,
            ollama_num_ctx_override=args.ollama_num_ctx,
            context_window=getattr(args, "context_window", 6),
            context_window_ahead=getattr(args, "context_window_ahead", 3),
            batch_size=getattr(args, "batch_size", 20),
            reflect=getattr(args, "reflect", False),
            notes_cb=lambda notes, did=d["id"]: db.save_translation_notes(
                did, notes, id_by_idx=_id_by_idx),
            progress_cb=_progress,
            # Same as the Workspace Translate job: writes `en` only.
            save_cb=lambda lines, did=d["id"]: db.save_lines(did, lines, fields=("en",)),
            usage_cb=lambda inp, out, cache_read=0, cache_write=0, did=d["id"]: db.log_usage(
                did, args.engine, getattr(engine, "model", args.engine), "translate", inp, out,
                translate_engines.estimate_cost_for_engine(engine, inp, out, cache_read, cache_write),
                cache_read_tokens=cache_read),
            cost_cap_usd=cost_cap,
            cap_cb=lambda spent: cap_reached.update(spent=spent),
        )
        # Same post-translate steps as the Workspace Translate job (Step 25c):
        # enforce_exact glossary terms, density flags, a saved version,
        # persisted batch errors -- and "translated" only once no line is
        # left, so the retry suggested below (default --status aligned)
        # still finds this drama.
        bulk_translate.finish_translation_run(
            d["id"], lines, engine, args.engine, style_preset, glossary_terms, batch_errors,
            summary_engine=summary_engine, summary_engine_choice=summary_engine_choice)
        if "spent" in cap_reached:
            print(f"\n#{d['id']} stopped at the spending cap after about ${cap_reached['spent']:.2f} "
                  f"-- finished lines were kept; re-run with a higher cap to continue.")
        elif batch_errors:
            print(f"\n#{d['id']} translated with {len(batch_errors)} batch failure(s) after "
                  f"backoff retries -- re-run this command to retry just the missing lines.")
        else:
            print(f"\n#{d['id']} translated.")

    # Step 25w: only --engine ollama actually touches the GPU here (every
    # other translate engine is a remote API call) -- the cross-process
    # lock only needs to guard that case, not every translate run.
    _gpu_ctx = (_gpu_lock(f"CLI translate --engine ollama ({len(dramas)} drama(s))")
               if args.engine == "ollama" else contextlib.nullcontext(None))
    with _gpu_ctx as _gpu_holder:
        _run_batch(dramas, step, "translate")


def cmd_dub(args):
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status="translated")

    def step(d):
        # Step 26c: original-language narration speaks ln.zh, so it needs
        # source text, not a translation -- matches the Workspace tab's
        # own dub button, which has no "must be translated first" gate.
        is_narration = d.get("content_mode") == "novel_narration"
        narrate_original = is_narration and (d.get("narration_language") or "translation") == "original"
        rows = db.load_lines(d["id"])
        if not rows or not any(r.get("zh" if narrate_original else "en") for r in rows):
            print(f"#{d['id']} skipped: {'no source text' if narrate_original else 'not translated yet'}.")
            return
        lines = lines_from_rows(rows)
        ddir = db.drama_dir(d["id"])

        source_lang = d.get("source_language") or "zh"
        default_voice_pool = (dub_module.DEFAULT_VOICE_POOL_BY_LANGUAGE.get(
            source_lang, dub_module.DEFAULT_VOICE_POOL) if narrate_original
            else dub_module.DEFAULT_VOICE_POOL)
        chars = db.list_characters(d["id"])
        voice_map = {c["speaker_label"]: c["tts_voice"] for c in chars if c.get("tts_voice")}
        offline_voice_map = {c["speaker_label"]: c["offline_voice"] for c in chars
                             if c.get("offline_voice")}
        clone_map = dub_module.clone_map_from_characters(
            chars, ddir, gpt_sovits_url=getattr(args, "gpt_sovits_url", None),
            ref_language=source_lang)
        speakers = {ln.speaker for ln in lines if ln.speaker}
        voice_map = dub_module.fill_missing_voices(voice_map, speakers, default_voice_pool)
        offline_voice_map = dub_module.fill_missing_voices(
            offline_voice_map, speakers, dub_module.DEFAULT_OFFLINE_VOICE_POOL)

        build_fn = dub_module.build_narration_track if is_narration else dub_module.build_dub_track
        stretch = {} if is_narration else dict(
            max_speedup=getattr(args, "max_speedup", None) or dub_module.DUB_MAX_SPEEDUP,
            max_slowdown=getattr(args, "max_slowdown", None) or dub_module.DUB_MAX_SLOWDOWN)
        narration_kwargs = (dict(narrate_original=narrate_original, source_language=source_lang)
                            if is_narration else {})
        keep_bg = bool(getattr(args, "keep_background", False)) and not is_narration
        bg_source = None
        if keep_bg:
            bg_source = (os.path.join(ddir, d["audio_filename"]) if d.get("audio_filename") else None)
            if not bg_source or not os.path.exists(bg_source):
                print(f"#{d['id']} skipped: --keep-background needs the drama's source audio.")
                return
        print(f"#{d['id']} generating {'narration' if is_narration else 'dub'} track...")

        # Step 25w: same clone_map_uses_local_model check the Workspace tab's
        # own Dub job uses to decide gpu_touching -- only some clone/TTS
        # backends actually load a local model onto the GPU (GPT-SoVITS,
        # OmniVoice, ...); edge-tts/cloud backends don't, and don't need to
        # wait on the cross-process GPU lock at all.
        _gpu_holder_box = [None]

        def _progress(frac, did=d["id"]):
            if _gpu_holder_box[0]:
                db.heartbeat_gpu_lock(_gpu_holder_box[0])
            print(f"  #{did}: {frac*100:.0f}%", end="\r")

        _dub_gpu_ctx = (_gpu_lock(f"CLI dub #{d['id']}")
                        if dub_module.clone_map_uses_local_model(clone_map)
                        else contextlib.nullcontext(None))
        with _dub_gpu_ctx as _gpu_holder_box[0]:
            out_path, dub_errors = build_fn(
                lines, ddir, voice_map, default_voice=default_voice_pool[0],
                character_clone_map=clone_map,
                emotion_map=db.load_emotions(d["id"]), offline_voice_map=offline_voice_map,
                tts_engine=getattr(args, "tts_engine", None) or "edge_tts",
                progress_cb=_progress,
                **stretch, **narration_kwargs,
            )
        if bg_source:
            # Step 95: same background mix the Dub API job does; a failed
            # separation keeps the plain dub track.
            try:
                dub_module.mix_original_background(
                    out_path, bg_source, ddir, backend=d.get("separation_backend") or "auto")
            except audio_preprocess.VocalSeparationError as exc:
                print(f"\n#{d['id']} background music not mixed: {exc}")
        # Narration rewrites every line's timing to match its audio -- same
        # fields the Workspace tab saves after narration.
        db.save_lines(d["id"], lines,
                      fields=("dub_filename", "start", "end") if is_narration else ("dub_filename",))
        db.update_drama(d["id"], status="dubbed")
        if is_narration and getattr(args, "m4b", False):
            m4b_path = dub_module.export_narration_m4b(
                lines, ddir, title=d.get("title_en") or d.get("title_zh"),
                narrate_original=narrate_original)
            print(f"\n#{d['id']} audiobook: {m4b_path}")
        if dub_errors:
            print(f"\n#{d['id']} track: {out_path} ({len(dub_errors)} line(s) silent due to "
                  f"synthesis failures -- re-run to retry just those; already-generated clips are reused.)")
        else:
            print(f"\n#{d['id']} track: {out_path}")

    _run_batch(dramas, step, "dub")


def cmd_inspect_line(args):
    """Step 58's "what happened here?" view, headless -- same real data
    the Workspace Review & edit tab's 🔍 What happened? button shows."""
    import debug_view
    drama = db.get_drama(args.id)
    if not drama:
        print(f"No drama #{args.id}")
        return
    lines = db.load_line_objects(args.id)
    line = next((ln for ln in lines if ln.idx == args.line - 1), None)
    if not line:
        print(f"No line #{args.line} in drama #{args.id} ({len(lines)} line(s) total)")
        return
    info = debug_view.explain_line(args.id, line, lines)
    print(f"Line #{args.line} -- {info['zh']}")
    print(f"  Translation: {info['en']}")
    print(f"  Speaker: {info['speaker'] or '—'}" + (" (manual)" if info["speaker_manual"] else ""))
    print(f"  Engine/model: {info['engine'] or '—'} / {info['model'] or '—'} ({info['engine_source']})")
    if info["flag"]:
        print(f"  Flag: {info['flag_reason']}" + (f" -- {info['flag_note']}" if info["flag_note"] else ""))
    if info["glossary_matches"]:
        print("  Glossary matches: " + ", ".join(
            f"{t['term_original']} -> {t['term_translation']}" for t in info["glossary_matches"]))
    print(f"  ({info['glossary_matches_note']})")
    if info["translation_notes"]:
        print("  Translation notes: " + "; ".join(
            f"{n['term']}: {n['note']}" for n in info["translation_notes"]))
    print(f"  ({info['context_window_note']})")


def cmd_run(args):
    """Align then translate a single drama in one shot."""
    cmd_align(args)
    cmd_translate(args)


def cmd_doctor(args):
    """Step 97: pre-flight one engine's credentials/reachability before
    committing a batch job to it -- catches a dead API key or an
    unreachable local Ollama server up front, with a real (but minimal,
    single-line) call, instead of discovering it mid-job."""
    result = diagnostics.check_engine_reachable(
        args.engine, args.api_key, args.model, getattr(args, "ollama_url", None))
    if result["ok"]:
        print(f"OK: {result['engine']} is reachable and responding.")
    else:
        print(f"FAILED: {result['engine']} -- {result['error']}")
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description="Headless batch driver for the drama library")
    sub = p.add_subparsers(dest="command", required=True)

    p_narrate = sub.add_parser("narrate-prep", help="Chunk + speaker-tag a novel-narration drama (no audio)")
    p_narrate.add_argument("--id", type=int, default=None)
    p_narrate.add_argument("--engine", default="claude", choices=list(translate_engines.ENGINES))
    p_narrate.add_argument("--api-key", default=None)
    p_narrate.add_argument("--model", default=None)
    p_narrate.add_argument("--ollama-url", default=None,
                           help="Base URL for a non-default Ollama server (e.g. remote/Docker).")
    p_narrate.set_defaults(func=cmd_narrate_prep)

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default=None)
    p_list.set_defaults(func=cmd_list)

    p_inspect = sub.add_parser("inspect-line", help="Step 58: \"what happened here?\" for one line")
    p_inspect.add_argument("--id", type=int, required=True)
    p_inspect.add_argument("--line", type=int, required=True, help="1-based line number")
    p_inspect.set_defaults(func=cmd_inspect_line)

    p_align = sub.add_parser("align")
    p_align.add_argument("--id", type=int, default=None)
    p_align.add_argument("--whisper-size", default=None, choices=list(WHISPER_MODELS),
                         help="Defaults to the drama's own saved choice (Workspace's own "
                              f"'3. Recognition accuracy'), or '{DEFAULT_WHISPER_SIZE}' if it "
                              "has none.")
    p_align.add_argument("--fast", action="store_true",
                         help="Batched decoding (~4x faster on a GPU, more VRAM)")
    p_align.set_defaults(func=cmd_align)

    p_diarize = sub.add_parser("diarize", help="Re-run speaker detection on stored audio (no re-transcription)")
    p_diarize.add_argument("--id", type=int, default=None)
    p_diarize.add_argument("--hf-token", default=None)
    p_diarize.add_argument("--num-speakers", type=int, default=0)
    p_diarize.add_argument("--overwrite-manual", action="store_true",
                           help="Also replace speakers you corrected by hand")
    p_diarize.set_defaults(func=cmd_diarize)

    p_translate = sub.add_parser("translate")
    p_translate.add_argument("--id", type=int, default=None)
    p_translate.add_argument("--status", default=None)
    p_translate.add_argument("--engine", default="claude", choices=list(translate_engines.ENGINES))
    p_translate.add_argument("--api-key", required=True)
    p_translate.add_argument("--model", default=None)
    p_translate.add_argument("--episode-summary-engine", default="ollama",
                             choices=list(translate_engines.ENGINES),
                             help="Step 74: engine for the once-per-episode running-summary call "
                                  "made after a drama finishes translating, fed forward as "
                                  "continuity context into the next episode of the same series. "
                                  "Defaults to local Ollama (a fixed once-per-episode cost); if "
                                  "it's unreachable, or a cloud engine is picked with no key, the "
                                  "summary is skipped rather than failing the translate run.")
    p_translate.add_argument("--episode-summary-api-key", default=None,
                             help="API key for --episode-summary-engine, if it isn't ollama.")
    p_translate.add_argument("--style-note", default=None)
    p_translate.add_argument("--style-preset", default=None,
                              choices=list(tguide.STYLE_PRESETS),
                              help="Matches the Workspace tab's own style-guidance preset -- "
                                   "affects phrasing/pacing guidance, not language or content. "
                                   "Defaults to the same per-content-mode preset Workspace picks "
                                   "(\"novel\" for a novel-narration drama, \"audio_drama\" "
                                   "otherwise) unless set explicitly.")
    p_translate.add_argument("--locale", default="en-US", choices=["en-US", "en-GB", "en-AU"])
    p_translate.add_argument("--force", action="store_true",
                              help="Re-translate everything, including lines that already have a translation")
    p_translate.add_argument("--ollama-num-ctx", type=int, default=None,
                              help="Override Ollama's context window size. Only ever raises it "
                                   "above the automatic per-prompt estimate, never below -- "
                                   "leave unset to size it automatically (recommended).")
    p_translate.add_argument("--ollama-url", default=None,
                             help="Base URL for a non-default Ollama server (e.g. remote/Docker).")
    p_translate.add_argument("--reflect", action="store_true",
                             help="Step 7 'High quality' Reflect mode: three passes per batch "
                                  "(faithful draft, critique, rewrite) instead of one -- costs "
                                  "about 3x as much. The critique is saved as a translation note "
                                  "per line.")
    p_translate.add_argument("--cost-cap", type=float, default=None,
                           help="Stop a drama's translation once its estimated spend reaches this "
                                "many USD (finished lines are kept).")
    p_translate.add_argument("--monthly-cap", type=float,
                           default=float(os.environ.get("BAIHE_MONTHLY_CAP_USD") or 0) or None,
                           help="Refuse to start / stop once this calendar month's logged spend "
                                "reaches this many USD. Defaults to BAIHE_MONTHLY_CAP_USD.")
    # Step 32: matches the Workspace tab's own three sliders -- this command
    # used to have no way to set any of them, always using
    # translate_lines_with_engine's own defaults (context_window=6,
    # context_window_ahead=3, batch_size=20).
    p_translate.add_argument("--context-window", type=int, default=6,
                           help="Lines of already-translated context shown from before each "
                                "batch (default 6). 0 turns this off.")
    p_translate.add_argument("--context-window-ahead", type=int, default=3,
                           help="Lines of source text shown from after each batch, to resolve "
                                "a reference that's only disambiguated later (default 3). 0 "
                                "turns this off.")
    p_translate.add_argument("--batch-size", type=int, default=20,
                           help="Lines translated per request (default 20). More lines per "
                                "request is cheaper/faster overall but a bigger single point "
                                "of failure.")
    p_translate.set_defaults(func=cmd_translate)

    p_dub = sub.add_parser("dub")
    p_dub.add_argument("--id", type=int, default=None)
    p_dub.add_argument("--max-speedup", type=float, default=None,
                       help="Dub (not narration): the most a line may be sped up to fit its "
                            f"original timing (default {dub_module.DUB_MAX_SPEEDUP}); past it the "
                            "line runs over instead")
    p_dub.add_argument("--max-slowdown", type=float, default=None,
                       help="Dub (not narration): the most a short line may be slowed toward its "
                            f"original timing (default {dub_module.DUB_MAX_SLOWDOWN}; 1 turns "
                            "slowing off)")
    p_dub.add_argument("--keep-background", action="store_true",
                       help="Dub (not narration): mix the original's background music/ambience "
                            "(the source audio minus its vocals, via the drama's separation "
                            "backend) back under the dub track")
    p_dub.add_argument("--tts-engine", default="edge_tts", choices=["edge_tts", "offline"],
                       help="Fallback TTS engine used where a character has no cloned voice "
                            "reference set (same choice as Workspace's own 8. AI dub / "
                            "narration section). Step 25d item 10: this command used to have "
                            "no such flag at all, so it could only ever use edge-tts.")
    p_dub.add_argument("--gpt-sovits-url", default=None,
                       help="GPT-SoVITS server for characters using it "
                            f"(default {dub_module.GPT_SOVITS_DEFAULT_URL})")
    p_dub.add_argument("--m4b", action="store_true",
                       help="For novel narration: also export an M4B audiobook with chapter markers")
    p_dub.set_defaults(func=cmd_dub)

    p_run = sub.add_parser("run")
    p_run.add_argument("--id", type=int, required=True)
    p_run.add_argument("--whisper-size", default=DEFAULT_WHISPER_SIZE)
    p_run.add_argument("--engine", default="claude", choices=list(translate_engines.ENGINES))
    p_run.add_argument("--api-key", required=True)
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--style-note", default=None)
    # cmd_run calls cmd_translate(args) directly, reusing this same
    # Namespace -- it needs everything cmd_translate itself does (--status,
    # --force, --style-preset, --locale), a real pre-existing gap this
    # surfaced: cmd_run has always raised AttributeError the moment it
    # reached cmd_translate, since these were never defined here.
    p_run.add_argument("--status", default=None)
    p_run.add_argument("--style-preset", default=None, choices=list(tguide.STYLE_PRESETS))
    p_run.add_argument("--locale", default="en-US", choices=["en-US", "en-GB", "en-AU"])
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--ollama-num-ctx", type=int, default=None)
    p_run.add_argument("--ollama-url", default=None)
    p_run.add_argument("--reflect", action="store_true")
    p_run.add_argument("--cost-cap", type=float, default=None,
                           help="Stop a drama's translation once its estimated spend reaches this "
                                "many USD (finished lines are kept).")
    p_run.add_argument("--monthly-cap", type=float,
                           default=float(os.environ.get("BAIHE_MONTHLY_CAP_USD") or 0) or None,
                           help="Refuse to start / stop once this calendar month's logged spend "
                                "reaches this many USD. Defaults to BAIHE_MONTHLY_CAP_USD.")
    p_run.set_defaults(func=cmd_run)

    p_export_video = sub.add_parser("export-video")
    p_export_video.add_argument("--id", type=int, default=None)
    p_export_video.add_argument("--style", default="hardsub", choices=["hardsub", "softsub"])
    p_export_video.add_argument("--subs", default="english", choices=["english", "bilingual", "chinese"])
    p_export_video.set_defaults(func=cmd_export_video)

    p_doctor = sub.add_parser("doctor", help="Step 97: pre-flight one engine's credentials/"
                              "reachability with a real, minimal translate call")
    p_doctor.add_argument("--engine", required=True, choices=list(translate_engines.ENGINES))
    p_doctor.add_argument("--api-key", default=None)
    p_doctor.add_argument("--model", default=None)
    p_doctor.add_argument("--ollama-url", default=None,
                          help="Base URL for a non-default Ollama server (e.g. remote/Docker).")
    p_doctor.set_defaults(func=cmd_doctor)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
