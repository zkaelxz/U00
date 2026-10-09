"""The settings a job run used, shown in the Jobs page Details panel.

Job args are never exposed (they hold paths, keys and URLs). A job instead
records an allow-listed view of its settings here when it starts;
jobs_service.project_result_json merges it into the stored result as
"run_settings" when the job's record is written, and project_result
re-sanitises it on every read, so an older or hand-edited row can't leak a
key this module doesn't allow.

Allow-list only. Each key has one kind, and a value of the wrong kind is
dropped rather than coerced. Strings are limited to short identifier-like
tokens (model names, engine ids, codes) that survive scrub_text unchanged;
anything that looks like a path, a URL or a credential is dropped, never
masked. Prompts and other free text have no key here.
"""
import re
import threading

_NAME = "name"
_INT = "int"
_NUM = "num"
_BOOL = "bool"

ALLOWED_KEYS = {
    # Engines and models
    "engine": _NAME, "model": _NAME, "asr_backend": _NAME, "alignment_method": _NAME,
    "whisper_size": _NAME, "separation_backend": _NAME, "sensitivity_preset": _NAME,
    "transcript_mode": _NAME, "style_preset": _NAME, "locale": _NAME,
    "source_language": _NAME, "chinese_script": _NAME,
    # Numbers
    "beam_size": _INT, "batch_size": _INT, "context_window": _INT, "context_window_ahead": _INT,
    "min_silence_ms": _INT, "qwen_batch_size": _INT, "expected_speakers": _INT,
    "min_speakers": _INT, "max_speakers": _INT, "segment_seconds": _NUM, "overlap_seconds": _NUM,
    "max_minutes": _NUM, "vad_threshold": _NUM,
    "hallucination_silence_sec": _NUM, "min_pause_sec": _NUM,
    # On/off toggles
    "use_gpu": _BOOL, "diarize": _BOOL, "use_groq": _BOOL, "separate_vocals_first": _BOOL,
    "realign_long_segments": _BOOL, "whisper_fast_mode": _BOOL, "whisper_repeat_guard": _BOOL,
    "split_by_sentences": _BOOL, "force_retranslate": _BOOL, "reflect": _BOOL, "bulk": _BOOL,
    "thinking": _BOOL, "own_lines_only": _BOOL, "glossary": _BOOL, "style_guide": _BOOL,
    "style_note": _BOOL, "pronoun_hint": _BOOL, "genre_notes": _BOOL, "overwrite_manual": _BOOL,
    "reply_without_thinking": _BOOL,
}

# Letters, digits and . _ : + - / @ only, starting alphanumeric: "large-v3",
# "qwen2.5:7b", "Org/Model-1.7B". No backslash, drive colon, space or "://".
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@/-]{0,63}$")
_MAX_ENTRIES = 256

_lock = threading.Lock()
_recorded: dict = {}


def _clean_name(value):
    if not isinstance(value, str) or not _NAME_RE.match(value):
        return None
    if ".." in value or "//" in value or ":/" in value or value.startswith("/"):
        return None
    # Imported lazily: jobs_service imports this module.
    from services.jobs_service import scrub_text
    return value if scrub_text(value) == value else None


def _clean(kind, value):
    if kind == _BOOL:
        return value if isinstance(value, bool) else None
    if kind == _INT:
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if kind == _NUM:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value if value == value and abs(value) != float("inf") else None
    return _clean_name(value)


def sanitise(raw) -> dict:
    """The allow-listed, scalar-only view of raw ({} for anything else)."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, kind in ALLOWED_KEYS.items():
        if key in raw:
            value = _clean(kind, raw[key])
            if value is not None:
                out[key] = value
    return out


def record(job_id: str, *sources: dict, **settings) -> None:
    """Remembers the sanitised settings of the run just started under
    job_id. Later sources and keyword settings override earlier ones, so a
    caller can pass a drama row and override the derived keys. Never raises:
    a job must not fail because its settings couldn't be recorded."""
    try:
        merged = {}
        for source in sources:
            if isinstance(source, dict):
                merged.update(source)
        merged.update(settings)
        clean = sanitise(merged)
    except Exception:
        return
    with _lock:
        _recorded.pop(job_id, None)
        if clean:
            _recorded[job_id] = clean
        while len(_recorded) > _MAX_ENTRIES:
            del _recorded[next(iter(_recorded))]


def get(job_id) -> dict:
    with _lock:
        return dict(_recorded.get(job_id) or {})
