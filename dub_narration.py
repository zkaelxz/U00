"""
dub_narration.py -- novel narration: chapter detection, narration track
assembly and M4B audiobook export. Split out of dub.py, which re-exports
every name here; import it through dub, not directly (it needs dub's
voice and clip helpers, and dub imports this module).
"""

import os
import re

from dub import (NO_VOICE_ERROR, M4B_ENCODE_TIMEOUT_SECONDS, clip_signature,
                 _synthesize_cloned, _voice_for_signature)


# Novel narration generates a text unit of up to this many characters in
# one TTS call -- several consecutive lines of the same speaker joined, for
# better cross-sentence prosody than one call per subtitle-sized line.
NARRATION_TTS_MAX_CHARS = 500

NOVEL_SOURCE_FILENAME = "novel_narration_source.txt"

# A line that's a chapter heading of its own: 第十二章 / 第3回 / 第二卷 /
# 序章 / "Chapter 12: Title" / "Prologue". Kept short and without
# sentence-ending punctuation, so an ordinary sentence that merely starts
# the same way (第一回合他就输了。 / "Chapter and verse, ...") isn't one.
_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(第\s*[0-9０-９零〇一二三四五六七八九十百千两兩]+\s*[章回节節卷]|序章|序言|楔子|引子|"
    r"尾声|尾聲|后记|後記|番外|(chapter\s+\S+|prologue|epilogue)\s*(?:[:：.\-–—]|$))", re.IGNORECASE)
_CHAPTER_HEADING_MAX_CHARS = 60
_SENTENCE_END = tuple("。！？!?…」』”\"")


def is_chapter_heading(ln) -> bool:
    for text in (ln.zh or "", ln.en or ""):
        text = text.strip()
        if (text and len(text) <= _CHAPTER_HEADING_MAX_CHARS and not text.endswith(_SENTENCE_END)
                and _CHAPTER_HEADING_RE.match(text)):
            return True
    return False


def narration_paragraph_ends(lines, drama_dir: str):
    """Set of line idx that end a paragraph of the novel's saved source
    text (see core.novel_paragraph_ends), or None if there's no source
    text or the lines no longer match it."""
    from core import novel_paragraph_ends
    path = os.path.join(drama_dir, NOVEL_SOURCE_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return novel_paragraph_ends(lines, f.read())


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
    character_clone_map is as for build_dub_track; a speaker with no entry
    (NO_VOICE_ERROR) is treated like any other failed unit.

    Returns (path_to_wav, errors) -- a line whose unit failed is skipped
    (silent gap inserted instead) rather than aborting the whole narration.
    """
    from types import SimpleNamespace
    from pydub import AudioSegment
    from translate_engines import call_with_backoff
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
        # Signature in the name for the same reason as build_dub_track's
        # clips: an edited unit gets a fresh clip, an unchanged one is reused.
        unit["clip_path"] = os.path.join(clips_dir, f"line_{span}_{signature}.wav")

    def generate(unit):
        """Returns an error string instead of raising, so one failed unit
        can't stop the rest."""
        # Written under a temporary name and renamed once complete, so a
        # Cancel (which kills the process, possibly mid-write) never leaves
        # a partial clip behind for the next run's "already exists" check
        # to reuse.
        partial = unit["clip_path"][:-len(".wav")] + ".partial.wav"
        try:
            call_with_backoff(lambda: _synthesize_cloned(unit["clone"], unit["text"], partial))
            os.replace(partial, unit["clip_path"])
            return None
        except Exception as e:
            if os.path.exists(partial):
                os.remove(partial)
            return str(e)

    finished = 0

    def record(unit, error):
        nonlocal finished
        unit["error"] = error
        finished += 1
        if progress_cb:
            progress_cb(finished / len(units))

    for unit in units:
        if unit["error"]:
            record(unit, unit["error"])
        elif os.path.exists(unit["clip_path"]):
            record(unit, None)
        else:
            record(unit, generate(unit))

    track = AudioSegment.silent(duration=0)
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
                track += AudioSegment.silent(duration=gap_ms)
            continue

        clip = AudioSegment.from_file(item["clip_path"])
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
        track += clip
        cursor_ms = end_ms
        track += AudioSegment.silent(duration=gap_ms)
        cursor_ms += gap_ms

    out_path = os.path.join(drama_dir, "narration_track.wav")
    track.export(out_path, format="wav")
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


def _wav_duration_ms(path: str):
    import wave
    try:
        with wave.open(path, "rb") as w:
            return int(w.getnframes() * 1000 / w.getframerate())
    except Exception:
        return None


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
    """Encodes narration_track.wav as an M4B audiobook (AAC) with chapter
    markers (narration_chapters). Needs the narration generated first --
    lines carrying the timing build_narration_track gave them -- and
    ffmpeg on PATH. Returns the .m4b path. narrate_original:
    chapter titles come from the source text, matching the narration
    audio's own language. cancel_job_id: a thread job whose cancel request
    kills the ffmpeg run (background_jobs.run_cancellable)."""
    import subprocess
    wav_path = os.path.join(drama_dir, "narration_track.wav")
    if not os.path.exists(wav_path):
        raise FileNotFoundError("No narration_track.wav yet -- generate the narration first.")
    total_ms = _wav_duration_ms(wav_path) or int(max((ln.end for ln in lines), default=0.0) * 1000)
    chapters = narration_chapters(lines, narration_paragraph_ends(lines, drama_dir),
                                  narrate_original=narrate_original)
    meta_path = os.path.join(drama_dir, "narration_chapters.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(narration_ffmetadata(chapters, total_ms, title))
    out_path = out_path or os.path.join(drama_dir, "narration.m4b")
    cmd = ["ffmpeg", "-y", "-i", wav_path, "-i", meta_path, "-map", "0:a",
           "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", "64k",
           "-f", "ipod", out_path]
    if cancel_job_id:
        import background_jobs
        background_jobs.run_cancellable(cancel_job_id, cmd)
    else:
        subprocess.run(cmd, check=True, capture_output=True,
                       timeout=M4B_ENCODE_TIMEOUT_SECONDS)
    return out_path
