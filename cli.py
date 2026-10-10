"""
cli.py -- headless batch driver. Runs align + translate (+ optionally
dub) across your whole library, or a filtered subset, without opening
the web app. Meant for unattended overnight/background runs
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
  # ...or when only a range is known (2 to 4 voices)
  python cli.py diarize --id 12 --min-speakers 2 --max-speakers 4

  # Mark lines (ids from inspect-line) or a whole speaker as English;
  # --lang default puts them back to the title's language
  python cli.py set-language --id 12 --speaker SPEAKER_01 --lang en

  # Transcribe a title's audio (options are saved on the title, as in the app)
  python cli.py transcribe --id 12 --language zh --whisper-size large-v3 --diarize

  # Run Auto QC (no engine, no cost) on one title or the whole library
  python cli.py qc --id 12

  # The series glossary of title 12
  python cli.py glossary list --id 12
  python cli.py glossary add --id 12 --original 沈清疑 --translation "Shen Qingyi"
  python cli.py glossary import --id 12 terms.csv
  python cli.py glossary export --id 12 --output terms.csv
  python cli.py glossary remove --id 12 --term 沈清疑 --yes

  # List what's in the library and its status
  python cli.py list
"""

# Must run before any other import in this file -- see portable.py's own
# docstring (api/__main__.py does the same, before its other imports).
import portable
portable.activate_portable_mode()

import argparse
import contextlib
import os
import sys
import time
import traceback

import audio_preprocess
import core as core_module
import whisper_models as wm
import segment_splitting
import db
import ollama_unload
import diagnostics_report
from core import (
    Line, split_user_transcript, transcribe_for_timing, align_transcript_to_timing,
    chunk_novel_text, lines_from_rows, line_from_row,
)
from whisper_models import release_gpu_models, WHISPER_MODELS, DEFAULT_WHISPER_SIZE, ModelDownloadError
import subtitle_formats, glossary_io as gio
import translate_engines
import translation_guide as tguide
import raw_transcript
import dub as dub_module
import dub_narration as dn
import real_model_check_cli
import background_jobs
import cli_subtitle
import cli_timing
from jobs import job_store
from services import (dub_service, export_service, glossary_service, jobs_service, lines_service,
                      narration_service, review_extras_service, settings_service, transcribe_pipeline,
                      transcribe_service)
from services.narration_service import TAG_ENGINES
from lib.errors import DependencyUnavailableError, ServiceError


def _gemini_free_tier(engine_name: str) -> bool:
    """The saved Settings "Gemini free tier" toggle, for the gemini engine only."""
    return engine_name == "gemini" and settings_service.get_gemini_free_tier()


def _ollama_url(args):
    """--ollama-url when given, else the saved Settings Ollama URL."""
    return getattr(args, "ollama_url", None) or settings_service.resolve_key("ollama_url") or None


def _replace_drama_lines(drama_id: int, lines, snapshot_label: str) -> bool:
    """Full-sync lines with the same safeguards as the service paths
    (transcribe_service / narration_service): never let an empty result
    wipe an already-populated drama, cancel line-writing jobs, and save a
    history snapshot of the existing lines first. Returns False (and
    leaves the drama untouched) when the result was empty and lines exist."""
    existing = db.load_line_objects(drama_id)
    if not lines and existing:
        print(f"#{drama_id} produced no lines -- kept the existing {len(existing)} line(s).")
        return False
    background_jobs.cancel_line_jobs(drama_id)
    # cancel_line_jobs only sees this process's in-memory jobs; flag the
    # cross-process job_records rows too, so an API job running
    # on this drama notices the cancel. (Only queued/running rows change.)
    for prefix in background_jobs.LINE_WRITING_JOB_PREFIXES:
        job_store.request_cancel(f"{prefix}{drama_id}")
    if existing:
        db.save_line_history_snapshot(drama_id, existing, snapshot_label)
    db.save_lines(drama_id, lines)
    return True


@contextlib.contextmanager
def _gpu_lock(description: str, poll_interval: float = 5.0):
    """Cross-process GPU guard, shared with the live UI's
    background_jobs.py through the gpu_lock table in the shared library.db
    (see db.try_acquire_gpu_lock's own docstring). The CLI never starts
    background jobs, so the in-process guard never covers a CLI
    run; it takes a slot through background_jobs.try_take_gpu_slot, so the
    "GPU jobs at once" setting and its free-VRAM check apply to a CLI run
    the same as to the app's jobs. Waits and
    retries rather than failing outright, matching this module's own
    "built for unattended overnight runs" framing -- yields the holder id
    a caller running a multi-drama batch under this lock can use to send
    its own periodic heartbeat_gpu_lock() calls, so a long batch doesn't
    look abandoned partway through. poll_interval is a test-only knob;
    real callers use the 5-second default."""
    holder = f"cli:{os.getpid()}"
    waited = False
    while not background_jobs.try_take_gpu_slot(holder, description):
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
            err = translate_engines.redact_secrets(str(e))
            failed.append((d["id"], err))
            print(f"\n#{d['id']} FAILED during {label}: {err}", file=sys.stderr)
            if os.environ.get("BAIHE_CLI_DEBUG"):
                print(translate_engines.redact_secrets(traceback.format_exc()),
                      file=sys.stderr, end="")

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
    engine_name = args.engine or "claude"
    # Same rules as narration_service.start_narration_run: only engines
    # that can tag speakers, and a clear error (never a silent all-
    # "Narrator" result) when no key is configured.
    if engine_name not in TAG_ENGINES:
        print(f"Engine {engine_name!r} cannot tag speakers; use one of "
              f"{', '.join(TAG_ENGINES)}.", file=sys.stderr)
        sys.exit(2)
    api_key = args.api_key or ("local" if engine_name == "ollama"
                               else settings_service.resolve_key(engine_name))
    if not api_key:
        print(f"No {engine_name} key is configured. Set one in Settings first, "
              f"or pass --api-key.", file=sys.stderr)
        sys.exit(2)
    engine = translate_engines.get_engine(
        engine_name, api_key, args.model, free_tier=_gemini_free_tier(engine_name),
        base_url=_ollama_url(args) if engine_name == "ollama" else None)

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
        known = [c["character_name"] for c in db.list_characters(d["id"]) if c["character_name"]]
        # Same resume as the API job (narration_service).
        done, on_batch = narration_service.tagging_checkpoint(
            d["id"], text, engine_name, getattr(engine, "model", args.model), known,
            fresh=getattr(args, "fresh", False))
        if done:
            print(f"#{d['id']} resuming: {len(done)} of {len(lines)} chunks already tagged.")
        # Labels come back keyed by each chunk's idx; a chunk with no
        # label defaults to "Narrator" -- never paired by list position.
        by_idx = translate_engines.tag_speakers_by_id(
            {ln.idx: ln.zh for ln in lines}, engine, known,
            usage_cb=lambda inp, out, did=d["id"]: db.log_usage(
                did, engine_name, getattr(engine, "model", engine_name), "tag_speakers",
                inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)),
            done=done, on_batch=on_batch)
        for ln in lines:
            ln.speaker = (by_idx.get(ln.idx) or "").strip() or "Narrator"
        if not _replace_drama_lines(d["id"], lines, "before chunk & tag speakers"):
            return
        narration_service.finish_tagging_checkpoint(d["id"])
        # After the empty-result check, so an empty result changes nothing.
        for label in sorted({ln.speaker for ln in lines}) or ["Narrator"]:
            db.upsert_character(d["id"], label, character_name=label)
        db.update_drama(d["id"], status="aligned")
        print(f"#{d['id']} prepared {len(lines)} narration chunks.")

    _run_batch(dramas, step, "narrate-prep")


