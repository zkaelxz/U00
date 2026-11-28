"""
live_translate.py -- near-live translation of an ongoing live stream.

Not the same problem as the rest of this app: everything else works on
a file that already exists in full, start to finish. Here there's no end
yet -- audio keeps arriving, so the design is "process what's arrived so
far, in bounded chunks, and keep going" rather than "process the whole
file once."

Pipeline, per chunk:
  1. live_fetch fetches the resolved stream (following HLS playlists)
     and pipes it into ffmpeg, which segments it into fixed-length audio
     chunks (its own `segment` muxer) -- this is what makes the "live"
     part possible at all: segmenting the stream as it arrives means
     each chunk is ready to transcribe long before the stream itself
     ends.
  2. Each completed chunk is transcribed with the same Whisper backend
     used everywhere else in this app (core.transcribe_for_timing), with
     chunk-relative timestamps shifted to be stream-relative.
  3. Each chunk's lines are translated with whichever engine/API key
     the person picked, in one request, on live_pipeline's worker thread
     while Whisper moves on to the next chunk.
  4. Cues accumulate in the job's result list, which the UI polls.

This is deliberately NOT held to the same accuracy/latency bar as the
rest of the app -- "it doesn't have to be as accurate" (the actual ask
this was built for) is what makes it feasible at all. A shorter chunk
means lower latency but Whisper loses cross-sentence context at each
cut, so a sentence split across a chunk boundary can transcribe worse
than the same audio would in one piece. 15-30s is a reasonable range;
no setting removes this tradeoff entirely.

Known limitations, stated plainly rather than glossed over:
  - Latency is at least one chunk's length (a 20s chunk means the
    translated line for something said at t=0 doesn't appear until
    roughly t=20-40, after transcription+translation finish). This is
    not, and cannot be, real-time in the broadcast sense.
  - A break in the stream (a network drop, the resolved URL expiring,
    a stall) reconnects by itself once the session has captioned a chunk:
    the page link is resolved again and capture resumes at the live edge,
    with the captions, the translator's context and the timeline kept. The
    speech during the break is not heard; the delayed player starts again on
    the new stream link. See _LiveRun._reconnect.
  - Chunk boundaries: ffmpeg still cuts hard, non-overlapping chunks,
    but each chunk is transcribed with the last few seconds of the
    PREVIOUS chunk's actual audio prepended (overlap_seconds, see
    run_live_job), so a sentence cut at a boundary is heard whole the
    second time round; the re-heard overlap is then removed from what's
    newly emitted by an exact suffix/prefix text match against what the
    previous chunk already emitted there (see dedup_overlap). The
    previous chunk's transcribed tail is also still carried as
    initial_prompt. What's deliberately NOT done: fuzzy near-duplicate
    matching. If the two transcriptions of the overlap disagree word-
    for-word, the fallback only drops new lines that sit entirely inside
    the overlap window, so a line straddling the boundary can still
    repeat a few words in slightly different form. A line the previous
    chunk already emitted is never retracted or re-translated with the
    fuller context, either -- the rescued second half of a cut sentence
    is translated on its own. A name that never appeared in a prior
    chunk still gets no priming benefit (unlike the whole-file
    initial_prompt name list used elsewhere in this app).
  - Built and verified with a real ffmpeg segment-capture pipeline
    against a local looping source (proving the capture -> chunk-
    detection -> transcribe -> translate loop genuinely works end to
    end), and since run on real live news broadcasts. A
    reconnect after a real broadcast break is covered only by mocked
    tests.
"""

import itertools
import os
import re
import subprocess
import threading
import time
import unicodedata
from typing import Any

import background_jobs
from lib.errors import DependencyUnavailableError
from lib.no_window import NO_WINDOW
from live import live_audio
from live import live_cue_feed
from live import live_fetch
from live import live_pipeline
from live import live_player
from live import live_whisper
from live.live_audio import read_wav_tail, write_padded_chunk  # noqa: F401 -- re-exported for callers and tests
from live.live_caption import ChunkCaptioner
from live.live_cue_translation import CueTranslator
from live.live_resolve import LiveCaptureError, StreamEnded, resolve_stream_url
from live.live_tokens import LEADING_NON_WORD_RE, TOKEN_RE, tokens


# What ffmpeg may read the piped stream as: MPEG-TS and fMP4 (HLS
# segments) and the containers a direct live stream comes in. Not hls,
# dash, concat or any other format that names further URLs: ffmpeg 6.1's
# DASH demuxer opens http fragment URLs even under `-protocol_whitelist
# pipe`, so this list is a guard of its own, not a duplicate.
FFMPEG_FORMAT_WHITELIST = "mpegts,mov,aac,mp3,flv,matroska,ogg,wav"


class SegmentCapture:
    """A running capture: ffmpeg segmenting what live_fetch.StreamPump
    writes to its stdin. Stop it with stop_capture()."""

    def __init__(self, proc: subprocess.Popen, pump: live_fetch.StreamPump):
        self.proc = proc
        self.pump = pump

    def poll(self):
        return self.proc.poll()

    @property
    def error(self):
        """The fetcher's fixed failure message, or None."""
        return self.pump.error

    @property
    def ended_live(self):
        return self.pump.ended_live


def start_segment_capture(source_url: str, out_dir: str, segment_seconds: int = 20,
                           sample_rate: int = 16000, proxy: str | None = None,
                           start_number: int = 0, live_only: bool = False,
                           lead_in_seconds: float = 0.0) -> SegmentCapture:
    """
    Starts ffmpeg writing numbered mono WAV chunks (chunk_00000.wav,
    chunk_00001.wav, ...), each `segment_seconds` long, from its stdin,
    and a live_fetch.StreamPump fetching `source_url` (through `proxy`
    when given) into that stdin. The caller stops it (stop_capture()).

    ffmpeg opens nothing itself (`-protocol_whitelist pipe`), so no
    playlist entry, redirect or manifest can make it connect anywhere;
    every URL is fetched by the pump, through the proxy.

    Any chunk_*.wav/padded_*.wav already in out_dir (left by an earlier
    run sharing the directory) is removed first, so ffmpeg's new chunk
    numbering never mixes with old audio that run_live_job would then
    process as this run's (clear_stale_chunks).

    start_number: the first chunk's number, so a capture restarted after a
    stream break carries on the session's numbering. live_only: see
    live_fetch.StreamPump. lead_in_seconds: silence put before the audio so
    the first chunk closes early (live_audio.first_chunk_lead_in); the caller
    shifts its timeline to match.
    """
    os.makedirs(out_dir, exist_ok=True)
    clear_stale_chunks(out_dir)
    pattern = os.path.join(out_dir, "chunk_%05d.wav")
    # A synthetic lead-in is cheaper than a custom cut list: every later cut stays
    # on the fixed segment_time grid, so the chunk-slot arithmetic is untouched.
    lead_in = ["-af", f"adelay={int(round(lead_in_seconds * 1000))}:all=1"] if lead_in_seconds > 0 else []
    cmd = ["ffmpeg", "-y", "-protocol_whitelist", "pipe", "-format_whitelist",
           FFMPEG_FORMAT_WHITELIST, "-i", "pipe:0", "-vn", *lead_in, "-ac", "1", "-ar", str(sample_rate),
           "-f", "segment", "-segment_time", str(segment_seconds), "-reset_timestamps", "1",
           "-segment_start_number", str(int(start_number)), pattern]
    # Its own process group/session, so background_jobs.kill_tree (the cancel
    # path) takes down anything ffmpeg started along with it.
    group: dict[str, Any]
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW} if os.name == "nt"
             else {"start_new_session": True})
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **group)
    try:
        pump = live_fetch.StreamPump(source_url, proc.stdin, proxy=proxy, live_only=live_only).start()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    return SegmentCapture(proc, pump)


