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
  3. Each transcribed line is translated with whichever engine/API key
     the person picked -- the same call a normal drama's lines get.
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
  - The resolved stream URL (from yt-dlp) can expire after a few hours
    on some platforms. If capture silently stops producing new chunks
    on a long-running stream, stop and restart the job to re-resolve a
    fresh URL.
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
    end); NOT tested against an actual live YouTube/Twitch broadcast,
    since that needs a real stream running at test time. Sanity-check
    the first couple of chunks after starting before relying on it for
    a whole stream.
"""

import os
import re
import subprocess
import threading
import time

import background_jobs
import live_audio
import live_cue_feed
import live_fetch
import live_whisper
from live_audio import read_wav_tail, write_padded_chunk  # noqa: F401 -- re-exported for callers and tests
from live_cue_translation import CueTranslator
from live_tokens import LEADING_NON_WORD_RE, TOKEN_RE, tokens


class LiveCaptureError(RuntimeError):
    """Raised when the stream URL can't be resolved or capture can't
    start, so callers can show a clear message instead of a raw
    yt-dlp/subprocess traceback."""


# Attempted in order until one returns a usable format. "No video formats
# found" on a confirmed-live, confirmed-up-to-date-yt-dlp stream is a real,
# widely-reported YouTube-side issue, not a bug specific to this app: since
# 2024 YouTube has increasingly required a proof-of-origin ("PO") token for
# the default web player client, and yt-dlp's own maintainers' documented
# workaround is to request formats through a different player client that
# doesn't need one -- which one currently works shifts over time as YouTube
# and yt-dlp keep adjusting, so several are tried rather than betting on one.
_YOUTUBE_CLIENT_FALLBACKS = ["android", "tv", "web_safari"]

# Deliberately small: these fail identically on every attempt (wrong link,
# no access, gone), so retrying would just waste time. Everything else --
# including yt-dlp/YouTube error strings not seen before, like "no video
# formats found" or "the page needs to be reloaded" -- is assumed to be a
# transient/format-availability issue worth retrying through the
# player-client fallbacks below, rather than hardcoding each new error
# string as it's discovered.
_NON_RETRYABLE_STREAM_ERROR_PHRASES = (
    "private video",
    "video unavailable",
    "video has been removed",
    "this video is no longer available",
    "not available in your country",
)


def resolve_stream_url(url: str, cookies_browser: str = None, cookies_file: str = None,
                       proxy: str = None) -> str:
    """
    Resolves a page URL (YouTube live, or anything yt-dlp supports) to a
    direct, ffmpeg-playable media URL WITHOUT downloading anything -- the
    piece that lets ffmpeg read an ongoing live broadcast the same way it
    reads a file.

    cookies_browser/cookies_file: pass yt-dlp the person's own login (see
    video_download.cookie_options()) for a stream page that needs it.
    proxy: when given, every request yt-dlp makes (redirects included) goes
    through this proxy URL (yt-dlp's `proxy` option).
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise ImportError("Live capture needs yt-dlp: pip install yt-dlp") from exc

    import video_download
    cookie_opts = video_download.cookie_options(cookies_browser, cookies_file)

    def _try(fmt, player_client=None):
        opts = {"format": fmt, "quiet": True, "no_warnings": True,
                # Since late 2025, YouTube downloads need an external JS
                # runtime through yt-dlp's EJS system, or formats silently
                # go missing -- likely the real cause behind at least some
                # of the "no video formats found" failures this already
                # works around below. Deno is yt-dlp's own default; listing
                # the others too means it still works if only one of them
                # happens to be installed.
                "js_runtimes": {"deno": {}, "node": {}, "bun": {}, "quickjs": {}},
                **cookie_opts}
        if proxy:
            opts["proxy"] = proxy
        if player_client:
            opts["extractor_args"] = {"youtube": {"player_client": [player_client]}}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    last_exc = None
    for fmt, player_client in (
        [("bestaudio/best", None), ("best", None)]
        + [("best", c) for c in _YOUTUBE_CLIENT_FALLBACKS]
    ):
        try:
            info = _try(fmt, player_client)
            break
        except Exception as exc:
            last_exc = exc
            if any(phrase in str(exc).lower() for phrase in _NON_RETRYABLE_STREAM_ERROR_PHRASES):
                raise LiveCaptureError(
                    f"Couldn't resolve that stream URL.\n\n{type(exc).__name__}: {exc}\n\n"
                    "Common causes: the link is wrong/private/region-locked, the stream "
                    "hasn't started yet, or it's already ended."
                ) from exc
    else:
        raise LiveCaptureError(
            f"Couldn't resolve that stream URL.\n\n{type(last_exc).__name__}: {last_exc}\n\n"
            "Still failed after trying every known player-client workaround "
            f"({', '.join(_YOUTUBE_CLIENT_FALLBACKS)}). This usually means either YouTube's "
            "proof-of-origin token requirement, or that yt-dlp has no JavaScript runtime to "
            "use (Deno, Node, Bun or QuickJS; check the Diagnostics tab). If you don't have "
            "one, install Deno (https://deno.land) and run `pip install -U yt-dlp`. Otherwise "
            "this is a known, actively-shifting YouTube/yt-dlp issue, not specific to this "
            "app -- check https://github.com/yt-dlp/yt-dlp/issues for the current recommended "
            "workaround (often a specific --extractor-args player_client value, or supplying "
            "browser cookies via --cookies-from-browser), since which client currently works "
            "changes as both sides keep adjusting."
        ) from last_exc

    stream_url = info.get("url")
    if not stream_url:
        raise LiveCaptureError(
            "yt-dlp resolved the page but didn't return a direct stream URL -- "
            "this can happen for a stream that hasn't gone live yet."
        )
    return stream_url


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


def start_segment_capture(source_url: str, out_dir: str, segment_seconds: int = 20,
                           sample_rate: int = 16000, proxy: str = None) -> SegmentCapture:
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
    """
    os.makedirs(out_dir, exist_ok=True)
    clear_stale_chunks(out_dir)
    pattern = os.path.join(out_dir, "chunk_%05d.wav")
    cmd = ["ffmpeg", "-y", "-protocol_whitelist", "pipe", "-format_whitelist",
           FFMPEG_FORMAT_WHITELIST, "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(sample_rate),
           "-f", "segment", "-segment_time", str(segment_seconds), "-reset_timestamps", "1",
           pattern]
    # Its own process group/session, so background_jobs.kill_tree (the cancel
    # path) takes down anything ffmpeg started along with it.
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **group)
    try:
        pump = live_fetch.StreamPump(source_url, proc.stdin, proxy=proxy).start()
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


def list_completed_chunks(out_dir: str, last_completed_index: int):
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


def dedup_overlap(segments: list, overlap_seconds: float, tail_text: str) -> list:
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

    if cut is None:
        return [seg for seg in segments if seg["end"] > overlap_seconds]

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


# Whisper's initial_prompt has a real, hard limit (roughly the last 224
# tokens) -- capping what's carried forward here to a generous but bounded
# tail keeps every chunk's prompt cheap to build, not because a longer
# value would break anything, since Whisper itself only ever uses the end
# of whatever's passed.
_CONTEXT_CARRY_CHARS = 200

# A few seconds: long enough to re-hear a word or short phrase cut at a
# chunk boundary, short enough that the extra audio re-transcribed per
# chunk stays a small fraction of a 15-30s chunk.
DEFAULT_OVERLAP_SECONDS = 3

# Chunks left waiting when the loop looks (the one being cut plus one more is
# the normal pipeline); beyond this the oldest are dropped, not queued, so
# captions stay near the live edge when Whisper runs slower than the stream.
MAX_BACKLOG_CHUNKS = 2


def process_chunk(chunk_path: str, chunk_index: int, segment_seconds: float,
                   source_language: str, whisper_size: str, engine, use_gpu: bool = False,
                   context_prompt: str = "", overlap_seconds: float = 0.0,
                   overlap_tail_text: str = "", on_stage=None, is_cancelled=None,
                   translator=None, first_id: int = 0, on_cues=None, guard=None,
                   audio_shift: float = 0.0, on_transcribed=None):
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
    directly. audio_shift: seconds the start of an over-long chunk was trimmed by,
    added to the timestamps. on_transcribed(took_seconds): called when Whisper
    returns.
    """
    import core

    def checkpoint():
        if is_cancelled is not None and is_cancelled():
            raise background_jobs.JobCancelled(f"live chunk {chunk_index}")

    if on_stage:
        on_stage("transcribing")
    translator = translator or CueTranslator(engine, source_language)
    started = time.monotonic()
    segments = (guard or (lambda call: call(None)))(lambda progress_cb: core.transcribe_for_timing(
        chunk_path, model_size=whisper_size, language=source_language, use_gpu=use_gpu,
        initial_prompt=context_prompt, progress_cb=progress_cb))
    if on_transcribed:
        on_transcribed(time.monotonic() - started)
    checkpoint()
    segments = dedup_overlap(segments, overlap_seconds, overlap_tail_text)
    offset = chunk_index * segment_seconds - overlap_seconds + audio_shift
    cues = live_cue_feed.pending_cues(segments, offset, first_id)
    live_cue_feed.translate_cues(cues, translator, checkpoint, on_stage, on_cues)
    return cues


# {job_id: generation}, a plain module dict rather than something
# behind background_jobs' own lock -- a single int increment/read per
# job_id needs no more ceremony than that, same reasoning as
# background_jobs' own is_cancel_requested flag.
_generations = {}


def bump_generation(job_id: str) -> int:
    """Advances this live session's generation counter and returns the
    new value. Call this from the Stop button's own handler (alongside
    request_cancel) -- that's the one place a NEW generation can start
    while an OLDER one's chunk is still genuinely in flight (mid
    transcribe/translate call inside run_live_job's own thread, which
    only notices a stop request between iterations). A restarted session
    can't itself race the one it replaces: start_job() already refuses a
    second run under the same job_id while the first is still "running",
    so by the time a new run_live_job call can begin, the old one has
    already returned -- but bumping here too costs nothing and keeps the
    guard honest if that single-instance assumption ever changes."""
    _generations[job_id] = _generations.get(job_id, 0) + 1
    return _generations[job_id]


def current_generation(job_id: str) -> int:
    return _generations.get(job_id, 0)


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


def _wait(seconds: float, should_stop, step: float = 0.25):
    """Sleeps up to `seconds`, returning early once should_stop() is true."""
    end = time.monotonic() + seconds
    while not should_stop():
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(step, remaining))


