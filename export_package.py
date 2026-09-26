"""
export_package.py -- bundles everything for ONE drama (audio/video,
every subtitle format, dub/narration track, metadata, character list)
into a single zip. Different from the whole-library backup in
tabs/library.py -- this is for archiving or sharing a single finished
title, not your whole working library.
"""

import os
import json
import zipfile

import subtitle_formats


def build_drama_export_package(db_module, drama_id: int, out_path: str,
                                lines_to_srt_fn, lines_to_bilingual_srt_fn, line_cls):
    """
    Assembles a zip containing:
      - metadata.json (drama fields + character list)
      - english.srt / chinese.srt / bilingual.srt (if lines exist)
      - the original audio/video file (if present)
      - dub_track.wav / narration_track.wav (if present)
      - reference novel translation (if present)

    Returns (out_path, manifest) where manifest lists what was actually
    included, so the caller can show the person what they're getting
    (and so a missing piece -- e.g. no dub generated yet -- doesn't
    silently produce an incomplete zip with no explanation).
    """
    drama = db_module.get_drama(drama_id)
    if not drama:
        raise ValueError(f"No drama with id {drama_id}")
    ddir = db_module.drama_dir(drama_id)
    manifest = []

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Metadata
        characters = db_module.list_characters(drama_id)
        metadata = {
            "drama": {k: v for k, v in drama.items()},
            "characters": characters,
        }
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        manifest.append("metadata.json")

        # Subtitles
        rows = db_module.load_lines(drama_id)
        if rows:
            lines = [line_cls(idx=r["idx"], start=r["start"], end=r["end"],
                               zh=r["zh"], en=r.get("en") or "", speaker=r.get("speaker"),
                               sfx=bool(r.get("sfx")))
                     for r in rows]
            # Step 25d item 6: Workspace's own export already promises "never
            # export an overlapping (invalid) cue" -- this path skipped that
            # clamp, so a line whose timing overlaps the next one (a manual
            # edit or merge can produce this) could reach the zip unclamped.
            lines, _ = subtitle_formats.clamp_overlaps(lines)
            zf.writestr("subtitles/english.srt", lines_to_srt_fn(lines, "en"))
            zf.writestr("subtitles/chinese.srt", lines_to_srt_fn(lines, "zh"))
            zf.writestr("subtitles/bilingual.srt", lines_to_bilingual_srt_fn(lines))
            manifest += ["subtitles/english.srt", "subtitles/chinese.srt", "subtitles/bilingual.srt"]
        else:
            manifest.append("(no subtitles -- drama not yet aligned/translated)")

        # Original audio/video
        for field, subdir in [("audio_filename", "source"), ("source_video_filename", "source")]:
            fname = drama.get(field)
            if fname:
                fpath = os.path.join(ddir, fname)
                if os.path.exists(fpath):
                    arcname = f"{subdir}/{fname}"
                    zf.write(fpath, arcname)
                    manifest.append(arcname)

        # Dub/narration tracks
        for track_name in ["dub_track.wav", "narration_track.wav"]:
            fpath = os.path.join(ddir, track_name)
            if os.path.exists(fpath):
                arcname = f"audio/{track_name}"
                zf.write(fpath, arcname)
                manifest.append(arcname)

        # Reference novel translation
        if drama.get("novel_reference_filename"):
            fpath = os.path.join(ddir, drama["novel_reference_filename"])
            if os.path.exists(fpath):
                arcname = "reference/novel_reference.txt"
                zf.write(fpath, arcname)
                manifest.append(arcname)

    return out_path, manifest
