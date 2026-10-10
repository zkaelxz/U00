"""
core.py -- shared pipeline logic with NO UI dependency, so it can be
imported by both the API services and cli.py (headless batch mode)
without pulling in a UI framework.
"""

import json
import os
import re
import difflib
import tempfile
from dataclasses import dataclass, field, replace

from segment_splitting import _word_dicts, tighten_to_words, words_for_text


# The source languages the app handles, and the full names LLM and aligner
# prompts use for them. Callers fall back to "Chinese" for anything else.
SOURCE_LANGUAGES = ("zh", "ja", "ko")
LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}
# What one line's spoken language (Line.lang) may be: a title can mix
# speakers of several languages, English among them.
LINE_LANGUAGES = SOURCE_LANGUAGES + ("en",)


# The per-line columns db.save_lines writes. `idx` is the line's current
# position (display order) -- it changes on every merge/split; `id` is the
# permanent identity notes, emotions and background jobs attach to.
LINE_FIELDS = ("idx", "start", "end", "zh", "en", "speaker", "dub_filename", "flag", "flag_note",
               "speaker_manual", "sfx", "lang")


@dataclass
class Line:
    idx: int
    start: float
    end: float
    zh: str
    en: str = ""
    speaker: str = None
    dub_filename: str = None
    flag: str = None       # a key from translate_engines.FLAG_REASONS / SYSTEM_FLAG_REASONS, or None
    flag_note: str = ""    # brief reason from flag_uncertain_lines, e.g. "ambiguous 'her'"
    # True once someone set this line's speaker by hand -- re-running
    # speaker detection (diarize.merge_speakers) leaves it alone unless
    # told to overwrite corrections.
    speaker_manual: bool = False
    # A non-verbal/SFX cue ("door slams") rather than dialogue --
    # exported bracketed and styled apart from speech (see sfx_cue_text).
    sfx: bool = False
    # This line's spoken language, a LINE_LANGUAGES code; None means the
    # title's source_language, so titles saved before this field existed
    # behave exactly as before.
    lang: str = None
    # Permanent row id (lines.id). None for a line not saved yet.
    id: int = field(default=None, compare=False)
    # encode_line_words' stored form, read through line_words. None means "not
    # loaded": db.save_lines then leaves the column alone (and clears it when the
    # text changes), so a caller that never loads it can't wipe or keep stale words.
    word_timings: str = field(default=None, compare=False, repr=False)
    # Field values as last loaded from / saved to the database. db.save_lines
    # only writes a field whose value differs from this, so two writers
    # (a background job and the page) can't clobber each other's fields.
    # None means "unknown" -- every field is written.
    orig: dict = field(default=None, compare=False, repr=False)
    # Ids of lines merged into this one since the last save -- db.save_lines
    # re-points their notes/emotions here before deleting their rows.
    merged_ids: list = field(default_factory=list, compare=False, repr=False)


def atomic_write(path: str, data, binary: bool = False) -> None:
    """Writes `data` to a temp file in path's folder, then os.replace()s it
    over `path`, so a crash mid-write never leaves a truncated file."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def normalize_line_lang(value):
    """A Line.lang value from outside (API body, CLI): None or "" -> None,
    a LINE_LANGUAGES code in any case -> that code lower-cased. Anything
    else raises InvalidInputError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    code = value.strip().lower() if isinstance(value, str) else None
    if code not in LINE_LANGUAGES:
        from lib.errors import InvalidInputError
        raise InvalidInputError(f"lang must be one of {', '.join(LINE_LANGUAGES)} or empty.",
                                details={"allowed": list(LINE_LANGUAGES)})
    return code


def _stored_line_lang(value):
    # A row from an imported backup (or a hand-edited database) may hold a
    # code this version doesn't know; reading it as the title's default is
    # safer than refusing to load the lines.
    code = value.strip().lower() if isinstance(value, str) else None
    return code if code in LINE_LANGUAGES else None


def line_from_row(row) -> "Line":
    """The one shared db row (db.load_lines dict) -> Line conversion. Carries
    id and every field through, so nothing (flags, speaker, dub clip) is
    silently dropped by a caller that forgets one, and records `orig` so a
    later save writes only what actually changed."""
    ln = Line(idx=row["idx"], start=row["start"], end=row["end"], zh=row.get("zh") or "",
              en=row.get("en") or "", speaker=row.get("speaker"),
              dub_filename=row.get("dub_filename"), flag=row.get("flag"),
              flag_note=row.get("flag_note") or "", speaker_manual=bool(row.get("speaker_manual")),
              sfx=bool(row.get("sfx")), lang=_stored_line_lang(row.get("lang")), id=row.get("id"),
              word_timings=row.get("word_timings"))
    ln.orig = {f: getattr(ln, f) for f in LINE_FIELDS}
    if "word_timings" in row:
        ln.orig["word_timings"] = ln.word_timings
    return ln


