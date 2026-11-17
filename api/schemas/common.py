"""api/schemas/common.py -- Shared building blocks: the error envelope and the
API version, plus any model used by more than one domain module.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

__all__ = [
    "API_VERSION",
    "ErrorInfo",
    "ErrorResponse",
    "error_responses",
    "ConfirmRequest",
    "UpgradeRequest",
    "JobState",
    "JobStarted",
    "TranslateEngine",
    "LibraryPreset",
    "TimingPreferences",
    "TimingPreferencesUpdate",
    "QueueSettingRow",
    "NotificationStatus",
    "NotificationTarget",
    "SourcesUrlImportRequest",
    "DiagnosticsInstallResult",
    "DiagnosticsTorchVerify",
    "AssStyleOverrides",
    "AssExportRequest",
    "DramaSummary",
    "DramaDetail",
]


API_VERSION = "0.1"


class ErrorInfo(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. `not_found`.")
    message: str = Field(description="Human-readable explanation, safe to display.")
    details: Optional[Any] = Field(default=None, description="Optional structured extra.")


class ErrorResponse(BaseModel):
    error: ErrorInfo


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """The `responses=` mapping that documents each status code with the error envelope."""
    return {code: {"model": ErrorResponse} for code in codes}


class ConfirmRequest(BaseModel):
    # Subclassed once per route so each keeps its own name in the OpenAPI schema.
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class UpgradeRequest(BaseModel):
    """The version the user is about to confirm (the last update check's
    target); 409 when that check no longer says so."""
    model_config = ConfigDict(extra="forbid")
    target: Optional[StrictStr] = Field(None, max_length=64,
                                        pattern=r"^[0-9][0-9A-Za-z.+!_-]*$")


class JobState(BaseModel):
    status: Optional[str] = None
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None


class JobStarted(BaseModel):
    job_id: str
    started: bool


class TranslateEngine(BaseModel):
    """One entry from engine_backends.engine_registry.ENGINES --
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
    # The profile's other settings (Translate run options, transcribe
    # options) and whether New title preselects it.
    options: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class TimingPreferences(BaseModel):
    """Settings > Timing and splitting as GET /api/settings returns it (one field per row
    of lib/settings_schema.py in the three timing sections). Units and limits are in the
    settings schema, not repeated here."""
    sentence_split_min_silence_ms: int = 2000
    sensitive_vad_threshold: float = 0.35
    difficult_no_speech_threshold: float = 0.8
    difficult_log_prob_threshold: float = -1.5
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
    difficult_no_speech_threshold: Optional[Union[StrictInt, StrictFloat]] = None
    difficult_log_prob_threshold: Optional[Union[StrictInt, StrictFloat]] = None
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


class QueueSettingRow(BaseModel):
    """One \"label: value\" line of a step's settings, shown by the Queue and by Quick video translate."""
    label: str
    value: str
    # Set on the engine row: its id, which the page words.
    engine: Optional[str] = None


class NotificationStatus(BaseModel):
    """Configured booleans only: never a webhook URL, host or topic."""
    discord_configured: bool
    ntfy_configured: bool
    ntfy_allow_local: bool


class NotificationTarget(BaseModel):
    """Where a "waiting" bell entry's Open goes: a page and the one id it needs
    (notification_service.clean_target). Never a URL or path; the app builds
    the link."""
    model_config = ConfigDict(extra="forbid")
    page: Literal["glossary", "review", "series", "jobs"]
    drama_id: Optional[StrictInt] = Field(None, ge=1)
    series_id: Optional[StrictInt] = Field(None, ge=1)


