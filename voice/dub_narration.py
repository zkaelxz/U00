"""
dub_narration.py -- novel narration: chapter detection, narration track
assembly and M4B audiobook export. Split out of dub.py; depends on dub's
voice and clip helpers, never the other way round.
"""

import contextlib
import os
import re
import video_export
from lib.atomic_write import replace_file
from novel_text import (  # noqa: F401 (re-exported)
    NARRATION_TRACK_NAMES, NOVEL_SOURCE_FILENAME, find_narration_track, is_chapter_heading,
    narration_paragraph_ends)

from audio_levelling import level_clip
from voice.dub import (NO_VOICE_ERROR, engine_refusal, M4B_ENCODE_TIMEOUT_SECONDS, VENV_ENGINES, VENV_LINE_FAILED,
                 clip_signature, speak_in_venv, _voice_for_signature)


# Novel narration generates a text unit of up to this many characters in
# one TTS call -- several consecutive lines of the same speaker joined, for
# better cross-sentence prosody than one call per subtitle-sized line.
NARRATION_TTS_MAX_CHARS = 500


def _narration_steps(lines, character_clone_map, paragraph_ends, narrate_original: bool = False):
    """Splits narration lines, in order, into ("blank", line) for a line
    with nothing to say and ("unit", unit) for one TTS call. A unit joins
    consecutive lines while they share a speaker (so one voice), up to
    NARRATION_TTS_MAX_CHARS -- never across a paragraph end or a chapter
    heading, which always stands alone so it can mark an audiobook chapter.

    narrate_original: speaks ln.zh (the drama's source text)
    instead of ln.en -- for novel narration's "original language" mode."""
    steps, current = [], None
    for ln in lines:
        text = (ln.zh if narrate_original else ln.en).strip()
        if not text:
            steps.append(("blank", ln))
            current = None
            continue
        clone = character_clone_map.get(ln.speaker or None)
        heading = is_chapter_heading(ln)
        if (current is not None and not heading and current["speaker"] == ln.speaker
                and len(current["text"]) + 1 + len(text) <= NARRATION_TTS_MAX_CHARS):
            current["lines"].append(ln)
            current["text"] += " " + text
        else:
            current = {"lines": [ln], "text": text, "speaker": ln.speaker, "clone": clone,
                       "error": None}
            steps.append(("unit", current))
        if heading or (paragraph_ends is not None and ln.idx in paragraph_ends):
            current = None
    return steps


def _partial_path(unit) -> str:
    return unit["clip_path"][:-len(".wav")] + ".partial.wav"


def _generate_units(units, record, language: str, clips_dir: str) -> None:
    """Calls record(unit, error) for every unit: a stored error, a clip kept from an
    earlier run, or a new clip. Units are spoken one batch per engine, so each model
    loads once."""
    in_venv = {}
    for unit in units:
        if unit["error"]:
            record(unit, unit["error"])
        elif os.path.exists(unit["clip_path"]):
            record(unit, None)
        elif unit["clone"].get("engine") in VENV_ENGINES:
            in_venv.setdefault(unit["clone"]["engine"], []).append(unit)
        else:
            # A removed or unknown engine says so in plain words.
            record(unit, engine_refusal(unit["clone"].get("engine")) or VENV_LINE_FAILED)
    if in_venv:
        # A cancel kills the batch before it can remove its spec, which holds the text.
        for name in os.listdir(clips_dir):
            if name.startswith("voice_spec_") and name.endswith(".json"):
                os.remove(os.path.join(clips_dir, name))
    for engine, batch in in_venv.items():
        requests = [{"text": u["text"], "out": _partial_path(u), "language": language,
                     "ref_audio": u["clone"].get("ref_audio"), "ref_text": u["clone"].get("ref_text"),
                     "instruct": u["clone"].get("instruct")}
                    for u in batch]
        with contextlib.closing(speak_in_venv(engine, requests, clips_dir)) as spoken:
            for i, error in spoken:
                record(batch[i], error if error else _keep_partial(requests[i]["out"],
                                                                   batch[i]["clip_path"]))
                if error and os.path.exists(requests[i]["out"]):
                    os.remove(requests[i]["out"])