def stop_capture(capture, timeout: float = 5.0, kill: bool = False):
    """Stops the fetcher (no new bytes), then ffmpeg -- SIGTERM lets it
    flush the segment it's writing, a kill follows after `timeout` -- and
    then waits for the fetcher's threads, which by then have nothing left
    to block on. Also takes a bare Popen. kill=True skips the graceful
    flush and kills ffmpeg's whole process tree at once: for a cancel, where
    the half-written segment is thrown away anyway."""
    pump = getattr(capture, "pump", None)
    proc = getattr(capture, "proc", capture)
    if pump is not None:
        pump.halt()
    try:
        if kill:
            if proc.poll() is None:
                background_jobs.kill_tree(proc)
                proc.wait()
        elif proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    finally:
        if pump is not None:
            pump.join()


_CHUNK_RE = re.compile(r"chunk_(\d{5})\.wav$")


def list_completed_chunks(out_dir: str, last_completed_index: int) -> list[tuple[int, str]]:
    """
    A chunk is only safe to process once ffmpeg has moved on to the next
    one -- reading it earlier risks transcribing a truncated, still-being-
    written file. Returns [(index, path), ...] for every chunk after
    `last_completed_index` that already has a successor on disk (proof
    ffmpeg finished writing it), in order.
    """
    if not os.path.isdir(out_dir):
        return []
    indices = set()
    for f in os.listdir(out_dir):
        m = _CHUNK_RE.search(f)
        if m:
            indices.add(int(m.group(1)))
    completed = sorted(i for i in indices if i > last_completed_index and (i + 1) in indices)
    return [(i, os.path.join(out_dir, f"chunk_{i:05d}.wav")) for i in completed]


def list_remaining_chunks(out_dir: str, last_completed_index: int):
    """Every chunk after `last_completed_index`, the newest included: only for
    once ffmpeg has exited, when no file is still being written."""
    if not os.path.isdir(out_dir):
        return []
    found = sorted({int(m.group(1)) for m in map(_CHUNK_RE.search, os.listdir(out_dir)) if m})
    return [(i, os.path.join(out_dir, f"chunk_{i:05d}.wav")) for i in found if i > last_completed_index]


_STALE_RE = re.compile(r"(?:chunk|padded)_\d{5}(?:_newest)?\.wav$")


def clear_stale_chunks(out_dir: str):
    """Removes leftover chunk_*.wav/padded_*.wav files from out_dir. A
    fixed, shared capture directory otherwise hands a new run the previous
    run's chunks, which list_completed_chunks() would treat as new audio."""
    if not os.path.isdir(out_dir):
        return
    for f in os.listdir(out_dir):
        if _STALE_RE.fullmatch(f):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass


# How many tokens at the very start of the padded transcription may be
# skipped before the match begins -- the overlap audio starts at an
# arbitrary point, often mid-word, and Whisper can render that partial
# word as something that isn't in the previous chunk's text at all.
# Skipped tokens are inside the overlap window, so dropping them loses
# nothing the previous chunk didn't already emit.
_MAX_LEADING_SKIP = 2


def dedup_overlap(segments: list, overlap_seconds: float, tail_text: str,
                  covered_seconds: float | None = None, on_outcome=None) -> list:
    """
    Drops the re-transcribed overlap region from a padded chunk's
    segments (timestamps still relative to the padded audio, so the
    first `overlap_seconds` are audio the previous chunk already
    covered).

    tail_text: what the previous chunk actually emitted for its last
    `overlap_seconds` -- its cues that reach into that window.

    Exact matching only, no fuzzy near-duplicate suppression: finds the
    longest run of tokens that is a SUFFIX of tail_text and also a
    PREFIX of the segments that start inside the overlap window
    (allowing up to _MAX_LEADING_SKIP garbled leading tokens before it,
    and requiring at least 2 matched tokens when any are skipped), then
    removes everything up to and including that run. What's left of a
    segment cut part-way through is the speech right after the chunk
    boundary, so its start moves to the boundary.

    If tail_text is empty, the previous chunk emitted nothing there, so
    there's nothing to duplicate: segments are returned unchanged. If
    there's text but no exact match (the two transcriptions of the
    overlap disagree), only segments that END inside the overlap window
    are dropped -- they're wholly audio the previous chunk already
    emitted text for -- and a segment straddling the boundary is kept
    whole, preferring a possible repeated word over losing new speech.

    covered_seconds: how much of the overlap the previous chunk emitted text
    for, when it held back its last lines (live_caption.hold_back_overlap);
    the fallback then drops only segments ending inside that part, so the
    held-back speech heard again here is kept. None means all of it.

    on_outcome("matched" or "fallback"): called once when a previous tail was
    compared with this chunk's start, so a session can count how often the exact
    match failed. Not called when either side had no words to compare.
    """
    segments = list(segments)
    if overlap_seconds <= 0 or not segments:
        return segments
    tail = tokens(tail_text)
    if not tail:
        return segments

    head = []  # (token, segment position, char offset just past the token)
    for pos, seg in enumerate(segments):
        if seg["start"] >= overlap_seconds:
            break
        for m in TOKEN_RE.finditer(seg.get("text") or ""):
            head.append((m.group(0).casefold(), pos, m.end()))
    head_tokens = [t for t, _, _ in head]

    cut = None
    for k in range(min(len(tail), len(head_tokens)), 0, -1):
        for skip in range(0, min(_MAX_LEADING_SKIP, len(head_tokens) - k) + 1):
            if skip and k < 2:
                continue
            if head_tokens[skip:skip + k] == tail[-k:]:
                cut = skip + k
                break
        if cut is not None:
            break

    # An empty head (silent or dropped overlap audio) gave the exact match nothing to
    # compare, so it is not a failure of the match.
    if on_outcome and head_tokens:
        on_outcome("fallback" if cut is None else "matched")
    if cut is None:
        covered = overlap_seconds if covered_seconds is None else min(covered_seconds, overlap_seconds)
        return [seg for seg in segments if seg["end"] > covered]

    _, cut_pos, cut_char = head[cut - 1]
    kept = []
    for pos, seg in enumerate(segments):
        if pos < cut_pos:
            continue
        if pos == cut_pos:
            rest = LEADING_NON_WORD_RE.sub("", (seg.get("text") or "")[cut_char:]).strip()
            if not rest:
                continue
            start = seg["start"] if seg["end"] <= overlap_seconds else max(seg["start"], overlap_seconds)
            seg = {**seg, "text": rest, "start": start}
        kept.append(seg)
    return kept