class SourcesUrlImportRequest(BaseModel):
    """Novel text only (thin version). The drama must be a novel drama."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(min_length=1, max_length=2000)
    drama_id: int = Field(ge=1)


class DiagnosticsInstallResult(BaseModel):
    package: str
    ok: bool
    output_tail: List[str]
    # A plain-English next step for a known failure (pip's cache unwritable).
    hint: Optional[str] = None


class DiagnosticsTorchVerify(BaseModel):
    torch: Optional[str] = None
    torchvision: Optional[str] = None
    torchaudio: Optional[str] = None
    cuda_build: Optional[str] = None
    cuda_available: Optional[bool] = None
    device: Optional[str] = None
    error: Optional[str] = None


class DramaSummary(BaseModel):
    """One row of the Library's drama list."""
    id: int
    title_zh: Optional[str] = None
    title_en: Optional[str] = None
    author: Optional[str] = None
    studio: Optional[str] = None
    director: Optional[str] = None
    voice_actors: Optional[str] = Field(default=None, description="Comma-separated, as entered.")
    status: Optional[str] = Field(
        default=None, description="Pipeline status: not started / aligned / translated / "
                                  "dubbed / exported.")
    source_language: Optional[str] = Field(default=None, description="`zh`, `ja` or `ko`.")
    media_type: Optional[str] = None
    content_mode: Optional[str] = None
    series_id: Optional[int] = None
    release_date: Optional[str] = Field(
        default=None, description="Stream / release date, YYYY-MM-DD; empty or null = unknown.")
    translation_engine: Optional[str] = None
    custom_tags: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_private: Optional[bool] = Field(
        default=None, description="Hidden from the household (a drama in a series follows its "
                                  "series). Only set in the Library list.")
    owned_by_me: Optional[bool] = Field(
        default=None, description="The signed-in viewer may change its sharing: they created it, "
                                  "or they are an admin and it was made at the PC (or they are at "
                                  "the PC). Only set in the Library list.")
    line_count: int = Field(default=0, description="Lines the title has. Only counted in the Library list.")
    translated_count: int = Field(default=0, description="Lines with English text. Only counted in the Library list.")
    page_count: int = Field(default=0, description="Pages a comic has. Only counted in the Library list.")
    pages_translated: int = Field(default=0, description="Comic pages with translated text. Only counted in the Library list.")
    new_chapters: int = Field(default=0, description="New chapters found by a source check and not yet dismissed, for a title linked to a tracked series. Only counted in the Library list.")


class DramaDetail(DramaSummary):
    """Everything a client needs to show one drama, minus internals."""
    summary: Optional[str] = None
    genre: Optional[str] = None
    publication_status: Optional[str] = None
    chapter_count: Optional[int] = None
    # Shown and edited in the Workspace's Edit details.
    source_url: Optional[str] = None
    episode_number: Optional[int] = None
    episode_summary: Optional[str] = None
    episode_brief: Optional[str] = None
    narration_language: Optional[str] = None
    author_romanized: Optional[str] = None
    studio_romanized: Optional[str] = None
    director_romanized: Optional[str] = None
    voice_actors_romanized: Optional[str] = None
    series_instructions: Optional[str] = None
    has_audio: bool = Field(description="A source audio/video file is attached.")
    has_novel_reference: bool
    has_cover_art: bool
    keep_original: bool = Field(
        default=False, description="\"Keep original files\": storage cleanup never removes "
                                   "this title's originals and an export always includes them.")


class AssStyleOverrides(BaseModel):
    """Per-request ASS style overrides. Only fields the
    client sets replace the preset's values; unknown keys are a 422. An
    explicit JSON null for a field is also a 422 (omit the key instead)."""
    model_config = ConfigDict(extra="forbid")
    font: Optional[str] = None
    size: Optional[int] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    primary: Optional[str] = None
    outline: Optional[str] = None
    outline_width: Optional[int] = None
    shadow: Optional[int] = None
    alignment: Optional[str] = None
    sfx_alignment: Optional[str] = None
    notes_alignment: Optional[str] = None


class AssExportRequest(BaseModel):
    field: str = "en"
    style: Optional[AssStyleOverrides] = None
    preset: str = "Clean"
    speaker_colors: Optional[Dict[str, str]] = None
    per_speaker_colors: bool = True
    include_notes: bool = False
    notes_as_separate_line: bool = False
    wrap_chars_en: Optional[int] = Field(default=None, ge=0, le=200)
    wrap_chars_source: Optional[int] = Field(default=None, ge=0, le=200)
