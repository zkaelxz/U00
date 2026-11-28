"""
dub_narration.py -- novel narration: chapter detection, narration track
assembly and M4B audiobook export. Split out of dub.py; depends on dub's
voice and clip helpers, never the other way round.
"""

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import video_export
from lib.atomic_write import remove_file, replace_file
from novel_text import (  # noqa: F401 (re-exported)
    NARRATION_TRACK_NAMES, NARRATION_WAITING_NAME, NOVEL_SOURCE_FILENAME, find_narration_track, heading_title,
    is_chapter_heading, narration_paragraph_ends)

from audio_levelling import level_clip
from voice.dub import (NO_VOICE_ERROR, engine_refusal, M4B_ENCODE_TIMEOUT_SECONDS, VENV_ENGINES, VENV_LINE_FAILED,
                 clip_signature, speak_in_venv, _voice_for_signature)
from lib.no_window import NO_WINDOW


# Novel narration generates a text unit of up to this many characters in
# one TTS call -- several consecutive lines of the same speaker joined, for
# better cross-sentence prosody than one call per subtitle-sized line.
NARRATION_TTS_MAX_CHARS = 500

# One even gap: longer pauses at paragraph ends put a stop after every line of a novel
# written one paragraph per line, which the owner found worse (2026-11-22).
CLIP_GAP_MS = 350
PACE_MIN, PACE_MAX = 0.75, 1.25

# A book's narration runs for hours; one log line this often shows whether memory or disk
# crept up and how far it got if it stops.
PROGRESS_LOG_SECONDS = 600


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
                     "instruct": u["clone"].get("instruct"), "cfg_weight": u["clone"].get("cfg_weight")}
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
               "ref_text": clone.get("ref_text"), "instruct": clone.get("instruct"),
               "cfg_weight": clone.get("cfg_weight")}
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


def _hours_minutes(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _process_rss_mb():
    """This process's resident memory in MB, or None where it can't be read."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                            ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
                            ("quota_peak_paged", ctypes.c_size_t), ("quota_paged", ctypes.c_size_t),
                            ("quota_peak_nonpaged", ctypes.c_size_t), ("quota_nonpaged", ctypes.c_size_t),
                            ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t)]
            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            # Without argtypes ctypes passes the 64-bit pseudo-handle as a 32-bit int and refuses.
            kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters),
                                                         wintypes.DWORD]
            if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                                    ctypes.byref(counters), counters.cb):
                return None
            return counters.working_set / 2 ** 20
        with open("/proc/self/status", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024
    except Exception:
        pass
    return None


def _never_raises(method):
    """A log line that can't be written must not stop the narration it describes."""
    def quiet(*args, **kwargs):
        with contextlib.suppress(Exception):
            method(*args, **kwargs)
    return quiet


class _RunLog:
    """Log lines for a narration run: what was reused, memory and disk every
    PROGRESS_LOG_SECONDS, and the track's length against the time it took. Lines carry
    counts and sizes only, never the title's text or paths."""

    def __init__(self, drama_dir: str):
        self.drama_dir, self.done = drama_dir, {}
        self.began = self.last = time.monotonic()

    @property
    def log(self):
        import applog
        return applog.get_logger()

    def _resources(self) -> str:
        import memory_headroom
        rss, ram = _process_rss_mb(), memory_headroom.read_memory_mb("ram")
        try:
            disk = f"{shutil.disk_usage(self.drama_dir).free / 2 ** 30:.1f} GB"
        except OSError:
            disk = "unknown"
        return (f"process memory {'unknown' if rss is None else f'{rss:.0f} MB'}, "
                f"free RAM {'unknown' if ram is None else f'{ram[1]:.0f} MB'}, free disk {disk}")

    @_never_raises
    def started(self, units) -> None:
        to_speak = [u for u in units if not u["error"] and not os.path.exists(u["clip_path"])]
        self.log.info("Narration started: %d clips, %d reused from an earlier run, %d to speak; %s",
                      len(units), len(units) - len(to_speak), len(to_speak), self._resources())

    @_never_raises
    def tick(self, stage: str, total: int) -> None:
        """One more unit through `stage`."""
        done = self.done[stage] = self.done.get(stage, 0) + 1
        now = time.monotonic()
        if now - self.last < PROGRESS_LOG_SECONDS:
            return
        self.last = now
        self.log.info("Narration %s: %d of %d after %s; %s", stage, done, total,
                      _hours_minutes(now - self.began), self._resources())

    @_never_raises
    def finished(self, expected_ms: float, track_path: str) -> None:
        actual_ms = _track_duration_ms(track_path)
        took = _hours_minutes(time.monotonic() - self.began)
        if actual_ms is None:
            self.log.info("Narration track made in %s; expected length %s, actual length unreadable; %s",
                          took, _hours_minutes(expected_ms / 1000), self._resources())
            return
        line = (f"Narration track made in {took}: {_hours_minutes(actual_ms / 1000)} long, "
                f"expected {_hours_minutes(expected_ms / 1000)}; {self._resources()}")
        if expected_ms and abs(actual_ms - expected_ms) > 0.05 * expected_ms:
            self.log.warning("%s (%+.0f%%, more than 5%% off)", line, 100 * (actual_ms - expected_ms) / expected_ms)
        else:
            self.log.info("%s", line)

    @_never_raises
    def limit_hit(self, limit_seconds: float) -> None:
        self.log.warning("Narration stopped by the %s ffmpeg wait limit after %s; %s",
                         _hours_minutes(limit_seconds), _hours_minutes(time.monotonic() - self.began),
                         self._resources())


def _start_ffmpeg(cmd, stderr):
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr, creationflags=NO_WINDOW)