# A streamer really does ask viewers to subscribe, so those stock lines stay in Live.
_SUBSCRIBE_RE = re.compile(r"チャンネル登録|subscribe|구독", re.IGNORECASE)


# A name fixed during a stream is one short word or phrase, and a session keeps a handful.
MAX_NAME_FIXES = 50
MAX_FIX_SOURCE_CHARS = 40
MAX_FIX_ENGLISH_CHARS = 80
MAX_PROMPTED_FIXES = 10


class TermHints:
    """The glossary a session translates with: calling it looks terms up in some texts (for the
    translator), and `names` is the glossary's own names as a Whisper prompt ("" when there are none).
    Names fixed during the stream (add_fix) win in both: first in the lookup, and last in the
    prompt, since Whisper keeps only the end of a long one. The viewer has just seen the
    glossary's or the model's reading go wrong."""

    def __init__(self, lookup=None, names: str = ""):
        self.lookup = lookup
        self._names = names
        self._fixes: dict = {}
        self._lock = threading.Lock()

    def add_fix(self, source: str, english: str) -> None:
        with self._lock:
            if source not in self._fixes and len(self._fixes) >= MAX_NAME_FIXES:
                raise ValueError("too many fixes")
            self._fixes.pop(source, None)
            self._fixes[source] = english

    def has_room(self, source: str) -> bool:
        with self._lock:
            return source in self._fixes or len(self._fixes) < MAX_NAME_FIXES

    def fixes(self) -> list:
        with self._lock:
            return list(self._fixes.items())

    @property
    def names(self) -> str:
        # The newest few only: the previous chunk's tail comes after these and needs its room too.
        primed = set(re.split(r"[、。]", self._names))
        fixed = [s for s, _ in self.fixes() if s not in primed][-MAX_PROMPTED_FIXES:]
        if not fixed:
            return self._names
        return self._names + "、".join(fixed) + "。"

    def __call__(self, texts):
        text = "\n".join(t for t in texts if isinstance(t, str))
        fixed = [(s, e, "") for s, e in self.fixes() if s in text]
        taken = {row[0] for row in fixed}
        found = self.lookup(texts) if self.lookup is not None else []
        return fixed + [row for row in found if row[0] not in taken]


def drop_name_list_echo(segments: list, names: str) -> list:
    """Drops a line that is only three or more of the primed names in a row. Whisper reads
    the name prompt back as a line over music or silence; a streamer calling out three names
    in a row with nothing else is rare, and the one line is the cost."""
    known = {n for n in re.split(r"[、。\s]+", names or "") if n}
    if not known:
        return segments

    def echo(text):
        pieces = [p for p in re.split(r"[、，,。.!！?？\s]+", text or "") if p]
        return len(pieces) >= 3 and all(p in known for p in pieces)
    return [seg for seg in segments if not echo(seg.get("text"))]


def drop_stock_phrases(segments: list) -> list:
    """Drops a line that is only a credit or outro phrase ("ご視聴ありがとうございました").
    Whisper invents these on laughter and pauses mid-stream, and the file pipeline's
    rule (drop one only with 5 s of silence each side) rarely applies inside one
    short chunk. A streamer who really says it loses that one line from a feed that
    is not saved."""
    import whisper_hallucination

    def invented(text):
        return whisper_hallucination.is_stock_phrase(text) and not _SUBSCRIBE_RE.search(text)
    return [seg for seg in segments if not invented(seg.get("text") or "")]


# A line Whisper writes again right after itself is dropped only from this many
# letters: a short "はい" said twice is real speech, a whole sentence twice is not.
MIN_REPEATED_LINE_CHARS = 8
# Letters a repeat may carry in front of the line before it: Whisper can hang
# the first word of a line on the end of the one before ("…です手" / "拍子を…").
REPEAT_LEADING_SLACK = 2


def drop_repeated_lines(segments: list) -> list:
    """Drops a line that repeats the one just before it. ja-1.5b on TBS news
    wrote a sentence a second time straight after itself, squeezed into 0.5-1.3 s,
    and both copies were shown and translated."""
    kept = []
    for seg in segments:
        text = re.sub(r"\W+", "", seg.get("text") or "")
        prev = re.sub(r"\W+", "", kept[-1].get("text") or "") if kept else ""
        if len(prev) >= MIN_REPEATED_LINE_CHARS and text.endswith(prev) \
                and len(text) - len(prev) <= REPEAT_LEADING_SLACK:
            kept[-1] = {**kept[-1], "end": max(kept[-1]["end"], seg["end"])}
            continue
        kept.append(seg)
    return kept


# A forced cut never leaves a piece shorter than this many CJK characters: a
# few letters flashed on screen read worse than a line a little over the limit.
MIN_FORCED_PIECE_CHARS = 8
# How far either side of an even cut a forced cut may move to land between two
# kinds of script (kanji, hiragana, katakana, Hangul, Latin), which in unpunctuated
# Japanese is far more often a word boundary than a cut mid-run.
FORCED_CUT_REACH = 4


def split_long_caption_lines(segments: list) -> list:
    """Cuts a line longer than a caption can hold (segment_splitting's 8 s / 40
    CJK characters) at its sentence ends, then commas, spaces beside CJK text and
    word pauses. whisper-ja (ja-1.5b) often writes 10-20 s of speech as one
    segment, which sat on screen as a wall of text. Without the re-split rules an
    English "Mr. Smith" can be cut after "Mr."; a CJK line with none of these cuts
    is then cut by length (force_cut_cjk): a Live caption is read once and not
    saved, so an imperfect cut beats a wall of text."""
    import segment_splitting
    out = []
    for seg in segment_splitting.split_long_segments(segments):
        out.extend(force_cut_cjk(seg))
    return out


# A line this short that only finishes the sentence before it ("당부했습니다.")
# joins that line. Sent on its own, the translator often gave it the whole
# sentence, or the next line's, and every line after it read one line off.
TRAILING_FRAGMENT_SECONDS = 2.0
# Only a pause shorter than this keeps the two as one sentence.
FRAGMENT_JOIN_GAP_SECONDS = 1.0


def join_trailing_fragments(segments: list) -> list:
    """Joins each short line that ends a sentence onto the line before it when
    that line has no sentence end, the pause between them is short and the
    joined line still fits a caption (8 s / 40 CJK characters). Unpunctuated
    speech is left as Whisper cut it: nothing there says where a sentence ends."""
    import segment_splitting
    out = []
    for seg in segments:
        prev = out[-1] if out else None
        text = (seg.get("text") or "").strip()
        if (prev is not None and text
                and segment_splitting._SENTENCE_END_RE.search(text[-3:])
                and seg["end"] - seg["start"] <= TRAILING_FRAGMENT_SECONDS
                and seg["start"] - prev["end"] <= FRAGMENT_JOIN_GAP_SECONDS
                and not segment_splitting._SENTENCE_END_RE.search(prev["text"].rstrip()[-3:])
                and seg["end"] - prev["start"] <= segment_splitting.SPLIT_MAX_SECONDS):
            joined = _join_text(prev["text"], text)
            if len(segment_splitting._CJK_RE.findall(joined)) <= segment_splitting.SPLIT_MAX_CJK_CHARS:
                out[-1] = {**{k: v for k, v in prev.items() if k != "words"},
                           "text": joined, "end": max(prev["end"], seg["end"])}
                continue
        out.append(seg)
    return out


