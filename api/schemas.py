"""
api/schemas.py -- the API contract: every request/response shape the
FastAPI routes expose, as Pydantic models.

These are the only shapes a client (the React frontend, later the
browser extension) may rely on. They are deliberately *not* the SQLite
row: internal columns (stored filenames, last-error JSON, anything that
names a path on disk) are left out or reduced to a boolean, and
`custom_tags` is a list instead of the comma-joined text column, so the
database schema can keep evolving without breaking a client. Adding a
field is a compatible change; renaming or removing one is not -- bump
`API_VERSION` when that has to happen.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "0.1"


class ErrorInfo(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. `not_found`.")
    message: str = Field(description="Human-readable explanation, safe to display.")
    details: Optional[Any] = Field(default=None, description="Optional structured extra.")


class ErrorResponse(BaseModel):
    error: ErrorInfo


class HealthResponse(BaseModel):
    status: str = Field(description="`ok` whenever the server can answer at all.")


class MetaResponse(BaseModel):
    app: str
    api_version: str
    environment: str = Field(description="`development` or `production`.")


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
    translation_engine: Optional[str] = None
    custom_tags: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DramaDetail(DramaSummary):
    """Everything a client needs to show one drama, minus internals."""
    summary: Optional[str] = None
    genre: Optional[str] = None
    publication_status: Optional[str] = None
    chapter_count: Optional[int] = None
    narration_language: Optional[str] = None
    author_romanized: Optional[str] = None
    studio_romanized: Optional[str] = None
    director_romanized: Optional[str] = None
    voice_actors_romanized: Optional[str] = None
    series_instructions: Optional[str] = None
    has_audio: bool = Field(description="A source audio/video file is attached.")
    has_novel_reference: bool
    has_cover_art: bool


class DramaListResponse(BaseModel):
    items: List[DramaSummary]
    count: int


class ReaderPageResponse(BaseModel):
    """One page of a drama's Reader view. `html` is a complete,
    self-contained document (Migration Slice 4) -- render it in a
    sandboxed iframe via `srcDoc`, the same way Streamlit's `st.iframe`
    embeds it today. Definitions baked into `html` are only ever
    whatever's already been looked up and saved for this drama; this
    endpoint never makes a live/paid dictionary call itself."""
    html: str
    page: int
    page_count: int
    total_lines: int


class DependencyStatus(BaseModel):
    installed: bool
    powers: str
    tier: str


class FileCompleteness(BaseModel):
    missing_top_level: List[str]
    missing_tabs: List[str]
    all_present: bool


class GpuStatus(BaseModel):
    available: bool
    name: Optional[str] = None
    vram_used_gb: Optional[float] = None
    vram_total_gb: Optional[float] = None
    torch_cuda_version: Optional[str] = None
    message: Optional[str] = None


class ModelEngineVersion(BaseModel):
    name: str
    version: Optional[str] = None
    url: Optional[str] = None
    installed: bool
    package: Optional[str] = None
    help: Optional[str] = None


class RunningJob(BaseModel):
    job_id: str
    status: Optional[str] = None
    progress: Optional[float] = None
    message: str = ""
    error: Optional[str] = None
    description: Optional[str] = None
    gpu_touching: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class DiagnosticsOverview(BaseModel):
    """A read-only snapshot of Diagnostics' routine view (Migration
    Slice 5) -- no admin action (install/upgrade/delete) is exposed
    here; those stay Streamlit-only. Log lines and job messages/errors
    are redacted the same way the Streamlit tab's own "copy for
    support" export already is."""
    dependencies: dict[str, DependencyStatus]
    file_completeness: FileCompleteness
    library_writable: bool
    gpu: GpuStatus
    model_engine_versions: List[ModelEngineVersion]
    running_jobs: List[RunningJob]
    recent_log_lines: List[str]


class JobRecord(BaseModel):
    """One job's cross-process record (Migration Slice 8, reading
    Migration Slice 7's job_records mirror) -- the last status this app
    knows about, from any process, not necessarily the current one (see
    job_records' own "no resume" limitation)."""
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    error: Optional[str] = None
    description: Optional[str] = None
    gpu_touching: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float


