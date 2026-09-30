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
  # ...or when only a range is known (2 to 4 voices)
  python cli.py diarize --id 12 --min-speakers 2 --max-speakers 4

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
    DEFAULT_WHISPER_SIZE, ModelDownloadError, line_from_row,
)
import subtitle_formats
import translate_engines
import translation_guide as tguide
import bulk_translate
import raw_transcript
import dub as dub_module
import background_jobs
from services import (engine_routing_service, glossary_retranslate_service,
                      line_provenance_service, narration_service, settings_service,
                      transcribe_service, translate_service, workspace_job_service)
from services.narration_service import TAG_ENGINES
from services.service_errors import DependencyUnavailableError
from services.translate_run_service import _cap_applies, get_translate_config_defaults


def _gemini_free_tier(engine_name: str) -> bool:
    """The saved Settings "Gemini free tier" toggle, for the gemini engine only."""
    return engine_name == "gemini" and settings_service.get_gemini_free_tier()


def _ollama_url(args):
    """--ollama-url when given, else the saved Settings Ollama URL."""
    return getattr(args, "ollama_url", None) or settings_service.resolve_key("ollama_url") or None


def _monthly_cap_setting():
    """The saved monthly cap (Settings, then .env), same source as the service."""
    return settings_service.get_monthly_cap_usd() or None


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
    # cross-process job_records rows too, so a Streamlit/API job running
    # on this drama notices the cancel. (Only queued/running rows change.)
    for prefix in background_jobs.LINE_WRITING_JOB_PREFIXES:
        db.request_job_record_cancel(f"{prefix}{drama_id}")
    if existing:
        db.save_line_history_snapshot(drama_id, existing, snapshot_label)
    db.save_lines(drama_id, lines)
    return True


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
        # Step 41: same resume as the API job (narration_service).
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
    """(mode, preset) for export-video. --style used to mean hardsub/softsub;
    those two values still work there, anything else is an ASS preset name."""
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
        if use_ass:
            ass_text = export_service.generate_ass_text(
                d["id"], field={"english": "en", "bilingual": "bilingual", "chinese": "zh"}[args.subs],
                preset=preset or "Clean",
                per_speaker_colors=not getattr(args, "no_speaker_colors", False))
            video_export.burn_ass(video_path, ass_text, out_path)
        else:
            srt_text = {"english": lines_to_srt(lines, "en"), "bilingual": lines_to_bilingual_srt(lines),
                        "chinese": lines_to_srt(lines, "zh")}[args.subs]
            if mode == "hardsub":
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
            turns, model, embeddings = diarize.diarize(
                audio_path, hf_token, num_speakers=num_speakers,
                return_model=True, return_embeddings=True,
                use_gpu=settings_service.get_use_gpu(),
                min_speakers=min_speakers, max_speakers=max_speakers, run_info=run_info)
        finally:
            release_gpu_models()
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
        if alignment_method == "qwen3_forced_align":
            # Checked before any transcription, as the API does.
            try:
                transcribe_service._require_qwen3_packages("Qwen3 forced alignment")
            except DependencyUnavailableError as exc:
                raise _qwen3_missing(exc) from exc
        # Glossary names plus raw-novel excerpt, shared with the API path.
        initial_prompt = transcribe_service.build_auto_initial_prompt(d["id"])
        # Same saved tuning the service's transcribe job uses
        # (transcribe_service.get_transcribe_config); --fast still wins.
        cfg = transcribe_service.get_transcribe_config(d["id"])
        use_gpu = settings_service.get_use_gpu()
        language = d.get("source_language") or "zh"
        print(f"#{d['id']} aligning ({d['title_en'] or d['title_zh']})...")
        db.heartbeat_gpu_lock(_gpu_holder)
        if cfg["separate_vocals_first"]:
            audio_path = audio_preprocess.separate_vocals(
                audio_path, os.path.join(os.path.dirname(audio_path), "vocals.wav"),
                backend=cfg["separation_backend"])
        segments = transcribe_for_timing(
            audio_path, whisper_size, language=language, use_gpu=use_gpu,
            local_model_path=settings_service.get_whisper_model_path(),
            fast_mode=getattr(args, "fast", False) or cfg["whisper_fast_mode"],
            initial_prompt=initial_prompt, beam_size=cfg["beam_size"],
            min_silence_duration_ms=cfg["min_silence_ms"], vad_threshold=cfg["vad_threshold"])
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
                        audio_path, user_lines, segments, language=language, use_gpu=use_gpu)
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
            language=language, mode="aligned_transcript")
        db.update_drama(d["id"], status="aligned")
        print(f"#{d['id']} aligned {len(lines)} lines.")

    with _gpu_lock(f"CLI align ({len(dramas)} drama(s))") as _gpu_holder:
        _run_batch(dramas, step, "align")


