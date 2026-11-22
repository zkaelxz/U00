"""
dub_narration.py -- novel narration: chapter detection, narration track
assembly and M4B audiobook export. Split out of dub.py; depends on dub's
voice and clip helpers, never the other way round.
"""

import contextlib
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import video_export
from lib.atomic_write import remove_file, replace_file
from novel_text import (  # noqa: F401 (re-exported)
    NARRATION_TRACK_NAMES, NOVEL_SOURCE_FILENAME, find_narration_track, heading_title,
    is_chapter_heading, narration_paragraph_ends)

from audio_levelling import level_clip
from voice.dub import (NO_VOICE_ERROR, engine_refusal, M4B_ENCODE_TIMEOUT_SECONDS, VENV_ENGINES, VENV_LINE_FAILED,
                 clip_signature, speak_in_venv, _voice_for_signature)


# Novel narration generates a text unit of up to this many characters in
# one TTS call -- several consecutive lines of the same speaker joined, for
# better cross-sentence prosody than one call per subtitle-sized line.
NARRATION_TTS_MAX_CHARS = 500

# One even gap: longer pauses at paragraph ends put a stop after every line of a novel
# written one paragraph per line, which Alex found worse (2026-11-22).
CLIP_GAP_MS = 350
PACE_MIN, PACE_MAX = 0.75, 1.25


def _repeated_headings(lines, narrate_original: bool) -> set:
    """idx of each chapter heading line followed straight away by another heading that
    gives the same title, or the start of it (a site's own title over the chapter's,
    "第1章 第一章 若是我早些……" then "第一章 若是我早些来"), so the chapter is
    announced once, by the later line."""
    voiced = [ln for ln in lines if (ln.zh if narrate_original else ln.en).strip()]
    out = set()
    for ln, nxt in zip(voiced, voiced[1:]):
        if not (is_chapter_heading(ln) and is_chapter_heading(nxt)):
            continue
        a = heading_title(ln.zh if narrate_original else ln.en)
        b = heading_title(nxt.zh if narrate_original else nxt.en)
        if a and b and (a.startswith(b) or b.startswith(a)):
            out.add(ln.idx)
    return out


def _narration_steps(lines, character_clone_map, paragraph_ends, narrate_original: bool = False):
    """Splits narration lines, in order, into ("blank", line) for a line
    with nothing to say (or a repeated chapter heading) and ("unit", unit) for one TTS call. A unit joins
    consecutive lines while they share a speaker (so one voice), up to
    NARRATION_TTS_MAX_CHARS -- never across a paragraph end or a chapter
    heading, which always stands alone so it can mark an audiobook chapter.

    narrate_original: speaks ln.zh (the drama's source text)
    instead of ln.en -- for novel narration's "original language" mode."""
    steps, current = [], None
    repeated = _repeated_headings(lines, narrate_original)
    for ln in lines:
        text = (ln.zh if narrate_original else ln.en).strip()
        if not text or ln.idx in repeated:
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


# pydub keeps 8-bit audio signed in memory (it adds the WAV bias back only when writing a WAV).
_PCM_FORMATS = {1: "s8", 2: "s16le", 3: "s24le", 4: "s32le"}
_PIPE_CHUNK_BYTES = 8 * 1024 * 1024
# The engines' own output, for a track that has no clip to take the format from.
_DEFAULT_FORMAT = (24000, 1, 2)


def _start_ffmpeg(cmd, stderr):
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr)