class JobListResponse(BaseModel):
    items: List[JobRecord]
    count: int


class SettingsOverview(BaseModel):
    """Non-secret settings snapshot (Migration Slice 10) -- engine_keys
    reports only whether a key/endpoint is configured, never its value
    (D2: keys are server-side only)."""
    engine_keys: dict[str, bool]
    gpu_limit_enabled: bool
    notify_on_completion: bool


class TranslateEngine(BaseModel):
    """One entry from translate_engines.ENGINES (Migration Slice 11) --
    key_configured is a boolean only, never a key value (D2)."""
    name: str
    label: str
    free: bool
    models: Optional[List[str]] = None
    key_configured: bool


class TranslateEngineListResponse(BaseModel):
    items: List[TranslateEngine]


class TranslateHistoryEntry(BaseModel):
    source_language: str
    target_language: str
    engine: str
    source_text: str
    translated_text: str
    created_at: Optional[str] = None


class TranslateHistoryResponse(BaseModel):
    items: List[TranslateHistoryEntry]


class TranslateRequest(BaseModel):
    """Never carries an API key (D2) -- the server resolves one per engine
    itself; see services/translate_service.py's own resolve logic."""
    text: str
    engine: str
    source_language: str
    target_language: str
    model: Optional[str] = None
    free_tier: bool = False
    base_url: Optional[str] = None


class TranslateResponse(BaseModel):
    translated_text: str


class ClearHistoryResult(BaseModel):
    cleared: bool


class ExportReadiness(BaseModel):
    """Read-only export-readiness summary for one drama (Migration Slice
    12) -- counts only, never flags a line or generates a file."""
    drama_id: int
    total_lines: int
    zh_filled: int
    en_filled: int
    fully_translated: bool
    test_mode_output: bool
    overlap_count: int
    auto_qc_issue_count: int
    dense_line_count: int


class FlagActionResult(BaseModel):
    """A flagging action's result (Migration Slice 15) -- 0 is not an
    error, just nothing new to flag."""
    flagged_count: int


class AutoQcFlagResult(BaseModel):
    """auto_qc.run_auto_qc's own counts (Migration Slice 15) -- see its
    docstring for exactly what each counts."""
    flagged: int
    cleared: int
    already_flagged: int
    checked: int


class DiarizationConfig(BaseModel):
    """Read-only Diarize-stage summary for one drama (Migration Slice
    16) -- hf_token_configured is a boolean only, never the token value
    itself (D2)."""
    drama_id: int
    hf_token_configured: bool
    expected_speakers: Optional[int] = None
    audio_available: bool


class DiarizationRunResult(BaseModel):
    job_id: str


class SourceConfig(BaseModel):
    """Source-stage config for one drama (Migration Slice 19) -- config
    only (language/script/content mode/transcript mode) plus read-only
    audio/video/transcript-source presence. Never includes an upload or
    a secret."""
    drama_id: int
    source_language: str
    chinese_script: str
    content_mode: str
    has_audio_pipeline: bool
    audio_available: bool
    has_video_source: bool
    transcript_mode: str
    transcript_mode_options: List[str]
    has_raw_novel_context: bool


class SourceConfigUpdate(BaseModel):
    """All fields optional -- only what's passed is validated and
    written (a field-scoped partial update, matching db.update_drama's
    own shape)."""
    source_language: Optional[str] = None
    chinese_script: Optional[str] = None
    content_mode: Optional[str] = None
    transcript_mode: Optional[str] = None


class TranscribeConfig(BaseModel):
    """Read-only Transcript-stage summary for one drama (Migration Slice
    20) -- which action the transcribe button would run (from Slice 19's
    transcript_mode) plus every tuning knob's current value, falling back
    to the same defaults the Streamlit widgets use."""
    drama_id: int
    transcript_mode: str
    has_audio_pipeline: bool
    audio_available: bool
    alignment_method: str
    asr_backend_choice: str
    whisper_size: str
    whisper_model_cached: bool
    beam_size: int
    min_silence_ms: int
    vad_threshold: float
    separate_vocals_first: bool
    separation_backend: str
    realign_long_segments: bool
    whisper_fast_mode: bool
    use_groq: bool
    has_video_source: bool
    hardsub_ocr_backend: str
    hardsub_interval_sec: float