# The engine a bare --api-key (no --engine) is assumed to belong to: the
# CLI's old --engine default.
_API_KEY_DEFAULT_ENGINE = "claude"


def _flag_or(args, name, defaults):
    """An explicit CLI flag wins; unset (None/missing) uses the per-drama default."""
    value = getattr(args, name, None)
    return defaults[name] if value is None else value


def _parse_fallback_arg(value, reflect=False) -> list:
    """--fallback "<engine>[,<engine>]" as a list of engine names, checked
    the way the translate run API checks fallback_chain (at most
    translate_engines.MAX_FALLBACK_ENGINES, known engines, normal runs
    only); the chain rules against the primary engine are checked per
    drama (translate_engines.fallback_chain_error)."""
    names = [n.strip() for n in (value or "").split(",") if n.strip()]
    if not names:
        return []
    if len(names) > translate_engines.MAX_FALLBACK_ENGINES:
        raise SystemExit(f"translate: --fallback takes at most "
                         f"{translate_engines.MAX_FALLBACK_ENGINES} engines.")
    if reflect:
        raise SystemExit("translate: --fallback only applies to a normal translation run, "
                         "not --reflect.")
    if any(n not in translate_engines.ENGINES for n in names):
        raise SystemExit("translate: --fallback names an unknown translate engine.")
    return names


