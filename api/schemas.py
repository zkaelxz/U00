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

from pydantic import BaseModel, ConfigDict, Field, StrictBool

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
    use_gpu: bool = False
    gemini_free_tier: bool = False


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
    clip_ms: Optional[float] = None
    window_ms: Optional[float] = None


class DubPacing(BaseModel):
    """Per-line pacing from the last dub run; `available` is False when
    there is nothing to show (never dubbed, or narration). D2: no paths."""
    available: bool
    counts: Dict[str, int]
    lines: List[DubPacingLine]


class AssStyleOverrides(BaseModel):
    """Per-request ASS style overrides (Migration Slice 27). Only fields the
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


class AssStyleOptions(BaseModel):
    presets: Dict[str, Dict[str, Any]]
    default_preset: str
    fonts: List[str]
    custom_font_allowed: bool
    alignments: Dict[str, int]
    size_range: List[int]
    outline_width_range: List[int]
    shadow_range: List[int]



class DramaCreateRequest(BaseModel):
    """Create a drama (Migration Slice 35). `source_language` is required
    (zh/ja/ko); `series_id` and `new_series_name` are mutually exclusive."""
    model_config = ConfigDict(extra="forbid")
    source_language: str
    title_en: str = Field(default="", max_length=300)
    title_zh: str = Field(default="", max_length=300)
    author: str = Field(default="", max_length=300)
    studio: str = Field(default="", max_length=300)
    director: str = Field(default="", max_length=300)
    voice_actors: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=5000)
    media_type: str = "audio_drama"
    series_id: Optional[int] = Field(default=None, ge=1, le=2147483647)
    new_series_name: Optional[str] = Field(default=None, max_length=300)
    preset_id: Optional[int] = Field(default=None, ge=1, le=2147483647)


class DramaMetadataUpdate(BaseModel):
    """Partial metadata update: only fields present in the body are applied.
    Unknown keys (status, content_mode, *_filename, ...) are rejected. For
    `chapter_count`/`episode_number`, 0 clears the value."""
    model_config = ConfigDict(extra="forbid")
    title_en: Optional[str] = Field(default=None, max_length=300)
    title_zh: Optional[str] = Field(default=None, max_length=300)
    author: Optional[str] = Field(default=None, max_length=300)
    studio: Optional[str] = Field(default=None, max_length=300)
    director: Optional[str] = Field(default=None, max_length=300)
    voice_actors: Optional[str] = Field(default=None, max_length=300)
    summary: Optional[str] = Field(default=None, max_length=5000)
    genre: Optional[str] = Field(default=None, max_length=300)
    custom_tags: Optional[str] = Field(default=None, max_length=2000)
    source_url: Optional[str] = Field(default=None, max_length=2000)
    episode_summary: Optional[str] = Field(default=None, max_length=5000)
    project_instructions: Optional[str] = Field(default=None, max_length=5000)
    chapter_count: Optional[int] = Field(default=None, ge=0, le=2147483647)
    episode_number: Optional[int] = Field(default=None, ge=0, le=2147483647)
    media_type: Optional[str] = None
    publication_status: Optional[str] = None
    series_id: Optional[int] = Field(default=None, ge=1, le=2147483647)


class DramaPresetDefaults(BaseModel):
    """A preset's session-only values, returned for the client to hold
    (only the preset's translation engine is persisted on the drama)."""
    style_preset: Optional[str] = None
    locale: Optional[str] = None
    default_female_pronouns: bool
    include_genre_notes: bool


class DramaCreateResult(DramaDetail):
    preset_defaults: Optional[DramaPresetDefaults] = None


class TranslateRunStylePreset(BaseModel):
    key: str
    label: str


class TranslateRunWorkflowTier(BaseModel):
    key: str
    label: str
    translation_engine: str
    engine_model: Optional[str] = None
    reflect: bool
    auto_qc: bool


class TranslateRunDefaults(BaseModel):
    context_window: int
    context_window_ahead: int
    batch_size: int


class TranslateRunConfig(BaseModel):
    """Read-only Translate-stage summary (Migration Slice 39). Booleans and
    numbers only -- never a key or the novel text (D2)."""
    drama_id: int
    translation_engine: str
    engines: List[TranslateEngine]
    style_presets: List[TranslateRunStylePreset]
    default_style_preset: str
    locales: List[str]
    workflow_tiers: List[TranslateRunWorkflowTier]
    defaults: TranslateRunDefaults
    project_instructions: Optional[str] = None
    series_instructions: Optional[str] = None
    has_novel_reference: bool
    line_count: int
    untranslated_count: int
    last_translate_errors: Optional[Any] = None
    previous_episode_summary_present: bool
    monthly_cap_usd: float
    month_spend: float
    cap_applies_by_engine: Dict[str, bool]
    bulk_supported_engines: List[str]


class TranslateRunEstimate(BaseModel):
    """Advisory pre-run cost estimate (Migration Slice 39)."""
    engine: str
    model: Optional[str] = None
    estimated_usd: Optional[float] = None
    target_line_count: int
    free: bool
    cap_applies: bool
    effective_cap_usd: Optional[float] = None
    monthly_refusal: bool
    estimate_above_cap: bool


class CharactersEntry(BaseModel):
    """One speaker's character/voice settings. No reference-audio
    filename or path -- only the two booleans (D2)."""
    speaker_label: str
    character_name: str
    pronouns: str
    tts_voice: str
    offline_voice: str
    clone_engine: str
    voice_design: str
    has_ref_audio: bool
    ref_text_present: bool
    series_character_id: Optional[int] = None
    series_character_name: str
    line_count: int


class CharactersUpdateRequest(BaseModel):
    """speaker_label identifies the speaker; every other field is
    optional -- omitted (or null) leaves the stored value alone, an
    explicit "" clears it (character_name can't be blank)."""
    model_config = {"extra": "forbid"}

    speaker_label: str
    character_name: Optional[str] = None
    pronouns: Optional[str] = None
    tts_voice: Optional[str] = None
    offline_voice: Optional[str] = None
    clone_engine: Optional[str] = None
    voice_design: Optional[str] = None
    ref_text: Optional[str] = None


class CharactersSeriesEntry(BaseModel):
    id: int
    character_name: str
    aliases: str
    notes: str
    pronouns: str


class CharactersCloneEngineItem(BaseModel):
    id: str
    label: str
    is_default: bool
    language_gated: bool
    local_model: bool


class CharactersCloneEngines(BaseModel):
    source_language: str
    default_engine: str
    engines: List[CharactersCloneEngineItem]


class CharactersVoiceBankEntry(BaseModel):
    """Voice bank picklist entry: metadata only, never the clip file."""
    id: int
    name: str
    clone_engine: str
    voice_design: str
    language: str
    notes: str
    ref_text_present: bool


class CharactersVoiceBankApply(BaseModel):
    speaker_label: str
    voice_bank_id: int = Field(ge=1, le=2147483647)


# --- Glossary, instructions and catalogues (Migration Slice 46) -----------


class GlossaryTerm(BaseModel):
    id: int
    term_original: str
    term_translation: str
    notes: str = ""
    category: Optional[str] = None
    policy: Optional[str] = None
    enforce_exact: bool = False
    aliases: List[str] = Field(default_factory=list)
    banned_translations: List[str] = Field(default_factory=list)


class GlossaryTermUpsert(BaseModel):
    """With `id` the term is updated in place (omitted fields keep their
    stored value); without it the term is keyed on term_original.
    term_original/term_translation are required for a new term (the
    service enforces that)."""
    model_config = {"extra": "forbid"}

    id: Optional[int] = None
    term_original: Optional[str] = None
    term_translation: Optional[str] = None
    notes: Optional[str] = None
    category: Optional[str] = None
    policy: Optional[str] = None
    enforce_exact: Optional[bool] = None
    aliases: Optional[List[str]] = None
    banned_translations: Optional[List[str]] = None


class GlossaryDeleteResult(BaseModel):
    deleted: bool


class GlossaryInstructions(BaseModel):
    project_instructions: str
    series_instructions: str


class GlossaryInstructionsUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    text: str


class GlossaryCatalogueOption(BaseModel):
    key: str
    label: str


class GlossaryTermPolicyOption(BaseModel):
    key: str
    label: str
    example: str = ""


class GlossaryWorkflowTier(BaseModel):
    key: str
    label: str
    translation_engine: str
    engine_model: Optional[str] = None
    reflect: bool
    auto_qc: bool


class GlossaryCatalogues(BaseModel):
    style_presets: List[GlossaryCatalogueOption]
    term_categories: List[GlossaryCatalogueOption]
    term_policies: List[GlossaryTermPolicyOption]
    workflow_tiers: List[GlossaryWorkflowTier]


# --- Review read-only line views (Migration Slice 47) -----------------------
# Names are prefixed `ReviewLines` on purpose. Identity is always the permanent
# line `id`; `idx` is display-only.


class ReviewLinesLine(BaseModel):
    id: int
    idx: int
    start: float
    end: float
    zh: str
    en: str
    speaker: Optional[str] = None
    speaker_manual: bool
    sfx: bool
    flag: Optional[str] = None
    flag_note: Optional[str] = None
    dub_filename: Optional[str] = None


class ReviewLinesPage(BaseModel):
    lines: List[ReviewLinesLine]
    page: int
    page_size: int
    total: int = Field(description="Lines in the filtered view (before paging).")
    flagged_count: int = Field(description="Flagged lines in the whole drama.")
    untranslated_count: int = Field(description="Untranslated lines in the whole drama.")


class ReviewLinesFindReplaceRequest(BaseModel):
    """Body of a PREVIEW only -- nothing is written."""
    model_config = {"extra": "forbid"}
    find: str = Field(max_length=500)
    replace: str = Field(default="", max_length=500)
    case_sensitive: bool = False
    use_regex: bool = False


class ReviewLinesMatch(BaseModel):
    id: int
    idx: int
    old_text: str
    new_text: str


class ReviewLinesCoverageEntry(BaseModel):
    """One coverage finding. Which fields are set depends on the list it is
    in (long_lines / large_gaps / blank_zh / blank_en)."""
    idx: Optional[int] = None
    id: Optional[int] = None
    start: Optional[float] = None
    end: Optional[float] = None
    duration: Optional[float] = None
    zh: Optional[str] = None
    char_count: Optional[int] = None
    note: Optional[str] = None
    after_idx: Optional[int] = None
    before_idx: Optional[int] = None
    after_id: Optional[int] = None
    before_id: Optional[int] = None
    gap_start: Optional[float] = None
    gap_end: Optional[float] = None
    gap_seconds: Optional[float] = None


class ReviewLinesCoverage(BaseModel):
    long_lines: List[ReviewLinesCoverageEntry]
    large_gaps: List[ReviewLinesCoverageEntry]
    blank_zh: List[ReviewLinesCoverageEntry]
    blank_en: List[ReviewLinesCoverageEntry]


class ReviewLinesPacingFlag(BaseModel):
    id: Optional[int] = None
    idx: int
    issue: str
    detail: Optional[str] = None


class ReviewLinesPacing(BaseModel):
    flags: List[ReviewLinesPacingFlag]
    count: int


class ReviewLinesProvenance(BaseModel):
    """`debug_view.explain_line`, read-only. The list-valued sections have
    variable row shapes, so they are passed through as `Any`."""
    line_id: int
    line_idx: int
    zh: str
    en: str
    speaker: Optional[str] = None
    speaker_manual: bool
    flag: Optional[str] = None
    flag_reason: Optional[str] = None
    flag_note: Optional[str] = None
    translation_notes: List[Any] = []
    emotion: Optional[Any] = None
    edit_samples: List[Any] = []
    consistency_issues: List[Any] = []
    glossary_matches: List[Any] = []
    glossary_matches_note: Optional[str] = None
    context_window_used: Optional[Any] = None
    context_window_note: Optional[str] = None
    current_neighbors_before: List[Any] = []
    current_neighbors_after: List[Any] = []
    engine: Optional[str] = None
    model: Optional[str] = None
    engine_source: Optional[str] = None
    prompt_version_note: Optional[str] = None


class ReviewLinesOriginalText(BaseModel):
    line_id: int
    idx: int
    current_zh: str
    has_raw_transcript: bool
    original_text: Optional[str] = None
    differs: bool


# ---------------------------------------------------------------------------
# Review read-only records (Migration Slice 48) -- see
# services/review_records_service.py. Every model is prefixed
# ReviewRecords to stay clear of the sibling ReviewLines* models.
# ---------------------------------------------------------------------------


class ReviewRecordsHistoryItem(BaseModel):
    id: int
    drama_id: int
    label: Optional[str] = None
    created_at: Optional[str] = None


class ReviewRecordsSnapshotLine(BaseModel):
    id: Optional[int] = None
    idx: Optional[int] = None
    start: Optional[float] = None
    end: Optional[float] = None
    zh: str
    en: str
    speaker: Optional[str] = None
    speaker_manual: bool
    dub_filename: Optional[str] = Field(default=None, description="Bare filename, never a path.")


class ReviewRecordsSnapshot(BaseModel):
    id: int
    drama_id: int
    label: Optional[str] = None
    created_at: Optional[str] = None
    lines: List[ReviewRecordsSnapshotLine]


class ReviewRecordsVersionItem(BaseModel):
    id: int
    drama_id: int
    label: Optional[str] = None
    engine: str
    model: str
    is_active: bool
    created_at: Optional[str] = None


class ReviewRecordsVersionRef(BaseModel):
    id: int
    label: Optional[str] = None


class ReviewRecordsDiff(BaseModel):
    idx: int
    zh: str
    left_en: str
    right_en: str


class ReviewRecordsCompare(BaseModel):
    drama_id: int
    left: ReviewRecordsVersionRef
    right: ReviewRecordsVersionRef
    left_line_count: int
    diff_count: int
    diffs: List[ReviewRecordsDiff]


class ReviewRecordsNote(BaseModel):
    id: int
    drama_id: int
    line_id: Optional[int] = None
    line_idx: Optional[int] = None
    term: Optional[str] = None
    note_type: Optional[str] = None
    note: Optional[str] = None
    created_at: Optional[str] = None


class ReviewRecordsConsistencyIssue(BaseModel):
    id: int
    term: str
    variants: List[str]
    note: str
    created_at: Optional[str] = None


class ReviewRecordsEmotionTag(BaseModel):
    line_idx: int
    emotion: str
    intensity: Optional[float] = None
    note: str


class ReviewRecordsEmotionSummary(BaseModel):
    drama_id: int
    total: int
    by_emotion: Dict[str, int]
    high_risk: int
    lines: List[ReviewRecordsEmotionTag]


class ReviewRecordsTendencyStats(BaseModel):
    total: int
    shortened: int
    expanded: int
    rephrased: int
    avg_word_delta: float


class ReviewRecordsStyleProfile(BaseModel):
    summary: str
    confidence: Optional[Any] = None
    preferences: List[str]
    sample_count: int
    updated_at: Optional[str] = None


class ReviewRecordsTendencies(BaseModel):
    drama_id: int
    scope: str
    tendencies: ReviewRecordsTendencyStats
    profile: Optional[ReviewRecordsStyleProfile] = None


class ReviewRecordsTmSuggestion(BaseModel):
    line_id: Optional[int] = None
    line_idx: int
    zh: str
    en: str
    suggestion: str
    similarity: float
    exact: bool
    entry_id: int


class SettingsUpdateRequest(BaseModel):
    """Non-secret Settings writes (Migration Slice 23). Booleans only;
    unknown fields are rejected -- keys/URLs/paths are never accepted."""
    model_config = ConfigDict(extra="forbid")
    gpu_limit_enabled: Optional[StrictBool] = None
    notify_on_completion: Optional[StrictBool] = None
    use_gpu: Optional[StrictBool] = None
    gemini_free_tier: Optional[StrictBool] = None


class DramaDeleteResult(BaseModel):
    deleted: bool
    drama_id: int


# --- Migration Slice 43: per-line edit writes (services/lines_service.py) ---


class LinesPatchRequest(BaseModel):
    """Partial line edit: only fields the client sets are applied. `expected`
    maps field -> the old value the client saw (409 if the line differs)."""
    model_config = ConfigDict(extra="forbid")
    start: Optional[float] = None
    end: Optional[float] = None
    zh: Optional[str] = Field(default=None, max_length=2000)
    en: Optional[str] = Field(default=None, max_length=2000)
    speaker: Optional[str] = Field(default=None, max_length=100)
    sfx: Optional[bool] = None
    expected: Optional[Dict[str, Any]] = None


class LinesMatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    old_text: str = Field(max_length=2000)
    new_text: str = Field(max_length=2000)


class LinesFindReplaceApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matches: List[LinesMatchIn] = Field(max_length=1000)


class LinesFindReplaceApplyResult(BaseModel):
    applied: int
    stale: int
    applied_ids: List[int]
    stale_ids: List[int]


class LinesAcceptTmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: int = Field(ge=1)


class LinesNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: int = Field(ge=1)
    term: str = Field(max_length=500)
    note_type: str = Field(max_length=40)
    note: str = Field(max_length=2000)


class LinesNote(BaseModel):
    id: int
    line_id: int
    line_idx: Optional[int] = None
    term: str
    note_type: str
    note: str


class LinesNoteDeleteResult(BaseModel):
    deleted: bool
    note_id: int


class JobCancelResult(BaseModel):
    job_id: str
    cancel_requested: bool
    status: str


class ArtifactInfo(BaseModel):
    name: str
    size: int
    kind: str


class MediaUploadResult(BaseModel):
    name: str
    size: int
    kind: str


class NarrationEngineOption(BaseModel):
    key: str
    key_configured: bool


class NarrationConfig(BaseModel):
    drama_id: int
    is_narration: bool
    has_novel_source: bool
    engines: List[NarrationEngineOption]
    default_engine: str
    max_chunk_chars: int
    existing_line_count: int
    replaces_existing_lines: bool
    job_running: bool


class NarrationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(default=None, max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)


class NarrationRunResult(BaseModel):
    job_id: str


class DubRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tts_engine: str = Field(default="edge_tts", max_length=40)
    max_speedup: Optional[float] = Field(default=None, ge=1.0, le=2.0)
    max_slowdown: Optional[float] = Field(default=None, ge=0.5, le=1.0)
    narration_language: Optional[str] = Field(default=None, max_length=20)


class DubRunStarted(BaseModel):
    job_id: str


class TranslateRunStart(BaseModel):
    """Start a normal translation (Migration Slice 40). No keys/URLs."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=200)
    style_preset: Optional[str] = Field(None, max_length=40)
    style_note: str = Field("", max_length=4000)
    locale: str = Field("en-US", max_length=10)
    force_retranslate: bool = False
    context_window: Optional[int] = Field(None, ge=0, le=100)
    context_window_ahead: Optional[int] = Field(None, ge=0, le=100)
    batch_size: Optional[int] = Field(None, ge=1, le=200)
    line_ids: Optional[List[int]] = Field(None, max_length=100000)
    gemini_free_tier: bool = False
    job_cost_cap_usd: Optional[float] = Field(None, ge=0)


class TranslateRunStarted(BaseModel):
    job_id: str
    drama_id: int
    engine: str
    model: Optional[str] = None
    target_line_count: int


class MediaStatus(BaseModel):
    drama_id: int
    has_audio: bool
    has_source_video: bool
    upload_max_mb: int


class UploadAndTranscribeResult(BaseModel):
    upload: MediaUploadResult
    job_id: str


class LibraryUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    estimated_cost_usd: float
    call_count: int


class LibraryDashboard(BaseModel):
    total_dramas: int
    by_status: Dict[str, int]
    by_media_type: Dict[str, int]
    total_lines: int
    translated_lines: int
    usage: LibraryUsage


class LibraryDramaRef(BaseModel):
    id: int
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    status: Optional[str] = None
    updated_at: Optional[str] = None
    media_type: Optional[str] = None


class LibraryRecentResponse(BaseModel):
    items: List[LibraryDramaRef]


class LibraryCostRow(BaseModel):
    id: int
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    translation_engine: Optional[str] = None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    estimated_cost_usd: float
    call_count: int


class LibraryCostResponse(BaseModel):
    items: List[LibraryCostRow]


class LibrarySeries(BaseModel):
    id: int
    name: str
    character_count: int
    glossary_term_count: int
    dramas: List[LibraryDramaRef]


class LibrarySeriesResponse(BaseModel):
    items: List[LibrarySeries]


class LibrarySearchHit(BaseModel):
    drama_id: int
    idx: int
    zh: Optional[str] = None
    en: Optional[str] = None
    title_en: Optional[str] = None
    title_zh: Optional[str] = None


class LibrarySearchResponse(BaseModel):
    count: int
    items: List[LibrarySearchHit]


class LibraryHistoryEntry(BaseModel):
    drama_id: int
    line_idx: Optional[int] = None
    percent_complete: Optional[float] = None
    accessed_at: Optional[str] = None
    title_en: Optional[str] = None
    title_zh: Optional[str] = None


class LibraryHistoryResponse(BaseModel):
    items: List[LibraryHistoryEntry]


class LibraryPreset(BaseModel):
    id: int
    name: str
    translation_engine: Optional[str] = None
    engine_model: Optional[str] = None
    style_preset: Optional[str] = None
    locale: Optional[str] = None
    default_female_pronouns: Optional[int] = None
    include_genre_notes: Optional[int] = None


class LibraryPresetsResponse(BaseModel):
    items: List[LibraryPreset]


class LibraryVoice(BaseModel):
    id: int
    name: str
    language: Optional[str] = None
    clone_engine: Optional[str] = None
    source_drama: Optional[str] = None
    source_speaker: Optional[str] = None
    clip_available: bool


class LibraryVoiceBankResponse(BaseModel):
    items: List[LibraryVoice]


class LibraryRename(BaseModel):
    """Rename a preset or voice-bank entry; nothing else changes."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class MediaAnalysis(BaseModel):
    """Numbers/booleans only (Migration Slice 37); never a path."""
    drama_id: int
    duration_seconds: float
    has_video: bool
    has_audio: bool
    audio_track_count: int
    sample_rate: Optional[int] = None


class AutofillRequest(BaseModel):
    """Exactly one of url / page_text. No keys: resolved server-side."""
    model_config = ConfigDict(extra="forbid")
    url: Optional[str] = Field(None, max_length=2000)
    page_text: Optional[str] = Field(None, max_length=200000)
    engine: Optional[str] = Field(None, max_length=40)


class AutofillSuggestion(BaseModel):
    drama_id: int
    suggestion: dict[str, str]
    found: bool


class AutofillApply(BaseModel):
    """Whitelisted suggestion fields only; unknown keys are a 422."""
    model_config = ConfigDict(extra="forbid")
    title_en: Optional[str] = Field(None, max_length=300)
    title_zh: Optional[str] = Field(None, max_length=300)
    author: Optional[str] = Field(None, max_length=300)
    studio: Optional[str] = Field(None, max_length=300)
    director: Optional[str] = Field(None, max_length=300)
    voice_actors: Optional[str] = Field(None, max_length=300)
    summary: Optional[str] = Field(None, max_length=5000)
    source_url: Optional[str] = Field(None, max_length=2000)


class NovelAttachTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=2_000_000)
    mode: str = Field(default="replace", max_length=10)


class NovelAttachResult(BaseModel):
    char_count: int


class NovelOcrResult(BaseModel):
    job_id: str


class NovelStatus(BaseModel):
    drama_id: int
    has_novel_text: bool
    char_count: int
    chapters: int
    ocr_running: bool