class _FlacStream:
    """ffmpeg encoding the track to FLAC from raw samples fed one clip at a time, so a book's
    narration never sits in memory or in a temporary WAV (pydub's export writes one to the
    system temp folder and leaves it there when it fails). Silence is written as zeros up to
    each clip's start, so the audio stays on the line times. ffmpeg is killed once the run has
    waited on it for `timeout` seconds in all (a hung ffmpeg would block a write for ever) and
    on any failure; time spent reading the clips does not count. `track_format` (rate,
    channels, sample width) is the track's; without it the first clip's is used."""

    def __init__(self, path: str, pace: float, workdir: str, timeout: float = M4B_ENCODE_TIMEOUT_SECONDS,
                 track_format=None, run_log=None):
        self.path, self.pace, self.workdir, self.timeout = path, pace, workdir, timeout
        self.run_log = run_log
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
            if self.run_log:
                self.run_log.limit_hit(self.timeout)
            message = (f"ffmpeg took more than {int(self.timeout // 3600)} hours to write the "
                       "narration track, so it was stopped. The spoken clips are kept, so "
                       "starting the narration again only joins them.")
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


def _name_clips(units, clips_dir: str, lang) -> None:
    for unit in units:
        first, last = unit["lines"][0].idx, unit["lines"][-1].idx
        if unit["clone"] is None:
            unit["error"] = NO_VOICE_ERROR
            continue
        signature = clip_signature(unit["text"], _voice_for_signature(unit["clone"], lang=lang))
        span = f"{first:04d}" if first == last else f"{first:04d}-{last:04d}"
        # Signature in the name: an edited unit gets a fresh clip, an unchanged
        # one is reused.
        unit["clip_path"] = os.path.join(clips_dir, f"line_{span}_{signature}.wav")