def lines_from_rows(rows) -> list:
    return [line_from_row(r) for r in rows]


# Fields a snapshot/version records since the undo fix; older ones lack them.
SAVED_MARK_FIELDS = ("flag", "flag_note", "sfx", "lang")


def adopt_ids(restored, current, recorded=()) -> list:
    """For restoring a saved snapshot/translation version over the current
    lines: gives each restored line the permanent id (and `orig`) of the
    current line it replaces -- by id when the snapshot recorded one, else
    by position (snapshots from before line ids existed have no ids) -- so notes and
    emotions stay attached instead of being deleted with the old rows.
    Fields a snapshot doesn't store (dub_filename in a version; flag,
    flag_note, sfx and lang in one saved before they were recorded) are carried
    over from the matched line rather than wiped. `recorded` names the
    SAVED_MARK_FIELDS the snapshot did store: those keep the snapshot's own
    value, since after a merge the matched line may hold another line's flag.

    Positional fallback only applies to a line whose snapshot never
    recorded an id at all (older snapshot). A line whose id *was* recorded but
    no longer resolves -- it was merged away since the snapshot was taken
    -- must not fall back to matching by position: idx numbering shifts
    after a merge, so that would silently reattach the snapshot's notes,
    emotions and flags to whatever unrelated line now sits at that
    position. Such a line gets a fresh id instead (below)."""
    by_id = {ln.id: ln for ln in current if getattr(ln, "id", None) is not None}
    by_idx = {ln.idx: ln for ln in current}
    used = set()
    for ln in restored:
        id_was_recorded = ln.id is not None
        match = by_id.get(ln.id) if id_was_recorded else None
        if match is not None and match.id in used:
            match = None
        if match is None and not id_was_recorded:
            match = by_idx.get(ln.idx)
            if match is not None and match.id in used:
                match = None
        if match is None:
            ln.id = None
            continue
        used.add(match.id)
        ln.id, ln.orig = match.id, match.orig
        for f in ("flag", "flag_note", "dub_filename", "lang"):
            if f not in recorded and getattr(ln, f) in (None, ""):
                setattr(ln, f, getattr(match, f))
        if "sfx" not in recorded:
            ln.sfx = ln.sfx or match.sfx
        # Older snapshots/versions didn't record
        # speaker_manual -- restoring the same speaker the line has now
        # keeps its hand-corrected mark instead of silently dropping it.
        if ln.speaker == match.speaker:
            ln.speaker_manual = ln.speaker_manual or match.speaker_manual
    return restored


def lines_from_saved(rows) -> list:
    """A saved line-history snapshot's or translation version's line dicts
    (db.get_line_history_snapshot / get_translation_version) -> Lines. A
    line's word timings come back only when they were stored for its text."""
    return [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r.get("zh") or "",
                 en=r.get("en") or "", speaker=r.get("speaker"),
                 dub_filename=r.get("dub_filename"), flag=r.get("flag") or None,
                 flag_note=r.get("flag_note") or "", sfx=bool(r.get("sfx")),
                 speaker_manual=bool(r.get("speaker_manual")),
                 lang=_stored_line_lang(r.get("lang")), id=r.get("id"),
                 word_timings=words_for_text(r.get("word_timings"), r.get("zh") or ""))
            for r in rows]


def saved_matches_lines(rows, current) -> bool:
    """True when a saved snapshot/version was taken over exactly the current
    lines (same permanent ids) -- no merge, split or re-segmentation since."""
    saved_ids = [r.get("id") for r in rows]
    current_ids = [ln.id for ln in current]
    return (None not in saved_ids and None not in current_ids
            and len(saved_ids) == len(current_ids) and set(saved_ids) == set(current_ids))


