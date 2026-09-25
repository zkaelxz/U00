"""
live_translate.py -- near-live translation of an ongoing live stream.

Not the same problem as the rest of this app: everything else works on
a file that already exists in full, start to finish. Here there's no end
yet -- audio keeps arriving, so the design is "process what's arrived so
far, in bounded chunks, and keep going" rather than "process the whole
file once."

Pipeline, per chunk:
  1. ffmpeg reads the resolved stream URL and segments it into fixed-
     length audio chunks (its own `segment` muxer) -- this is what makes
     the "live" part possible at all: ffmpeg can read an ongoing HLS/DASH
     stream the same way it reads a file, and segmenting it as it arrives
     means each chunk is ready to transcribe long before the stream
     itself ends.
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
  - Each chunk's own transcribed tail IS now carried into the next
    chunk's Whisper call as initial_prompt (see process_chunk), which
    directly helps a sentence split across a chunk boundary -- but this
    is still each chunk's OWN audio transcribed independently, not a
    normal single-pass aligned transcription: the previous chunk's
    actual audio is never re-heard, only a text hint of what was said,
    and a name that never appeared in a prior chunk gets no priming
    benefit at all (unlike the whole-file initial_prompt name list used
    elsewhere in this app, built from the drama's known character names
    up front). Proper-noun accuracy is still a bit worse than a normal
    aligned transcription of the same content.
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

import background_jobs


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


def resolve_stream_url(url: str) -> str:
    """
    Resolves a page URL (YouTube live, or anything yt-dlp supports) to a
    direct, ffmpeg-playable media URL WITHOUT downloading anything -- the
    piece that lets ffmpeg read an ongoing live broadcast the same way it
    reads a file.
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise ImportError("Live capture needs yt-dlp: pip install yt-dlp") from exc

    def _try(fmt, player_client=None):
        opts = {"format": fmt, "quiet": True, "no_warnings": True,
                # Since late 2025, YouTube downloads need an external JS
                # runtime through yt-dlp's EJS system, or formats silently
                # go missing -- likely the real cause behind at least some
                # of the "no video formats found" failures this already
                # works around below. Deno is yt-dlp's own default; listing
                # the others too means it still works if only one of them
                # happens to be installed.
                "js_runtimes": {"deno": {}, "node": {}, "bun": {}, "quickjs": {}}}
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
            # Only worth ever retrying for this exact failure shape -- any
            # other error (private video, bad URL, network) will fail the
            # same way on every attempt, so don't burn time looping on it.
            if "no video formats found" not in str(exc).lower():
                raise LiveCaptureError(
                    f"Couldn't resolve that stream URL.\n\n{type(exc).__name__}: {exc}\n\n"
                    "Common causes: the link is wrong/private/region-locked, the stream "
                    "hasn't started yet, or it's already ended."
                ) from exc
    else:
        raise LiveCaptureError(
            f"Couldn't resolve that stream URL.\n\n{type(last_exc).__name__}: {last_exc}\n\n"
            "\"No video formats found\" even on a confirmed-live stream with current yt-dlp "
            "usually means either YouTube's proof-of-origin token requirement -- tried the "
            "standard player-client workarounds "
            f"({', '.join(_YOUTUBE_CLIENT_FALLBACKS)}) without success -- or that yt-dlp has "
            "no JavaScript runtime to use (Deno, Node, Bun or QuickJS; check the Diagnostics "
            "tab). If you don't have one, install Deno (https://deno.land) and run "
            "`pip install -U yt-dlp`. Otherwise this is a known, actively-shifting "
            "YouTube/yt-dlp issue, not specific to this app -- check "
            "https://github.com/yt-dlp/yt-dlp/issues for the current recommended workaround "
            "(often a specific --extractor-args player_client value, or supplying browser "
            "cookies via --cookies-from-browser), since which client currently works changes "
            "as both sides keep adjusting."
        ) from last_exc

    stream_url = info.get("url")
    if not stream_url:
        raise LiveCaptureError(
            "yt-dlp resolved the page but didn't return a direct stream URL -- "
            "this can happen for a stream that hasn't gone live yet."
        )
    return stream_url