def _resolve_export_options(args):
    """(mode, preset) for export-video. --style accepts hardsub/softsub for
    backward compatibility; anything else is an ASS preset name."""
    mode = getattr(args, "mode", None)
    style = getattr(args, "style", None)
    if style in ("hardsub", "softsub"):
        mode, style = mode or style, None
    mode = mode or "hardsub"
    preset = None
    if style:
        presets = subtitle_formats.ASS_PRESETS
        matches = [n for n in presets if n.lower() == style.lower()]
        if not matches:
            print(f"Unknown style {style!r}. Valid styles: {', '.join(presets)}.",
                  file=sys.stderr)
            sys.exit(2)
        preset = matches[0]
    if mode == "softsub" and (preset or getattr(args, "no_speaker_colors", False)):
        print("--style and --no-speaker-colors only apply to burned-in (hardsub) video.",
              file=sys.stderr)
        sys.exit(2)
    return mode, preset


def cmd_export_video(args):
    import video_export
    from core import lines_to_srt, lines_to_bilingual_srt
    from services import export_service

    mode, preset = _resolve_export_options(args)
    # Burned-in video uses the same ASS the API/React export produces (styled,
    # one colour per speaker); --plain keeps the flat SRT burn.
    use_ass = mode == "hardsub" and not getattr(args, "plain", False)
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
        lines = [line_from_row(r) for r in db.load_lines(d["id"])]
        # Never burn in an overlapping (invalid) cue.
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

        out_ext = os.path.splitext(video_path)[1]
        out_path = os.path.join(ddir, f"subtitled_episode{out_ext}")
        print(f"#{d['id']} rendering {mode} video...")
        soft = not use_ass and mode != "hardsub"
        if soft:
            out_path = os.path.splitext(out_path)[0] + video_export.softsub_output_extension(video_path)
        # Render beside the final file and replace it only on success, so a
        # failed or interrupted run never leaves a partial file under the real name.
        tmp_path = os.path.splitext(out_path)[0] + ".partial" + os.path.splitext(out_path)[1]
        try:
            if use_ass:
                ass_text = export_service.generate_ass_text(
                    d["id"], field={"english": "en", "bilingual": "bilingual", "chinese": "zh"}[args.subs],
                    preset=preset or "Clean",
                    per_speaker_colors=not getattr(args, "no_speaker_colors", False))
                video_export.burn_ass(video_path, ass_text, tmp_path)
            else:
                srt_text = {"english": lines_to_srt(lines, "en"), "bilingual": lines_to_bilingual_srt(lines),
                            "chinese": lines_to_srt(lines, "zh")}[args.subs]
                if mode == "hardsub":
                    video_export.burn_subtitles(video_path, srt_text, tmp_path)
                else:
                    video_export.mux_soft_subtitles(video_path, srt_text, tmp_path)
            os.replace(tmp_path, out_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        print(f"#{d['id']} exported: {out_path}")

    _run_batch(dramas, step, "export-video")


def cmd_list(args):
    dramas = db.list_dramas(status=args.status or "")
    for d in dramas:
        print(f"#{d['id']:<4} [{d['status']:<10}] {d['title_en'] or d['title_zh']}")
    print(f"\n{len(dramas)} drama(s)")


def cmd_diarize(args):
    """Re-runs speaker detection on each drama's stored audio and re-labels
    its existing lines -- text and timing untouched, no ASR. Same as the
    Workspace's "Re-run speaker detection" button: hand-corrected speakers
    are kept unless --overwrite-manual is given."""
    import diarize
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas()
    hf_token = (args.hf_token or os.environ.get("HF_TOKEN") or os.environ.get("BAIHE_HF_TOKEN")
                or settings_service.resolve_key("hf_token"))
    if not hf_token:
        print("Needs a Hugging Face token: --hf-token, the HF_TOKEN environment variable, "
              "or one saved in Settings.")
        return
    try:
        num_speakers, min_speakers, max_speakers = diarize.validate_speaker_hints(
            args.num_speakers, getattr(args, "min_speakers", None),
            getattr(args, "max_speakers", None))
    except ValueError as exc:
        print(exc)
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
            run_info = {}
            last_beat = [time.monotonic()]

            def on_progress(frac, message):
                # A long CPU retry must not outlast the GPU lock's stale window.
                if message == diarize.OOM_FALLBACK_MESSAGE:
                    print(f"#{d['id']} {message}.")
                if time.monotonic() - last_beat[0] > 30:
                    last_beat[0] = time.monotonic()
                    db.heartbeat_gpu_lock(_gpu_holder)

            started = time.monotonic()
            turns, model, embeddings = diarize.diarize(
                audio_path, hf_token, num_speakers=num_speakers,
                return_model=True, return_embeddings=True,
                use_gpu=settings_service.get_use_gpu(),
                min_speakers=min_speakers, max_speakers=max_speakers, run_info=run_info,
                on_progress=on_progress)
        finally:
            release_gpu_models()
        # Same history the app's speaker-detection estimate reads.
        transcribe_service.record_diarize_speed(
            run_info.get("device") == "cuda", transcribe_pipeline._audio_duration_seconds(audio_path),
            time.monotonic() - started)
        if run_info.get("fell_back_to_cpu"):
            print(f"#{d['id']} WARNING: {diarize.fallback_done_message(run_info.get('fallback_kind'))}")
        diarize.save_turns(ddir, turns, num_speakers=num_speakers, model=model,
                          embeddings=embeddings, min_speakers=min_speakers,
                          max_speakers=max_speakers, device=run_info.get("device"))
        result = diarize.merge_speakers(lines, turns, overwrite_manual=args.overwrite_manual)
        for label in sorted({ln.speaker for ln in lines if ln.speaker}):
            db.upsert_character(d["id"], label)
        db.save_lines(d["id"], lines, fields=("speaker", "speaker_manual"))
        print(f"#{d['id']} {result['changed']} line(s) relabelled with {model} "
              f"on {run_info.get('device', 'cpu')}"
              + (f"; kept {result['kept_manual']} hand-corrected line(s) "
                 f"(--overwrite-manual to replace them)." if result["kept_manual"] else "."))

    with _gpu_lock(f"CLI diarize ({len(dramas)} drama(s))") as _gpu_holder:
        _run_batch(dramas, step, "diarize")


def _qwen3_missing(exc) -> RuntimeError:
    """Same as the API (dependency_missing): a drama saved to use Qwen3
    forced alignment fails rather than quietly using a method nobody chose."""
    detail = translate_engines.redact_secrets(str(exc))
    return RuntimeError(
        "Qwen3-ASR isn't installed, so Qwen3 forced alignment can't run. "
        "Install qwen-asr from Diagnostics (or: pip install qwen-asr torch), "
        f"or change this drama's alignment method. ({detail})")


def _read_transcript_option(args):
    """The --transcript text (FILE, or - for stdin), or None when not given.
    One transcript can only belong to one drama, so it needs --id."""
    source = getattr(args, "transcript", None)
    if not source:
        return None
    if not args.id:
        print("--transcript needs --id: one transcript can't be aligned to several dramas.",
              file=sys.stderr)
        sys.exit(2)
    try:
        if source == "-":
            return sys.stdin.read()
        with open(source, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Couldn't read the transcript: {translate_engines.redact_secrets(str(exc))}",
              file=sys.stderr)
        sys.exit(2)


def cmd_align(args):
    given_transcript = _read_transcript_option(args)
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status="not started")

    def step(d):
        ddir = db.drama_dir(d["id"])
        audio_path = os.path.join(ddir, d["audio_filename"]) if d["audio_filename"] else None
        transcript_path = os.path.join(ddir, "transcript.txt")
        if not audio_path or not os.path.exists(audio_path):
            print(f"#{d['id']} skipped: no audio file found in {ddir}")
            return
        if given_transcript is not None:
            transcript_text = given_transcript
        elif os.path.exists(transcript_path):
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript_text = f.read()
        else:
            print(f"#{d['id']} skipped: no transcript. Pass --transcript FILE (or - for stdin), "
                  f"or place your Chinese transcript at {transcript_path}")
            return
        # UI parity: use the drama's saved Whisper size and alignment
        # method plus its series glossary for recognition priming, as the
        # app does. (asr_backend_choice is not read: it only affects
        # transcripts with no user-supplied script, and this command always
        # requires transcript.txt.)
        whisper_size = args.whisper_size or transcribe_service.stored_whisper_size(d)
        alignment_method = d.get("alignment_method") or "whisper_diff"
        if alignment_method == "qwen3_forced_align":
            # Checked before any transcription, as the API does.
            try:
                transcribe_service.require_qwen3_packages("Qwen3 forced alignment")
            except DependencyUnavailableError as exc:
                raise _qwen3_missing(exc) from exc
        # Glossary names plus raw-novel excerpt, shared with the API path.
        initial_prompt = transcribe_service.build_auto_initial_prompt(d["id"])
        # Same saved tuning the service's transcribe job uses
        # (transcribe_service.get_transcribe_config); --fast still wins.
        cfg = transcribe_service.get_transcribe_config(d["id"])
        fast = getattr(args, "fast", False) or cfg["whisper_fast_mode"]
        use_gpu = settings_service.get_use_gpu()
        language = d.get("source_language") or "zh"
        print(f"#{d['id']} aligning ({d['title_en'] or d['title_zh']})...")
        db.heartbeat_gpu_lock(_gpu_holder)
        if cfg["separate_vocals_first"]:
            audio_path = audio_preprocess.separate_vocals(
                audio_path, os.path.join(os.path.dirname(audio_path), "vocals.wav"),
                backend=cfg["separation_backend"], use_gpu=use_gpu)
        use_groq = bool(d.get("use_groq"))
        groq_api_key = settings_service.resolve_key("groq") if use_groq else None
        if use_groq and not groq_api_key:
            # Same refusal as the app, before any audio is processed.
            raise RuntimeError(
                "use_groq is on but no Groq API key is configured. Set one in Settings first.")
        local_model_path = settings_service.get_whisper_model_path()
        app_gpu_settings = raw_transcript.current_gpu_app_settings()
        gpu_fallback = []
        started = time.monotonic()
        if use_groq:
            try:
                segments = core_module.transcribe_with_groq(audio_path, language, groq_api_key)
            except core_module.GroqTranscriptionError as exc:
                raise RuntimeError(
                    f"Groq transcription failed: {translate_engines.redact_secrets(str(exc))}"
                ) from exc
        else:
            segments = transcribe_for_timing(
                audio_path, whisper_size, language=language, use_gpu=use_gpu,
                local_model_path=local_model_path,
                fast_mode=fast,
                initial_prompt=initial_prompt, beam_size=cfg["beam_size"],
                min_silence_duration_ms=cfg["min_silence_ms"], vad_threshold=cfg["effective_vad_threshold"],
                sensitivity_preset=cfg["sensitivity_preset"],
                hallucination_silence_sec=cfg["hallucination_silence_sec"],
                repeat_guard=cfg["whisper_repeat_guard"],
                on_gpu_fallback=lambda exc: gpu_fallback.append(wm.short_reason(exc)))
        if "ollama_notice" in (notice := ollama_unload.take_notice_result()):
            print(f"#{d['id']} WARNING: {notice['ollama_notice']}")
        if not segments:
            release_gpu_models()
            print(f"#{d['id']} skipped: no speech was found in the audio, so nothing was "
                  f"aligned and the existing lines were left alone.")
            return
        if not use_groq:
            # The model's own load can fall back to CPU before any inference runs.
            load_error = wm.get_whisper_device_info(
                whisper_size, use_gpu=use_gpu, local_model_path=local_model_path).get("gpu_error")
            if load_error:
                gpu_fallback.insert(0, load_error)
        if gpu_fallback:
            print(f"#{d['id']} WARNING: "
                  f"{wm.gpu_fallback_notice('Transcription', gpu_fallback[0])}")
        elif segments and not use_groq and not fast:
            # Same history the app's estimate reads; fast mode runs at another speed.
            transcribe_service.record_transcribe_speed(
                whisper_size, bool(use_gpu), transcribe_pipeline._audio_duration_seconds(audio_path),
                time.monotonic() - started)
        if cfg["realign_long_segments"] and segments:
            import word_align
            try:
                segments = word_align.realign_oversized_segments(
                    segments, audio_path, language,
                    chinese_script=d.get("chinese_script") or "simplified")
            except word_align.WordAlignError as exc:
                print(f"#{d['id']} long-line realignment skipped: {exc}")
        user_lines = split_user_transcript(transcript_text)
        try:
            if alignment_method == "qwen3_forced_align":
                try:
                    import forced_align
                    lines = forced_align.align_with_qwen3(
                        audio_path, user_lines, segments, language=language, use_gpu=use_gpu,
                        on_gpu_fallback=lambda exc: print(
                            f"#{d['id']} WARNING: " + wm.gpu_fallback_notice(
                                "Qwen3 forced alignment", wm.short_reason(exc))))
                except ImportError as exc:
                    raise _qwen3_missing(exc) from exc
                except ModelDownloadError as exc:
                    detail = translate_engines.redact_secrets(str(exc))
                    raise RuntimeError(
                        f"Qwen3 forced alignment model download failed: {detail}") from exc
                except ValueError as exc:
                    print(f"#{d['id']} Qwen3 forced alignment couldn't align this transcript "
                          f"({translate_engines.redact_secrets(str(exc))}) -- using the default character-alignment method for this run.")
                    lines = align_transcript_to_timing(user_lines, segments)
            else:
                lines = align_transcript_to_timing(user_lines, segments)
        finally:
            # Also on a failure: _run_batch carries on with the next drama.
            release_gpu_models()
        if not _replace_drama_lines(d["id"], lines, "before re-transcribe"):
            return
        # Same untouched-output record the Workspace transcription writes.
        raw_transcript.write_raw_transcript(
            ddir, segments, lines, backend="whisper", model=whisper_size,
            language=language, mode="aligned_transcript",
            settings=raw_transcript.build_run_settings(
                asr_backend="whisper", whisper_size=whisper_size,
                local_model_path=local_model_path, language=language,
                transcript_mode="have_transcript", alignment_method=alignment_method,
                min_silence_ms=cfg["min_silence_ms"], vad_threshold=cfg["effective_vad_threshold"],
                sensitivity_preset=cfg["sensitivity_preset"], beam_size=cfg["beam_size"],
                hallucination_silence_sec=cfg["hallucination_silence_sec"],
                whisper_fast_mode=fast,
                whisper_repeat_guard=cfg["whisper_repeat_guard"], use_groq=use_groq,
                separate_vocals_first=cfg["separate_vocals_first"],
                separation_backend=cfg["separation_backend"],
                realign_long_segments=cfg["realign_long_segments"],
                use_gpu=use_gpu, gpu_fallback_msgs=gpu_fallback,
                initial_prompt=initial_prompt, **app_gpu_settings))
        db.update_drama(d["id"], status="aligned")
        print(f"#{d['id']} aligned {len(lines)} lines.")

    with _gpu_lock(f"CLI align ({len(dramas)} drama(s))") as _gpu_holder:
        _run_batch(dramas, step, "align")