def build_narration_track(lines, drama_dir: str, character_clone_map: dict, progress_cb=None,
                           pace: float = 1.0,
                           narrate_original: bool = False, source_language: str = "zh",
                           on_partial=None):
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

    on_partial: while the clips are made, the part spoken so far is joined into the title's
    track at chapter ends (_Checkpoints) and on_partial(times) is called after each join.

    Returns (path_to_flac, errors) -- a line whose unit failed is skipped
    (silent gap inserted instead) rather than aborting the whole narration.
    RuntimeError, with the title's track left as it was, when more than half the
    spoken lines failed.
    """
    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)

    _check_pace(pace)
    steps = _narration_steps(lines, character_clone_map,
                             narration_paragraph_ends(lines, drama_dir), narrate_original=narrate_original)
    units = [unit for kind, unit in steps if kind == "unit"]
    _name_clips(units, clips_dir, source_language if narrate_original else None)

    finished = 0
    # A title that already has a track keeps it whole until this run's track is complete: a part
    # would replace it before anything new is spoken (its clips are all cached), and a cancel or an
    # engine that fails to start would then leave the title with only that part.
    early = None if find_narration_track(drama_dir) else on_partial
    checkpoints = _Checkpoints(steps, drama_dir, pace, narrate_original, early)
    run_log = _RunLog(drama_dir)

    def record(unit, error):
        nonlocal finished
        unit["error"] = error
        unit["done"] = True
        finished += 1
        run_log.tick("speaking clips", len(units))
        if progress_cb:
            progress_cb(finished / len(units))
        checkpoints.maybe_join()

    remove_partial_track(drama_dir)
    run_log.started(units)
    _generate_units(units, record, source_language if narrate_original else "en", clips_dir)

    _refuse_mostly_failed(units, drama_dir, checkpoints.joined_any)
    errors, waiting = _join_track(steps, lines, drama_dir, pace, narrate_original, final=True,
                                  run_log=run_log)
    # A regenerated title must not keep serving, or backing up, its old WAV copy.
    legacy = os.path.join(drama_dir, NARRATION_TRACK_NAMES[1])
    if os.path.isfile(legacy):
        os.remove(legacy)
    return os.path.join(drama_dir, NARRATION_WAITING_NAME if waiting else NARRATION_TRACK_NAMES[0]), errors


def _join_track(steps, lines, drama_dir: str, pace: float, narrate_original: bool,
                final: bool = False, run_log=None) -> tuple:
    """Joins the clips of `steps` (all of the title's, or the part spoken so far) into the
    title's track and sets the times and clip of `lines` (theirs). Returns (the failed lines'
    errors, whether the track waits under NARRATION_WAITING_NAME).
    final: the whole track, which is kept under NARRATION_WAITING_NAME when the old one stays held.
    run_log: the run's _RunLog; only the whole track's join is logged."""
    from types import SimpleNamespace
    from pydub import AudioSegment
    from resegment import split_times

    errors = []
    units = [item for kind, item in steps if kind == "unit"]
    out_path = os.path.join(drama_dir, NARRATION_TRACK_NAMES[0])
    # Written beside the final name and renamed, so a kill mid-export can't leave a
    # truncated track that the Reader would serve as complete.
    part_path = _new_partial_track(drama_dir)
    stream = _FlacStream(part_path, pace, drama_dir, run_log=run_log,
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
            if run_log:
                run_log.tick("joining clips", len(units))
        stream.finish(cursor_ms)
        waiting = _put_track_in_place(part_path, out_path, drama_dir, final)
    finally:
        stream.abort()
        remove_partial_track(drama_dir)

    if run_log:
        # atempo plays the track at pace, so its length is the joined length divided by it.
        run_log.finished(cursor_ms / pace, os.path.join(drama_dir, NARRATION_WAITING_NAME) if waiting else out_path)
    _stretch_times(lines, pace)
    return errors, waiting


# The job's note when the finished track had to wait: users quote it when they report a stale track.
TRACK_IN_USE_NOTICE = ("The new narration is finished, but the old track was in use (the Reader may be "
                       "playing it), so it could not be replaced yet. Stop playback and open the Reader "
                       "again to hear the new one; it is swapped in once the old file is free.")
TRACK_IN_USE_ERROR = ("The new narration was finished but could not be saved, because the old track was "
                      "in use (the Reader may be playing it). Stop playback and choose Make again; the "
                      "clips already spoken are reused.")


def _put_track_in_place(part_path: str, out_path: str, drama_dir: str, final: bool) -> bool:
    """Whether the finished track waits under NARRATION_WAITING_NAME."""
    try:
        # Retries for about a second while the Reader is still streaming the old track on Windows.
        replace_file(part_path, out_path)
    except PermissionError as exc:
        if not final:
            raise
        # A listener can keep the old track open for as long as it plays; failing the run here
        # would throw away hours of narration, so the track waits for find_narration_track.
        try:
            replace_file(part_path, os.path.join(drama_dir, NARRATION_WAITING_NAME))
        except PermissionError as again:
            _log_track_in_use("could not be saved beside it either, so the run failed", again)
            raise RuntimeError(TRACK_IN_USE_ERROR) from again
        _log_track_in_use(f"waits as {NARRATION_WAITING_NAME} until the old file is free", exc)
        return True
    with contextlib.suppress(OSError):
        os.remove(os.path.join(drama_dir, NARRATION_WAITING_NAME))
    return False


def _log_track_in_use(outcome: str, exc: OSError) -> None:
    # strerror, not str(exc): the latter names the title's folder.
    import applog
    from engine_backends.shared import redact_secrets
    reason = redact_secrets(f"{type(exc).__name__}: {exc.strerror or 'file in use'}")
    applog.get_logger().warning(f"narration: the finished track could not replace the old one, "
                                f"which is in use (the Reader may be playing it); it {outcome} ({reason})")


# A join of the part spoken so far reads all of its clips again, so the next join waits this many
# times as long as the last one took: joining then costs about 1/JOIN_SPACING of the run.
JOIN_SPACING = 30
# On a title without chapters every line end is a checkpoint; each join also saves every line and
# reloads the Reader's player, so they come no closer together than this.
MIN_JOIN_GAP_SECONDS = 60.0


def _step_lines(kind, item) -> list:
    return [item] if kind == "blank" else item["lines"]


class _Checkpoints:
    """While a run goes on, joins the units spoken so far (the first up to the first one not
    spoken yet, cut back to a chapter start when the title has chapters) into the title's track,
    so its start can be heard long before a whole book is spoken and a cancelled or failed run
    keeps it. Each join is handed to on_partial(times): (line id, start, end, clip) for every
    line, the lines not joined yet at the part's end with no clip."""

    def __init__(self, steps, drama_dir, pace, narrate_original, on_partial):
        self.steps, self.drama_dir = steps, drama_dir
        self.pace, self.narrate_original, self.on_partial = pace, narrate_original, on_partial
        self.joined_upto = 0
        self.joined_any = False
        self.next_after = 0.0
        self.chapter_starts = sorted(i for i, (kind, item) in enumerate(steps)
                                     if kind == "unit" and is_chapter_heading(item["lines"][0]))

    def _spoken_upto(self) -> int:
        i = self.joined_upto
        while i < len(self.steps):
            kind, item = self.steps[i]
            if kind == "unit" and not item.get("done"):
                break
            i += 1
        return i

    def maybe_join(self) -> None:
        if self.on_partial is None or time.monotonic() < self.next_after:
            return
        end = self._spoken_upto()
        if end >= len(self.steps):
            return  # the whole track is joined once, after the last clip
        if self.chapter_starts:
            end = max((i for i in self.chapter_starts if self.joined_upto < i <= end), default=0)
        if end <= self.joined_upto:
            return
        part = self.steps[:end]
        spoken = [item for kind, item in part if kind == "unit"]
        # As _refuse_mostly_failed: mostly silence must not replace the title's track.
        if not spoken or _mostly_failed(spoken):
            return
        began = time.monotonic()
        gap = 0.0 if self.chapter_starts else MIN_JOIN_GAP_SECONDS
        try:
            _join_track(part, [ln for kind, item in part for ln in _step_lines(kind, item)],
                        self.drama_dir, self.pace, self.narrate_original)
        except Exception as exc:
            # Only the whole track may fail the run: an early part is a convenience, and on Windows
            # its replace fails while the Reader is still streaming the previous part.
            import applog
            applog.get_logger().warning(f"narration: the part made so far was not joined: {type(exc).__name__}")
            self.next_after = time.monotonic() + max(gap, JOIN_SPACING * (time.monotonic() - began))
            return
        self.next_after = time.monotonic() + max(gap, JOIN_SPACING * (time.monotonic() - began))
        self.joined_upto, self.joined_any = end, True
        self.on_partial(self._times(end))

    def _times(self, end: int) -> list:
        # Plain tuples made now: the result queue pickles later, by when the lines have moved on.
        out, part_end = [], 0.0
        for i, (kind, item) in enumerate(self.steps):
            for ln in _step_lines(kind, item):
                if i < end:
                    failed = kind == "unit" and item["error"]
                    out.append((ln.id, ln.start, ln.end, None if failed else ln.dub_filename))
                    part_end = max(part_end, ln.end)
                else:
                    out.append((ln.id, part_end, part_end, None))
        return out


