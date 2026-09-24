"""
core.py -- shared pipeline logic with NO Streamlit dependency, so it can
be imported by both app.py (the GUI) and cli.py (headless batch mode)
without pulling in a UI framework.
"""

import re
import difflib
from dataclasses import dataclass


@dataclass
class Line:
    idx: int
    start: float
    end: float
    zh: str
    en: str = ""
    speaker: str = None
    dub_filename: str = None
    flag: str = None       # a key from translate_engines.FLAG_REASONS, or None
    flag_note: str = ""    # brief reason from flag_uncertain_lines, e.g. "ambiguous 'her'"


def fmt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def lines_to_srt(lines, field="en") -> str:
    out = []
    for i, ln in enumerate(lines, start=1):
        text = getattr(ln, field)
        out.append(f"{i}\n{fmt_ts(ln.start)} --> {fmt_ts(ln.end)}\n{text}\n")
    return "\n".join(out)


def lines_to_bilingual_srt(lines) -> str:
    out = []
    for i, ln in enumerate(lines, start=1):
        text = f"{ln.en}\n{ln.zh}" if ln.en else ln.zh
        out.append(f"{i}\n{fmt_ts(ln.start)} --> {fmt_ts(ln.end)}\n{text}\n")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Step 1: transcribe audio for timing (faster-whisper)
# ---------------------------------------------------------------------------

_whisper_model_cache = {}


class ModelDownloadError(RuntimeError):
    """Raised when a model can't be fetched, so callers can show a useful
    explanation instead of a Hugging Face stack trace."""


def diagnose_hostname(hostname: str = "huggingface.co") -> dict:
    """Checks whether a hostname resolves, and distinguishes the three
    outcomes that look alike from inside a stack trace:

      ok        - resolves normally
      blocked   - resolves to 0.0.0.0 / :: , which is what a DNS-level
                  blocker (Pi-hole, AdGuard, some corporate filters)
                  returns for a domain on a blocklist
      no_dns    - doesn't resolve at all

    'blocked' is worth calling out separately: the fix is whitelisting a
    domain on your own network, which is nothing like a broken connection.
    """
    import socket
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(hostname, None)}
    except Exception as exc:
        return {"status": "no_dns", "hostname": hostname, "addresses": [],
                "detail": f"{type(exc).__name__}: {exc}"}

    # Loopback is only a blackhole signal for a PUBLIC domain -- localhost
    # legitimately resolves to 127.0.0.1 and must not be flagged.
    loopback_names = {"localhost", "127.0.0.1", "::1"}
    blackholes = {"0.0.0.0", "::"}
    if hostname.lower() not in loopback_names:
        blackholes |= {"127.0.0.1", "::1"}

    if addrs and addrs.issubset(blackholes):
        return {"status": "blocked", "hostname": hostname, "addresses": sorted(addrs),
                "detail": (f"{hostname} resolves to {', '.join(sorted(addrs))}, which means a "
                           "DNS-level blocker on your network (Pi-hole, AdGuard, or similar) is "
                           "blocking it. Whitelist it there rather than changing anything here.")}
    return {"status": "ok", "hostname": hostname, "addresses": sorted(addrs), "detail": ""}