def cmd_dub(args):
    # Up front, as the API does: a bad pacing limit or missing TTS package
    # would otherwise fail the same way for every drama in the batch.
    tts_engine = getattr(args, "tts_engine", None) or dub_module.DEFAULT_CLONE_ENGINE
    try:
        max_speedup, max_slowdown = dub_service.resolve_pacing_limits(
            getattr(args, "max_speedup", None), getattr(args, "max_slowdown", None))
        dub_service.require_can_generate(tts_engine, [])
    except ServiceError as e:
        raise SystemExit(f"dub: {e.message}")
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status="translated")

    def step(d):
        # Original-language narration speaks ln.zh, so it needs
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
        chars = db.list_characters(d["id"])
        # Raised, not skipped, so _run_batch counts the drama as failed.
        dub_service.require_can_generate(tts_engine, chars, narrate_original, source_lang)
        clone_map = dub_module.clone_map_from_characters(
            chars, ddir, default_engine=tts_engine,
            speaker_labels={ln.speaker or None for ln in lines})

        build_fn = dub_service.track_builder(is_narration)
        stretch = {} if is_narration else dict(
            max_speedup=max_speedup, max_slowdown=max_slowdown)
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

        # Same clone_map_uses_local_model check the Workspace tab's
        # own Dub job uses to decide gpu_touching -- only some clone/TTS
        # backends actually load a local model onto the GPU; only an empty map
        # skips the cross-process GPU lock.
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
                lines, ddir, clone_map,
                progress_cb=_progress,
                **stretch, **narration_kwargs,
            )
        if bg_source:
            # Same background mix the Dub API job does; a failed
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
            m4b_path = dn.export_narration_m4b(
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
    """The "what happened here?" view, headless -- same real data
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
    print(f"Line #{args.line} (id {line.id}) -- {info['zh']}")
    print(f"  Translation: {info['en']}")
    print(f"  Speaker: {info['speaker'] or '—'}" + (" (manual)" if info["speaker_manual"] else ""))
    print(f"  Language: {line.lang or 'title language'}")
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


def cmd_set_language(args):
    """Sets the spoken language of some lines, same service as the
    Workspace's "Set language" action. --lang default (or "") reverts the
    lines to the title's own language."""
    lang = None if args.lang.strip().lower() == "default" else args.lang
    try:
        result = lines_service.set_lines_lang(
            args.id, lang,
            line_ids=args.lines,
            speaker=args.speaker)
    except ServiceError as e:
        print(f"Error: {e.message}")
        sys.exit(1)
    label = core_module.normalize_line_lang(lang) or "title language"
    msg = f"#{args.id}: set {label} on {result['updated']} line(s)"
    if result["skipped_ids"]:
        msg += f"; skipped {len(result['skipped_ids'])} id(s) not in this drama: " + \
            ", ".join(str(i) for i in result["skipped_ids"])
    print(msg)


def cmd_clean_en(args):
    """The Review stage's "Fix common errors" pass (deterministic, no AI):
    a preview by default, --apply saves it after a line-history snapshot."""
    try:
        preview = review_extras_service.preview_en_cleanup(args.id)
        for rule in preview["rules"]:
            print(f"  {rule['label']}: {rule['lines']} line(s)")
        shown = args.show if args.show is not None else 5
        for ch in preview["changes"][:shown]:
            print(f"  #{ch['idx'] + 1}: {ch['before']!r} -> {ch['after']!r}")
        print(f"#{args.id}: {preview['lines_changed']} of {preview['lines_scanned']} line(s) would change"
              + (f"; {preview['lines_skipped']} over-long line(s) skipped" if preview["lines_skipped"] else ""))
        if args.apply and preview["lines_changed"]:
            result = review_extras_service.apply_en_cleanup(args.id, preview["plan_hash"])
            print(f"#{args.id}: cleaned {result['applied']} line(s)"
                  + (f"; {result['stale']} edited meanwhile and kept" if result["stale"] else "")
                  + " (previous text is in Line history)")
    except ServiceError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


_JOB_POLL_SECONDS = 1.0


def _wait_for_job(job_id: str, label: str, poll_interval: float = _JOB_POLL_SECONDS):
    """Blocks until a job this process started has ended, echoing its stage
    text as it changes. Returns (outcome, message, result), the first two as
    the app's job list derives them (jobs_service.derive_outcome)."""
    last = None
    while True:
        job = background_jobs.get_status(job_id)
        if job is None:
            return "failed", "The job disappeared before it finished.", {}
        status = job.get("status")
        if status in ("done", "error", "cancelled"):
            result = job.get("result")
            outcome, message = jobs_service.derive_outcome(
                status, job.get("error"), jobs_service.project_result(result))
            return outcome, message, result if isinstance(result, dict) else {}
        note = (job.get("message") or "").strip()
        if note and note != last:
            print(f"{label}: {translate_engines.redact_secrets(note)}", flush=True)
            last = note
        time.sleep(poll_interval)


def cmd_transcribe(args):
    """Transcribes (or aligns --transcript against) one title's stored audio
    through the Workspace's Transcribe service. Tuning options are saved
    on the title."""
    tuning = dict(
        whisper_size=args.whisper_size, asr_backend_choice=args.asr_backend,
        beam_size=args.beam_size, min_silence_ms=args.min_silence_ms,
        min_pause_sec=args.min_pause,
        vad_threshold=args.vad_threshold, sensitivity_preset=args.sensitivity,
        separation_backend=args.separation_backend,
        separate_vocals_first=args.separate_vocals)
    transcript_text = _read_transcript_option(args)
    try:
        if any(v is not None for v in tuning.values()):
            transcribe_service.update_transcribe_config(args.id, **tuning)
        job = transcribe_service.start_transcribe_run(
            args.id, source_language=args.language, chinese_script=args.chinese_script,
            transcript_text=transcript_text, run_diarize=args.diarize,
            expected_speakers=args.num_speakers, min_speakers=args.min_speakers,
            max_speakers=args.max_speakers, initial_prompt=args.initial_prompt or "",
            extra_names=args.extra_names or "")
    except ServiceError as e:
        raise SystemExit(f"transcribe: {translate_engines.redact_secrets(e.message)}")
    label = f"#{args.id}"
    print(f"{label} transcribing (GPU setting: "
          f"{'on' if settings_service.get_use_gpu() else 'off'})...", flush=True)
    outcome, message, result = _wait_for_job(job["job_id"], label)
    if result.get("device"):
        print(f"{label} device: {result['device']}")
    if result.get("device_notice"):
        print(f"{label} NOTICE: {result['device_notice']}")
    for key in ("coverage_warning", "ollama_notice", "word_align_error", "forced_align_error",
                "asr_backend_notice"):
        if result.get(key):
            print(f"{label} WARNING: {translate_engines.redact_secrets(str(result[key]))}")
    if outcome not in ("ok", "partial"):
        print(f"{label} {outcome}: {translate_engines.redact_secrets(message or '')}",
              file=sys.stderr)
        sys.exit(1)
    print(f"{label} transcribed: {result.get('line_count', 0)} line(s).")
    if result.get("diarize_started"):
        d_outcome, d_message, _ = _wait_for_job(f"diarize_{args.id}", f"{label} speakers")
        if d_outcome not in ("ok", "partial"):
            print(f"{label} speaker detection {d_outcome}: "
                  f"{translate_engines.redact_secrets(d_message or '')}", file=sys.stderr)
            sys.exit(1)
        print(f"{label} speaker detection done.")
    cli_timing.wait_after_transcribe(args.id, label, _wait_for_job)


def cmd_qc(args):
    """Auto QC's factual-detail check (numbers, names, banned terms), the same
    service as the Workspace's "Run Auto QC". It updates review flags in place
    and uses no engine, so it never spends anything."""
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas()
    if args.id and dramas[0] is None:
        raise SystemExit(f"qc: No drama with id {args.id}.")

    def step(d):
        r = export_service.run_auto_qc_flagging(d["id"])
        print(f"#{d['id']} checked {r['checked']} line(s): {r['flagged']} newly flagged, "
              f"{r['already_flagged']} already flagged, {r['cleared']} cleared.")

    _, failed = _run_batch(dramas, step, "qc")
    if failed:
        sys.exit(1)


def _term_line(t: dict) -> str:
    extras = [x for x in (t["category"], t["policy"], "exact" if t["enforce_exact"] else None) if x]
    return (f"{t['id']}\t{t['term_original']}\t{t['term_translation']}"
            + (f"\t[{', '.join(extras)}]" if extras else ""))


def cmd_glossary(args):
    """List, add, remove, import or export a title's series glossary through
    glossary_service (the app's own validation). A glossary belongs to the
    series, so any title in the series reaches the same terms."""
    try:
        _glossary_action(args)
    except ServiceError as e:
        raise SystemExit(f"glossary: {translate_engines.redact_secrets(e.message)}")


def _glossary_action(args):
    action = args.glossary_action
    if action == "list":
        terms = glossary_service.list_glossary_terms(args.id)
        for t in terms:
            print(_term_line(t))
        print(f"{len(terms)} term(s).")
    elif action == "add":
        fields = {"term_original": args.original, "term_translation": args.translation}
        for key, value in (("notes", args.notes), ("category", args.category),
                           ("policy", args.policy), ("aliases", args.alias),
                           ("banned_translations", args.banned)):
            if value:
                fields[key] = value
        if args.enforce_exact:
            fields["enforce_exact"] = True
        print("Saved: " + _term_line(glossary_service.upsert_glossary_term(args.id, fields)))
    elif action == "remove":
        by_text = {t["term_original"]: t["id"] for t in glossary_service.list_glossary_terms(args.id)}
        ids = list(args.term_id or [])
        missing = [text for text in args.term or [] if text not in by_text]
        ids += [by_text[text] for text in args.term or [] if text in by_text]
        if missing:
            raise SystemExit("glossary: no term with original text: " + ", ".join(missing))
        if not ids:
            raise SystemExit("glossary: name a term with --term TEXT or --term-id N.")
        if not args.yes:
            raise SystemExit(f"glossary: this deletes {len(ids)} term(s); add --yes to confirm.")
        result = glossary_service.bulk_delete_glossary_terms(args.id, ids, confirm=True)
        print(f"Deleted {len(result['deleted'])} term(s)"
              + (f"; not found: {result['not_found']}" if result["not_found"] else "") + ".")
    elif action == "import":
        try:
            with open(args.file, encoding="utf-8-sig") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise SystemExit(f"glossary: can't read the file: {getattr(exc, 'strerror', None) or exc}")
        r = glossary_service.import_glossary_text(
            args.id, text, filename=os.path.basename(args.file),
            overwrite_existing=args.overwrite)
        print(f"Imported: {len(r['added'])} added, {len(r['overwritten'])} overwritten, "
              f"{len(r['skipped_existing'])} already there (use --overwrite to replace), "
              f"{len(r['invalid'])} invalid.")
        for w in r["warnings"]:
            print(f"  warning: {w}")
    elif action == "export":
        csv_text = glossary_service.glossary_csv(args.id)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
                    f.write(csv_text)
            except OSError as exc:
                raise SystemExit(f"glossary: can't write the file: {exc.strerror or exc}")
            print(f"Wrote {args.output}")
        else:
            sys.stdout.write(csv_text)


def cmd_run(args):
    """Align then translate a single drama in one shot."""
    cmd_align(args)
    from cli_translate import cmd_translate  # at call time: cli_translate imports this module
    cmd_translate(args)


def cmd_doctor(args):
    """Pre-flight one engine's credentials/reachability before
    committing a batch job to it -- catches a dead API key or an
    unreachable local Ollama server up front, with a real (but minimal,
    single-line) call, instead of discovering it mid-job."""
    result = diagnostics_report.check_engine_reachable(
        args.engine, args.api_key, args.model, _ollama_url(args))
    if result["ok"]:
        print(f"OK: {result['engine']} is reachable and responding.")
    else:
        print(f"FAILED: {result['engine']} -- {result['error']}")
        sys.exit(1)


def _parse_line_ids(text):
    try:
        return [int(part) for part in text.split(",") if part.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("expected comma-separated integers, e.g. 12,13,20")


def main():
    p = argparse.ArgumentParser(description="Headless batch driver for the drama library")
    sub = p.add_subparsers(dest="command", required=True)

    p_narrate = sub.add_parser("narrate-prep", help="Chunk + speaker-tag a novel-narration drama (no audio)")
    p_narrate.add_argument("--id", type=int, default=None)
    p_narrate.add_argument("--engine", default=None, choices=list(translate_engines.ENGINES))
    p_narrate.add_argument("--api-key", default=None)
    p_narrate.add_argument("--model", default=None)
    p_narrate.add_argument("--fresh", action="store_true",
                           help="Ignore batches an interrupted run already tagged; start over.")
    p_narrate.add_argument("--ollama-url", default=None,
                           help="Base URL for a non-default Ollama server (e.g. remote/Docker).")
    p_narrate.set_defaults(func=cmd_narrate_prep)

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default=None)
    p_list.set_defaults(func=cmd_list)

    p_inspect = sub.add_parser("inspect-line", help="Show \"what happened here?\" for one line")
    p_inspect.add_argument("--id", type=int, required=True)
    p_inspect.add_argument("--line", type=int, required=True, help="1-based line number")
    p_inspect.set_defaults(func=cmd_inspect_line)

    p_lang = sub.add_parser("set-language", help="Set the spoken language of some lines")
    p_lang.add_argument("--id", type=int, required=True)
    group = p_lang.add_mutually_exclusive_group(required=True)
    group.add_argument("--lines", type=_parse_line_ids, help="Comma-separated line ids (see inspect-line)")
    group.add_argument("--speaker", help="Every line with this speaker label")
    p_lang.add_argument("--lang", required=True,
                        help="A language code, or 'default' / '' for the title's language")
    p_lang.set_defaults(func=cmd_set_language)

    p_clean = sub.add_parser("clean-en", help="Fix common errors in the English (no AI); preview unless --apply")
    p_clean.add_argument("--id", type=int, required=True)
    p_clean.add_argument("--apply", action="store_true", help="Save the changes (a Line history snapshot is taken first)")
    p_clean.add_argument("--show", type=int, help="How many before/after examples to print (default 5)")
    p_clean.set_defaults(func=cmd_clean_en)

    p_align = sub.add_parser("align")
    p_align.add_argument("--id", type=int, default=None)
    p_align.add_argument("--whisper-size", default=None, choices=list(WHISPER_MODELS),
                         help="Defaults to the drama's own saved choice (Workspace's own "
                              f"'3. Recognition accuracy'), or '{DEFAULT_WHISPER_SIZE}' if it has none.")
    p_align.add_argument("--fast", action="store_true",
                         help="Batched decoding (~4x faster on a GPU, more VRAM)")
    p_align.add_argument("--transcript", default=None, metavar="FILE",
                         help="Chinese transcript to align (- for stdin); needs --id. "
                              "Default: <drama folder>/transcript.txt.")
    p_align.set_defaults(func=cmd_align)

    p_diarize = sub.add_parser("diarize", help="Re-run speaker detection on stored audio (no re-transcription)")
    p_diarize.add_argument("--id", type=int, default=None)
    p_diarize.add_argument("--hf-token", default=None)
    p_diarize.add_argument("--num-speakers", type=int, default=0)
    p_diarize.add_argument("--min-speakers", type=int, default=None,
                           help="Lower bound of a speaker-count range (instead of --num-speakers)")
    p_diarize.add_argument("--max-speakers", type=int, default=None,
                           help="Upper bound of a speaker-count range (instead of --num-speakers)")
    p_diarize.add_argument("--overwrite-manual", action="store_true",
                           help="Also replace speakers you corrected by hand")
    p_diarize.set_defaults(func=cmd_diarize)

    import cli_translate  # at call time: it imports helpers from this module
    cli_translate.add_translate_parser(sub)

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
    # No argparse choices: a removed engine name gets the same plain refusal
    # as the API instead of a generic "invalid choice" error.
    p_dub.add_argument("--tts-engine", default=dub_module.DEFAULT_CLONE_ENGINE,
                       help="Voice engine for speakers whose character has none of its own "
                            f"({', '.join(dub_module.CLONE_ENGINES)}; same choice as the Dub "
                            f"stage). Defaults to {dub_module.DEFAULT_CLONE_ENGINE}.")
    p_dub.add_argument("--m4b", action="store_true",
                       help="For novel narration: also export an M4B audiobook with chapter markers")
    p_dub.set_defaults(func=cmd_dub)

    p_transcribe = sub.add_parser("transcribe", help="Transcribe one title's audio (or align --transcript)")
    p_transcribe.add_argument("--id", type=int, required=True)
    p_transcribe.add_argument("--language", default=None,
                              help="Spoken language code (default: the title's own).")
    p_transcribe.add_argument("--chinese-script", default=None, choices=["simplified", "traditional"])
    p_transcribe.add_argument("--whisper-size", default=None,
                              help="Whisper model size, saved on the title.")
    p_transcribe.add_argument("--asr-backend", default=None,
                              choices=transcribe_service.ASR_BACKEND_CHOICES,
                              help="Speech recognition backend, saved on the title.")
    p_transcribe.add_argument("--beam-size", type=int, default=None, help="Whisper beam size (1-10).")
    p_transcribe.add_argument("--min-silence-ms", type=int, default=None,
                              help="VAD: silence that splits speech (300-3000).")
    p_transcribe.add_argument("--min-pause", type=float, default=None,
                              help="Pause (seconds) a long line may be cut at, "
                                   f"{segment_splitting.MIN_WORD_GAP_SECONDS_MIN:g}-"
                                   f"{segment_splitting.MIN_WORD_GAP_SECONDS_MAX:g}; saved on the title.")
    p_transcribe.add_argument("--vad-threshold", type=float, default=None,
                              help="VAD speech threshold (0.1-0.9).")
    p_transcribe.add_argument("--sensitivity", choices=("normal", "sensitive"), default=None,
                              help="'sensitive' catches quieter or fast speech (lower VAD threshold, no "
                                   "repeat penalties) and may add false text on music or breathing; "
                                   "saved on the title.")
    p_transcribe.add_argument("--separate-vocals", action=argparse.BooleanOptionalAction, default=None,
                              help="Separate vocals from music before recognising.")
    p_transcribe.add_argument("--separation-backend", default=None,
                              choices=["auto", "audio_separator", "demucs"])
    p_transcribe.add_argument("--diarize", action="store_true",
                              help="Detect speakers afterwards (needs a Hugging Face token in Settings).")
    p_transcribe.add_argument("--num-speakers", type=int, default=None)
    p_transcribe.add_argument("--min-speakers", type=int, default=None)
    p_transcribe.add_argument("--max-speakers", type=int, default=None)
    p_transcribe.add_argument("--transcript", default=None, metavar="FILE",
                              help="Transcript to align (- for stdin); required when the title "
                                   "is in have-a-transcript mode.")
    p_transcribe.add_argument("--initial-prompt", default=None, help="Replace Whisper's automatic prompt.")
    p_transcribe.add_argument("--extra-names", default=None, help="Extra names added to the automatic prompt.")
    p_transcribe.set_defaults(func=cmd_transcribe)

    p_qc = sub.add_parser("qc", help="Run Auto QC (numbers, names, banned terms) and flag lines")
    p_qc.add_argument("--id", type=int, default=None, help="One title (default: the whole library).")
    p_qc.set_defaults(func=cmd_qc)
    cli_subtitle.register(sub)
    cli_timing.register(sub, _wait_for_job)

    p_gloss = sub.add_parser("glossary", help="List, add, remove, import or export a title's series glossary")
    gsub = p_gloss.add_subparsers(dest="glossary_action", required=True)
    g_list = gsub.add_parser("list")
    g_add = gsub.add_parser("add", help="Add a term, or update the one with the same original text")
    g_add.add_argument("--original", required=True)
    g_add.add_argument("--translation", required=True)
    g_add.add_argument("--notes", default=None)
    g_add.add_argument("--category", default=None, choices=gio.TERM_CATEGORIES)
    g_add.add_argument("--policy", default=None, choices=gio.TERM_POLICIES)
    g_add.add_argument("--alias", action="append", default=None)
    g_add.add_argument("--banned", action="append", default=None, help="A translation never to use.")
    g_add.add_argument("--enforce-exact", action="store_true")
    g_rm = gsub.add_parser("remove")
    g_rm.add_argument("--term", action="append", help="Original text of a term (repeatable).")
    g_rm.add_argument("--term-id", type=int, action="append", help="Term id from 'glossary list'.")
    g_rm.add_argument("--yes", action="store_true", help="Confirm the deletion.")
    g_imp = gsub.add_parser("import", help="Import a CSV, TSV or JSON glossary file")
    g_imp.add_argument("file")
    g_imp.add_argument("--overwrite", action="store_true", help="Replace terms already in the glossary.")
    g_exp = gsub.add_parser("export", help="Export the glossary as CSV (stdout unless --output)")
    g_exp.add_argument("--output", default=None, metavar="FILE")
    for g in (g_list, g_add, g_rm, g_imp, g_exp):
        g.add_argument("--id", type=int, required=True,
                       help="A title in the series (the glossary is shared by the series).")
    p_gloss.set_defaults(func=cmd_glossary)

    p_run = sub.add_parser("run")
    p_run.add_argument("--id", type=int, required=True)
    p_run.add_argument("--whisper-size", default=None)
    p_run.add_argument("--engine", default=None, choices=list(translate_engines.ENGINES))
    p_run.add_argument("--api-key", default=None,
                             help="Key for --engine; omit to use the saved key.")
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--style-note", default=None)
    # cmd_run calls cmd_translate(args) directly, reusing this same
    # Namespace -- it needs everything cmd_translate itself does (--status,
    # --force, --style-preset, --locale), a real pre-existing gap this
    # surfaced: cmd_run has always raised AttributeError the moment it
    # reached cmd_translate, since these were never defined here.
    p_run.add_argument("--status", default=None)
    p_run.add_argument("--style-preset", default=None, choices=list(tguide.STYLE_PRESETS))
    p_run.add_argument("--locale", default=None, choices=list(settings_service.LOCALE_CHOICES),
                        help="Default: the Settings English variant (en-US until changed).")
    p_run.add_argument("--female-pronouns", action="store_true", default=None,
                           help="Default ambiguous pronouns to she/her and save that choice "
                                "for the title. Without --female-pronouns or "
                                "--no-female-pronouns the title's saved choice applies (off "
                                "until chosen).")
    p_run.add_argument("--no-female-pronouns", action="store_false", dest="female_pronouns",
                           default=None, help="Turn the she/her default off and save that.")
    p_run.add_argument("--no-genre-notes", action="store_true", default=None,
                           help="Leave out the baihe/GL genre guidance and save that choice "
                                "for the title (on until chosen otherwise).")
    p_run.add_argument("--genre-notes", action="store_false", dest="no_genre_notes",
                           default=None, help="Include the genre guidance and save that.")
    p_run.add_argument("--transcript", default=None, metavar="FILE",
                       help="Chinese transcript to align (- for stdin); needs --id. "
                            "Default: <drama folder>/transcript.txt.")
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--ollama-num-ctx", type=int, default=None)
    p_run.add_argument("--ollama-url", default=None)
    p_run.add_argument("--reflect", action="store_true")
    p_run.add_argument("--context-window", type=int, default=None)
    p_run.add_argument("--context-window-ahead", type=int, default=None)
    p_run.add_argument("--batch-size", type=int, default=None)
    p_run.add_argument("--cost-cap", type=float, default=None,
                       help="Stop a drama's translation once its estimated spend reaches this "
                            "many USD (finished lines are kept).")
    p_run.add_argument("--monthly-cap", type=float, default=None,
                       help="Refuse to start / stop once this calendar month's logged spend "
                            "reaches this many USD. Defaults to the saved Settings/.env monthly cap.")
    p_run.set_defaults(func=cmd_run)

    p_export_video = sub.add_parser("export-video")
    p_export_video.add_argument("--id", type=int, default=None)
    p_export_video.add_argument("--mode", default=None, choices=["hardsub", "softsub"],
                                help="hardsub (burned in, default) or softsub (selectable track, SRT).")
    p_export_video.add_argument("--style", default=None, metavar="PRESET",
                                help="ASS style preset for the burned-in subtitles, by the export "
                                     "stage's names: " + ", ".join(subtitle_formats.ASS_PRESETS)
                                     + " (default Clean). The old hardsub/softsub values still work.")
    p_export_video.add_argument("--no-speaker-colors", action="store_true",
                                help="One colour for every speaker (default: a colour per speaker).")
    p_export_video.add_argument("--plain", action="store_true",
                                help="Burn a plain SRT (flat style, no speaker colours) instead of ASS.")
    p_export_video.add_argument("--subs", default="english", choices=["english", "bilingual", "chinese"])
    p_export_video.set_defaults(func=cmd_export_video)

    p_doctor = sub.add_parser("doctor", help="Pre-flight one engine's credentials/"
                              "reachability with a real, minimal translate call")
    p_doctor.add_argument("--engine", required=True, choices=list(translate_engines.ENGINES))
    p_doctor.add_argument("--api-key", default=None)
    p_doctor.add_argument("--model", default=None)
    p_doctor.add_argument("--ollama-url", default=None,
                          help="Base URL for a non-default Ollama server (e.g. remote/Docker).")
    p_doctor.set_defaults(func=cmd_doctor)

    real_model_check_cli.register(sub)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