def _mostly_failed(units) -> bool:
    total = sum(len(u["lines"]) for u in units)
    return sum(len(u["lines"]) for u in units if u["error"]) * 2 > total


def _refuse_mostly_failed(units, drama_dir: str, part_joined: bool = False) -> None:
    """Keeps the title's track when more than half the spoken lines failed (an engine that
    would not start, say): a track that is mostly silence would replace a good one.
    part_joined: the run already put the part it spoke well in place of that track."""
    if not _mostly_failed(units):
        return
    total = sum(len(u["lines"]) for u in units)
    failed = [u for u in units if u["error"]]
    failed_lines = sum(len(u["lines"]) for u in failed)
    if part_joined:
        kept = "so only the part made before that can be played"
    elif find_narration_track(drama_dir):
        kept = "so the previous track was kept"
    else:
        kept = "so no track was made"
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
                       timeout=M4B_ENCODE_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
    return out_path


# Qwen3-TTS (qwen-tts 0.1.1 README): "If you set x_vector_only_mode=True, only the speaker
# embedding is used so ref_text is not required, but cloning quality may be reduced"; with the
# transcript it clones in-context, from the clip's words and sound together. A clip saved with
# none is heard with the app's own Whisper settings for a full transcription instead.
CLIP_TRANSCRIPT_WHISPER = "large-v3"
CLIP_TRANSCRIPT_BEAM = 5
CLIP_TRANSCRIPT_STAGE = "Writing down what a voice clip says..."