def run_live_job(job_id: str, url: str, out_dir: str, segment_seconds: int,
                  source_language: str, whisper_size: str, engine, use_gpu: bool = False,
                  poll_interval: float = 2.0, cookies_browser: str = None, cookies_file: str = None,
                  overlap_seconds: float = DEFAULT_OVERLAP_SECONDS, max_seconds: float = None,
                  stream_url_check=None, proxy: str = None, report_stage=None,
                  reply_without_thinking: bool = True, report_note=None, whisper_clock=None):
    """
    The background-thread target (see background_jobs.start_job). Runs
    until request_cancel(job_id) is set or the stream itself ends, then
    stops capture and returns -- background_jobs marks the job "done"
    once this returns. Cues accumulate in the job's result
    (background_jobs.get_status(job_id)["result"]), replaced with a
    fresh full list after every new chunk rather than appended one cue
    at a time, so a caller reading it mid-update never sees a partial
    write.

    Stale-chunk guard: this call's generation (bump_generation(), captured
    at the top) is re-checked before a chunk is applied. If Stop bumped it
    mid-chunk, its cues are not added; whatever on_cues last published
    (pending, cancelled or done) remains the final result.

    overlap_seconds: how much of each chunk's own audio tail is prepended
    to the next chunk before transcribing it (see the module docstring
    and dedup_overlap()). 0 turns overlap off entirely; capped at half a
    chunk so padding can never outweigh the chunk itself.

    Stale-file guard: start_segment_capture() clears any chunk_*.wav/
    padded_*.wav left in out_dir by an earlier run before ffmpeg starts.

    max_seconds: a hard stop -- once this much wall-clock time has passed
    since the call began, capture stops as if Stop were pressed. None
    means no limit.

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
    None drops them. whisper_clock: a test seam.

    Whisper runs on one live_whisper.WhisperRunner thread at a time: Stop
    interrupts it within a poll, and a chunk it cannot finish in max(30 s,
    6 x chunk) is skipped, as are chunks that arrive while it is running.

    A fetch failure, or ffmpeg ending with an error, ends the job with
    a LiveCaptureError carrying a fixed message (no URL); a stream that
    simply ends finishes it normally.
    """
    overlap_seconds = max(0.0, min(float(overlap_seconds or 0), segment_seconds / 2))
    my_generation = bump_generation(job_id)
    started = time.monotonic()

    def should_stop():
        return (background_jobs.is_cancel_requested(job_id)
                or current_generation(job_id) != my_generation)

    def report(message, **stage_options):
        """Names the current step. report_stage (the service's) also takes
        the cancel text and slow-step note; without one only the message."""
        if report_stage is not None:
            report_stage(message, **stage_options)
        else:
            background_jobs.update_progress(job_id, 0.0, message)

    note = report_note or (lambda text, key=None: None)

    runner = live_whisper.WhisperRunner(
        should_stop, note, clock=whisper_clock, gpu=use_gpu,
        unload=live_whisper.unload_scope_for(engine, use_gpu, whisper_size))

    report("Resolving the stream address...")
    try:
        source_url = _run_abortable(
            lambda: resolve_stream_url(url, cookies_browser=cookies_browser,
                                       cookies_file=cookies_file, proxy=proxy),
            should_stop)
    except background_jobs.JobCancelled:
        background_jobs.update_progress(job_id, 1.0, "Stopped.")
        return
    if stream_url_check is not None:
        stream_url_check(source_url)

    device = "GPU" if use_gpu else "CPU"
    # Before capture starts, so the load and first CUDA call don't put chunk 1
    # behind the live edge.
    report(f"Loading Whisper {whisper_size} ({device}) before capture...",
           cancel_message="Cancelling... Whisper is loading; the load ends by itself when it finishes.",
           slow_after=60, slow_note="Still loading after {secs} s: the model may be "
                                    "downloading for the first time.")
    try:
        note(f"Whisper {whisper_size} ready in "
             f"{live_whisper.warm_start(runner, whisper_size, use_gpu):.1f} s.")
    except background_jobs.JobCancelled:
        background_jobs.update_progress(job_id, 1.0, runner.finish())
        return
    except Exception:
        # The first chunk repeats the load and shows the real error.
        note("Whisper could not be warmed up; chunk 0 will try loading it.")
    runner.flush_ollama_notice()

    from engine_backends.local import abort_check_var
    from translate_engines import _cancel_check_var
    report("Starting capture...")
    proc = start_segment_capture(source_url, out_dir, segment_seconds, proxy=proxy)

    # The engine name and model are not secrets (no key, no URL), so they can
    # be named in the status the owner reads.
    engine_label = (getattr(engine, "model", None) or getattr(engine, "name", "") or "the engine")
    engine_name = str(getattr(engine, "name", "") or "the translator").capitalize()
    stage_clock = {"key": None}
    # audio/took: seconds of audio Whisper was handed for the current / last chunk.
    chunk_info = {"audio": 0.0, "took": None, "last_audio": 0.0}
    limit = live_whisper.chunk_timeout(segment_seconds)

    def enter_stage(stage, idx):
        """False when that stage (of that chunk) is already the current one."""
        key = (stage, idx)
        if stage_clock["key"] == key:
            return False
        stage_clock["key"] = key
        return True

    def speed_text():
        return live_whisper.speed_text(chunk_info["took"], chunk_info["last_audio"])

    def set_chunk_stage(stage, idx):
        if not enter_stage(stage, idx):
            return
        if stage == "transcribing":
            report(
                f"Chunk {idx}: transcribing {chunk_info['audio']:.0f} s of audio with Whisper "
                f"{whisper_size} ({device})",
                cancel_message=(f"Cancelling... stopping Whisper on chunk {idx} (running for "
                                "{secs} s); this takes a second or two."),
                slow_after=min(90.0, limit / 2),
                slow_note="Still transcribing after {secs} s: Whisper is slow on this hardware "
                          f"or the GPU is busy. The chunk is skipped at {limit:.0f} s.")
        else:
            speed = speed_text()
            report(
                f"Chunk {idx}: translating with {engine_label} ({engine_name})"
                + (f" · transcribed {speed}" if speed else ""),
                slow_after=60,
                slow_note="Still waiting on " + engine_name + " after {secs} s: it may be "
                          "loading the model or busy with another job. Stop still works.")

    def set_waiting_stage():
        if not enter_stage("capturing", last_completed + 1):
            return
        speed = speed_text()
        report(
            f"Capturing audio: waiting for chunk {last_completed + 1} "
            f"({segment_seconds} s of stream each)" + (f" · last chunk: {speed}" if speed else ""),
            slow_after=segment_seconds * 2 + 30,
            slow_note="No new audio for {secs} s: the stream may have stalled.")

    def transcribed(idx, took):
        audio = chunk_info["audio"]
        chunk_info.update(took=took, last_audio=audio)
        if took > audio > 0 and not chunk_info.get("slow_noted"):
            chunk_info["slow_noted"] = True
            note(f"Chunk {idx}: Whisper took {took:.1f} s for {audio:.0f} s of audio, "
                 "slower than the stream, so the oldest audio will be skipped.")

    # Backoff waits notice a cancel; a blocked Ollama request is abandoned.
    cancel_token = _cancel_check_var.set(should_stop)
    abort_token = abort_check_var.set(should_stop)

    skips = live_whisper.SkipNotes(note, segment_seconds)
    translator = CueTranslator(engine, source_language, reply_without_thinking)
    all_cues = []
    last_completed = -1
    context_prompt = ""
    # read_wav_tail() of the last processed chunk, plus "index" and
    # "text" (what that chunk emitted for its own tail) once processed.
    prev_tail = None
    try:
        while True:
            if should_stop():
                break
            if max_seconds is not None and time.monotonic() - started >= max_seconds:
                background_jobs.update_progress(job_id, 0.0, "Stopped: time limit reached.")
                break
            if proc.error:
                raise LiveCaptureError(proc.error)
            if proc.poll() is not None:
                if proc.poll() != 0:
                    raise LiveCaptureError(
                        "ffmpeg could not read the stream (an unsupported format, or the "
                        "stream broke off).")
                background_jobs.update_progress(
                    job_id, 0.0, "Capture stopped (stream likely ended).")
                break

            waiting, dropped_through = live_whisper.drop_backlog(
                list_completed_chunks(out_dir, last_completed), MAX_BACKLOG_CHUNKS, skips)
            if dropped_through is not None:
                last_completed = dropped_through
                # What was said before the gap is not what comes next.
                context_prompt = ""
            # One chunk per pass, so the backlog is looked at again after each.
            for idx, path in waiting[:1]:
                if (should_stop()
                        or (max_seconds is not None and time.monotonic() - started >= max_seconds)):
                    break
                scratch = []
                try:
                    # Read before this chunk's file is deleted below --
                    # it's the audio the NEXT chunk gets padded with.
                    window = live_audio.window_for_chunk(
                        path, idx, out_dir, segment_seconds, prev_tail, overlap_seconds)
                    tail, scratch = window.tail, window.scratch
                    if window.trimmed_seconds:
                        note(f"Chunk {idx} held {window.audio_seconds + window.trimmed_seconds:.0f} s "
                             f"of audio: skipped its first {window.trimmed_seconds:.0f} s to catch up.")
                    chunk_info["audio"] = window.audio_seconds
                    timing = runner.timing(path, window.audio_seconds)

                    def whisper_done(took, idx=idx):
                        timing.whisper_done(took)
                        transcribed(idx, took)
                    new_cues = process_chunk(
                        window.path, idx, segment_seconds, source_language, whisper_size,
                        engine, use_gpu=use_gpu, context_prompt=context_prompt,
                        translator=translator, first_id=len(all_cues),
                        on_cues=lambda chunk: background_jobs.set_result(
                            job_id, live_cue_feed.snapshot(all_cues, chunk)),
                        overlap_seconds=window.pad_seconds, overlap_tail_text=window.tail_text,
                        audio_shift=window.trimmed_seconds,
                        guard=lambda call, idx=idx: runner.run(call, limit, f"chunk {idx}"),
                        on_transcribed=whisper_done,
                        on_stage=lambda stage, idx=idx: set_chunk_stage(stage, idx),
                        # Only the Stop button's generation bump discards a chunk
                        # between steps; a plain cancel lets the lines already
                        # transcribed finish (as it always did) and ends the
                        # job at the next loop check. Whisper and a blocked
                        # Ollama call are aborted either way (guard, cancel check).
                        is_cancelled=lambda: current_generation(job_id) != my_generation)
                    if current_generation(job_id) != my_generation:
                        # The session moved on while this one chunk's own
                        # transcribe/translate call was in flight -- its
                        # result is stale, so it's dropped, not applied.
                        continue
                    runner.mark_model_ready()
                    timing.report(idx, note)
                    all_cues.extend(new_cues)
                    if new_cues:
                        # Carries this chunk's own tail into the NEXT chunk's
                        # transcription -- see process_chunk's docstring.
                        # Independent of the last chunk's translation, so a
                        # translation failure never breaks this.
                        joined = " ".join(c["text"] for c in new_cues)
                        context_prompt = joined[-_CONTEXT_CARRY_CHARS:]
                    if tail:
                        # Only cues that reach into this chunk's own last
                        # tail["seconds"] -- an older line that merely
                        # repeats a phrase must never match the next
                        # chunk's overlap and get new speech trimmed.
                        chunk_end = idx * segment_seconds + tail["chunk_seconds"]
                        tail["index"] = idx
                        tail["text"] = " ".join(
                            c["text"] for c in new_cues if c["end"] > chunk_end - tail["seconds"])
                    prev_tail = tail
                    background_jobs.set_result(job_id, list(all_cues))
                except live_whisper.ChunkTimeout as exc:
                    skips.timed_out(idx, exc.seconds)
                    prev_tail = None
                except live_whisper.WhisperBusy as exc:
                    skips.busy(idx, exc.label)
                    prev_tail = None
                except background_jobs.JobCancelled:
                    # The while loop's own check ends the job.
                    break
                finally:
                    last_completed = idx
                    runner.flush_ollama_notice()
                    for leftover in [path, *scratch]:
                        try:
                            os.remove(leftover)
                        except OSError:
                            pass
            if waiting:
                continue
            set_waiting_stage()
            _wait(poll_interval, should_stop)
    finally:
        _cancel_check_var.reset(cancel_token)
        abort_check_var.reset(abort_token)
        # A cancel kills ffmpeg's tree at once; a stream that just ended
        # lets it flush its last segment.
        if should_stop():
            stop_capture(proc, kill=True)
        else:
            stop_capture(proc)
        background_jobs.update_progress(job_id, 1.0, runner.finish())