def _join_text(a: str, b: str) -> str:
    a = a.rstrip()
    # Japanese and Chinese run words together; Korean and Latin text use spaces.
    if a and b and _script(a[-1]) in ("kanji", "hiragana", "katakana") \
            and _script(b[0]) in ("kanji", "hiragana", "katakana"):
        return a + b
    return f"{a} {b}"


def _script(ch: str) -> str:
    code = ord(ch)
    if 0x3040 <= code <= 0x309F:
        return "hiragana"
    if 0x30A0 <= code <= 0x30FF:
        return "katakana"
    if 0x3400 <= code <= 0x9FFF:
        return "kanji"
    if 0xAC00 <= code <= 0xD7AF:
        return "hangul"
    return "other"


def force_cut_cjk(seg: dict) -> list:
    """`seg` in even pieces of at most 8 s / 40 CJK characters, with times shared
    by character count, each cut moved to the nearest change of script within
    FORCED_CUT_REACH. Returned whole when it fits, holds no CJK text, or the pieces
    would fall under MIN_FORCED_PIECE_CHARS."""
    import math
    import segment_splitting
    text = seg.get("text") or ""
    dur = seg["end"] - seg["start"]
    cjk = len(segment_splitting._CJK_RE.findall(text))
    pieces = max(math.ceil(dur / segment_splitting.SPLIT_MAX_SECONDS),
                 math.ceil(cjk / segment_splitting.SPLIT_MAX_CJK_CHARS))
    while pieces > 1 and len(text) / pieces < MIN_FORCED_PIECE_CHARS:
        pieces -= 1
    if pieces < 2 or not cjk or dur <= 0:
        return [seg]
    cuts, last = [], 0
    for k in range(1, pieces):
        even = round(len(text) * k / pieces)
        lo = max(last + MIN_FORCED_PIECE_CHARS, even - FORCED_CUT_REACH)
        hi = min(len(text) - MIN_FORCED_PIECE_CHARS, even + FORCED_CUT_REACH)
        if lo > hi:
            continue
        turns = [i for i in range(lo, hi + 1) if _script(text[i - 1]) != _script(text[i])]
        at = min(turns, key=lambda i: abs(i - even)) if turns else min(max(even, lo), hi)
        # A Thai vowel or tone mark belongs to the letter before it.
        while at < len(text) and unicodedata.category(text[at]) == "Mn":
            at += 1
        cuts.append(at)
        last = at
    bounds = [0, *cuts, len(text)]
    bare = {k: v for k, v in seg.items() if k != "words"}
    out = []
    for a, b in zip(bounds, bounds[1:]):
        piece = text[a:b].strip()
        if piece:
            out.append({**bare, "text": piece, "start": seg["start"] + dur * a / len(text),
                        "end": seg["start"] + dur * b / len(text)})
    return out or [seg]



# A few seconds: long enough to re-hear a word or short phrase cut at a
# chunk boundary, short enough that the extra audio re-transcribed per
# chunk stays a small fraction of a 15-30s chunk.
DEFAULT_OVERLAP_SECONDS = 3

# Waits before each reconnect try after a stream break: quick first, since most
# breaks are a blip, then no faster than every 30 s for a stream that went away.
RECONNECT_DELAYS = (2, 5, 10, 20, 30)
# How long after a break Live keeps trying; a streamer restarting their
# broadcast software is usually back within this.
RECONNECT_GIVE_UP_SECONDS = 300
FFMPEG_BROKE = ("ffmpeg could not read the stream (an unsupported format, or the "
                "stream broke off).")
# The same exit once the stream has worked: the format is not in question, and
# the reconnect status already says the stream broke off.
FFMPEG_STOPPED = "ffmpeg stopped reading the stream."
RESOLVE_FAILED = "The stream could not be found again."
# Every break shares one note, so a stream that breaks often does not push the
# skip counts and other notes out of the session's short list.
RECONNECT_NOTE_KEY = "reconnect"
# The clock a break's length on the stream timeline is measured with; a test seam.
_timeline_clock = time.monotonic

# Chunks left waiting when the loop looks (the one being cut plus one more is
# the normal pipeline); beyond this the oldest are dropped, not queued, so
# captions stay near the live edge when Whisper runs slower than the stream.
MAX_BACKLOG_CHUNKS = 2


def process_chunk(chunk_path: str, chunk_index: int, segment_seconds: float,
                   source_language: str, whisper_size: str, engine, use_gpu: bool = False,
                   context_prompt: str = "", overlap_seconds: float = 0.0, name_prompt: str = "",
                   overlap_tail_text: str = "", on_stage=None, is_cancelled=None,
                   translator=None, first_id: int = 0, on_cues=None, guard=None,
                   audio_shift: float = 0.0, on_transcribed=None, translate: bool = True,
                   overlap_covered_seconds: float | None = None, on_overlap=None):
    """
    Transcribes one chunk and translates each resulting line, shifting
    timestamps by this chunk's position in the stream so cues from
    different chunks share one continuous timeline instead of each
    chunk restarting at zero.

    overlap_seconds: how much of the previous chunk's audio was
    prepended to `chunk_path` (write_padded_chunk()); 0.0 for an
    unpadded chunk. overlap_tail_text: what the previous chunk emitted
    for that same audio. The re-heard overlap is removed with
    dedup_overlap() BEFORE translating, so it's never translated or
    shown twice.

    context_prompt: the tail end of the PREVIOUS chunk's own transcribed
    text, passed through as Whisper's initial_prompt for this chunk. Each
    chunk is still transcribed independently (see the module docstring's
    latency tradeoff) -- this doesn't give Whisper the previous chunk's
    actual audio, only a hint of what was just said -- but it's a real,
    direct fix for the specific failure this module's own docstring used
    to call out as a known, unaddressed gap: a sentence split across a
    chunk boundary has nothing to anchor its second half to without this,
    the same way a name primed via initial_prompt elsewhere in this app
    measurably helps recognition. Empty for the very first chunk, since
    there's no previous chunk yet.

    on_stage("transcribing" | "translating"): called as each step starts.
    is_cancelled: polled between steps; true raises
    background_jobs.JobCancelled. The caller passes only the Stop button's
    generation check, not a plain cancel, which lets the chunk finish; a
    guard (below) is what interrupts the Whisper call itself.
    translator: kept across chunks so a cue sees the lines before it.
    first_id / on_cues(cues): cue ids start here; on_cues sees the chunk's cues
    right after Whisper (untranslated) and after each translation.
    guard(call): runs the Whisper call, given as call(progress_cb), so the caller
    can time-limit or abandon it (live_whisper.run_guarded); None calls it
    directly. audio_shift: seconds added to the timestamps: the start of an
    over-long chunk that was trimmed, plus earlier timestamp jumps. on_transcribed(took_seconds): called when Whisper
    returns. translate=False returns the cues untranslated (pending), for a
    caller that translates them elsewhere (run_live_job's worker).
    """
    import whisper_transcribe

    def checkpoint():
        if is_cancelled is not None and is_cancelled():
            raise background_jobs.JobCancelled(f"live chunk {chunk_index}")

    if on_stage:
        on_stage("transcribing")
    translator = translator or CueTranslator(engine, source_language)
    started = time.monotonic()
    segments = (guard or (lambda call: call(None)))(lambda progress_cb: whisper_transcribe.transcribe_for_timing(
        chunk_path, model_size=whisper_size, language=source_language, use_gpu=use_gpu,
        initial_prompt=name_prompt + context_prompt, progress_cb=progress_cb))
    if on_transcribed:
        on_transcribed(time.monotonic() - started)
    checkpoint()
    segments = dedup_overlap(segments, overlap_seconds, overlap_tail_text, overlap_covered_seconds, on_overlap)
    segments = drop_name_list_echo(segments, name_prompt)
    segments = join_trailing_fragments(split_long_caption_lines(drop_repeated_lines(drop_stock_phrases(segments))))
    offset = chunk_index * segment_seconds - overlap_seconds + audio_shift
    cues = live_cue_feed.pending_cues(segments, offset, first_id)
    if translate:
        live_cue_feed.translate_cues(cues, translator, checkpoint, on_stage, on_cues)
    return cues


