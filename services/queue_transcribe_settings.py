"""
services/queue_transcribe_settings.py -- the Transcribe half of the Queue card's
"Saved settings for this step": the read-only rows, the fields one queued item
may change from the title's saved values, and the words for what it changed.
What a run accepts is transcribe_options_service.check_run_overrides'; this
only describes it to the page.
"""

from typing import Optional

import whisper_models
import sensitivity_preset
from services import transcribe_options_service, transcribe_service

_LANGUAGES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}


def _words(code) -> str:
    return str(code or "").replace("_", " ").capitalize()


def _on(flag) -> str:
    return "On" if flag else "Off"


def _row(label: str, value, engine: Optional[str] = None) -> dict:
    # `engine` is the engine's id: the page words it, since engine names live in the frontend's label table.
    return {"label": label, "value": str(value), "engine": engine}


def _transcribe_rows(drama: dict) -> list:
    config = transcribe_service.get_transcribe_config(drama["id"])
    backend = config["asr_backend_choice"]
    model = f"Whisper {config['whisper_size']}" if backend == "whisper" else _words(backend)
    rows = [
        _row("Speech model", model),
        _row("Source language", _LANGUAGES.get(drama.get("source_language") or "zh", "Chinese")),
        _row("Transcript", _words(config["transcript_mode"])),
        _row("Alignment", _words(config["alignment_method"])),
        _row("Voice detection", _words(config["sensitivity_preset"])),
        _row("Beam size", config["beam_size"]),
        _row("Separate vocals first", _on(config["separate_vocals_first"])),
        _row("Speaker detection", _on(config["run_start"]["run_diarize"])),
        _row("Groq (paid)", _on(config["use_groq"])),
    ]
    if (drama.get("source_language") or "zh") == "zh":
        rows.insert(2, _row("Chinese script", _words(drama.get("chinese_script") or "simplified")))
    return rows


_BACKEND_LABELS = {"whisper": "Whisper", "qwen3_asr": "Qwen3-ASR",
                   "qwen3_asr_vad": "Qwen3-ASR with voice detection",
                   "qwen3_asr_long": "Qwen3-ASR for long audio", "anime_whisper": "Anime-whisper (Japanese only)"}
_MODE_LABELS = {"whisper": "Whisper", "hardsub_ocr": "On-screen subtitles",
                "have_transcript": "Transcript you paste (can't run from the queue)"}
_ALIGNMENTS = {"whisper_diff": "Whisper diff", "qwen3_forced_align": "Qwen3 forced alignment"}
_FIELD_LABELS = {"asr_backend_choice": "Speech engine", "whisper_size": "Whisper model",
                 "source_language": "Source language", "transcript_mode": "Transcript",
                 "alignment_method": "Alignment", "sensitivity_preset": "Voice detection",
                 "beam_size": "Beam size", "separate_vocals_first": "Separate vocals first",
                 "use_groq": "Groq (paid)"}
_PAID_ONLY = "Needs paid-engine permission."
_NEEDS_EDIT = "Needs permission to edit lines."


def _options(labels: dict, current) -> list:
    out = [{"value": v, "label": label} for v, label in labels.items()]
    if current not in labels:
        out.append({"value": current, "label": _words(current)})
    return out


def _option_labels(key: str, drama: dict) -> dict:
    if key == "source_language":
        return _LANGUAGES
    if key == "asr_backend_choice":
        return _BACKEND_LABELS
    if key == "whisper_size":
        return {size: size for size in whisper_models.selectable_whisper_sizes("transcribe")}
    if key == "transcript_mode":
        return _MODE_LABELS
    if key == "alignment_method":
        return _ALIGNMENTS
    return {p: _words(p) for p in sensitivity_preset.PRESETS}


def _transcribe_fields(drama: dict, holds_paid: bool, holds_edit: bool = True) -> list:
    """The Transcribe settings one queued item may change, each with the title's saved value
    to start from. What the run accepts is transcribe_options_service.check_run_overrides'."""
    config = transcribe_service.get_transcribe_config(drama["id"])
    values = {"asr_backend_choice": config["asr_backend_choice"], "whisper_size": config["whisper_size"],
              "source_language": drama.get("source_language") or "zh",
              "transcript_mode": config["transcript_mode"], "alignment_method": config["alignment_method"],
              "sensitivity_preset": config["sensitivity_preset"], "beam_size": config["beam_size"],
              "separate_vocals_first": config["separate_vocals_first"], "use_groq": config["use_groq"]}
    fields = []
    for key in transcribe_options_service.QUEUE_SETTING_KEYS:
        field = {"key": key, "label": _FIELD_LABELS[key], "value": values[key], "options": [],
                 "min": None, "max": None, "locked": None}
        if key == "beam_size":
            field.update(kind="number", min=1, max=10)
        elif key in ("separate_vocals_first", "use_groq"):
            field["kind"] = "toggle"
            if key == "use_groq" and not holds_paid:
                field["locked"] = _PAID_ONLY
        else:
            field["kind"] = "select"
            field["options"] = _options(_option_labels(key, drama), values[key])
        if not holds_edit:
            field["locked"] = _NEEDS_EDIT
        fields.append(field)
    return fields


def _setting_text(key: str, value, drama: dict) -> str:
    if isinstance(value, bool):
        return _on(value)
    if key == "beam_size":
        return str(value)
    return _option_labels(key, drama).get(value) or _words(value)


def _changed_rows(item: dict, drama: Optional[dict]) -> list:
    if not item.get("settings") or not drama:
        return []
    return [_row(_FIELD_LABELS[k], _setting_text(k, v, drama)) for k, v in item["settings"].items()]