def restore_saved_lines(rows, current, translation_only: bool = False) -> list:
    """The one restore path Workspace's Restore (a line-history snapshot)
    and Activate (a translation version) share, so the two can't drift.

    translation_only (Activate): when the version was saved over exactly
    the current lines, only each line's `en` changes -- speaker (and its
    hand-corrected mark), source text and timing stay as they are now,
    since activating a version picks a translation, not a rollback of the
    whole line. A version saved over a different line structure (merged,
    split, re-segmented since, or from before line ids existed) can only be
    restored whole, since its translations belong to its own lines."""
    if translation_only and saved_matches_lines(rows, current):
        en_by_id = {r["id"]: r.get("en") or "" for r in rows}
        return [replace(ln, en=en_by_id[ln.id]) for ln in current]
    recorded = {f for f in SAVED_MARK_FIELDS if rows and all(f in r for r in rows)}
    return adopt_ids(lines_from_saved(rows), current, recorded)


def fmt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def notes_suffix(line_idx: int, notes_by_idx: dict) -> str:
    """notes_by_idx: {line_idx: [{"term", "note"}, ...]}, from
    db.list_translation_notes() grouped by line -- see
    translation_guide.group_notes_by_line(). Renders as a bracketed
    aside appended to that line's subtitle text, e.g. "[Qijutang: lit.
    'Hall of Sitting Together', used as a joke]", so a reader watching
    the exported video (not just the in-app Reader) sees why a wordplay
    or reference reads the way it does, right where it happens.
    """
    if not notes_by_idx or line_idx not in notes_by_idx:
        return ""
    asides = "; ".join(f"{n['term']}: {n['note']}" for n in notes_by_idx[line_idx])
    return f"\n[{asides}]"


def sfx_cue_text(text: str, italic_tags: bool = True) -> str:
    """A non-verbal/SFX cue's subtitle text -- bracketed, so it
    reads as "[door slams]" rather than as something a character said.
    Already-bracketed text isn't double-bracketed. italic_tags wraps it in
    <i>...</i>, which SRT/VTT players (and an SRT burn-in) render; ASS
    passes False and styles the cue through its own "SFX" style instead."""
    text = (text or "").strip()
    if not text:
        return ""
    inner = text[1:-1].strip() if (text[0], text[-1]) in (("[", "]"), ("(", ")")) else text
    return f"<i>[{inner}]</i>" if italic_tags else f"[{inner}]"


def lines_to_srt(lines, field="en", notes_by_idx: dict = None) -> str:
    out = []
    for i, ln in enumerate(lines, start=1):
        text = getattr(ln, field)
        if getattr(ln, "sfx", False):
            text = sfx_cue_text(text)
        text += notes_suffix(ln.idx, notes_by_idx)
        out.append(f"{i}\n{fmt_ts(ln.start)} --> {fmt_ts(ln.end)}\n{text}\n")
    return "\n".join(out)


def lines_to_bilingual_srt(lines, notes_by_idx: dict = None) -> str:
    out = []
    for i, ln in enumerate(lines, start=1):
        en, zh = ln.en, ln.zh
        if getattr(ln, "sfx", False):
            en, zh = sfx_cue_text(en), sfx_cue_text(zh)
        text = f"{en}\n{zh}" if en else zh
        text += notes_suffix(ln.idx, notes_by_idx)
        out.append(f"{i}\n{fmt_ts(ln.start)} --> {fmt_ts(ln.end)}\n{text}\n")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Transcribe audio for timing (faster-whisper)
# ---------------------------------------------------------------------------

_whisper_model_cache = {}

# Speech-recognition models offered in the Workspace picker (faster-whisper
# names). large-v3-turbo is the default: in our benchmarks (docs/asr-experiments.md)
# it matched large-v3 on Japanese, trailed it by about half a point on Korean and
# by more on clean Chinese, and ran about twice as fast. medium was never ahead
# of it. The labels and the Transcribe stage's note say only what was measured.
WHISPER_MODELS = {
    "small": "small -- fastest, least accurate",
    "medium": "medium -- no faster or more accurate than turbo in our tests",
    "large-v3": "large-v3 -- slightly more accurate on Korean and clean Chinese, about 2x slower, ~3GB",
    "large-v3-turbo": "large-v3-turbo -- default; close to large-v3 in our tests, about 2x faster",
}
DEFAULT_WHISPER_SIZE = "large-v3-turbo"
# Bounds of the "Speech-splitting sensitivity" (min_silence_ms) setting, enforced
# for the saved value and for auto-tune candidates. Keep in sync with
# frontend/src/pages/workspace/sourceForm.ts. 100 ms is about three Silero VAD
# windows, so shorter values stop being distinguishable from the detector's noise.
MIN_SILENCE_MS_MIN = 100
MIN_SILENCE_MS_MAX = 3000
# Auto-tune's default candidate min_silence_duration_ms values -- spans the
# "Speech-splitting sensitivity" slider's real range meaningfully (300 is the
# app's default, services/transcribe_service._DEFAULT_TUNING; 3000 the
# slider's max) without an unbounded number of full re-transcriptions.
DEFAULT_AUTOTUNE_CANDIDATES_MS = [300, 800, 1500]