class TranscribeConfigUpdate(BaseModel):
    """All fields optional -- only what's passed is validated and
    written."""
    whisper_size: Optional[str] = None
    alignment_method: Optional[str] = None
    asr_backend_choice: Optional[str] = None
    beam_size: Optional[int] = None
    min_silence_ms: Optional[int] = None
    vad_threshold: Optional[float] = None
    separate_vocals_first: Optional[bool] = None
    separation_backend: Optional[str] = None
    realign_long_segments: Optional[bool] = None
    whisper_fast_mode: Optional[bool] = None
    use_groq: Optional[bool] = None
    hardsub_ocr_backend: Optional[str] = None
    hardsub_interval_sec: Optional[float] = None


class TranscribeRunRequest(BaseModel):
    """transcript_text is required (and only used) when this drama's
    transcript_mode is "have_transcript" -- per Slice 19, it's never
    persisted server-side. tesseract_cmd is an optional, client-supplied
    path to the tesseract binary (hardsub_ocr with the "tesseract"
    backend only) -- Streamlit's own equivalent Settings value has no
    settings_service-backed home yet (Migration Slice 21)."""
    source_language: Optional[str] = None
    chinese_script: Optional[str] = None
    transcript_text: Optional[str] = None
    run_diarize: bool = False
    expected_speakers: Optional[int] = Field(default=None, ge=0, le=20)
    initial_prompt: str = ""
    tesseract_cmd: Optional[str] = None


class TranscribeRunResult(BaseModel):
    job_id: str


class DubTtsEngine(BaseModel):
    """One selectable TTS engine for the Dub stage (Migration Slice 25)."""
    key: str
    label: str
    requires_internet: bool


class DubSpeaker(BaseModel):
    """One speaker's resolved voices and engine, as the Generate button
    would resolve them. D2: no reference-audio path, only a boolean."""
    speaker_label: str
    character_name: Optional[str] = None
    edge_voice: Optional[str] = None
    offline_voice: Optional[str] = None
    engine: str
    has_clone_ref: bool


class DubDefaults(BaseModel):
    """Pacing-limit defaults and slider ranges; null for narration."""
    max_speedup: float
    max_slowdown: float
    speedup_range: List[float]
    slowdown_range: List[float]


class DubConfig(BaseModel):
    """Read-only Dub-stage summary for one drama (Migration Slice 25).
    D2: no filesystem path, no GPT-SoVITS URL or secret -- only the
    `gpt_sovits_configured` boolean."""
    drama_id: int
    content_mode: Optional[str] = None
    is_narration: bool
    narration_language: str
    narration_language_options: List[str]
    source_language: str
    tts_engines: List[DubTtsEngine]
    defaults: Optional[DubDefaults] = None
    speakers: List[DubSpeaker]
    gpu_required: bool
    speakable_line_count: int
    track_available: bool
    gpt_sovits_configured: bool


class DubPacingLine(BaseModel):
    """One line's fit against its original timing window. status is
    "fit", "stretched" or "overflow"."""
    idx: int
    status: str
    factor: float
    clip_ms: Optional[int] = None
    window_ms: Optional[int] = None


class DubPacing(BaseModel):
    """Per-line pacing from the last dub run; `available` is False when
    there is nothing to show (never dubbed, or narration). D2: no paths."""
    available: bool
    counts: Dict[str, int]
    lines: List[DubPacingLine]

class AssStyleOverrides(BaseModel):
    """Per-request ASS style overrides (Migration Slice 27). Only fields the
    client sets replace the preset's values; unknown keys are a 422."""
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
    wrap_chars_en: Optional[int] = Field(default=None, ge=0)
    wrap_chars_source: Optional[int] = Field(default=None, ge=0)


class AssStyleOptions(BaseModel):
    presets: Dict[str, Dict[str, Any]]
    default_preset: str
    fonts: List[str]
    custom_font_allowed: bool
    alignments: Dict[str, int]
    size_range: List[int]
    outline_width_range: List[int]
    shadow_range: List[int]