def _is_gpu_error(exc: Exception) -> bool:
    """CUDA/cuBLAS/cuDNN library-loading and device errors.

    Distinct from _is_network_error and from a genuine audio/data
    problem. Matters because ctranslate2 (which faster-whisper wraps)
    defers ALL CUDA initialization until the first actual inference
    call -- constructing a WhisperModel(device="cuda") never touches
    the GPU, it just stores config. So a broken or missing CUDA
    install (a missing cublas64_12.dll, a driver/toolkit version
    mismatch, no CUDA-capable device at all) can only ever be caught
    here, at the point transcription actually runs -- not at model
    construction time, no matter how that's wrapped.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = ("cublas", "cudnn", "cuda", "nvidia", "dll is not found",
               "cannot be loaded", "no cuda-capable device", "out of memory")
    return any(m in text for m in markers)


def _is_network_error(exc: Exception) -> bool:
    """Whisper models download from Hugging Face on first use. A failure
    there is almost always network (DNS, firewall, proxy, VPN) rather
    than anything wrong with the audio or the app, and deserves a
    completely different message."""
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = ("getaddrinfo", "connecterror", "localentrynotfound", "connection",
               "timed out", "timeout", "network", "temporary failure in name resolution",
               "max retries", "ssl", "proxy", "unreachable", "errno 11004",
               "client has been closed", "hf_hub", "huggingface", "name or service not known")
    return any(m in text for m in markers)


def is_whisper_model_cached(model_size: str) -> bool:
    """Whether a model is already downloaded, so the UI can warn about a
    large download before starting rather than failing partway."""
    import os as _os
    hub = _os.environ.get("HF_HOME") or _os.path.join(
        _os.path.expanduser("~"), ".cache", "huggingface")
    hub_dir = _os.path.join(hub, "hub")
    if not _os.path.isdir(hub_dir):
        return False
    needle = f"faster-whisper-{model_size}".lower()
    try:
        return any(needle in d.lower() for d in _os.listdir(hub_dir))
    except OSError:
        return False


def load_whisper_model(model_size: str, use_gpu: bool = False, local_model_path: str = None,
                        hf_token: str = None):
    """Loads (and on first use, downloads) a Whisper model.

    use_gpu: try CUDA with float16, falling back to CPU automatically if
    the GPU or the CUDA build of the runtime isn't available -- so
    enabling it on a machine without a GPU degrades rather than breaks.

    local_model_path: a directory containing an already-downloaded model.
    Lets the app work fully offline, or on a machine where the download
    is blocked, by fetching the model elsewhere and pointing at it.
    """
    target = local_model_path or model_size
    cache_key = f"{target}_{'gpu' if use_gpu else 'cpu'}"
    if cache_key in _whisper_model_cache:
        return _whisper_model_cache[cache_key]

    import os as _os
    from faster_whisper import WhisperModel

    # An HF token isn't required for public models, but without one you get
    # anonymous rate limits and slower downloads -- and a warning saying so.
    _tok = hf_token or _os.environ.get("HF_TOKEN") or _os.environ.get("HUGGINGFACE_TOKEN")
    if _tok:
        _os.environ.setdefault("HF_TOKEN", _tok)

    def _build(device, compute_type):
        return WhisperModel(target, device=device, compute_type=compute_type)

    try:
        if use_gpu:
            try:
                model = _build("cuda", "float16")
            except Exception as gpu_exc:
                if _is_network_error(gpu_exc):
                    raise
                model = _build("cpu", "int8")   # no GPU: degrade, don't fail
        else:
            model = _build("cpu", "int8")
    except Exception as exc:
        if _is_network_error(exc):
            diag = diagnose_hostname("huggingface.co")
            if diag["status"] == "blocked":
                raise ModelDownloadError(
                    f"Couldn't download the '{model_size}' model — huggingface.co is being "
                    f"blocked by a DNS blocker on your network.\n\n"
                    f"{diag['detail']}\n\n"
                    "Whitelist these (the cdn-lfs ones serve the actual model files, so "
                    "allowing only the first will fail mid-download):\n"
                    "  huggingface.co\n  cdn-lfs.huggingface.co\n  cdn-lfs-us-1.hf.co\n  hf.co\n\n"
                    "Then run: ipconfig /flushdns"
                ) from exc
            raise ModelDownloadError(
                f"Couldn't download the '{model_size}' speech-recognition model.\n\n"
                "This is a network problem, not a problem with your audio. The model is "
                "fetched from Hugging Face the first time you use it.\n\n"
                "Common causes on Windows:\n"
                "  - antivirus or firewall blocking Python's network access\n"
                "  - a VPN that's connected but not routing properly\n"
                "  - DNS not resolving huggingface.co\n\n"
                "Check with:  python -c \"import socket; print(socket.gethostbyname('huggingface.co'))\"\n\n"
                "If you can't get network access on this machine, download the model on "
                "another one and set a local model path in Settings."
            ) from exc
        raise

    _whisper_model_cache[cache_key] = model
    return model


def build_initial_prompt(terms, max_terms: int = 40) -> str:
    """
    Builds a hint string of proper nouns for Whisper's `initial_prompt`.

    Whisper conditions on this text, which makes a large difference for
    exactly the words it otherwise gets wrong: character names, sects,
    place names. Chinese is especially unforgiving here because a
    misheard name is usually still a valid word, so nothing looks wrong
    until you read the translation.

    `terms` accepts glossary rows or plain strings, so a glossary built
    from the novel can feed straight back into transcription.
    """
    names = []
    for t in (terms or []):
        term = t.get("term_original") if isinstance(t, dict) else t
        if term and str(term).strip():
            names.append(str(term).strip())
    if not names:
        return ""
    return "、".join(names[:max_terms]) + "。"


def transcribe_for_timing(audio_path: str, model_size: str = "medium", language: str = "zh",
                           use_gpu: bool = False, local_model_path: str = None,
                           hf_token: str = None, initial_prompt: str = "",
                           beam_size: int = 5, min_silence_duration_ms: int = 2000,
                           on_gpu_fallback=None, progress_cb=None):
    """
    initial_prompt: proper nouns to prime recognition with -- see
    build_initial_prompt(). Costs nothing and is the single biggest free
    accuracy win for names.

    beam_size: higher searches more alternatives before committing.
    5 is faster-whisper's default; 8-10 is measurably better on difficult
    audio at a real speed cost.

    min_silence_duration_ms: the voice-activity detector's default (2000ms)
    merges any two stretches of speech separated by LESS than 2 seconds of
    silence into one continuous segment. For content with back-to-back
    dialogue, internal-monologue narration, or quick exchanges -- pauses
    well under 2s between distinct lines -- this default routinely merges
    several real lines into one oversized segment, which then gets treated
    as a single subtitle line with only its first sentence's text. Lowering
    this (500-1000ms) splits those apart at real pauses instead. Too low
    and it starts splitting mid-sentence on natural speech pauses, so this
    is a genuine tradeoff, not a strictly-better default.

    on_gpu_fallback: optional callback invoked with the original exception
    if a requested GPU run fails at actual inference time and this
    transparently retries on CPU -- so the caller can tell the person
    their GPU didn't actually get used, since CPU is meaningfully slower
    and silently downgrading without saying so would be confusing.

    progress_cb: optional callback invoked with a 0.0-1.0 fraction as
    segments come in. faster-whisper's `transcribe()` returns a lazy
    generator -- it doesn't process the whole file up front -- so this can
    report real progress instead of a spinner that never moves, which is
    the difference between a stuck-looking app and a working one on a
    multi-hour file. Progress is estimated from how far into the audio the
    latest segment ends (`info.duration` is faster-whisper's own total
    length estimate); silently reports nothing if that's unavailable.
    """
    model = load_whisper_model(model_size, use_gpu=use_gpu, local_model_path=local_model_path,
                                hf_token=hf_token)
    kwargs = {
        "language": language, "vad_filter": True, "beam_size": beam_size,
        "vad_parameters": {"min_silence_duration_ms": min_silence_duration_ms},
    }
    if initial_prompt.strip():
        kwargs["initial_prompt"] = initial_prompt.strip()

    def _collect(segments, info):
        duration = getattr(info, "duration", None) or 0
        result = []
        for s in segments:
            result.append({"start": s.start, "end": s.end, "text": s.text.strip()})
            if progress_cb:
                progress_cb(min(s.end / duration, 1.0) if duration else 0.0)
        return result

    try:
        segments, _info = model.transcribe(audio_path, **kwargs)
        return _collect(segments, _info)
    except Exception as exc:
        # ctranslate2 defers CUDA init until this exact point -- a broken
        # or missing CUDA install (mismatched toolkit version, a missing
        # cublas64_12.dll, no CUDA-capable device) can ONLY be caught
        # here, never at model construction, no matter how that's wrapped.
        # See load_whisper_model's own GPU->CPU fallback, which protects
        # a different (earlier, rarer) failure point and cannot catch this.
        if use_gpu and _is_gpu_error(exc):
            if on_gpu_fallback:
                on_gpu_fallback(exc)
            cpu_model = load_whisper_model(model_size, use_gpu=False,
                                            local_model_path=local_model_path, hf_token=hf_token)
            segments, _info = cpu_model.transcribe(audio_path, **kwargs)
            return _collect(segments, _info)
        raise


# ---------------------------------------------------------------------------
# Step 2: align user transcript to Whisper timing
# ---------------------------------------------------------------------------

def chunk_novel_text(raw_text: str, max_chars: int = 200):
    """Splits novel prose into narration-sized chunks: paragraph-aware,
    falling back to sentence splits for long paragraphs. Used for the
    'novel narration' content mode where there's no source audio to
    align to -- these chunks become the Lines directly."""
    raw_text = raw_text.strip()
    paragraphs = [p.strip() for p in re.split(r"\n+", raw_text) if p.strip()]
    chunks = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            sentences = re.split(r"(?<=[。！？…～])", para)
            buf = ""
            for s in sentences:
                if len(buf) + len(s) > max_chars and buf:
                    chunks.append(buf)
                    buf = s
                else:
                    buf += s
            if buf:
                chunks.append(buf)
    return chunks


def extract_audio_from_video(video_path: str, out_path: str):
    """Pulls the audio track out of a video file via ffmpeg, so the
    same timing/alignment pipeline can run on it as on audio-only files."""
    import subprocess
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
           "-ar", "16000", "-ac", "1", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def extract_audio_slice(audio_path: str, start: float, end: float, out_path: str):
    """Cuts a [start, end) slice of audio via ffmpeg. Shared by
    forced_align.py (per-chunk forced alignment) and asr_backend.py
    (per-segment Qwen3-ASR re-transcription), both of which need to hand
    a short audio clip to a model that only accepts a few minutes at a
    time, rather than the whole file."""
    import subprocess
    cmd = ["ffmpeg", "-y", "-i", audio_path, "-ss", str(max(start, 0.0)), "-to", str(end),
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def merge_adjacent_short_lines(lines, min_duration: float = 1.2, max_gap: float = 0.5, max_chars: int = 80):
    """
    Combines consecutive short subtitle lines from the same speaker
    into a single natural subtitle, when they're close enough in time
    that splitting them was probably just an artifact of the source
    transcript's line breaks rather than a real pause. This is a real
    merge (produces a new, shorter list), unlike smart_segment_lines()
    in translate_engines.py which only flags pacing issues without
    changing anything.

    A pair merges when: same speaker (or both unlabeled), the gap
    between them is under max_gap seconds, and the combined line
    wouldn't exceed max_chars. Only lines under min_duration are
    considered candidates for merging -- a line that's already a
    comfortable length is left alone.
    """
    if not lines:
        return lines

    merged = [lines[0]]
    for ln in lines[1:]:
        prev = merged[-1]
        prev_duration = prev.end - prev.start
        gap = ln.start - prev.end
        same_speaker = (prev.speaker or None) == (ln.speaker or None)
        combined_zh_len = len(prev.zh) + len(ln.zh)
        combined_en_len = len(prev.en) + len(ln.en) + 1  # +1 for the joining space
        combined_duration = ln.end - prev.start

        # Both the already-short prev AND the merged result must stay short --
        # otherwise a short line followed by a naturally long one would keep
        # getting absorbed just because prev alone was under the threshold.
        if (prev_duration < min_duration and same_speaker and gap <= max_gap
                and combined_zh_len <= max_chars and combined_en_len <= max_chars
                and combined_duration < min_duration * 2.5):
            prev.zh = (prev.zh.rstrip() + ln.zh.strip())
            prev.en = (prev.en.rstrip() + " " + ln.en.strip()).strip()
            prev.end = ln.end
        else:
            merged.append(ln)

    for i, ln in enumerate(merged):
        ln.idx = i
    return merged


def split_user_transcript(raw_text: str):
    raw_text = raw_text.strip()
    manual_lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    if len(manual_lines) > 1:
        return manual_lines
    # CJK full-width punctuation covers zh/ja; Korean text often also
    # uses standard Latin punctuation, so that's included as a fallback.
    parts = re.split(r"(?<=[。！？…～\.\!\?])\s*", raw_text)
    return [p.strip() for p in parts if p.strip()]


def _lines_from_char_times(user_lines, per_line_times, total_audio_end):
    """Shared reconstruction step: given, for each line index, whichever
    character timestamps could be attributed to it, produce ordered,
    non-overlapping Line objects -- interpolating from neighboring known
    lines wherever a line matched nothing.

    Factored out of align_transcript_to_timing() so forced_align.py's
    Qwen3-ForcedAligner path can reuse the same interpolation/ordering
    logic against a different (non-diffed, directly-matched) source of
    per-line timestamps, rather than duplicating it.
    """
    raw_bounds = {}
    for li in range(len(user_lines)):
        t = per_line_times.get(li, [])
        raw_bounds[li] = (min(t), max(t)) if t else None
    known_idxs = [li for li, b in raw_bounds.items() if b is not None]

    lines_out = []
    for li in range(len(user_lines)):
        b = raw_bounds[li]
        if b is not None:
            start, end = b
        else:
            prev_known = max([k for k in known_idxs if k < li], default=None)
            next_known = min([k for k in known_idxs if k > li], default=None)
            prev_end = raw_bounds[prev_known][1] if prev_known is not None else 0.0
            next_start = raw_bounds[next_known][0] if next_known is not None else total_audio_end
            start, end = prev_end, next_start
        lines_out.append(Line(idx=li, start=start, end=max(end, start + 0.5), zh=user_lines[li]))

    for i in range(1, len(lines_out)):
        if lines_out[i].start < lines_out[i - 1].end:
            lines_out[i].start = lines_out[i - 1].end + 0.05
        if lines_out[i].end <= lines_out[i].start:
            lines_out[i].end = lines_out[i].start + 1.0
    return lines_out


def align_transcript_to_timing(user_lines, whisper_segments):
    w_chars, w_times = [], []
    for seg in whisper_segments:
        text = re.sub(r"\s+", "", seg["text"])
        n = max(len(text), 1)
        dur = max(seg["end"] - seg["start"], 0.01)
        for i, ch in enumerate(text):
            w_chars.append(ch)
            w_times.append(seg["start"] + dur * (i / n))
    w_stream = "".join(w_chars)

    u_chars, u_line_of_char = [], []
    for li, line in enumerate(user_lines):
        for ch in re.sub(r"\s+", "", line):
            u_chars.append(ch)
            u_line_of_char.append(li)
    u_stream = "".join(u_chars)

    sm = difflib.SequenceMatcher(a=w_stream, b=u_stream, autojunk=False)
    per_line_times = {li: [] for li in range(len(user_lines))}
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            li = u_line_of_char[block.b + k]
            per_line_times[li].append(w_times[block.a + k])

    total_audio_end = whisper_segments[-1]["end"] if whisper_segments else 0.0
    return _lines_from_char_times(user_lines, per_line_times, total_audio_end)


# ---------------------------------------------------------------------------
# Using the raw source novel as transcription context
# ---------------------------------------------------------------------------

def extract_novel_excerpt_for_prompt(novel_text: str, max_chars: int = 800) -> str:
    """
    Pulls a usable excerpt from the raw novel for Whisper's initial_prompt.

    Whisper's initial_prompt has a real, hard limit -- only roughly the
    last ~224 tokens actually influence decoding, and anything before that
    is silently wasted. So this deliberately does NOT hand over a whole
    novel; it takes a bounded excerpt from early in the text, which is
    normally where the drama's episode 1 audio corresponds to.

    Kept separate from build_initial_prompt() (which lists proper nouns)
    so a caller can combine both: names first as the highest-value part,
    then a slice of real prose for phrasing and rhythm, trimmed to fit.
    """
    text = re.sub(r"\s+", " ", (novel_text or "").strip())
    if not text:
        return ""
    return text[:max_chars]


def combine_initial_prompt(name_prompt: str, novel_excerpt: str, max_chars: int = 900) -> str:
    """
    Merges the name-list prompt with a novel excerpt, names first since
    they're the highest-value part and must not get truncated away.
    """
    parts = [p for p in (name_prompt.strip(), novel_excerpt.strip()) if p]
    combined = "".join(parts)
    return combined[:max_chars]


def load_novel_text_for_context(file_bytes: bytes, filename: str) -> str:
    """
    Extracts plain text from an uploaded novel file for use as
    transcription/alignment context. Supports .txt/.md directly and
    .epub via epub_io (falls back to a clear error rather than silently
    returning nothing if ebooklib isn't installed).
    """
    name = (filename or "").lower()
    if name.endswith(".epub"):
        import tempfile
        import os as _os
        try:
            import epub_io
        except ImportError as exc:
            raise ImportError(
                "Reading .epub needs an extra package:\n    pip install ebooklib"
            ) from exc
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            return epub_io.import_epub_text(tmp_path)
        finally:
            _os.unlink(tmp_path)
    return file_bytes.decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Coverage & timing diagnostics -- for after alignment, to find lines that
# probably need attention before spending money translating them
# ---------------------------------------------------------------------------

def diagnose_line_coverage(lines, long_duration_seconds: float = 12.0,
                            gap_seconds: float = 3.0, chars_per_second: float = 4.5):
    """
    Scans aligned lines for the two patterns that most often mean real
    dialogue got missed or mangled, without needing to listen to the
    whole file to find them:

      - long_lines: a single line spanning an unusually long duration
        relative to its text length. Almost always means the voice
        activity detector merged multiple real lines of dialogue into
        one segment (see min_silence_duration_ms), so only the first
        sentence ended up as the line's text and everything spoken
        after it in that span has no subtitle at all.

      - large_gaps: a silent stretch between two consecutive lines
        longer than expected. Sometimes real silence; sometimes quiet
        dialogue (internal monologue, whispers) that the VAD didn't
        detect as speech at all.

    chars_per_second: a rough speaking-rate baseline (~4.5 chars/sec is
    reasonable for spoken Mandarin) used only to flag SEVERE outliers,
    not to judge normal pacing variation.

    Returns {"long_lines": [...], "large_gaps": [...], "blank_zh": [...],
    "blank_en": [...]} -- each entry has enough info to jump straight to
    the line in the review table.
    """
    long_lines, large_gaps, blank_zh, blank_en = [], [], [], []

    for ln in lines:
        duration = ln.end - ln.start
        char_count = len(ln.zh.strip())
        if not ln.zh.strip():
            blank_zh.append({"idx": ln.idx, "start": ln.start, "end": ln.end})
            continue
        if ln.zh.strip() and not ln.en.strip():
            blank_en.append({"idx": ln.idx, "zh": ln.zh, "start": ln.start})

        expected_duration = char_count / chars_per_second
        if duration > long_duration_seconds and duration > expected_duration * 2.5:
            long_lines.append({
                "idx": ln.idx, "start": ln.start, "end": ln.end, "duration": duration,
                "zh": ln.zh, "char_count": char_count,
                "note": (f"{duration:.1f}s for {char_count} character(s) -- "
                        f"plausibly several merged lines, not one"),
            })

    sorted_lines = sorted(lines, key=lambda l: l.start)
    for prev, cur in zip(sorted_lines, sorted_lines[1:]):
        gap = cur.start - prev.end
        if gap > gap_seconds:
            large_gaps.append({
                "after_idx": prev.idx, "before_idx": cur.idx,
                "gap_start": prev.end, "gap_end": cur.start, "gap_seconds": gap,
            })

    long_lines.sort(key=lambda x: -x["duration"])
    large_gaps.sort(key=lambda x: -x["gap_seconds"])
    return {"long_lines": long_lines, "large_gaps": large_gaps,
            "blank_zh": blank_zh, "blank_en": blank_en}