# Stops Whisper's repeated-phrase loops after music or silence;
# filter_hallucinated_segments is the backstop.
WHISPER_ANTI_LOOP_KWARGS = {"condition_on_previous_text": False}
# The per-title repeat guard. Off by default: a 3-token CJK sequence is often
# one common particle (的, の), so the ban rewrites or cuts real speech.
WHISPER_REPEAT_GUARD_KWARGS = {"no_repeat_ngram_size": 3, "repetition_penalty": 1.1}


# Seconds of silence inside a segment's word timings above which faster-whisper
# drops that segment as a likely hallucination. Off by default: it dropped real
# fast or quiet CJK lines.
DEFAULT_HALLUCINATION_SILENCE_SEC = 0.0


def release_gpu_models():
    """Call after a GPU stage (transcription, alignment, diarization)
    finishes: drops the cached Whisper / Qwen3-ASR / forced-aligner models
    and hands CUDA's cached memory back, so the next stage -- or a local
    translation model in Ollama, or TTS -- isn't fighting leftovers for
    the same VRAM. The next run of a stage reloads its model (seconds, from
    disk). Only touches modules that are already loaded, so it never
    imports torch or a model library just to clear it."""
    import gc
    import sys
    _whisper_model_cache.clear()
    _whisper_device_info.clear()
    for module_name, cache_name in (("asr_backend", "_asr_model_cache"),
                                    ("forced_align", "_aligner_model_cache")):
        module = sys.modules.get(module_name)
        if module is not None:
            getattr(module, cache_name).clear()
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass  # a broken CUDA install must not fail the stage that just succeeded


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


def is_gpu_error(exc: Exception) -> bool:
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


def is_network_error(exc: Exception) -> bool:
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
    hub = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface")
    hub_dir = os.path.join(hub, "hub")
    if not os.path.isdir(hub_dir):
        return False
    needle = f"faster-whisper-{model_size}".lower()
    try:
        return any(needle in d.lower() for d in os.listdir(hub_dir))
    except OSError:
        return False


_whisper_device_info = {}   # cache_key -> {"device", "compute_type", "gpu_error"}