class _FlacStream:
    """ffmpeg encoding the track to FLAC from raw samples fed one clip at a time, so a book's
    narration never sits in memory or in a temporary WAV (pydub's export writes one to the
    system temp folder and leaves it there when it fails). Silence is written as zeros up to
    each clip's start, so the audio stays on the line times. ffmpeg is killed once the run has
    waited on it for `timeout` seconds in all (a hung ffmpeg would block a write for ever) and
    on any failure; time spent reading the clips does not count. `track_format` (rate,
    channels, sample width) is the track's; without it the first clip's is used."""

    def __init__(self, path: str, pace: float, workdir: str, timeout: float = M4B_ENCODE_TIMEOUT_SECONDS,
                 track_format=None):
        self.path, self.pace, self.workdir, self.timeout = path, pace, workdir, timeout
        self.proc = self.err = self.timer = None
        self.track_format, self.format = track_format, None
        self.frames, self.timed_out, self.time_left = 0, False, timeout

    def _open(self, frame_rate: int, channels: int, sample_width: int) -> None:
        self.format = (frame_rate, channels, sample_width)
        tempo = ["-af", f"atempo={self.pace:.4f}"] if self.pace != 1.0 else []
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", _PCM_FORMATS[sample_width],
               "-ar", str(frame_rate), "-ac", str(channels), "-i", "pipe:0",
               *tempo, "-c:a", "flac", "-f", "flac", self.path]
        # In the title's folder, not the system temp folder; the OS removes it on close or kill.
        self.err = tempfile.TemporaryFile(dir=self.workdir)
        try:
            self.proc = _start_ffmpeg(cmd, self.err)
        except BaseException:
            self.err.close()
            raise

    def _time_out(self) -> None:
        self.timed_out = True
        with contextlib.suppress(OSError):
            self.proc.kill()

    @contextlib.contextmanager
    def _waiting_on_ffmpeg(self):
        """Counts the time inside against the timeout, and kills ffmpeg when it runs out."""
        self.timer = threading.Timer(max(self.time_left, 0.0), self._time_out)
        self.timer.daemon = True
        began = time.monotonic()
        self.timer.start()
        try:
            yield
        finally:
            self.timer.cancel()
            self.time_left -= time.monotonic() - began

    def _write(self, data) -> None:
        view = memoryview(data)
        try:
            with self._waiting_on_ffmpeg():
                for i in range(0, len(view), _PIPE_CHUNK_BYTES):
                    self.proc.stdin.write(view[i:i + _PIPE_CHUNK_BYTES])
        except OSError:
            # ffmpeg stopped reading (a full disk, say): its own message says why.
            self._fail()

    def _pad_to(self, ms: float) -> None:
        rate, channels, width = self.format
        missing = round(ms * rate / 1000) - self.frames
        if missing <= 0:
            return
        self.frames += missing
        frame_bytes = channels * width
        chunk_frames = max(1, _PIPE_CHUNK_BYTES // frame_bytes)
        while missing > 0:
            n = min(missing, chunk_frames)
            self._write(bytes(n * frame_bytes))
            missing -= n

    def add(self, clip, at_ms: float):
        """Writes `clip` starting at `at_ms` into the track; returns the clip as written (in the
        track's format), whose len() is its length in ms."""
        if self.proc is None:
            self._open(*(self.track_format or (clip.frame_rate, clip.channels, clip.sample_width)))
        rate, channels, width = self.format
        if clip.frame_rate != rate:
            clip = clip.set_frame_rate(rate)
        if clip.channels != channels:
            clip = clip.set_channels(channels)
        if clip.sample_width != width:
            clip = clip.set_sample_width(width)
        self._pad_to(at_ms)
        data = clip.raw_data
        self.frames += len(data) // (channels * width)
        self._write(data)
        return clip

    def finish(self, end_ms: float) -> None:
        if self.proc is None:
            self._open(*(self.track_format or _DEFAULT_FORMAT))
        self._pad_to(end_ms)
        try:
            with self._waiting_on_ffmpeg():
                self.proc.stdin.close()
                self.proc.wait()
        except OSError:
            self._fail()
        if self.proc.returncode or self.timed_out:
            self._fail()
        self.abort()

    def _fail(self):
        self._stop()
        if self.timed_out:
            message = (f"ffmpeg took more than {int(self.timeout // 3600)} hours to write the "
                       "narration track, so it was stopped.")
        else:
            self.err.seek(0)
            # ffmpeg names the output path; job errors never carry filesystem paths.
            said = self.err.read()[-500:].decode("utf-8", "replace").replace(self.path, os.path.basename(self.path))
            message = f"ffmpeg could not write the narration track: {said.strip()}"
        self.abort()
        raise RuntimeError(message)

    def _stop(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        with contextlib.suppress(OSError):
            self.proc.stdin.close()
        if self.proc.returncode is None:
            with contextlib.suppress(OSError):
                self.proc.kill()
        self.proc.wait()

    def abort(self) -> None:
        """Stops ffmpeg and closes its error file; safe to call more than once."""
        if self.proc is not None:
            self._stop()
            self.err.close()


def partial_track_path(drama_dir: str) -> str:
    """Where a track is written before it replaces the old one."""
    return os.path.join(drama_dir, NARRATION_TRACK_NAMES[0]) + ".part"


# A part name of its own is used while Windows still holds the usual one (below).
_PART_NAME = re.compile(re.escape(NARRATION_TRACK_NAMES[0]) + r"(?:\.[0-9a-f]{8})?\.part")


def remove_partial_track(drama_dir: str) -> None:
    """For a run that was killed (a cancel) before it could remove its half-written track.
    A killed ffmpeg can hold the file for a moment on Windows, so each removal waits a little."""
    try:
        names = [e.name for e in os.scandir(drama_dir) if _PART_NAME.fullmatch(e.name)]
    except OSError:
        return
    for name in names:
        with contextlib.suppress(OSError):
            remove_file(os.path.join(drama_dir, name))


def _new_partial_track(drama_dir: str) -> str:
    """This run's part path, with any left by a killed run removed. When one can't be removed
    (an ffmpeg that is still being killed holds it), this run writes beside it under a name of
    its own rather than failing on ffmpeg's open; the next run removes the old one."""
    remove_partial_track(drama_dir)
    path = partial_track_path(drama_dir)
    if not os.path.exists(path):
        return path
    return f"{path[:-len('.part')]}.{uuid.uuid4().hex[:8]}.part"


def _wav_format(path: str):
    """(rate, channels, sample width) from a WAV clip's header, or None when it has no
    readable one: read without decoding the clip."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(head):
        size = int.from_bytes(head[pos + 4:pos + 8], "little")
        if head[pos:pos + 4] == b"fmt " and pos + 24 <= len(head):
            channels = int.from_bytes(head[pos + 10:pos + 12], "little")
            rate = int.from_bytes(head[pos + 12:pos + 16], "little")
            width = int.from_bytes(head[pos + 22:pos + 24], "little") // 8
            return (rate, channels, width) if rate and channels and width in _PCM_FORMATS else None
        pos += 8 + size + (size & 1)
    return None


def _track_format(clip_paths):
    """The highest rate, channel count and sample width among the clips, so no clip loses
    quality to another's lower format; None when no clip says."""
    formats = [f for f in map(_wav_format, clip_paths) if f]
    if not formats:
        return None
    return tuple(max(f[i] for f in formats) for i in range(3))


# Only names build_narration_track writes: anything else in the folder is left alone.
_CLIP_NAME = re.compile(r"line_\d{4,}(?:-\d{4,})?_[0-9a-f]{10}\.wav")


def remove_unused_clips(drama_dir: str, used) -> int:
    """Deletes the narration clips in drama_dir's dub_clips folder that no entry of `used`
    (the lines' dub_filename values) names. Returns how many were deleted; a file that
    can't be deleted is left for the next run."""
    clips_dir = os.path.join(drama_dir, "dub_clips")
    # A dub_clips that is a symlink or junction leads out of the title's folder: nothing there is ours.
    inside = os.path.join(os.path.realpath(drama_dir), "dub_clips")
    if os.path.normcase(os.path.realpath(clips_dir)) != os.path.normcase(inside):
        return 0
    keep = {re.split(r"[\\/]", name)[-1] for name in used if name}
    try:
        entries = list(os.scandir(clips_dir))
    except OSError:
        return 0
    removed = 0
    for entry in entries:
        if entry.name in keep or not _CLIP_NAME.fullmatch(entry.name):
            continue
        if not entry.is_file(follow_symlinks=False):
            continue
        with contextlib.suppress(OSError):
            os.remove(entry.path)
            removed += 1
    return removed


def _check_pace(pace: float) -> None:
    if not PACE_MIN <= pace <= PACE_MAX:
        raise ValueError(f"Narration pace must be between {PACE_MIN} and {PACE_MAX}.")


def _stretch_times(lines, pace: float) -> None:
    """Line times to match a track played at `pace`."""
    if pace != 1.0:
        for ln in lines:
            ln.start, ln.end = ln.start / pace, ln.end / pace


def build_narration_track(lines, drama_dir: str, character_clone_map: dict, progress_cb=None,
                           pace: float = 1.0,
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

    Clips are generated first, each to its own file -- any that already exist from
    a prior run are reused, not regenerated -- then fed to ffmpeg one clip at a time
    (_FlacStream), so memory holds about one clip whatever the book's length.
    character_clone_map is as for clone_map_from_characters; a speaker with no entry
    (NO_VOICE_ERROR) is treated like any other failed unit.

    pace below 1 slows the track (not the saved clips) and stretches line times to match.

    Returns (path_to_flac, errors) -- a line whose unit failed is skipped
    (silent gap inserted instead) rather than aborting the whole narration.
    RuntimeError, with the title's track left as it was, when more than half the
    spoken lines failed.
    """
    from types import SimpleNamespace
    from pydub import AudioSegment
    from resegment import split_times

    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)
    errors = []

    _check_pace(pace)
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

    remove_partial_track(drama_dir)
    _generate_units(units, record, source_language if narrate_original else "en", clips_dir)

    _refuse_mostly_failed(units, drama_dir)

    out_path = os.path.join(drama_dir, NARRATION_TRACK_NAMES[0])
    # Written beside the final name and renamed, so a kill mid-export can't leave a
    # truncated track that the Reader would serve as complete.
    part_path = _new_partial_track(drama_dir)
    stream = _FlacStream(part_path, pace, drama_dir,
                         track_format=_track_format(u["clip_path"] for u in units if not u["error"]))
    try:
        cursor_ms = 0
        for kind, item in steps:
            if kind == "blank":
                item.start = item.end = cursor_ms / 1000.0
                # Nothing is spoken for it in this run, so a clip named by an earlier one is stale.
                item.dub_filename = None
                continue
            if item["error"]:
                for ln in item["lines"]:
                    errors.append({"line_idx": ln.idx, "error": item["error"]})
                    ln.start = cursor_ms / 1000.0
                    cursor_ms += CLIP_GAP_MS
                    ln.end = cursor_ms / 1000.0
                continue

            # Levelled here, not in the clip file, so a cached clip and its clip_signature stay valid.
            clip = stream.add(level_clip(AudioSegment.from_file(item["clip_path"])), cursor_ms)
            start_ms, end_ms = cursor_ms, cursor_ms + len(clip)
            # Freed before the next clip is decoded, so only one is ever in memory.
            del clip
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
            # Shortened by the pace, so the gap is the same length once the track is slowed.
            cursor_ms = end_ms + round(CLIP_GAP_MS * pace)
        stream.finish(cursor_ms)
        # Retries while the Reader is still streaming the old track on Windows.
        replace_file(part_path, out_path)
    finally:
        stream.abort()
        remove_partial_track(drama_dir)

    _stretch_times(lines, pace)
    # A regenerated title must not keep serving, or backing up, its old WAV copy.
    legacy = os.path.join(drama_dir, NARRATION_TRACK_NAMES[1])
    if os.path.isfile(legacy):
        os.remove(legacy)
    return out_path, errors


def _refuse_mostly_failed(units, drama_dir: str) -> None:
    """Keeps the title's track when more than half the spoken lines failed (an engine that
    would not start, say): a track that is mostly silence would replace a good one."""
    total = sum(len(u["lines"]) for u in units)
    failed = [u for u in units if u["error"]]
    failed_lines = sum(len(u["lines"]) for u in failed)
    if failed_lines * 2 <= total:
        return
    kept = ("so the previous track was kept" if find_narration_track(drama_dir)
            else "so no track was made")
    raise RuntimeError(f"Narration failed on {failed_lines} of {total} lines, {kept}. {failed[0]['error']}")


def narration_chapters(lines, paragraph_ends=None, narrate_original: bool = False) -> list:
    """[(start_seconds, title), ...] for an audiobook's chapter markers,
    from lines already timed by build_narration_track. The novel's own
    chapter headings when it has any; otherwise one chapter per paragraph
    of its source text; otherwise (source text missing or edited since)
    one per generated clip, which never crosses a paragraph end.

    narrate_original: "voiced" and chapter titles come from
    ln.zh (what was actually spoken) instead of ln.en."""
    text_field = "zh" if narrate_original else "en"
    repeated = _repeated_headings(lines, narrate_original)
    voiced = [ln for ln in lines if getattr(ln, text_field).strip() and ln.idx not in repeated]
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
                                      narrate_original=False, source_language="zh", pace=1.0):
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
            lines, drama_dir, character_clone_map, progress_cb=report, pace=pace,
            narrate_original=narrate_original, source_language=source_language)
        # failed_count: the job result lists only the first errors.
        result_queue.put(("ok", {"lines": lines, "out_path": out_path, "errors": errors,
                                 "failed_count": len(errors)}))
    except Exception as exc:
        result_queue.put(process_worker.error_item(exc))
