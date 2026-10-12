"""api/schemas/common.py -- Shared building blocks: the error envelope and the
API version, plus any model used by more than one domain module.
"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, StrictFloat, StrictInt

__all__ = [
    "API_VERSION",
    "ErrorInfo",
    "ErrorResponse",
    "TranslateEngine",
    "LibraryPreset",
    "TimingPreferences",
    "TimingPreferencesUpdate",
]


API_VERSION = "0.1"


class ErrorInfo(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. `not_found`.")
    message: str = Field(description="Human-readable explanation, safe to display.")
    details: Optional[Any] = Field(default=None, description="Optional structured extra.")


class ErrorResponse(BaseModel):
    error: ErrorInfo


class TranslateEngine(BaseModel):
    """One entry from translate_engines.ENGINES --
    key_configured is a boolean only, never a key value."""
    name: str
    label: str
    free: bool
    models: Optional[List[str]] = None
    # Label for an offered model that has no built-in entry (id -> text).
    model_labels: Dict[str, str] = Field(default_factory=dict)
    cloud_models: List[str] = Field(default_factory=list)
    # True when the engine can run: key set and SDK installed, or (Ollama) the server answers.
    key_configured: bool
    package_installed: bool = True


class LibraryPreset(BaseModel):
    id: int
    name: str
    translation_engine: Optional[str] = None
    engine_model: Optional[str] = None
    style_preset: Optional[str] = None
    locale: Optional[str] = None
    default_female_pronouns: Optional[int] = None
    include_genre_notes: Optional[int] = None


class TimingPreferences(BaseModel):
    """Settings > Timing and splitting as GET /api/settings returns it (one field per row
    of lib/settings_schema.py in the three timing sections). Units and limits are in the
    settings schema, not repeated here."""
    sentence_split_min_silence_ms: int = 2000
    sensitive_vad_threshold: float = 0.35
    qwen_vad_threshold: float = 0.5
    qwen_vad_min_silence_ms: int = 300
    qwen_vad_min_speech_ms: int = 250
    qwen_vad_pad_ms: int = 100
    qwen_long_vad_threshold: float = 0.35
    qwen_long_vad_min_speech_ms: int = 0
    qwen_long_vad_pad_ms: int = 300
    qwen_merge_gap_seconds: float = 1.0
    qwen_merge_gap_mixed_seconds: float = 0.3
    qwen_span_cap_seconds: float = 15.0
    qwen_span_cap_search_seconds: float = 3.0
    qwen_context_pad_seconds: float = 2.0
    qwen_long_merge_gap_seconds: float = 3.0
    qwen_long_window_seconds: float = 30.0
    qwen_long_cut_search_seconds: float = 8.0
    asmr_start_threshold: float = 0.5
    asmr_stop_threshold: float = 0.35
    speech_coverage_vad_threshold: float = 0.3
    split_max_seconds: float = 8.0
    split_max_cjk_chars: int = 40
    split_min_piece_seconds: float = 0.8
    split_min_piece_cjk_chars: int = 4
    split_min_piece_words: int = 2
    quick_split_seconds: float = 12.0
    realign_min_seconds: float = 12.0
    realign_pause_seconds: float = 0.6
    stock_phrase_isolation_seconds: float = 5.0
    repeat_loop_min_repeats: int = 4
    retime_window_pad_seconds: float = 3.0
    gap_fill_min_gap_seconds: float = 2.0
    scene_break_gap_seconds: float = 3.0
    sentence_group_gap_seconds: float = 1.0
    sentence_group_max_lines: int = 4
    sentence_group_max_chars: int = 60
    sentence_group_max_seconds: float = 12.0


class TimingPreferencesUpdate(BaseModel):
    """The same rows as optional writes; settings_service.set_settings re-validates
    every value and refuses a Developer Mode row while Developer Mode is off."""
    sentence_split_min_silence_ms: Optional[StrictInt] = None
    sensitive_vad_threshold: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_vad_threshold: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_vad_min_silence_ms: Optional[StrictInt] = None
    qwen_vad_min_speech_ms: Optional[StrictInt] = None
    qwen_vad_pad_ms: Optional[StrictInt] = None
    qwen_long_vad_threshold: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_long_vad_min_speech_ms: Optional[StrictInt] = None
    qwen_long_vad_pad_ms: Optional[StrictInt] = None
    qwen_merge_gap_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_merge_gap_mixed_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_span_cap_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_span_cap_search_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_context_pad_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_long_merge_gap_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_long_window_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    qwen_long_cut_search_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    asmr_start_threshold: Optional[Union[StrictInt, StrictFloat]] = None
    asmr_stop_threshold: Optional[Union[StrictInt, StrictFloat]] = None
    speech_coverage_vad_threshold: Optional[Union[StrictInt, StrictFloat]] = None
    split_max_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    split_max_cjk_chars: Optional[StrictInt] = None
    split_min_piece_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    split_min_piece_cjk_chars: Optional[StrictInt] = None
    split_min_piece_words: Optional[StrictInt] = None
    quick_split_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    realign_min_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    realign_pause_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    stock_phrase_isolation_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    repeat_loop_min_repeats: Optional[StrictInt] = None
    retime_window_pad_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    gap_fill_min_gap_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    scene_break_gap_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    sentence_group_gap_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    sentence_group_max_lines: Optional[StrictInt] = None
    sentence_group_max_chars: Optional[StrictInt] = None
    sentence_group_max_seconds: Optional[Union[StrictInt, StrictFloat]] = None
