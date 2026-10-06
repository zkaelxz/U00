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
import time
import wave

import background_jobs
import live_fetch


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
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        pump = live_fetch.StreamPump(source_url, proc.stdin, proxy=proxy).start()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    return SegmentCapture(proc, pump)


def stop_capture(capture, timeout: float = 5.0):
    """Stops the fetcher (no new bytes), then ffmpeg -- SIGTERM lets it
    flush the segment it's writing, a kill follows after `timeout` -- and
    then waits for the fetcher's threads, which by then have nothing left
    to block on. Also takes a bare Popen."""
    pump = getattr(capture, "pump", None)
    proc = getattr(capture, "proc", capture)
    if pump is not None:
        pump.halt()
    try:
        if proc.poll() is None:
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


_STALE_RE = re.compile(r"(?:chunk|padded)_\d{5}\.wav$")


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


def read_wav_tail(path: str, seconds: float):
    """
    The last `seconds` of a chunk's own PCM audio, kept in memory so it
    can be prepended to the NEXT chunk after this one's file is deleted.
    Returns {"params": (nchannels, sampwidth, framerate), "frames": bytes,
    "seconds": tail length actually read, "chunk_seconds": the whole
    chunk's length}, or None if the file can't be read as a WAV -- the
    caller then just skips the overlap for the next chunk rather than
    failing the job.
    """
    try:
        with wave.open(path, "rb") as w:
            nchannels, sampwidth, framerate, nframes = (
                w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
            tail_frames = min(nframes, int(round(seconds * framerate)))
            w.setpos(nframes - tail_frames)
            frames = w.readframes(tail_frames)
    except (OSError, EOFError, wave.Error):
        return None
    return {"params": (nchannels, sampwidth, framerate), "frames": frames,
            "seconds": tail_frames / framerate if framerate else 0.0,
            "chunk_seconds": nframes / framerate if framerate else 0.0}


def write_padded_chunk(tail: dict, chunk_path: str, out_path: str) -> float:
    """
    Writes `tail`'s audio (read_wav_tail() of the previous chunk)
    followed by `chunk_path`'s own audio into `out_path`, and returns how
    many seconds of padding were prepended -- the offset process_chunk
    needs to put this chunk's timestamps back on the stream's timeline.
    Returns 0.0 (and writes nothing) if the chunk can't be read or its
    format doesn't match the tail's, so the chunk is transcribed
    unpadded instead.
    """
    if not tail or not tail["frames"]:
        return 0.0
    try:
        with wave.open(chunk_path, "rb") as w:
            params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            frames = w.readframes(w.getnframes())
    except (OSError, EOFError, wave.Error):
        return 0.0
    if params != tail["params"]:
        return 0.0
    with wave.open(out_path, "wb") as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        out.writeframes(tail["frames"] + frames)
    return tail["seconds"]


# One token per CJK/kana character (no spaces to split on), one per run
# of other letters/digits (space-separated languages, Korean included).
# Punctuation and whitespace aren't tokens at all, so "北京。" and
# "北京，" compare equal -- Whisper's punctuation of the same audio often
# differs between two transcriptions, its words much less so.
_CJK_CHARS = "぀-ヿ㐀-䶿一-鿿豈-﫿"
_TOKEN_RE = re.compile(rf"[{_CJK_CHARS}]|[^\W_{_CJK_CHARS}]+")
_LEADING_NON_WORD_RE = re.compile(r"^[\W_]+")

# How many tokens at the very start of the padded transcription may be
# skipped before the match begins -- the overlap audio starts at an
# arbitrary point, often mid-word, and Whisper can render that partial
# word as something that isn't in the previous chunk's text at all.
# Skipped tokens are inside the overlap window, so dropping them loses
# nothing the previous chunk didn't already emit.
_MAX_LEADING_SKIP = 2


def _tokens(text: str):
    return [m.group(0).casefold() for m in _TOKEN_RE.finditer(text or "")]


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
    tail = _tokens(tail_text)
    if not tail:
        return segments

    head = []  # (token, segment position, char offset just past the token)
    for pos, seg in enumerate(segments):
        if seg["start"] >= overlap_seconds:
            break
        for m in _TOKEN_RE.finditer(seg.get("text") or ""):
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
            rest = _LEADING_NON_WORD_RE.sub("", (seg.get("text") or "")[cut_char:]).strip()
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


def process_chunk(chunk_path: str, chunk_index: int, segment_seconds: float,
                   source_language: str, whisper_size: str, engine, use_gpu: bool = False,
                   context_prompt: str = "", overlap_seconds: float = 0.0,
                   overlap_tail_text: str = ""):
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
    """
    import core

    segments = core.transcribe_for_timing(
        chunk_path, model_size=whisper_size, language=source_language, use_gpu=use_gpu,
        initial_prompt=context_prompt)
    segments = dedup_overlap(segments, overlap_seconds, overlap_tail_text)
    offset = chunk_index * segment_seconds - overlap_seconds
    cues = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        try:
            translated = engine.translate_batch([text], {})[0]
        except Exception as exc:
            from translate_engines import redact_secrets
            translated = f"[translation failed: {redact_secrets(str(exc))}]"
        cues.append({
            "start": offset + seg["start"], "end": offset + seg["end"],
            "text": text, "translated": translated,
        })
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


def run_live_job(job_id: str, url: str, out_dir: str, segment_seconds: int,
                  source_language: str, whisper_size: str, engine, use_gpu: bool = False,
                  poll_interval: float = 2.0, cookies_browser: str = None, cookies_file: str = None,
                  overlap_seconds: float = DEFAULT_OVERLAP_SECONDS, max_seconds: float = None,
                  stream_url_check=None, proxy: str = None):
    """
    The background-thread target (see background_jobs.start_job). Runs
    until request_cancel(job_id) is set or the stream itself ends, then
    stops capture and returns -- background_jobs marks the job "done"
    once this returns. Cues accumulate in the job's result
    (background_jobs.get_status(job_id)["result"]), replaced with a
    fresh full list after every new chunk rather than appended one cue
    at a time, so a caller reading it mid-update never sees a partial
    write.

    Stale-chunk guard: this call's own generation (bump_generation(),
    captured once at the top) is re-checked before a chunk's result is
    applied. If the Stop button bumped the generation while a chunk's
    transcribe/translate call was still running, that chunk's result is
    discarded -- it belongs to a session that already moved on -- instead
    of landing after the fact.

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

    A fetch failure, or ffmpeg ending with an error, ends the job with
    a LiveCaptureError carrying a fixed message (no URL); a stream that
    simply ends finishes it normally.
    """
    overlap_seconds = max(0.0, min(float(overlap_seconds or 0), segment_seconds / 2))
    my_generation = bump_generation(job_id)
    started = time.monotonic()

    background_jobs.update_progress(job_id, 0.0, "Resolving stream URL...")
    source_url = resolve_stream_url(url, cookies_browser=cookies_browser, cookies_file=cookies_file,
                                    proxy=proxy)
    if stream_url_check is not None:
        stream_url_check(source_url)

    background_jobs.update_progress(job_id, 0.0, "Starting capture...")
    proc = start_segment_capture(source_url, out_dir, segment_seconds, proxy=proxy)

    all_cues = []
    last_completed = -1
    context_prompt = ""
    # read_wav_tail() of the last processed chunk, plus "index" and
    # "text" (what that chunk emitted for its own tail) once processed.
    prev_tail = None
    try:
        while True:
            if (background_jobs.is_cancel_requested(job_id)
                    or current_generation(job_id) != my_generation):
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

            for idx, path in list_completed_chunks(out_dir, last_completed):
                if (current_generation(job_id) != my_generation
                        or (max_seconds is not None and time.monotonic() - started >= max_seconds)):
                    # Stopped mid-batch -- don't burn through the rest of
                    # this batch's already-queued chunks either.
                    break
                background_jobs.update_progress(job_id, 0.0, f"Processing chunk {idx}...")
                padded_path = None
                try:
                    # Read before this chunk's file is deleted below --
                    # it's the audio the NEXT chunk gets padded with.
                    tail = read_wav_tail(path, overlap_seconds) if overlap_seconds else None
                    chunk_input, pad_seconds, tail_text = path, 0.0, ""
                    if prev_tail and prev_tail["index"] == idx - 1:
                        padded_path = os.path.join(out_dir, f"padded_{idx:05d}.wav")
                        pad_seconds = write_padded_chunk(prev_tail, path, padded_path)
                        if pad_seconds:
                            chunk_input, tail_text = padded_path, prev_tail["text"]
                    new_cues = process_chunk(
                        chunk_input, idx, segment_seconds, source_language, whisper_size,
                        engine, use_gpu=use_gpu, context_prompt=context_prompt,
                        overlap_seconds=pad_seconds, overlap_tail_text=tail_text)
                    if current_generation(job_id) != my_generation:
                        # The session moved on while this one chunk's own
                        # transcribe/translate call was in flight -- its
                        # result is stale, so it's dropped, not applied.
                        continue
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
                finally:
                    last_completed = idx
                    for leftover in (path, padded_path):
                        if not leftover:
                            continue
                        try:
                            os.remove(leftover)
                        except OSError:
                            pass
            time.sleep(poll_interval)
    finally:
        stop_capture(proc)
        background_jobs.update_progress(job_id, 1.0, "Stopped.")