# {job_id: generation}, a plain module dict rather than something
# behind background_jobs' own lock -- a single int increment/read per
# job_id needs no more ceremony than that, same reasoning as
# background_jobs' own is_cancel_requested flag. A run removes its
# entry when it ends. Generations come from one process-wide counter, so
# a value is never handed out twice: a Stop that lands just after the
# run removed its entry adds a new one that still differs from the run's,
# and anything that run left behind keeps reading as superseded.
_generations = {}
_next_generation = itertools.count(1)


def bump_generation(job_id: str) -> int:
    """Gives this live session a new generation, one never handed out
    before, and returns it. Call this from the Stop button's own handler (alongside
    request_cancel) -- that's the one place a NEW generation can start
    while an OLDER one's chunk is still genuinely in flight (mid
    transcribe/translate call inside run_live_job's own thread, which
    only notices a stop request between iterations). A restarted session
    can't itself race the one it replaces: start_job() already refuses a
    second run under the same job_id while the first is still "running",
    so by the time a new run_live_job call can begin, the old one has
    already returned -- but bumping here too costs nothing and keeps the
    guard honest if that single-instance assumption ever changes."""
    generation = next(_next_generation)
    _generations[job_id] = generation
    return generation


def drop_generation(job_id: str) -> None:
    _generations.pop(job_id, None)


def current_generation(job_id: str) -> int:
    return _generations.get(job_id, 0)


# When each running session with a time limit stops, on time.monotonic()'s clock,
# so its owner can move the limit without a restart (extend_time_limit).
_deadlines = {}
_deadlines_lock = threading.Lock()
_finishing = set()


def extend_time_limit(job_id: str, seconds: float, longest: float) -> float | None:
    """Moves a running session's hard stop `seconds` later, but never more than
    `longest` from now; returns the seconds left, or None for a session that is
    not running or has no limit."""
    with _deadlines_lock:
        box = _deadlines.get(job_id)
        if box is None:
            return None
        now = time.monotonic()
        box[0] = max(box[0], min(box[0] + seconds, now + longest))
        return box[0] - now


def mark_finishing(job_id: str) -> None:
    """A session that is finishing (its time is up, or Stop was pressed while
    the tail is still captioned) can't be given more time: it stops anyway."""
    with _deadlines_lock:
        _deadlines.pop(job_id, None)
        _finishing.add(job_id)


def is_finishing(job_id: str) -> bool:
    with _deadlines_lock:
        return job_id in _finishing


def time_left(job_id: str) -> float | None:
    with _deadlines_lock:
        box = _deadlines.get(job_id)
        return None if box is None else max(0.0, box[0] - time.monotonic())


def _run_abortable(fn, should_stop, poll: float = 0.25):
    """Runs fn() on a helper thread and returns its result, or raises
    background_jobs.JobCancelled as soon as should_stop() is true. The
    thread is abandoned (daemon), not killed: for a call like yt-dlp's
    in-process lookup, which has no cancel point of its own."""
    box = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=work, daemon=True, name="live-resolve")
    worker.start()
    while True:
        worker.join(poll)
        if not worker.is_alive():
            break
        if should_stop():
            raise background_jobs.JobCancelled("live")
    if "error" in box:
        raise box["error"]
    return box["value"]