def _clips_missing_transcript(clone_map) -> dict:
    """{clip path: [speaker labels]} for the Qwen3-TTS voices whose clip has no transcript."""
    missing = {}
    for label, clone in clone_map.items():
        if (clone.get("engine") == "qwen3_tts" and clone.get("ref_audio")
                and not (clone.get("ref_text") or "").strip()):
            missing.setdefault(clone["ref_audio"], []).append(label)
    return missing


def fill_clip_transcripts(clone_map, use_gpu: bool, local_model_path=None) -> dict:
    """Writes what Whisper hears into each Qwen3-TTS clone entry whose clip has no transcript,
    in the clip's own language (detected), and frees Whisper before the voice engine loads.
    local_model_path: the Whisper model folder set in Settings, used as transcription uses it.
    Returns {speaker label: transcript} for the entries filled. A clip it hears nothing in, or
    cannot hear (no model while offline, say), keeps the speaker-embedding-only mode."""
    missing = _clips_missing_transcript(clone_map)
    if not missing:
        return {}
    import applog
    import whisper_models
    import whisper_transcribe
    from line_alignment import join_segment_texts
    filled = {}
    try:
        for path, labels in missing.items():
            try:
                segments = whisper_transcribe.transcribe_for_timing(
                    path, CLIP_TRANSCRIPT_WHISPER, language=None, use_gpu=use_gpu,
                    local_model_path=local_model_path, beam_size=CLIP_TRANSCRIPT_BEAM)
            except Exception as exc:
                applog.get_logger().warning(f"a voice clip's transcript was not written: {type(exc).__name__}")
                continue
            text = join_segment_texts(seg.get("text") or "" for seg in segments)
            for label in labels if text else ():
                clone_map[label]["ref_text"] = text
                filled[label] = text
    finally:
        whisper_models.release_whisper_models()
    return filled


def build_narration_subprocess_worker(lines, drama_dir, character_clone_map, result_queue,
                                      narrate_original=False, source_language="zh", pace=1.0,
                                      use_gpu=True, clip_whisper=None):
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
        transcripts = {}
        # clip_whisper: {"local_model_path"} when the model is on this PC, else None: a clip's
        # transcript is not worth a silent download of several GB in the middle of a narration.
        if clip_whisper is not None and _clips_missing_transcript(character_clone_map):
            process_worker.report_stage(result_queue, CLIP_TRANSCRIPT_STAGE)
            transcripts = fill_clip_transcripts(character_clone_map, use_gpu, clip_whisper.get("local_model_path"))
        out_path, errors = build_narration_track(
            lines, drama_dir, character_clone_map, progress_cb=report, pace=pace,
            narrate_original=narrate_original, source_language=source_language,
            on_partial=lambda times: process_worker.report_partial(result_queue, {"times": times}))
        # failed_count: the job result lists only the first errors.
        result = {"lines": lines, "out_path": out_path, "errors": errors, "failed_count": len(errors),
                  "clip_transcripts": transcripts}
        if os.path.basename(out_path) == NARRATION_WAITING_NAME:
            result["track_waiting"] = TRACK_IN_USE_NOTICE
        result_queue.put(("ok", result))
    except Exception as exc:
        result_queue.put(process_worker.error_item(exc))