def cmd_translate(args):
    fallback_names = _parse_fallback_arg(getattr(args, "fallback", None),
                                         reflect=getattr(args, "reflect", False))
    glossary_affected = getattr(args, "glossary_affected", False)
    if glossary_affected and not args.id:
        raise SystemExit("--glossary-affected needs --id (one drama at a time, as in the app).")
    if getattr(args, "include_hand_edited", False) and not glossary_affected:
        raise SystemExit("--include-hand-edited only applies with --glossary-affected.")
    query_status = args.status or "aligned"
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status=query_status)
    # Same default as the service: an explicit --engine, else the drama's
    # saved translation_engine, else the Settings engine for everyday
    # translation (Step 36 capability "translation.cheap").
    def _engine_name_for(d):
        return (args.engine or d.get("translation_engine")
                or engine_routing_service.resolve_capability("translation.cheap"))
    _engines = {}

    def _engine_for(name):
        # --api-key/--model belong to --engine when it's given, else to the
        # old default engine (claude). A drama saved with another engine
        # uses that engine's own configured key -- never someone else's.
        own_flags = (name == args.engine) if args.engine else name == _API_KEY_DEFAULT_ENGINE
        if name not in _engines:
            _engines[name] = translate_engines.get_engine(
                name,
                (args.api_key if own_flags and args.api_key
                 else translate_service.resolve_api_key(name)),
                args.model if own_flags else None,
                free_tier=_gemini_free_tier(name),
                base_url=_ollama_url(args) if name == "ollama" else None,
                libretranslate_url=(settings_service.resolve_key("libretranslate_url") or None)
                if name == "libretranslate" else None)
        return _engines[name]
    # Step 74: UI parity -- Workspace's own Translate button builds this
    # same optional summary_engine before starting the job (defaulting to
    # local Ollama); a missing/unreachable one just skips the summary
    # rather than failing the translate command.
    summary_engine_choice = (getattr(args, "episode_summary_engine", None)
                             or settings_service.get_preference("episode_summary_engine"))
    summary_key = getattr(args, "episode_summary_api_key", None) or (
        None if summary_engine_choice == "ollama"
        else translate_service.resolve_api_key(summary_engine_choice))
    try:
        if summary_engine_choice != "ollama" and not summary_key:
            raise ValueError("no key for the episode-summary engine")
        summary_engine = translate_engines.get_engine(
            summary_engine_choice, summary_key,
            free_tier=_gemini_free_tier(summary_engine_choice),
            base_url=_ollama_url(args) if summary_engine_choice == "ollama" else None)
    except Exception:
        summary_engine = None
    # A paid summary engine counts against the monthly cap too (checked
    # right before its call, in finish_translation_run).
    summary_monthly_cap = getattr(args, "monthly_cap", None)
    if summary_monthly_cap is None:
        summary_monthly_cap = _monthly_cap_setting()

    def step(d):
        rows = db.load_lines(d["id"])
        if not rows:
            print(f"#{d['id']} skipped: no aligned lines yet.")
            return
        lines = lines_from_rows(rows)
        engine_name = _engine_name_for(d)
        if args.api_key and not args.engine and engine_name != _API_KEY_DEFAULT_ENGINE:
            print(f"#{d['id']} skipped: saved engine {engine_name}; pass --engine {engine_name} "
                  f"and its key, or omit --api-key to use the saved keys.")
            return
        chain_names = [engine_name] + fallback_names
        chain_error = translate_engines.fallback_chain_error(chain_names)
        if chain_error:
            print(f"#{d['id']} skipped: {chain_error}")
            return
        missing = [n for n in fallback_names
                   if n != "nllb" and not translate_service.resolve_api_key(n)]
        if missing:
            print(f"#{d['id']} skipped: no {missing[0]} key is configured for --fallback.")
            return
        engine = _engine_for(engine_name)
        # Same defaults the service/React use (10/6/30 for novel narration).
        tdefaults = get_translate_config_defaults(d.get("content_mode") == "novel_narration")
        novel_reference = _load_novel_reference(d)
        # UI parity: without these, a CLI-run translation skipped the
        # series glossary, craft/style guidelines, and locale entirely --
        # a real, confirmed gap between what the Workspace Translate
        # button sends and what this command sent for the same drama.
        # The pronoun-default/genre-notes toggles aren't stored on the drama,
        # so they come from --female-pronouns / --no-genre-notes (defaults
        # match the API: she/her off, genre notes on). Everything else --
        # series glossary, learned style profile, emotion guidance, gender
        # hints, speaker names -- comes from the same builder the translate
        # run service uses (B-20).
        style_preset = args.style_preset or (
            "novel" if d.get("content_mode") == "novel_narration" else "audio_drama")
        glossary_terms, style_guidelines, character_names = \
            workspace_job_service.build_run_style_context(
                d["id"], d, lines, style_preset,
                include_genre_notes=not getattr(args, "no_genre_notes", False),
                default_female_pronouns=getattr(args, "female_pronouns", False))
        target_ids = None
        if glossary_affected:
            # Same selection as the app's "Re-translate lines affected by the
            # glossary": lines whose English isn't known to be machine-made
            # are left alone unless --include-hand-edited.
            target_ids = set(glossary_retranslate_service.affected_line_ids(
                d["id"], include_hand_edited=getattr(args, "include_hand_edited", False)))
            if not target_ids:
                print(f"#{d['id']} skipped: no machine-translated lines are affected by the "
                      f"glossary (hand-edited lines need --include-hand-edited).")
                return
        force = args.force or target_ids is not None
        print(f"#{d['id']} translating "
              f"{len(target_ids) if target_ids is not None else len(lines)} lines with {engine_name}"
              + (" (+ novel reference)" if novel_reference else "") + "...")
        _id_by_idx = {ln.idx: ln.id for ln in lines if getattr(ln, "id", None) is not None}
        # Same caps as the Workspace Translate job: per job (--cost-cap)
        # and per calendar month (--monthly-cap, or BAIHE_MONTHLY_CAP_USD).
        # Like the service, the monthly cap only covers paid engines
        # (_cap_applies: not local/free engines, not Gemini's free tier).
        # With --fallback, each engine in the chain gets its own cap
        # (FallbackEngine enforces it), as the translate run API does.
        monthly_setting = getattr(args, "monthly_cap", None)
        if monthly_setting is None:
            monthly_setting = _monthly_cap_setting()
        month_spend = db.get_month_spend() if monthly_setting else 0.0
        caps = []
        for name in chain_names:
            monthly_cap = (monthly_setting if _cap_applies(name, _gemini_free_tier(name))
                           else None)
            cap, refusal = translate_engines.resolve_cost_cap(
                getattr(args, "cost_cap", None), monthly_cap, month_spend if monthly_cap else 0.0)
            if refusal:
                raise RuntimeError(refusal)
            caps.append(cap)
        if fallback_names:
            engine = translate_engines.FallbackEngine(
                [engine] + [_engine_for(n) for n in fallback_names], chain_names, caps,
                failed_usage_cb=lambda choice, eng, inp, out, cache_read=0, cache_write=0,
                did=d["id"]: db.log_usage(
                    did, choice, getattr(eng, "model", choice), "translate", inp, out,
                    translate_engines.estimate_cost_for_engine(eng, inp, out, cache_read,
                                                               cache_write),
                    cache_read_tokens=cache_read))
            cost_cap = None
        else:
            cost_cap = caps[0]
        cap_reached = {}
        def _progress(frac, did=d["id"]):
            if _gpu_holder:
                # Step 25w: --engine ollama holds the cross-process GPU
                # lock for this whole batch (see below) -- refreshed here,
                # on every batch's own progress tick, so a long run doesn't
                # look abandoned to another process before it's done.
                db.heartbeat_gpu_lock(_gpu_holder)
            print(f"  #{did}: {frac*100:.0f}%", end="\r")

        style_note = (args.style_note if args.style_note is not None
                      else settings_service.get_preference("default_style_note"))
        # Same settings the Workspace job records with each line (Step 41).
        provenance = line_provenance_service.translate_run_tracker(
            d["id"], lines, engine, engine_name, glossary_terms,
            locale=args.locale or settings_service.get_preference("default_locale"),
            style_preset=style_preset, reflect=bool(getattr(args, "reflect", False)),
            context_window=_flag_or(args, "context_window", tdefaults),
            context_window_ahead=_flag_or(args, "context_window_ahead", tdefaults),
            batch_size=_flag_or(args, "batch_size", tdefaults),
            style_note=style_note or "", style_guidelines=style_guidelines or "")
        if force and any(ln.en for ln in lines):
            # Same data-loss guard as translate_run_service: keep the old
            # translation restorable from history before it's overwritten.
            db.save_line_history_snapshot(d["id"], lines, "before force re-translate")
        # Same as the Workspace Translate job: writes `en` only, and
        # records each translated line's provenance (Step 41).
        if target_ids is not None:
            save_cb, notes_cb = bulk_translate.own_lines_callbacks(d["id"], lines, provenance)
        else:
            def save_cb(ls, did=d["id"]):
                db.save_lines(did, ls, fields=("en",))
                provenance(ls)

            def notes_cb(notes, did=d["id"]):
                db.save_translation_notes(did, notes, id_by_idx=_id_by_idx)
        _, batch_errors = translate_engines.translate_lines_with_engine(
            lines, engine, drama_meta=d,
            style_note=style_note,
            novel_reference=novel_reference, force_retranslate=force, target_ids=target_ids,
            locale=args.locale or settings_service.get_preference("default_locale"),
            glossary_terms=glossary_terms,
            style_guidelines=style_guidelines, character_names=character_names,
            ollama_num_ctx_override=(args.ollama_num_ctx if args.ollama_num_ctx is not None
                                     else settings_service.get_ollama_num_ctx_override() or None),
            context_window=_flag_or(args, "context_window", tdefaults),
            context_window_ahead=_flag_or(args, "context_window_ahead", tdefaults),
            batch_size=_flag_or(args, "batch_size", tdefaults),
            reflect=getattr(args, "reflect", False),
            notes_cb=notes_cb,
            progress_cb=_progress,
            save_cb=save_cb,
            usage_cb=lambda inp, out, cache_read=0, cache_write=0, did=d["id"]: db.log_usage(
                did, (engine.active_choice if isinstance(engine, translate_engines.FallbackEngine)
                      else engine_name),
                getattr(engine, "model", engine_name), "translate", inp, out,
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
        recheck = set()
        bulk_translate.finish_translation_run(
            d["id"], lines, engine, engine_name, style_preset, glossary_terms, batch_errors,
            summary_engine=summary_engine, summary_engine_choice=summary_engine_choice,
            summary_monthly_cap_usd=summary_monthly_cap,
            line_scoped=target_ids is not None, enforce_ids=target_ids,
            flags_needing_recheck=recheck)
        if recheck:
            print(f"\n#{d['id']} {len(recheck)} line(s) changed while the job ran, so their "
                  f"review flags weren't saved; recheck line id(s) "
                  f"{', '.join(map(str, sorted(recheck)))}.")
        for ev in getattr(engine, "events", None) or []:
            print(f"\n#{d['id']} switched from {ev['from']} to {ev['to']} ({ev['reason']}).")
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
               if any("ollama" in (_engine_name_for(d), *fallback_names) for d in dramas if d)
               else contextlib.nullcontext(None))
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
            chars, ddir, gpt_sovits_url=(getattr(args, "gpt_sovits_url", None)
                                          or settings_service.resolve_key("gpt_sovits_url") or None),
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
        args.engine, args.api_key, args.model, _ollama_url(args))
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

    p_translate = sub.add_parser("translate")
    p_translate.add_argument("--id", type=int, default=None)
    p_translate.add_argument("--status", default=None)
    p_translate.add_argument("--engine", default=None, choices=list(translate_engines.ENGINES))
    p_translate.add_argument("--api-key", default=None,
                             help="Key for --engine; omit to use the saved key.")
    p_translate.add_argument("--model", default=None)
    p_translate.add_argument("--episode-summary-engine", default=None,
                             choices=list(translate_engines.ENGINES),
                             help="Step 74: engine for the once-per-episode running-summary call "
                                  "made after a drama finishes translating, fed forward as "
                                  "continuity context into the next episode of the same series. "
                                  "Defaults to the Settings episode-summary engine (local Ollama "
                                  "until changed; a fixed once-per-episode cost); if "
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
    p_translate.add_argument("--locale", default=None, choices=["en-US", "en-GB", "en-AU"],
                             help="Default: the Settings English variant (en-US until changed).")
    p_translate.add_argument("--female-pronouns", action="store_true",
                           help="Default ambiguous pronouns to she/her (the Workspace "
                                "checkbox / a preset's pronoun default).")
    p_translate.add_argument("--no-genre-notes", action="store_true",
                           help="Leave out the baihe/GL genre guidance (on by default, "
                                "as in the Workspace).")
    p_translate.add_argument("--force", action="store_true",
                              help="Re-translate everything, including lines that already have a translation")
    p_translate.add_argument("--glossary-affected", action="store_true",
                             help="Re-translate only the lines the glossary affects (a term or "
                                  "alias in the source, or a banned translation in the English). "
                                  "Needs --id. Hand-edited lines are left alone.")
    p_translate.add_argument("--include-hand-edited", action="store_true",
                             help="With --glossary-affected: also replace hand-edited lines "
                                  "(a snapshot is saved first).")
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
                           default=None,
                           help="Refuse to start / stop once this calendar month's logged spend "
                                "reaches this many USD. Defaults to the saved Settings/.env monthly cap.")
    # Step 32: matches the Workspace tab's own three sliders. Unset means
    # the service's per-drama defaults (translate_run_service.
    # get_translate_config_defaults): 6/3/20, or 10/6/30 for novel narration.
    p_translate.add_argument("--context-window", type=int, default=None,
                           help="Lines of already-translated context shown from before each "
                                "batch (default 6, 10 for novel narration). 0 turns this off.")
    p_translate.add_argument("--context-window-ahead", type=int, default=None,
                           help="Lines of source text shown from after each batch, to resolve "
                                "a reference that's only disambiguated later (default 3, 6 for "
                                "novel narration). 0 "
                                "turns this off.")
    p_translate.add_argument("--batch-size", type=int, default=None,
                           help="Lines translated per request (default 20, 30 for novel "
                                "narration). More lines per "
                                "request is cheaper/faster overall but a bigger single point "
                                "of failure.")
    p_translate.add_argument("--fallback", default=None, metavar="ENGINE[,ENGINE]",
                             help="Up to 2 engines tried in order if the main engine keeps "
                                  "failing (rate limit, timeout, connection, bad key) after "
                                  "its retries -- same kind as the main engine (AI with AI, "
                                  "translation-only with translation-only); not with --reflect. "
                                  "Same rules as the Translate stage's fallback engines.")
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
    p_run.add_argument("--locale", default=None, choices=["en-US", "en-GB", "en-AU"],
                        help="Default: the Settings English variant (en-US until changed).")
    p_run.add_argument("--female-pronouns", action="store_true",
                           help="Default ambiguous pronouns to she/her (the Workspace "
                                "checkbox / a preset's pronoun default).")
    p_run.add_argument("--no-genre-notes", action="store_true",
                           help="Leave out the baihe/GL genre guidance (on by default, "
                                "as in the Workspace).")
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
    p_run.add_argument("--monthly-cap", type=float,
                           default=None,
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