class _LiveRun(ChunkCaptioner):
    """State and steps of one run_live_job call; the docstring there is the contract."""

    def __init__(self, job_id, url, out_dir, segment_seconds, source_language, whisper_size,
                 engine, use_gpu, poll_interval, cookies_browser, cookies_file, overlap_seconds,
                 max_seconds, stream_url_check, proxy, report_stage, reply_without_thinking,
                 report_note, whisper_clock, report_stats, term_hints, spend_guard,
                 delayed_player, report_player=None):
        self.job_id = job_id
        self.url = url
        self.out_dir = out_dir
        self.segment_seconds = segment_seconds
        self.source_language = source_language
        self.whisper_size = whisper_size
        self.engine = engine
        self.use_gpu = use_gpu
        self.poll_interval = poll_interval
        self.cookies_browser = cookies_browser
        self.cookies_file = cookies_file
        self.overlap_seconds = max(0.0, min(float(overlap_seconds or 0), segment_seconds / 2))
        self.stream_url_check = stream_url_check
        self.proxy = proxy
        self.report_stage = report_stage
        self.reply_without_thinking = reply_without_thinking
        self.report_stats = report_stats
        self.term_hints = term_hints
        self.spend_guard = spend_guard
        self.delayed_player = delayed_player
        self.report_player = report_player
        self.my_generation = bump_generation(job_id)
        self.deadline = None if max_seconds is None else [time.monotonic() + max_seconds]
        self.note = report_note or (lambda text, key=None: None)
        self.runner = live_whisper.WhisperRunner(
            self.should_stop, self.note, clock=whisper_clock, gpu=use_gpu)
        self.device = "GPU" if use_gpu else "CPU"
        self.source_url: str | None = None
        self.state = {"last": -1, "context": "", "prev_tail": None, "jump": 0.0}
        self.proc: SegmentCapture | None = None
        self.player: live_player.PlayerRecorders | None = None
        self.capture_stopped = False
        self.capture_started = None
        # A break is only reconnected once this session has captioned a chunk:
        # a capture that never worked is a bad link or format, not a break.
        self.captured_any = False
        # {"since", "attempt", "reason"} from a stream break until a chunk arrives again.
        self.outage = None
        self.breaks = 0
        # Last, so an __init__ that raises leaves no deadline behind for extend to find.
        with _deadlines_lock:
            _finishing.discard(job_id)
            if self.deadline is not None:
                _deadlines[job_id] = self.deadline

    def should_stop(self):
        return (background_jobs.is_cancel_requested(self.job_id)
                or current_generation(self.job_id) != self.my_generation)

    def _superseded(self):
        return current_generation(self.job_id) != self.my_generation

    def report(self, message, **stage_options):
        """Names the current step. report_stage (the service's) also takes
        the cancel text and slow-step note; without one only the message."""
        if self.report_stage is not None:
            self.report_stage(message, **stage_options)
        else:
            background_jobs.update_progress(self.job_id, 0.0, message)

    def time_up(self):
        with _deadlines_lock:
            return self.deadline is not None and time.monotonic() >= self.deadline[0]

    def forget_deadline(self):
        with _deadlines_lock:
            _finishing.discard(self.job_id)
            if self.deadline is not None and _deadlines.get(self.job_id) is self.deadline:
                del _deadlines[self.job_id]

    def run(self):
        self.report("Resolving the stream address...")
        try:
            self.source_url = _run_abortable(
                lambda: resolve_stream_url(self.url, cookies_browser=self.cookies_browser,
                                           cookies_file=self.cookies_file, proxy=self.proxy),
                self.should_stop)
        except background_jobs.JobCancelled:
            background_jobs.update_progress(self.job_id, 1.0, "Stopped.")
            return
        if self.stream_url_check is not None:
            self.stream_url_check(self.source_url)
        if not self._warm_whisper():
            return
        self._capture_and_caption()

    def _warm_whisper(self):
        """False when a cancel ended the job during the load."""
        # Before capture starts, so the load and first CUDA call don't put chunk 1
        # behind the live edge.
        self.report(
            f"Loading Whisper {self.whisper_size} ({self.device}) before capture...",
            cancel_message="Cancelling... Whisper is loading; the load ends by itself when it finishes.",
            slow_after=60, slow_note="Still loading after {secs} s: the model may be "
                                     "downloading for the first time.")
        try:
            self.note(f"Whisper {self.whisper_size} ready in "
                      f"{live_whisper.warm_start(self.runner, self.whisper_size, self.use_gpu):.1f} s.")
        except background_jobs.JobCancelled:
            background_jobs.update_progress(self.job_id, 1.0, self.runner.finish())
            return False
        except Exception as exc:
            # The first chunk repeats the load and shows the real error.
            live_whisper.log_failure("Whisper warm-up failed", exc)
            self.note("Whisper could not be warmed up; chunk 0 will try loading it.")
        return True

    def _capture_and_caption(self):
        from engine_backends.local import abort_check_var
        from engine_backends.shared import _cancel_check_var
        self.report("Starting capture...")
        self.limit = live_whisper.chunk_timeout(self.segment_seconds)
        self.stage = live_pipeline.Stage(self.report, self.note, self.whisper_size, self.device,
                                         self.limit, self.engine, self.segment_seconds)
        self.board = live_pipeline.CueBoard(
            self.job_id, lambda: current_generation(self.job_id) == self.my_generation)
        self.stats = live_pipeline.LagStats(self.report_stats)
        # Backoff waits notice a cancel; a blocked Ollama request is abandoned.
        cancel_token = _cancel_check_var.set(self.should_stop)
        abort_token = abort_check_var.set(self.should_stop)

        self.skips = live_whisper.SkipNotes(self.note, self.segment_seconds)
        self.translator = CueTranslator(self.engine, self.source_language,
                                        self.reply_without_thinking, self.term_hints,
                                        self.spend_guard)
        control = live_pipeline.register(self.job_id)
        try:
            with live_pipeline.translation_worker(
                    self.translator, self.board, self.stage, self.stats, self.note,
                    self.should_stop, control.wake) as worker:
                if self.source_url is None:
                    raise RuntimeError("source_url is set before capture starts")
                lead_in = live_audio.first_chunk_lead_in(self.segment_seconds, self.source_language)
                # Only passed when used, so a capture without a lead-in keeps its old call.
                capture_args: dict[str, Any] = {"lead_in_seconds": lead_in} if lead_in else {}
                self.proc = start_segment_capture(
                    self.source_url, self.out_dir, self.segment_seconds, proxy=self.proxy,
                    **capture_args)
                # The lead-in puts every chunk's audio that much later in the file's clock.
                self.state["jump"] -= lead_in
                self.capture_started = _timeline_clock()
                self._start_player()
                finishing = self._capture_loop(control, worker)
                if self.outage is not None:
                    self._end_outage()
                if finishing and not self.should_stop():
                    self._caption_remaining(worker)
        finally:
            live_pipeline.unregister(self.job_id, control)
            _cancel_check_var.reset(cancel_token)
            abort_check_var.reset(abort_token)
            self._wind_down()

    def _capture_loop(self, control, worker):
        """Captions one waiting chunk per pass. True when it ended by finish,
        time limit or stream end, False on cancel."""
        if self.proc is None:
            raise RuntimeError("capture is started before the loop runs")
        while not self.should_stop():
            proc = self.proc
            if control.finish.is_set():
                return True
            if self.time_up():
                self._ended("Stopped: time limit reached.")
                return True
            watched = live_player.watch(self.player, self.note, self.out_dir)
            if watched is not self.player:
                self._set_player(watched)
            code = proc.poll()
            if getattr(proc, "ended_live", False):
                self._ended("Capture stopped: the stream is no longer live.")
                return True
            broke = proc.error or (FFMPEG_BROKE if code not in (None, 0) else None)
            if broke:
                if not self.captured_any or broke in live_fetch.PERMANENT:
                    raise LiveCaptureError(broke)
                outcome = self._reconnect(FFMPEG_STOPPED if broke == FFMPEG_BROKE else broke,
                                          control, worker)
                if outcome == "resumed":
                    continue
                return outcome == "finish"
            if code is not None:
                self._ended("Capture stopped (stream likely ended).")
                return True

            waiting, dropped_through = live_whisper.drop_backlog(
                list_completed_chunks(self.out_dir, self.state["last"]),
                MAX_BACKLOG_CHUNKS, self.skips, before_remove=self.count_jump)
            if dropped_through is not None:
                self.state["last"] = dropped_through
                # What was said before the gap is not what comes next.
                self.state["context"] = ""
            # One chunk per pass, so the backlog is looked at again after each.
            if waiting:
                self._resumed()
                try:
                    self.caption(*waiting[0], worker)
                except background_jobs.JobCancelled:
                    # The while loop's own check ends the job.
                    pass
                self.captured_any = True
                continue
            self.stage.waiting(self.state["last"] + 1)
            control.idle(self.poll_interval)
        return False

    def _reconnect(self, reason, control, worker):
        """After a stream break: captions what the broken capture wrote, then
        resolves the page link again (the stream URL may have expired) and
        starts a new capture, with waits of RECONNECT_DELAYS between tries.
        Returns "resumed" (a new capture runs; the outage ends with its first
        chunk), "finish" (Stop, the time limit, the stream no longer live or
        RECONNECT_GIVE_UP_SECONDS passed: end as a stream that ended) or
        "cancel". The board, the translator and its context stay as they are."""
        if self.outage is None:
            self.breaks += 1
            self.outage = {"since": time.monotonic(), "attempt": 0}
        self._drain_broken(worker)
        self.outage["reason"] = reason
        # A first Stop, the time limit or the reconnect budget must not wait
        # out a lookup that can take several yt-dlp tries (or DNS timeouts)
        # in an outage; the lookup thread is abandoned.
        give_up_at = self.outage["since"] + RECONNECT_GIVE_UP_SECONDS

        def interrupted():
            return (self.should_stop() or control.finish.is_set() or self.time_up()
                    or time.monotonic() >= give_up_at)
        check = self.stream_url_check
        while True:
            self.outage["attempt"] += 1
            attempt = self.outage["attempt"]
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS)) - 1]
            if time.monotonic() + delay - self.outage["since"] > RECONNECT_GIVE_UP_SECONDS:
                return self._give_up()
            self.stats.reconnect_attempt = attempt
            self.stats.send()
            if attempt == 1:
                self.note(f"Stream broke off: {self.outage['reason']} Reconnecting; "
                          "the captions so far are kept.", RECONNECT_NOTE_KEY)
            self.stage.outage(f"Stream broke off: {self.outage['reason']} Reconnecting "
                              f"in {delay} s (try {attempt})...")
            waited = self._outage_wait(delay, control)
            if waited != "resumed":
                self._end_outage()
                return waited
            self.stage.outage(f"Stream broke off: {self.outage['reason']} Reconnecting now "
                              f"(try {attempt})...")
            try:
                url = _run_abortable(
                    lambda: resolve_stream_url(self.url, cookies_browser=self.cookies_browser,
                                               cookies_file=self.cookies_file, proxy=self.proxy,
                                               live_only=True),
                    interrupted)
            except background_jobs.JobCancelled:
                return self._lookup_interrupted(control)
            except StreamEnded:
                self._end_outage()
                self._ended("Capture stopped: the stream is no longer live.")
                return "finish"
            except Exception as exc:
                live_whisper.log_failure(f"reconnect try {attempt}: resolve failed", exc)
                self.outage["reason"] = RESOLVE_FAILED
                continue
            # A refused address ends the job as it does at Start; a lookup that
            # failed (DNS on a flaky network, when reconnects happen) is retried.
            if check is not None:
                try:
                    _run_abortable(lambda: check(url), interrupted)
                except background_jobs.JobCancelled:
                    return self._lookup_interrupted(control)
                except DependencyUnavailableError as exc:
                    live_whisper.log_failure(f"reconnect try {attempt}: address check failed", exc)
                    self.outage["reason"] = RESOLVE_FAILED
                    continue
            # The lookup can return just as Stop is pressed: no capture to start and stop again.
            if self.should_stop():
                self._end_outage()
                return "cancel"
            if control.finish.is_set():
                self._end_outage()
                return "finish"
            try:
                proc = start_segment_capture(url, self.out_dir, self.segment_seconds,
                                             proxy=self.proxy, start_number=self.state["last"] + 1,
                                             live_only=True)
            except Exception as exc:
                live_whisper.log_failure(f"reconnect try {attempt}: capture failed", exc)
                self.outage["reason"] = live_fetch.UNREACHABLE
                continue
            self.source_url, self.proc, self.capture_stopped = url, proc, False
            self._skip_outage_on_timeline()
            if self.delayed_player:
                try:
                    self._restart_player()
                except background_jobs.JobCancelled:
                    self._end_outage()
                    return "cancel"
            self.stage.outage(f"Stream found again (try {attempt}): waiting for its first "
                              "chunk...")
            return "resumed"

    def _give_up(self):
        self._end_outage()
        self._ended(f"Stopped: the stream broke off and could not be reconnected "
                    f"within {RECONNECT_GIVE_UP_SECONDS // 60} minutes.")
        return "finish"

    def _lookup_interrupted(self, control):
        """Why a reconnect lookup was abandoned, as _reconnect's outcome."""
        if self.should_stop():
            self._end_outage()
            return "cancel"
        if control.finish.is_set():
            self._end_outage()
            return "finish"
        if self.time_up():
            self._end_outage()
            self._ended("Stopped: time limit reached.")
            return "finish"
        return self._give_up()

    def _start_player(self):
        self._set_player(live_player.start_if_possible(
            self.delayed_player, self.url, self.source_url, self.out_dir, self.note,
            self.should_stop, self.stream_url_check, self.proxy,
            self.cookies_browser, self.cookies_file))

    def _restart_player(self):
        """The recorders fetch the same expiring stream URLs as the capture, so
        after a reconnect they are started again on the newly resolved ones. The
        old copy is cleared first, so the page waits for the new one instead of
        playing a window that no longer grows."""
        if self.player is not None:
            self.player.stop(kill=True)
            self.player = None
        live_player.clear(self.out_dir)
        self._start_player()
        if self.player is not None:
            self.note(live_player.RESTARTED_NOTE, "player")

    def _set_player(self, player):
        """Tells the session whether its delayed player runs, so the page shows
        the site's own embed while it does not."""
        self.player = player
        if self.delayed_player and self.report_player is not None:
            self.report_player(player is not None)

    def _outage_wait(self, seconds, control):
        """"resumed" once `seconds` pass; "finish" or "cancel" if the session ends first."""
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if self.should_stop():
                return "cancel"
            if control.finish.is_set():
                return "finish"
            if self.time_up():
                self._ended("Stopped: time limit reached.")
                return "finish"
            control.idle(min(live_pipeline.IDLE_WAIT_SECONDS, max(0.0, until - time.monotonic())))
        return "resumed"

    def _drain_broken(self, worker):
        """Captions the chunks the broken capture left, its last partial one
        included. What follows the break is not the same speech, so nothing is
        held back for it and no overlap or Whisper prompt carries across."""
        if not self.capture_stopped:
            stop_capture(self.proc)
            self.capture_stopped = True
        for idx, path in list_remaining_chunks(self.out_dir, self.state["last"]):
            if self.should_stop():
                break
            try:
                self.caption(idx, path, worker, final=True)
            except background_jobs.JobCancelled:
                break
        if not self.should_stop():
            self.release_held(worker)
        self.state["prev_tail"] = None
        self.state["context"] = ""

    def _skip_outage_on_timeline(self):
        """Moves the chunks after the break by the stream time it lasted (the
        new capture joins at the live edge), so the timeline stays the stream's."""
        heard = (self.state["last"] + 1) * self.segment_seconds + self.state["jump"]
        elapsed = _timeline_clock() - (self.capture_started or _timeline_clock())
        self.state["jump"] += max(0.0, elapsed - heard)

    def _resumed(self):
        """The first chunk after a break: the outage is over."""
        if self.outage is None:
            return
        took = time.monotonic() - self.outage["since"]
        self._end_outage()
        if self.breaks == 1:
            self.note(f"Reconnected after {took:.0f} s; speech during the break was not captured.",
                      RECONNECT_NOTE_KEY)
        else:
            self.note(f"Reconnected {self.breaks} times (last after {took:.0f} s); speech during "
                      "the breaks was not captured.", RECONNECT_NOTE_KEY)

    def _end_outage(self):
        self.outage = None
        self.stage.outage(None)
        self.stats.reconnect_attempt = 0
        self.stats.send()

    def _ended(self, why):
        """Names why capture ended. Also kept as a note: the final status is
        replaced when the job ends, and the page then only says it finished."""
        background_jobs.update_progress(self.job_id, 0.0, why)
        self.note(why, "ended")
        mark_finishing(self.job_id)

    def _caption_remaining(self, worker):
        # ffmpeg writes out the segment it is in, so the speech up to
        # the stop is captioned too, not dropped with the backlog.
        if not self.capture_stopped:
            stop_capture(self.proc)
            self.capture_stopped = True
        for idx, path in list_remaining_chunks(self.out_dir, self.state["last"]):
            if self.should_stop():
                break
            try:
                self.caption(idx, path, worker, final=True)
            except background_jobs.JobCancelled:
                break
        if not self.should_stop():
            self.release_held(worker)
        worker.finish()

    def _wind_down(self):
        if current_generation(self.job_id) == self.my_generation:
            self.board.mark_pending(self.board.cues, live_cue_feed.CANCELLED)
        # A cancel kills ffmpeg's tree at once; a stream that just ended
        # lets it flush its last segment.
        if self.player:
            self.player.stop(kill=self.should_stop())
        if self.proc is not None and not self.capture_stopped:
            if self.should_stop():
                stop_capture(self.proc, kill=True)
            else:
                stop_capture(self.proc)
        background_jobs.update_progress(self.job_id, 1.0, self.runner.finish())

    def _transcribe_window(self, idx, path, window, shift=0.0, covered=None):
        self.stage.audio = window.audio_seconds
        timing = self.runner.timing(path, window.audio_seconds)

        def whisper_done(took):
            timing.whisper_done(took)
            self.stage.transcribed(idx, took)
        new_cues = process_chunk(
            window.path, idx, self.segment_seconds, self.source_language, self.whisper_size,
            self.engine, use_gpu=self.use_gpu, context_prompt=self.state["context"],
            name_prompt=getattr(self.term_hints, "names", ""), translator=self.translator, first_id=len(self.board.cues), translate=False,
            overlap_seconds=window.pad_seconds, overlap_tail_text=window.tail_text,
            audio_shift=window.trimmed_seconds + shift, overlap_covered_seconds=covered,
            on_overlap=self.stats.overlap_joined,
            guard=lambda call: self.runner.run(call, self.limit, f"chunk {idx}"),
            on_transcribed=whisper_done,
            on_stage=lambda step: self.stage.chunk(step, idx),
            # Only the Stop button's generation bump discards a chunk
            # between steps; a plain cancel lets the lines already
            # transcribed through (as it always did) and ends the
            # job at the next loop check. Whisper and a blocked
            # Ollama call are aborted either way (guard, cancel check).
            is_cancelled=self._superseded)
        return new_cues, timing