def start_segment_capture(source_url: str, out_dir: str, segment_seconds: int = 20,
                           sample_rate: int = 16000) -> subprocess.Popen:
    """
    Launches ffmpeg to read `source_url` continuously and write it out as
    numbered mono WAV chunks (chunk_00000.wav, chunk_00001.wav, ...), each
    `segment_seconds` long. Returns the running Popen handle -- the caller
    is responsible for stopping it (stop_capture()).

    `source_url` can be a real live stream URL (from resolve_stream_url)
    or, for testing, a local file -- ffmpeg treats both the same way once
    it's reading from them, which is what makes this testable without a
    real broadcast.
    """
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "chunk_%05d.wav")
    cmd = ["ffmpeg", "-y", "-i", source_url, "-vn", "-ac", "1", "-ar", str(sample_rate),
           "-f", "segment", "-segment_time", str(segment_seconds), "-reset_timestamps", "1",
           pattern]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_capture(proc: subprocess.Popen, timeout: float = 5.0):
    """Terminates ffmpeg cleanly (SIGTERM lets it flush the segment it's
    currently writing) rather than killing it outright."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


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


# Whisper's initial_prompt has a real, hard limit (roughly the last 224
# tokens) -- capping what's carried forward here to a generous but bounded
# tail keeps every chunk's prompt cheap to build, not because a longer
# value would break anything, since Whisper itself only ever uses the end
# of whatever's passed.
_CONTEXT_CARRY_CHARS = 200


def process_chunk(chunk_path: str, chunk_index: int, segment_seconds: float,
                   source_language: str, whisper_size: str, engine, use_gpu: bool = False,
                   context_prompt: str = ""):
    """
    Transcribes one chunk and translates each resulting line, shifting
    timestamps by this chunk's position in the stream so cues from
    different chunks share one continuous timeline instead of each
    chunk restarting at zero.

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
    offset = chunk_index * segment_seconds
    cues = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        try:
            translated = engine.translate_batch([text], {})[0]
        except Exception as exc:
            translated = f"[translation failed: {exc}]"
        cues.append({
            "start": offset + seg["start"], "end": offset + seg["end"],
            "text": text, "translated": translated,
        })
    return cues


def run_live_job(job_id: str, url: str, out_dir: str, segment_seconds: int,
                  source_language: str, whisper_size: str, engine, use_gpu: bool = False,
                  poll_interval: float = 2.0):
    """
    The background-thread target (see background_jobs.start_job). Runs
    until request_cancel(job_id) is set or the stream itself ends, then
    stops capture and returns -- background_jobs marks the job "done"
    once this returns. Cues accumulate in the job's result
    (background_jobs.get_status(job_id)["result"]), replaced with a
    fresh full list after every new chunk rather than appended one cue
    at a time, so a caller reading it mid-update never sees a partial
    write.
    """
    background_jobs.update_progress(job_id, 0.0, "Resolving stream URL...")
    source_url = resolve_stream_url(url)

    background_jobs.update_progress(job_id, 0.0, "Starting capture...")
    proc = start_segment_capture(source_url, out_dir, segment_seconds)

    all_cues = []
    last_completed = -1
    context_prompt = ""
    try:
        while True:
            if background_jobs.is_cancel_requested(job_id):
                break
            if proc.poll() is not None:
                background_jobs.update_progress(
                    job_id, 0.0, "Capture stopped (stream likely ended).")
                break

            for idx, path in list_completed_chunks(out_dir, last_completed):
                background_jobs.update_progress(job_id, 0.0, f"Processing chunk {idx}...")
                try:
                    new_cues = process_chunk(
                        path, idx, segment_seconds, source_language, whisper_size,
                        engine, use_gpu=use_gpu, context_prompt=context_prompt)
                    all_cues.extend(new_cues)
                    if new_cues:
                        # Carries this chunk's own tail into the NEXT chunk's
                        # transcription -- see process_chunk's docstring.
                        # Independent of the last chunk's translation, so a
                        # translation failure never breaks this.
                        joined = " ".join(c["text"] for c in new_cues)
                        context_prompt = joined[-_CONTEXT_CARRY_CHARS:]
                    background_jobs.set_result(job_id, list(all_cues))
                finally:
                    last_completed = idx
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            time.sleep(poll_interval)
    finally:
        stop_capture(proc)
        background_jobs.update_progress(job_id, 1.0, "Stopped.")