def short_reason(exc, limit: int = 200) -> str:
    """One-line, secret-redacted description of an exception, for
    surfacing why the GPU couldn't be used."""
    from translate_engines import redact_secrets
    raw = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    text = " ".join(redact_secrets(raw).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def get_whisper_device_info(model_size: str, use_gpu: bool = False,
                            local_model_path: str = None) -> dict:
    """What load_whisper_model actually did for these arguments:
    {"device": "cuda"|"cpu", "compute_type", "gpu_error": <short reason or
    None>}. Empty dict if that model hasn't been loaded in this process."""
    key = f"{local_model_path or model_size}_{'gpu' if use_gpu else 'cpu'}"
    return dict(_whisper_device_info.get(key, {}))


def describe_whisper_device(info: dict) -> str:
    """Plain-words one-liner for get_whisper_device_info()'s dict."""
    if not info:
        return ""
    if info.get("gpu_error"):
        return f"GPU unavailable ({info['gpu_error']}); using CPU"
    if info.get("device") == "cuda":
        return f"Using GPU ({info.get('compute_type')})"
    return f"Using CPU ({info.get('compute_type')})"


def gpu_fallback_notice(task: str, reason: str) -> str:
    """The plain past-tense sentence every silent GPU->CPU fallback reports
    (job result, CLI line). `reason` is already one redacted line (short_reason)."""
    reason = " ".join(str(reason or "").split()).rstrip(".")
    why = f" ({reason})" if reason else ""
    return f"{task} ran on the CPU because the GPU couldn't be used{why}. This was slower than on the GPU."


def gpu_status() -> dict:
    """Whether ctranslate2 (faster-whisper) sees a CUDA device and whether
    torch.cuda is available. Never raises; each half is None when its
    library isn't installed, plus short redacted errors if a probe failed."""
    status = {"ctranslate2_cuda_devices": None, "torch_cuda_available": None, "errors": []}
    try:
        import ctranslate2
        status["ctranslate2_cuda_devices"] = int(ctranslate2.get_cuda_device_count())
    except ImportError:
        pass
    except Exception as exc:
        status["errors"].append("ctranslate2: " + short_reason(exc))
    try:
        import torch
        status["torch_cuda_available"] = bool(torch.cuda.is_available())
    except ImportError:
        pass
    except Exception as exc:
        status["errors"].append("torch: " + short_reason(exc))
    return status


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
    import memory_headroom as mh
    mh.before_load("whisper", target, use_gpu, cache_key in _whisper_model_cache)
    if cache_key in _whisper_model_cache:
        return _whisper_model_cache[cache_key]

    from faster_whisper import WhisperModel

    # An HF token isn't required for public models, but without one you get
    # anonymous rate limits and slower downloads -- and a warning saying so.
    _tok = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if _tok:
        os.environ.setdefault("HF_TOKEN", _tok)

    def _build(device, compute_type):
        return WhisperModel(target, device=device, compute_type=compute_type)

    device_info = {"device": "cpu", "compute_type": "int8", "gpu_error": None}
    try:
        if use_gpu:
            try:
                model = _build("cuda", "float16")
                device_info = {"device": "cuda", "compute_type": "float16", "gpu_error": None}
            except Exception as gpu_exc:
                if is_network_error(gpu_exc):
                    raise
                # No usable GPU: degrade, don't fail -- but remember why, so
                # callers can say the GPU was NOT used.
                device_info["gpu_error"] = short_reason(gpu_exc)
                model = _build("cpu", "int8")
        else:
            model = _build("cpu", "int8")
    except Exception as exc:
        if is_network_error(exc):
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
    _whisper_device_info[cache_key] = device_info
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
    from the novel can feed straight back into transcription. A glossary
    row's `aliases` (pipe-separated alt spellings/transliterations
    of term_original) are primed too, not just the canonical original --
    whichever spelling Whisper actually latches onto still helps.
    """
    names = []
    for t in (terms or []):
        if isinstance(t, dict):
            term = t.get("term_original")
            if term and str(term).strip():
                names.append(str(term).strip())
            for alias in re.split(r"[|,，、]", t.get("aliases") or ""):
                alias = alias.strip()
                if alias:
                    names.append(alias)
        else:
            term = t
            if term and str(term).strip():
                names.append(str(term).strip())
    if not names:
        return ""
    return "、".join(names[:max_terms]) + "。"


# Credit/outro lines Whisper emits on silence because its training subtitles
# ended with them. Matched against the whole segment text only, after
# _stock_key() strips case, spaces and punctuation.
_STOCK_PHRASE_RES = [re.compile(p) for p in (
    r"subtitlesbytheamaraorgcommunity",
    r"amaraorg.{0,8}",
    r"thanks?youforwatching",
    r"thanksforwatching",
    r"字幕由.{0,30}提供",
    r"请不吝点赞.*",
    r"ご視聴ありがとうございま(?:した|す)",
    r"ご清聴ありがとうございま(?:した|す)",
    r"mbc뉴스.{0,12}입니다",
    r"시청해주셔서감사합니다",
)]
# A stock phrase is only dropped with at least this much silence on each side
# (a missing neighbour counts as silence), so dialogue that happens to say
# "thanks for watching" between other lines survives.
STOCK_PHRASE_ISOLATION_SEC = 5.0


def _stock_key(text):
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def _is_isolated_stock_phrase(segments, i):
    key = _stock_key(segments[i]["text"])
    if not any(rx.fullmatch(key) for rx in _STOCK_PHRASE_RES):
        return False
    seg = segments[i]
    try:
        if i > 0 and seg["start"] - segments[i - 1]["end"] < STOCK_PHRASE_ISOLATION_SEC:
            return False
        if i + 1 < len(segments) and segments[i + 1]["start"] - seg["end"] < STOCK_PHRASE_ISOLATION_SEC:
            return False
    except KeyError:
        return False
    return True


def filter_hallucinated_segments(segments, min_repeat_count: int = 4):
    """
    Whisper is well known to loop a short phrase across MANY separate
    segments when fed silence or background music -- often a training-
    data artifact like a stock "please subscribe" phrase, or just a
    single word repeated -- and this is a DIFFERENT failure mode from
    what faster-whisper's own decoding thresholds (compression_ratio,
    log_prob, no_speech -- already applied internally before this ever
    sees the output) catch: each individual repeated segment can score
    perfectly confident on its own, since the model isn't uncertain about
    a phrase it's hallucinating with full conviction. It's the SEQUENCE
    of many identical segments in a row that's the actual tell, not any
    one segment's own score -- which is why this needs its own pass
    working on the already-decoded segment list, not another decoding
    parameter.

    Collapses a run of `min_repeat_count` or more CONSECUTIVE segments
    with identical (whitespace-normalized) text down to just the first
    occurrence, on the assumption real speech repeated verbatim that many
    times back-to-back AS SEPARATE SEGMENTS (not within one segment's own
    text -- quick real repetition like "no no no no" almost always comes
    back as one segment) is far less likely than a hallucination loop.

    Trade-off, stated plainly: the rare genuine case of someone actually
    repeating one short line several times, each landing as its own
    segment, gets collapsed to a single line here. min_repeat_count
    defaults conservatively high (4) to keep that risk low -- lower it if
    loops are still getting through on your content, raise it if you've
    actually hit the false-positive case.

    Also drops a segment whose WHOLE text is a stock credit/outro phrase
    ("Thanks for watching", "字幕由…提供", "ご視聴ありがとうございました") when it
    has at least STOCK_PHRASE_ISOLATION_SEC of silence on each side.
    """
    if not segments:
        return segments

    def _norm(text):
        return "".join((text or "").split())

    out = []
    i = 0
    n = len(segments)
    while i < n:
        norm = _norm(segments[i]["text"])
        j = i + 1
        while j < n and norm != "" and _norm(segments[j]["text"]) == norm:
            j += 1
        if (j - i) >= min_repeat_count:
            out.append(segments[i])
        else:
            out.extend(segments[i:j])
        i = j
    return [seg for k, seg in enumerate(out) if not _is_isolated_stock_phrase(out, k)]


def _accepts_transcribe_kwarg(model, name):
    """False for a faster-whisper release that predates `name`, which would
    otherwise fail the whole run with a TypeError."""
    import inspect
    try:
        params = inspect.signature(model.transcribe).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


def transcribe_for_timing(audio_path: str, model_size: str = "medium", language: str = "zh",
                           use_gpu: bool = False, local_model_path: str = None,
                           hf_token: str = None, initial_prompt: str = "",
                           beam_size: int = 5, min_silence_duration_ms: int = 2000,
                           vad_threshold: float = 0.5, filter_hallucination_repeats: int = 4,
                           on_gpu_fallback=None, progress_cb=None, fast_mode: bool = False,
                           hallucination_silence_sec: float = DEFAULT_HALLUCINATION_SILENCE_SEC,
                           repeat_guard: bool = False, sensitivity_preset: str = "normal"):
    """
    initial_prompt: proper nouns to prime recognition with -- see
    build_initial_prompt(). Costs nothing and is the single biggest free
    accuracy win for names.

    beam_size: higher searches more alternatives before committing.
    5 is faster-whisper's default; 8-10 is measurably better on difficult
    audio at a real speed cost.

    The voice-activity detector faster-whisper runs before transcription
    IS Silero VAD (faster_whisper/vad.py is adapted directly from
    snakers4/silero-vad) -- not a separate technology worth swapping in,
    confirmed by reading faster-whisper's own source. What's actually
    missing is exposing more of Silero's own tunable parameters, which
    only min_silence_duration_ms was until this was added:

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

    vad_threshold: Silero's own speech-probability cutoff (0.0-1.0,
    default 0.5) -- how confident it must be that a stretch of audio is
    speech before keeping it. Lower (0.3-0.4) catches quiet/mumbled
    dialogue or a distant speaker that the default cuts as silence, at
    the cost of more background noise/music misclassified as speech.
    Higher (0.6-0.7) is the fix for the opposite failure: a noisy or
    music-heavy source getting non-speech transcribed as phantom lines.
    Same genuine tradeoff shape as min_silence_duration_ms -- there's no
    value that's strictly better for every source.

    filter_hallucination_repeats: passed to filter_hallucinated_segments()
    (see its own docstring) -- collapses a run of this many or more
    consecutive segments with identical text, a well-known Whisper
    failure mode on silence/music that its own per-segment decoding
    thresholds don't catch. Set to 0 or None to disable entirely.

    hallucination_silence_sec: faster-whisper's hallucination_silence_threshold --
    a segment with a silent gap this long inside its word timings is
    skipped as a likely hallucination. 0 or None turns it off. Not passed
    when faster-whisper is too old to know it, and ignored by faster-whisper
    itself in fast_mode (its batched pipeline hard-codes it off).

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

    fast_mode: opt-in -- runs faster-whisper's BatchedInferencePipeline,
    which decodes several VAD chunks at once (roughly 4x faster on a GPU).
    Same settings, same output shape; uses more VRAM while it runs.
    """
    # Imported here: db.py's import check copies core.py alone into a temp folder.
    from sensitivity_preset import decode_kwargs
    model = load_whisper_model(model_size, use_gpu=use_gpu, local_model_path=local_model_path,
                                hf_token=hf_token)
    kwargs = {
        "language": language, "vad_filter": True, "beam_size": beam_size,
        "vad_parameters": {"min_silence_duration_ms": min_silence_duration_ms,
                            "threshold": vad_threshold},
        "word_timestamps": True,
        **decode_kwargs(sensitivity_preset, WHISPER_ANTI_LOOP_KWARGS, WHISPER_REPEAT_GUARD_KWARGS, repeat_guard),
    }
    if initial_prompt.strip():
        kwargs["initial_prompt"] = initial_prompt.strip()
    if hallucination_silence_sec and not fast_mode and _accepts_transcribe_kwarg(
            model, "hallucination_silence_threshold"):
        kwargs["hallucination_silence_threshold"] = float(hallucination_silence_sec)

    def _collect(segments, info):
        duration = getattr(info, "duration", None) or 0
        result = []
        for s in segments:
            text = s.text.strip()
            # A segment with no letter, digit or CJK character (a lone "[" from
            # a cut-off sound tag, "...", a dash) is not a subtitle.
            if any(ch.isalnum() for ch in text):
                start, end = tighten_to_words(s.start, s.end, getattr(s, "words", None))
                seg = {"start": start, "end": end, "text": text}
                words = _word_dicts(getattr(s, "words", None))
                if words:
                    seg["words"] = words
                result.append(seg)
            if progress_cb:
                progress_cb(min(s.end / duration, 1.0) if duration else 0.0)
        if filter_hallucination_repeats:
            result = filter_hallucinated_segments(result, filter_hallucination_repeats)
        return result

    def _run(m):
        if fast_mode:
            from faster_whisper import BatchedInferencePipeline
            return BatchedInferencePipeline(model=m).transcribe(audio_path, **kwargs)
        return m.transcribe(audio_path, **kwargs)

    try:
        segments, _info = _run(model)
        return _collect(segments, _info)
    except Exception as exc:
        # ctranslate2 defers CUDA init until this exact point -- a broken
        # or missing CUDA install (mismatched toolkit version, a missing
        # cublas64_12.dll, no CUDA-capable device) can ONLY be caught
        # here, never at model construction, no matter how that's wrapped.
        # See load_whisper_model's own GPU->CPU fallback, which protects
        # a different (earlier, rarer) failure point and cannot catch this.
        if use_gpu and is_gpu_error(exc):
            if on_gpu_fallback:
                on_gpu_fallback(exc)
            cpu_model = load_whisper_model(model_size, use_gpu=False,
                                            local_model_path=local_model_path, hf_token=hf_token)
            segments, _info = _run(cpu_model)
            return _collect(segments, _info)
        raise


GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_DEFAULT_MODEL = "whisper-large-v3-turbo"
# verbose_json segments for an hour of speech are a few MB; Groq's own upload
# limit keeps a single file far below what would fill this.
GROQ_RESPONSE_MAX_BYTES = 32 * 1024 * 1024
GROQ_ERROR_MAX_BYTES = 64 * 1024


class GroqTranscriptionError(RuntimeError):
    """Raised when Groq's cloud transcription API can't be reached or
    returns an error, so callers can show a useful message instead of a
    raw requests/HTTP exception."""


def transcribe_with_groq(audio_path: str, language: str, api_key: str,
                         model: str = GROQ_DEFAULT_MODEL, progress_cb=None):
    """
    An opt-in, paid cloud alternative to transcribe_for_timing's
    local faster-whisper path -- sends the whole file to Groq's hosted
    Whisper Large-v3-Turbo API (the same model family this app defaults
    to locally, at ~$0.04/hour of audio) and returns the identical
    [{"start", "end", "text"}, ...] segment shape, so the result flows
    into the exact same downstream pipeline (alignment, diarization
    hand-off) as a local Whisper result -- callers don't need to know
    which one produced it.

    One blocking HTTP call, not a stream -- progress_cb (if given) is
    only ever called once, with 1.0, right before returning; there's no
    partial-progress signal available during the request itself.
    """
    import os as _os
    import translate_engines
    from lib import http

    too_big = "Groq's reply was too large or too slow to read."
    try:
        with open(audio_path, "rb") as f:
            resp = http.post(
                GROQ_TRANSCRIBE_URL, timeout=600, max_bytes=GROQ_RESPONSE_MAX_BYTES,
                max_error_bytes=GROQ_ERROR_MAX_BYTES,
                guard=None, headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (_os.path.basename(audio_path), f)},
                data={"model": model, "language": language,
                      "response_format": "verbose_json", "timestamp_granularities[]": "segment"})
    except (http.ResponseTooLarge, http.ResponseTooSlow):
        raise GroqTranscriptionError(too_big) from None
    except http.FetchError as exc:
        raise GroqTranscriptionError(exc.message) from None
    if resp.status != 200:
        raise GroqTranscriptionError(translate_engines.redact_secrets(
            f"Groq API returned {resp.status}: "
            f"{resp.body.decode('utf-8', 'replace')[:300]}"))
    try:
        data = json.loads(resp.body)
    except ValueError:
        raise GroqTranscriptionError(too_big) from None
    if not isinstance(data, dict):
        raise GroqTranscriptionError(too_big)
    result = [{"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()}
              for seg in data.get("segments", []) if seg.get("text", "").strip()]
    if progress_cb:
        progress_cb(1.0)
    return result


# ---------------------------------------------------------------------------
# Align user transcript to Whisper timing
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


def novel_paragraph_ends(lines, source_text: str):
    """Set of line idx whose line ends a paragraph of `source_text` -- the
    novel text the lines were chunked from (chunk_novel_text only ever
    splits a paragraph into pieces that join back into it). None when the
    lines no longer match the source (edited, merged or re-split since),
    rather than a guess that would put breaks in the wrong places."""
    paragraphs = [re.sub(r"\s+", "", p) for p in re.split(r"\n+", source_text or "") if p.strip()]
    ends, buf, p = set(), "", 0
    for ln in lines:
        piece = re.sub(r"\s+", "", ln.zh or "")
        if not piece:
            continue
        if p >= len(paragraphs):
            return None
        buf += piece
        if buf == paragraphs[p]:
            ends.add(ln.idx)
            buf, p = "", p + 1
        elif not paragraphs[p].startswith(buf):
            return None
    return ends if p == len(paragraphs) and not buf else None


# Pulling the audio out of a multi-hour video; only stops a hung ffmpeg.
EXTRACT_AUDIO_TIMEOUT_SECONDS = 4 * 3600


def extract_audio_from_video(video_path: str, out_path: str):
    """Pulls the audio track out of a video file via ffmpeg, so the
    same timing/alignment pipeline can run on it as on audio-only files."""
    import subprocess
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
           "-ar", "16000", "-ac", "1", out_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=EXTRACT_AUDIO_TIMEOUT_SECONDS)
    return out_path


# A line-sized slice takes well under a second; this only stops a hung ffmpeg.
SLICE_TIMEOUT_SECONDS = 120.0


def extract_audio_slice(audio_path: str, start: float, end: float, out_path: str,
                        timeout: float = SLICE_TIMEOUT_SECONDS):
    """Cuts a [start, end) slice of audio via ffmpeg. Shared by
    forced_align.py (per-chunk forced alignment) and asr_backend.py
    (per-segment Qwen3-ASR re-transcription), both of which need to hand
    a short audio clip to a model that only accepts a few minutes at a
    time, rather than the whole file. `timeout` (seconds, default
    SLICE_TIMEOUT_SECONDS; None disables it) raises
    subprocess.TimeoutExpired if ffmpeg runs longer."""
    import subprocess
    cmd = ["ffmpeg", "-y", "-i", audio_path, "-ss", str(max(start, 0.0)), "-to", str(end),
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
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
            if not prev.flag and ln.flag:
                prev.flag, prev.flag_note = ln.flag, ln.flag_note
            if getattr(prev, "lang", None) != getattr(ln, "lang", None):
                prev.lang = None
            if getattr(ln, "id", None) is not None:
                prev.merged_ids = list(prev.merged_ids) + [ln.id] + list(ln.merged_ids)
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


def lines_from_char_times(user_lines, per_line_times, total_audio_end):
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
    return lines_from_char_times(user_lines, per_line_times, total_audio_end)


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