def run_live_job(job_id: str, url: str, out_dir: str, segment_seconds: int,
                  source_language: str, whisper_size: str, engine, use_gpu: bool = False,
                  poll_interval: float = live_pipeline.IDLE_WAIT_SECONDS,
                  cookies_browser: str | None = None, cookies_file: str | None = None,
                  overlap_seconds: float = DEFAULT_OVERLAP_SECONDS, max_seconds: float | None = None,
                  stream_url_check=None, proxy: str | None = None, report_stage=None,
                  reply_without_thinking: bool = True, report_note=None, whisper_clock=None,
                  report_stats=None, term_hints=None, spend_guard=None, delayed_player=False,
                  report_player=None):
    """
    The background-thread target (see job_launch.start_job). Runs
    until request_cancel(job_id) is set, live_pipeline.request_finish(job_id)
    is called (Stop pressed once), the time limit passes or the stream itself
    ends, then stops capture and returns -- background_jobs marks the job
    "done" once this returns. Cues accumulate in the job's result
    (background_jobs.get_status(job_id)["result"]), replaced with a
    fresh full list on every change rather than appended one cue
    at a time, so a caller reading it mid-update never sees a partial
    write. Translation runs on live_pipeline.TranslationWorker's thread, so
    Whisper for the next chunk never waits for it.

    Ending: a finish request, the time limit and a stream that ends caption
    the chunks still waiting and the final partial one, and let the
    translations queued run out. A cancel or an error drops them and abandons
    the translation in flight.

    Stale-chunk guard: this call's generation (bump_generation(), captured
    at the top) is re-checked before a chunk is applied. If Stop bumped it
    mid-chunk, its cues are not added and nothing more is published;
    whatever was last published remains the final result.

    overlap_seconds: how much of each chunk's own audio tail is prepended
    to the next chunk before transcribing it (see the module docstring
    and dedup_overlap()). 0 turns overlap off entirely; capped at half a
    chunk so padding can never outweigh the chunk itself.

    Stale-file guard: start_segment_capture() clears any chunk_*.wav/
    padded_*.wav left in out_dir by an earlier run before ffmpeg starts.

    max_seconds: a hard stop -- once this much wall-clock time has passed
    since the call began, capture stops as if Stop were pressed
    (extend_time_limit moves it). None means no limit.

    stream_url_check: optional callable run on the stream URL yt-dlp
    resolved, before anything fetches it; it raises to refuse (the API
    checks scheme and public host). proxy is passed to both
    resolve_stream_url and start_segment_capture. Both default to None
    (no check, no proxy).

    report_stage(message, cancel_message=, slow_after=, slow_note=): how the
    job names the step it is in (services/job_stage_service.set_stage bound
    to the job id); None just sets the job's message.

    report_note(text, key=): a short fixed-text event worth keeping on screen
    after the status moves on; a note replaces the earlier one with its key.
    None drops them. report_stats(numbers): live_pipeline.LagStats figures
    after each chunk. report_player(running): with delayed_player, whether its
    recorders run, each time that changes (a failure, a restart after a reconnect).
    whisper_clock: a test seam.

    Whisper runs on one live_whisper.WhisperRunner thread at a time: Stop
    interrupts it within a poll, and a chunk it cannot finish in max(30 s,
    6 x chunk) is skipped, as are chunks that arrive while it is running.

    A fetch failure, or ffmpeg ending with an error, before any chunk was
    captioned (or one live_fetch.PERMANENT names) ends the job with a
    LiveCaptureError carrying a fixed message (no URL). After that a break
    reconnects (_LiveRun._reconnect) and, if the stream does not come back,
    ends like a stream that simply ends: a normal finish.
    """
    live_run = None
    try:
        live_run = _LiveRun(job_id, url, out_dir, segment_seconds, source_language, whisper_size,
                            engine, use_gpu, poll_interval, cookies_browser, cookies_file,
                            overlap_seconds, max_seconds, stream_url_check, proxy, report_stage,
                            reply_without_thinking, report_note, whisper_clock, report_stats,
                            term_hints, spend_guard, delayed_player, report_player)
        live_run.run()
    finally:
        if live_run is not None:
            live_run.forget_deadline()
        drop_generation(job_id)
