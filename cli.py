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

  # List what's in the library and its status
  python cli.py list
"""

import argparse
import os
import sys
import traceback

import db
from core import (
    Line, split_user_transcript, transcribe_for_timing, align_transcript_to_timing,
    chunk_novel_text, extract_audio_from_video,
)
import translate_engines
import translation_guide as tguide
import dub as dub_module


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
    engine = translate_engines.get_engine(args.engine, args.api_key, args.model) if args.api_key else None

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
            speakers = translate_engines.tag_speakers_llm([ln.zh for ln in lines], engine, known)
            for ln, sp in zip(lines, speakers):
                ln.speaker = sp
            for label in sorted(set(speakers)):
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
        lines = [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"], en=r.get("en") or "")
                 for r in rows]

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
        print(f"#{d['id']} aligning ({d['title_en'] or d['title_zh']})...")
        segments = transcribe_for_timing(audio_path, args.whisper_size, language=d.get("source_language") or "zh")
        user_lines = split_user_transcript(transcript_text)
        lines = align_transcript_to_timing(user_lines, segments)
        db.save_lines(d["id"], lines)
        db.update_drama(d["id"], status="aligned")
        print(f"#{d['id']} aligned {len(lines)} lines.")

    _run_batch(dramas, step, "align")


def cmd_translate(args):
    query_status = args.status or "aligned"
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status=query_status)
    engine = translate_engines.get_engine(args.engine, args.api_key, args.model)

    def step(d):
        rows = db.load_lines(d["id"])
        if not rows:
            print(f"#{d['id']} skipped: no aligned lines yet.")
            return
        # flag/flag_note carried through explicitly: save_cb below calls
        # db.save_lines() on this exact list on every batch, including for
        # lines this run doesn't touch -- dropping those fields here would
        # silently wipe every review-queue flag in the drama on every run.
        lines = [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"], en=r.get("en") or "",
                      speaker=r.get("speaker"), flag=r.get("flag"), flag_note=r.get("flag_note") or "")
                 for r in rows]
        novel_reference = _load_novel_reference(d)
        # UI parity: without these, a CLI-run translation skipped the
        # series glossary, craft/style guidelines, and locale entirely --
        # a real, confirmed gap between what the Workspace Translate
        # button sends and what this command sent for the same drama.
        glossary_terms = db.list_glossary_terms(d["series_id"]) if d.get("series_id") else None
        series_chars = db.list_series_characters(d["series_id"]) if d.get("series_id") else []
        drama_chars = db.list_characters_with_series_names(d["id"])
        style_guidelines = tguide.build_style_guidelines(
            style_preset=args.style_preset, glossary_terms=glossary_terms,
            custom_notes=tguide.build_character_gender_hints(series_chars, drama_chars))
        character_names = tguide.build_speaker_labels(drama_chars, series_chars)
        print(f"#{d['id']} translating {len(lines)} lines with {args.engine}"
              + (" (+ novel reference)" if novel_reference else "") + "...")
        _, batch_errors = translate_engines.translate_lines_with_engine(
            lines, engine, drama_meta=d, style_note=args.style_note or "",
            novel_reference=novel_reference, force_retranslate=args.force,
            locale=args.locale, glossary_terms=glossary_terms,
            style_guidelines=style_guidelines, character_names=character_names,
            ollama_num_ctx_override=args.ollama_num_ctx,
            progress_cb=lambda frac, did=d["id"]: print(f"  #{did}: {frac*100:.0f}%", end="\r"),
            save_cb=lambda lines, did=d["id"]: db.save_lines(did, lines),
        )
        db.update_drama(d["id"], status="translated", translation_engine=args.engine)
        if batch_errors:
            print(f"\n#{d['id']} translated with {len(batch_errors)} batch failure(s) after "
                  f"backoff retries -- re-run this command to retry just the missing lines.")
        else:
            print(f"\n#{d['id']} translated.")

    _run_batch(dramas, step, "translate")


def cmd_dub(args):
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status="translated")

    def step(d):
        rows = db.load_lines(d["id"])
        if not rows or not any(r.get("en") for r in rows):
            print(f"#{d['id']} skipped: not translated yet.")
            return
        # flag/flag_note carried through explicitly, same reasoning as
        # cmd_translate above -- db.save_lines() below on this exact list
        # would otherwise silently wipe every review-queue flag in the
        # drama on every dub run. A real, confirmed gap this replaces.
        lines = [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"], en=r.get("en") or "",
                      speaker=r.get("speaker"), flag=r.get("flag"), flag_note=r.get("flag_note") or "")
                 for r in rows]
        ddir = db.drama_dir(d["id"])

        chars = db.list_characters(d["id"])
        voice_map = {c["speaker_label"]: c["tts_voice"] for c in chars if c.get("tts_voice")}
        clone_map = {}
        for c in chars:
            if c.get("elevenlabs_voice_id"):
                clone_map[c["speaker_label"]] = {
                    "engine": "elevenlabs", "voice_id": c["elevenlabs_voice_id"],
                    "api_key": args.elevenlabs_key or "",
                }
            elif c.get("ref_audio_filename"):
                clone_map[c["speaker_label"]] = {
                    "ref_audio": os.path.join(ddir, c["ref_audio_filename"]),
                    "ref_text": c.get("ref_text") or "",
                }
        speakers = sorted({ln.speaker for ln in lines if ln.speaker})
        if speakers and not voice_map:
            voice_map = dub_module.assign_voices_to_characters(speakers)

        build_fn = dub_module.build_narration_track if d.get("content_mode") == "novel_narration" else dub_module.build_dub_track
        print(f"#{d['id']} generating {'narration' if d.get('content_mode') == 'novel_narration' else 'dub'} track...")
        out_path, dub_errors = build_fn(
            lines, ddir, voice_map, character_clone_map=clone_map,
            progress_cb=lambda frac, did=d["id"]: print(f"  #{did}: {frac*100:.0f}%", end="\r"),
        )
        db.save_lines(d["id"], lines)
        db.update_drama(d["id"], status="dubbed")
        if dub_errors:
            print(f"\n#{d['id']} track: {out_path} ({len(dub_errors)} line(s) silent due to "
                  f"synthesis failures -- re-run to retry just those; already-generated clips are reused.)")
        else:
            print(f"\n#{d['id']} track: {out_path}")

    _run_batch(dramas, step, "dub")


def cmd_run(args):
    """Align then translate a single drama in one shot."""
    cmd_align(args)
    cmd_translate(args)


def main():
    p = argparse.ArgumentParser(description="Headless batch driver for the drama library")
    sub = p.add_subparsers(dest="command", required=True)

    p_narrate = sub.add_parser("narrate-prep", help="Chunk + speaker-tag a novel-narration drama (no audio)")
    p_narrate.add_argument("--id", type=int, default=None)
    p_narrate.add_argument("--engine", default="claude", choices=list(translate_engines.ENGINES))
    p_narrate.add_argument("--api-key", default=None)
    p_narrate.add_argument("--model", default=None)
    p_narrate.set_defaults(func=cmd_narrate_prep)

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default=None)
    p_list.set_defaults(func=cmd_list)

    p_align = sub.add_parser("align")
    p_align.add_argument("--id", type=int, default=None)
    p_align.add_argument("--whisper-size", default="medium")
    p_align.set_defaults(func=cmd_align)

    p_translate = sub.add_parser("translate")
    p_translate.add_argument("--id", type=int, default=None)
    p_translate.add_argument("--status", default=None)
    p_translate.add_argument("--engine", default="claude", choices=list(translate_engines.ENGINES))
    p_translate.add_argument("--api-key", required=True)
    p_translate.add_argument("--model", default=None)
    p_translate.add_argument("--style-note", default=None)
    p_translate.add_argument("--style-preset", default="audio_drama",
                              choices=list(tguide.STYLE_PRESETS),
                              help="Matches the Workspace tab's own style-guidance preset -- "
                                   "affects phrasing/pacing guidance, not language or content.")
    p_translate.add_argument("--locale", default="en-US", choices=["en-US", "en-GB", "en-AU"])
    p_translate.add_argument("--force", action="store_true",
                              help="Re-translate everything, including lines that already have a translation")
    p_translate.add_argument("--ollama-num-ctx", type=int, default=None,
                              help="Override Ollama's context window size. Only ever raises it "
                                   "above the automatic per-prompt estimate, never below -- "
                                   "leave unset to size it automatically (recommended).")
    p_translate.set_defaults(func=cmd_translate)

    p_dub = sub.add_parser("dub")
    p_dub.add_argument("--id", type=int, default=None)
    p_dub.add_argument("--elevenlabs-key", default=None,
                        help="Required only for characters cloned via ElevenLabs")
    p_dub.set_defaults(func=cmd_dub)

    p_run = sub.add_parser("run")
    p_run.add_argument("--id", type=int, required=True)
    p_run.add_argument("--whisper-size", default="medium")
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
    p_run.add_argument("--style-preset", default="audio_drama", choices=list(tguide.STYLE_PRESETS))
    p_run.add_argument("--locale", default="en-US", choices=["en-US", "en-GB", "en-AU"])
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--ollama-num-ctx", type=int, default=None)
    p_run.set_defaults(func=cmd_run)

    p_export_video = sub.add_parser("export-video")
    p_export_video.add_argument("--id", type=int, default=None)
    p_export_video.add_argument("--style", default="hardsub", choices=["hardsub", "softsub"])
    p_export_video.add_argument("--subs", default="english", choices=["english", "bilingual", "chinese"])
    p_export_video.set_defaults(func=cmd_export_video)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