def speak_sample(text: str, clone: dict, out_path: str, language: str):
    """Speaks one text in `clone`'s voice into out_path: None once the file is in place, else the
    fixed error sentence. For hearing a voice before a whole narration is generated."""
    engine = clone.get("engine")
    if engine not in VENV_ENGINES:
        return engine_refusal(engine) or VENV_LINE_FAILED
    partial = out_path[:-len(".wav")] + ".partial.wav"
    request = {"text": text, "out": partial, "language": language, "ref_audio": clone.get("ref_audio"),
               "ref_text": clone.get("ref_text"), "instruct": clone.get("instruct")}
    error = None
    with contextlib.closing(speak_in_venv(engine, [request], os.path.dirname(out_path))) as spoken:
        for _, error in spoken:
            pass
    if error:
        if os.path.exists(partial):
            os.remove(partial)
        return error
    return _keep_partial(partial, out_path)


def _keep_partial(partial: str, clip_path: str):
    """None once the finished clip is in place, else the line's fixed error (a locked file
    on Windows; OSError text names both paths)."""
    try:
        os.replace(partial, clip_path)
        return None
    except OSError:
        return VENV_LINE_FAILED


def join_segments(pieces):
    """The pieces end to end, as repeated `+=` would give. pydub's `+` copies both
    sides, so adding one clip at a time is quadratic for a book of thousands of clips;
    joining neighbours in rounds copies the audio only log2(n) times."""
    pieces = list(pieces)
    while len(pieces) > 1:
        joined = [pieces[i] + pieces[i + 1] for i in range(0, len(pieces) - 1, 2)]
        if len(pieces) % 2:
            joined.append(pieces[-1])
        pieces = joined
    return pieces[0]


