"""The values behind Settings > Timing and splitting, as the services use them.

Root modules (segment_splitting, sentence_groups, asr_backend, ...) never read
settings: a service asks here and passes the numbers down as arguments, and the
module constants stay as the defaults for any caller that passes nothing. The CLI
reaches the same services, so it gets the same values. A changed value only
affects runs that start afterwards; nothing rewrites saved lines.

A Developer Mode row reads as its default while Developer Mode is off, so turning
it off puts the app back to its normal behaviour instead of leaving a hidden
override in force.
"""
import asr_backend
import sensitivity_preset
import sentence_groups
from lib import settings_schema
from segment_splitting import PieceFloors
from services import settings_service

KEYS = tuple(s.key for s in settings_schema.SETTINGS if s.section in settings_schema.TIMING_SECTIONS)


def values() -> dict:
    """{key: value} for every Timing and splitting row, as a run should use it."""
    # Imported here: the maintenance assistant imports settings_service, and a
    # transcription worker process imports this module without needing it.
    from services import maintenance_assistant_service
    developer = maintenance_assistant_service.developer_mode_enabled()
    out = {}
    for key in KEYS:
        row = settings_schema.BY_KEY[key]
        out[key] = (settings_schema.default_of(row) if row.dev_only and not developer
                    else settings_service.get(key))
    for lower, upper, _ in settings_schema.ORDERED_PAIRS:
        # set_settings refuses to save such a pair; this covers a row edited outside the app.
        if out[lower] >= out[upper]:
            for key in (lower, upper):
                out[key] = settings_schema.default_of(settings_schema.BY_KEY[key])
    return out


def value(key: str):
    return values()[key]


def defaults() -> dict:
    return {key: settings_schema.default_of(settings_schema.BY_KEY[key]) for key in KEYS}


def changed(vals: dict = None) -> dict:
    """The rows a run used that differ from the default, for the run's saved settings."""
    v = vals if vals is not None else values()
    base = defaults()
    return {key: v[key] for key in KEYS if v[key] != base[key]}


def asr_tuning(vals: dict = None) -> asr_backend.AsrTuning:
    """What a transcription run takes; `vals` is a values() dict (a worker process is
    handed it, since it must not read the database itself)."""
    v = vals if vals is not None else values()
    return asr_backend.AsrTuning(
        sentence_min_silence_ms=v["sentence_split_min_silence_ms"],
        max_seconds=v["split_max_seconds"],
        max_cjk_chars=v["split_max_cjk_chars"],
        floors=PieceFloors(v["split_min_piece_seconds"], v["split_min_piece_cjk_chars"],
                           v["split_min_piece_words"]),
        repeat_min_repeats=v["repeat_loop_min_repeats"],
        stock_isolation_sec=v["stock_phrase_isolation_seconds"],
        realign_min_seconds=v["realign_min_seconds"],
        realign_pause_seconds=v["realign_pause_seconds"],
        vad_threshold=v["qwen_vad_threshold"],
        vad_min_silence_ms=v["qwen_vad_min_silence_ms"],
        vad_min_speech_ms=v["qwen_vad_min_speech_ms"],
        vad_pad_ms=v["qwen_vad_pad_ms"],
        merge_gap_s=v["qwen_merge_gap_seconds"],
        merge_gap_mixed_s=v["qwen_merge_gap_mixed_seconds"],
        span_cap_s=v["qwen_span_cap_seconds"],
        span_cap_search_s=v["qwen_span_cap_search_seconds"],
        context_pad_s=v["qwen_context_pad_seconds"],
        long_vad_threshold=v["qwen_long_vad_threshold"],
        long_vad_min_speech_ms=v["qwen_long_vad_min_speech_ms"],
        long_vad_pad_ms=v["qwen_long_vad_pad_ms"],
        long_merge_gap_s=v["qwen_long_merge_gap_seconds"],
        long_window_s=v["qwen_long_window_seconds"],
        long_cut_search_s=v["qwen_long_cut_search_seconds"],
        asmr_start=v["asmr_start_threshold"], asmr_stop=v["asmr_stop_threshold"])


def resplit_limits() -> dict:
    """The fresh-transcription limits the Review re-split falls back to."""
    v = values()
    return {"line_seconds": v["split_max_seconds"], "max_cjk_chars": v["split_max_cjk_chars"],
            "floors": PieceFloors(v["split_min_piece_seconds"], v["split_min_piece_cjk_chars"],
                                  v["split_min_piece_words"])}


def sentence_limits(vals: dict = None) -> sentence_groups.GroupLimits:
    v = vals if vals is not None else values()
    return sentence_groups.GroupLimits(
        max_gap_seconds=v["sentence_group_gap_seconds"], max_lines=v["sentence_group_max_lines"],
        max_chars=v["sentence_group_max_chars"], max_seconds=v["sentence_group_max_seconds"])


def translate_limits() -> dict:
    """The keyword arguments the translate pipeline takes for scene breaks and sentences."""
    v = values()
    return {"scene_break_gap": v["scene_break_gap_seconds"], "sentence_limits": sentence_limits(v)}


def vad_threshold_for(drama) -> float:
    """sensitivity_preset.stored_vad_threshold with the Sensitive preset's own number."""
    return sensitivity_preset.stored_vad_threshold(
        drama, values()["sensitive_vad_threshold"])