def build_narration_track(lines, drama_dir: str, character_clone_map: dict, progress_cb=None,
                           gap_ms: int = 350,
                           narrate_original: bool = False, source_language: str = "zh"):
    """
    For novel-narration mode: there's no pre-existing timing to sync
    to, so clips are generated and simply concatenated in order with a
    small gap between them. Mutates each line's .start/.end to the
    actual timing of its generated audio -- so you get a usable .srt
    alongside the narration audio.

    narrate_original/source_language: when narrate_original,
    speaks ln.zh (the drama's source text, in source_language) instead of
    ln.en -- caller is responsible for warning that exported subtitles
    still need ln.en to stay bilingual.

    Each TTS call covers a unit of several consecutive lines where it
    can (see _narration_steps), so the audio has cross-sentence prosody
    while each line stays its own, shorter subtitle cue: a unit's clip
    time is divided back across its lines in proportion to their length
    (resegment.split_times, the same split re-segmentation uses), and
    every line in the unit points at the one shared clip.

    Clips are generated first, one at a time -- any that already exist from
    a prior run are reused, not regenerated -- then assembled in order.
    character_clone_map is as for clone_map_from_characters; a speaker with no entry
    (NO_VOICE_ERROR) is treated like any other failed unit.

    Returns (path_to_wav, errors) -- a line whose unit failed is skipped
    (silent gap inserted instead) rather than aborting the whole narration.
    """
    from types import SimpleNamespace
    from pydub import AudioSegment
    from resegment import split_times

    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)
    errors = []

    steps = _narration_steps(lines, character_clone_map,
                             narration_paragraph_ends(lines, drama_dir), narrate_original=narrate_original)
    units = [unit for kind, unit in steps if kind == "unit"]
    for unit in units:
        first, last = unit["lines"][0].idx, unit["lines"][-1].idx
        if unit["clone"] is None:
            unit["error"] = NO_VOICE_ERROR
            continue
        signature = clip_signature(unit["text"], _voice_for_signature(
            unit["clone"],
            lang=(source_language if narrate_original else None)))
        span = f"{first:04d}" if first == last else f"{first:04d}-{last:04d}"
        # Signature in the name: an edited unit gets a fresh clip, an unchanged
        # one is reused.
        unit["clip_path"] = os.path.join(clips_dir, f"line_{span}_{signature}.wav")

    finished = 0

    def record(unit, error):
        nonlocal finished
        unit["error"] = error
        finished += 1
        if progress_cb:
            progress_cb(finished / len(units))

    _generate_units(units, record, source_language if narrate_original else "en", clips_dir)

    pieces = [AudioSegment.silent(duration=0)]
    cursor_ms = 0
    for kind, item in steps:
        if kind == "blank":
            item.start = item.end = cursor_ms / 1000.0
            continue
        if item["error"]:
            for ln in item["lines"]:
                errors.append({"line_idx": ln.idx, "error": item["error"]})
                ln.start = cursor_ms / 1000.0
                cursor_ms += gap_ms
                ln.end = cursor_ms / 1000.0
                pieces.append(AudioSegment.silent(duration=gap_ms))
            continue

        # Levelled here, not in the clip file, so a cached clip and its clip_signature stay valid.
        clip = level_clip(AudioSegment.from_file(item["clip_path"]))
        start_ms, end_ms = cursor_ms, cursor_ms + len(clip)
        members = item["lines"]
        # Split proportionally by whichever text was actually
        # spoken (narrate_original: the source text, not the translation).
        _split_texts = [(ln.zh if narrate_original else ln.en) for ln in members]
        cuts = (split_times(SimpleNamespace(start=start_ms, end=end_ms), _split_texts)
                if len(members) > 1 else [])
        edges = [start_ms] + cuts + [end_ms]
        dub_filename = os.path.relpath(item["clip_path"], drama_dir)
        for k, ln in enumerate(members):
            ln.dub_filename = dub_filename
            ln.start = edges[k] / 1000.0
            ln.end = edges[k + 1] / 1000.0
        pieces.append(clip)
        cursor_ms = end_ms
        pieces.append(AudioSegment.silent(duration=gap_ms))
        cursor_ms += gap_ms

    out_path = os.path.join(drama_dir, NARRATION_TRACK_NAMES[0])
    track = join_segments(pieces)
    # Written beside the final name and renamed, so a kill mid-export can't leave a
    # truncated track that the Reader would serve as complete.
    part_path = out_path + ".part"
    try:
        track.export(part_path, format="flac")
        # Retries while the Reader is still streaming the old track on Windows.
        replace_file(part_path, out_path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(part_path)
    # A regenerated title must not keep serving, or backing up, its old WAV copy.
    legacy = os.path.join(drama_dir, NARRATION_TRACK_NAMES[1])
    if os.path.isfile(legacy):
        os.remove(legacy)
    return out_path, errors


def narration_chapters(lines, paragraph_ends=None, narrate_original: bool = False) -> list:
    """[(start_seconds, title), ...] for an audiobook's chapter markers,
    from lines already timed by build_narration_track. The novel's own
    chapter headings when it has any; otherwise one chapter per paragraph
    of its source text; otherwise (source text missing or edited since)
    one per generated clip, which never crosses a paragraph end.

    narrate_original: "voiced" and chapter titles come from
    ln.zh (what was actually spoken) instead of ln.en."""
    text_field = "zh" if narrate_original else "en"
    voiced = [ln for ln in lines if getattr(ln, text_field).strip()]
    if not voiced:
        return []
    headings = [ln for ln in voiced if is_chapter_heading(ln)]
    if headings:
        starts = headings if headings[0] is voiced[0] else [voiced[0]] + headings
    elif paragraph_ends is not None:
        starts, new_paragraph = [], True
        for ln in lines:
            if getattr(ln, text_field).strip() and new_paragraph:
                starts.append(ln)
                new_paragraph = False
            if ln.idx in paragraph_ends:
                new_paragraph = True
    else:
        starts, previous_clip = [], object()
        for ln in voiced:
            if ln.dub_filename != previous_clip:
                starts.append(ln)
            previous_clip = ln.dub_filename
    chapters = []
    for ln in starts:
        title = " ".join(getattr(ln, text_field).split())
        chapters.append((ln.start, title if len(title) <= 60 else title[:59] + "…"))
    return chapters


def _ffmetadata_escape(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def _track_duration_ms(path: str):
    if path.lower().endswith(".wav"):
        import wave
        try:
            with wave.open(path, "rb") as w:
                return int(w.getnframes() * 1000 / w.getframerate())
        except Exception:
            return None
    import media_inspect
    seconds = media_inspect.duration_seconds(path, timeout=30)
    return int(seconds * 1000) if seconds else None


def narration_ffmetadata(chapters, total_ms: int, title: str = None) -> str:
    """ffmpeg's FFMETADATA1 text for the given chapters. A chapter that
    wouldn't start after the one before it (two markers on the same
    instant) is dropped rather than written as a zero-length chapter."""
    out = [";FFMETADATA1"]
    if title:
        out.append(f"title={_ffmetadata_escape(title)}")
    kept = []
    for start_s, name in chapters:
        start_ms = int(round(start_s * 1000))
        if start_ms < total_ms and (not kept or start_ms > kept[-1][0]):
            kept.append((start_ms, name))
    for i, (start_ms, name) in enumerate(kept):
        end_ms = kept[i + 1][0] if i + 1 < len(kept) else total_ms
        out += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}",
                f"title={_ffmetadata_escape(name)}"]
    return "\n".join(out) + "\n"


def export_narration_m4b(lines, drama_dir: str, title: str = None, out_path: str = None,
                         narrate_original: bool = False, cancel_job_id: str = None) -> str:
    """Encodes narration_track.flac (or the .wav of an older title) as an M4B audiobook (AAC) with chapter
    markers (narration_chapters). Needs the narration generated first --
    lines carrying the timing build_narration_track gave them -- and
    ffmpeg on PATH. Returns the .m4b path. narrate_original:
    chapter titles come from the source text, matching the narration
    audio's own language. cancel_job_id: a thread job whose cancel request
    kills the ffmpeg run (background_jobs.run_cancellable)."""
    import subprocess
    track_name = find_narration_track(drama_dir)
    if not track_name:
        raise FileNotFoundError("No narration track yet -- generate the narration first.")
    track_path = os.path.join(drama_dir, track_name)
    total_ms = _track_duration_ms(track_path) or int(max((ln.end for ln in lines), default=0.0) * 1000)
    chapters = narration_chapters(lines, narration_paragraph_ends(lines, drama_dir),
                                  narrate_original=narrate_original)
    meta_path = os.path.join(drama_dir, "narration_chapters.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(narration_ffmetadata(chapters, total_ms, title))
    out_path = out_path or os.path.join(drama_dir, "narration.m4b")
    # The track can come from a backup import, so ffmpeg may open only local files, as the
    # formats this app writes. The export job encodes into its own work folder and moves the
    # result into place itself, so a kill can't leave a truncated audiobook under the final name.
    cmd = ["ffmpeg", "-y", *video_export.local_input("flac,wav"), "-i", track_path,
           *video_export.local_input("ffmetadata"), "-i", meta_path, "-map", "0:a",
           "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", "64k",
           "-f", "ipod", out_path]
    if cancel_job_id:
        import background_jobs
        background_jobs.run_cancellable(cancel_job_id, cmd, timeout=M4B_ENCODE_TIMEOUT_SECONDS)
    else:
        subprocess.run(cmd, check=True, capture_output=True,
                       timeout=M4B_ENCODE_TIMEOUT_SECONDS)
    return out_path


def build_narration_subprocess_worker(lines, drama_dir, character_clone_map, result_queue,
                                      narrate_original=False, source_language="zh"):
    """Process-job entry point for build_narration_track(), so Cancel can
    actually stop it. Safe to hard-stop: clips are reused from a prior
    partial run, and clips in flight are written under a temporary name
    first. Puts back the mutated lines because build_narration_track
    rewrites .start/.end and .dub_filename. Must stay a plain top-level
    picklable function; callers bind the options by keyword so
    background_jobs' trailing result_queue lands in the right slot."""
    from jobs import process_worker
    process_worker.start_own_process_group()

    def report(frac):
        # Clips are about 95% of the work; the join and export after them get the rest, so
        # a long book does not sit at "done" while it is still gluing.
        scaled = frac * 0.95
        process_worker.report_progress(
            result_queue, scaled,
            "Joining the clips into one track..." if frac >= 1.0 else f"Speaking clips ({scaled:.0%})...")

    try:
        out_path, errors = build_narration_track(
            lines, drama_dir, character_clone_map, progress_cb=report,
            narrate_original=narrate_original, source_language=source_language)
        result_queue.put(("ok", {"lines": lines, "out_path": out_path, "errors": errors}))
    except Exception as exc:
        result_queue.put(process_worker.error_item(exc))
