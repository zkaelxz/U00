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

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

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
    local: bool = Field(description="True when this request would pass a PC-only (local_only) "
                                    "route: the viewer is at the PC. A UI hint only; those "
                                    "routes still enforce it.")


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
    # Parity P10: shown and edited in the Workspace's Edit details.
    source_url: Optional[str] = None
    episode_number: Optional[int] = None
    episode_summary: Optional[str] = None
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
    # A redacted, allowlisted projection of the job's result dict
    # (services/jobs_service.project_result) plus a normalised outcome
    # (ok | failed | cancelled | partial | kept_existing), so a "done" job
    # that actually failed or was cancelled does not look like a success.
    result: Optional[Dict[str, Any]] = None
    outcome: Optional[str] = None
    outcome_message: Optional[str] = None
    # Still queued/running on record, but no owner has heartbeated it for
    # 15 minutes (server clock): left behind by a process that died.
    stale: bool = False


class JobListResponse(BaseModel):
    items: List[JobRecord]
    count: int


class SettingsPreferences(BaseModel):
    """Persisted PC-side preferences (settings parity G05, G08, G09, G13,
    G14, G15). Paths are paths only: a cookies file's contents are never
    read or returned. The four paths are returned only to the PC itself;
    any other caller gets "" there and only the *_configured booleans."""
    default_engine: str
    default_locale: str
    default_style_note: str
    episode_summary_engine: str
    monthly_cap_usd: Optional[float] = None
    ollama_num_ctx_override: int
    whisper_model_path: str
    ocr_backend: str
    ocr_prefer_paddle_vl_manga: bool
    tesseract_cmd: str
    cookies_browser: Optional[str] = None
    cookies_file: str
    lncrawl_cmd: str = ""
    whisper_model_path_configured: bool = False
    tesseract_cmd_configured: bool = False
    cookies_file_configured: bool = False
    lncrawl_cmd_configured: bool = False


class SettingsChoices(BaseModel):
    engines: List[str]
    locales: List[str]
    summary_engines: List[str]
    ocr_backends: List[str]
    cookie_browsers: List[str]


class SettingsOverview(BaseModel):
    """Non-secret settings snapshot (Migration Slice 10) -- engine_keys
    reports only whether a key/endpoint is configured, never its value
    (D2: keys are server-side only). endpoints carries the Ollama,
    LibreTranslate and GPT-SoVITS URLs only when they have no userinfo,
    query or fragment (settings_service.validate_endpoint_url)."""
    engine_keys: dict[str, bool]
    gpu_limit_enabled: bool
    notify_on_completion: bool
    use_gpu: bool = False
    gemini_free_tier: bool = False
    bulk_auto_resume: bool = False
    preferences: SettingsPreferences
    endpoints: Dict[str, Optional[str]]
    monthly_cap_env_usd: float = 0.0
    effective_monthly_cap_usd: float = 0.0
    choices: SettingsChoices


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
    free_tier: Optional[bool] = None  # None: the saved Gemini free-tier setting


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
    # Step 105: the speaker-count range the last run used, if any.
    min_speakers: Optional[int] = None
    max_speakers: Optional[int] = None
    # Step 101: "cuda" or "cpu" -- where the last run's pipeline ran.
    last_device: Optional[str] = None
    audio_available: bool
    manual_speaker_count: int = 0   # parity D06: hand-corrected speakers


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
    # Whisper prompt built from the series glossary and raw-novel excerpt;
    # a run with an empty initial_prompt uses this.
    auto_initial_prompt: str = ""


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
    backend only). The server runs it, so the run route accepts it only from
    the PC itself (403 otherwise); omitted, the path saved in Settings applies."""
    source_language: Optional[str] = None
    chinese_script: Optional[str] = None
    transcript_text: Optional[str] = None
    run_diarize: bool = False
    expected_speakers: Optional[int] = Field(default=None, ge=0, le=20)
    # Step 105: a speaker-count range for the chained speaker detection
    # (pyannote min_speakers/max_speakers); not combined with expected_speakers.
    min_speakers: Optional[int] = Field(default=None, ge=0, le=20)
    max_speakers: Optional[int] = Field(default=None, ge=0, le=20)
    # Non-empty: replaces the automatic prompt entirely. Empty: the server
    # builds glossary names + extra_names + raw-novel excerpt.
    initial_prompt: str = ""
    extra_names: str = Field("", max_length=1000)
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
    # Voice-clone setup: why this speaker won't be cloned as configured
    # (e.g. a clone engine with no clip or voice design falls back to plain TTS).
    clone_warning: Optional[str] = None


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
    can_keep_background: bool = False


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
    `chapter_count`/`episode_number`, 0 clears the value; `series_id` 0
    takes the drama out of its series. `new_series_name` ("+ New series")
    moves it into the series of that name, created for the caller if none
    exists; not together with `series_id`."""
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
    series_id: Optional[int] = Field(default=None, ge=0, le=2147483647)
    new_series_name: Optional[str] = Field(default=None, max_length=300)


class DramaPresetDefaults(BaseModel):
    """A preset's session-only values, returned for the client to hold
    (only the preset's translation engine is persisted on the drama)."""
    style_preset: Optional[str] = None
    locale: Optional[str] = None
    default_female_pronouns: bool
    include_genre_notes: bool
    engine_model: Optional[str] = None  # applies to the drama's saved translation_engine


class DramaCreateResult(DramaDetail):
    preset_defaults: Optional[DramaPresetDefaults] = None


class TranslateRunStylePreset(BaseModel):
    key: str
    label: str
    guidance: str = ""   # parity X04: what this style asks the translator for


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
    default_locale: str = "en-US"
    default_style_note: str = ""
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
    # parity X24; never the URL. None when the drama's engine isn't Ollama
    # (not probed).
    ollama_reachable: Optional[bool] = None


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
    voice_actor: str = ""
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
    # C07/C04: the linked series character's pronouns (the default when
    # this drama sets none) and up to two short sample source lines.
    series_pronouns: str = ""
    sample_lines: List[str] = Field(default_factory=list)


class CharactersUpdateRequest(BaseModel):
    """speaker_label identifies the speaker; every other field is
    optional -- omitted (or null) leaves the stored value alone, an
    explicit "" clears it (character_name can't be blank)."""
    model_config = {"extra": "forbid"}

    speaker_label: str
    character_name: Optional[str] = None
    voice_actor: Optional[str] = None
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


class GlossaryImportRequest(BaseModel):
    """Parity T03: a glossary file's text (CSV, TSV or JSON), pasted or read
    by the browser; filename only hints the format. overwrite_existing
    needs confirm=true."""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1_000_000)
    filename: str = Field(default="", max_length=255)
    overwrite_existing: StrictBool = False
    confirm: StrictBool = False


class GlossaryImportResult(BaseModel):
    added: List[str]
    overwritten: List[str]
    skipped_existing: List[str]
    invalid: List[str]
    warnings: List[str]


class GlossaryBulkDeleteRequest(BaseModel):
    """Parity X13: term ids (never positions); needs confirm=true."""
    model_config = ConfigDict(extra="forbid")
    term_ids: List[StrictInt] = Field(min_length=1, max_length=1000)
    confirm: StrictBool = False


class GlossaryBulkDeleteResult(BaseModel):
    deleted: List[int]
    not_found: List[int]


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
    """Non-secret Settings writes (Migration Slice 23; preferences added for
    settings parity). Unknown fields are rejected; keys and endpoint URLs
    are never accepted here (they have their own guarded routes).
    settings_service.set_settings re-validates every value. For
    monthly_cap_usd, null clears the saved cap (the .env value applies)."""
    model_config = ConfigDict(extra="forbid")
    gpu_limit_enabled: Optional[StrictBool] = None
    notify_on_completion: Optional[StrictBool] = None
    use_gpu: Optional[StrictBool] = None
    gemini_free_tier: Optional[StrictBool] = None
    bulk_auto_resume: Optional[StrictBool] = None
    default_engine: Optional[StrictStr] = Field(None, max_length=40)
    default_locale: Optional[StrictStr] = Field(None, max_length=8)
    default_style_note: Optional[StrictStr] = Field(None, max_length=2000)
    episode_summary_engine: Optional[StrictStr] = Field(None, max_length=40)
    monthly_cap_usd: Optional[Union[StrictInt, StrictFloat]] = None
    ollama_num_ctx_override: Optional[StrictInt] = None
    whisper_model_path: Optional[StrictStr] = Field(None, max_length=1024)
    ocr_backend: Optional[StrictStr] = Field(None, max_length=40)
    ocr_prefer_paddle_vl_manga: Optional[StrictBool] = None
    tesseract_cmd: Optional[StrictStr] = Field(None, max_length=1024)
    cookies_browser: Optional[StrictStr] = Field(None, max_length=40)
    cookies_file: Optional[StrictStr] = Field(None, max_length=1024)
    lncrawl_cmd: Optional[StrictStr] = Field(None, max_length=1024)


class DramaDeleteResult(BaseModel):
    deleted: bool
    drama_id: int
    warning: Optional[str] = None


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
    """expected_en: the line's English the client saw (409 if it changed)."""
    model_config = ConfigDict(extra="forbid")
    entry_id: int = Field(ge=1)
    expected_en: str = Field(max_length=20000)


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
    # B-09: set for a video -- the background audio-extraction job to poll.
    job_id: Optional[str] = None


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
    keep_background: bool = False


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
    gemini_free_tier: Optional[bool] = None  # None: the saved setting
    job_cost_cap_usd: Optional[float] = Field(None, ge=0)
    fallback_chain: Optional[List["TranslateFallbackEngine"]] = Field(None, max_length=2)
    reflect: bool = False  # Slice 41: Step 7's three-pass Reflect mode
    bulk: bool = False  # Slice 41: batch API / DeepSeek off-peak, job bulk_translate_{id}
    # A preset's prompt toggles; None = the tab's defaults (she/her off, genre notes on).
    default_female_pronouns: Optional[bool] = None
    include_genre_notes: Optional[bool] = None


class TranslateRunStarted(BaseModel):
    job_id: str
    drama_id: int
    engine: str
    model: Optional[str] = None
    target_line_count: int
    fallback_engines: List[str] = []
    reflect: bool = False
    bulk: bool = False


class TranslateFallbackEngine(BaseModel):
    """One entry of TranslateRunStart.fallback_chain (Step 97b)."""
    model_config = ConfigDict(extra="forbid")
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(None, max_length=200)


class GlossaryAffectedTerm(BaseModel):
    id: int
    term_original: str
    term_translation: str


class GlossaryAffectedMatch(BaseModel):
    term_id: int
    term_original: str
    term_translation: str
    # "source": the term or an alias is in the source text; "banned": the
    # English uses one of the term's banned translations.
    reason: Literal["source", "banned"]


class GlossaryAffectedLine(BaseModel):
    id: int
    idx: int
    start: Optional[float] = None
    end: Optional[float] = None
    zh: str
    en: str
    # True unless the English is exactly what the last recorded translate
    # run produced (unknown provenance counts as hand-edited).
    hand_edited: bool
    matched_terms: List[GlossaryAffectedMatch]


class GlossaryAffectedPreview(BaseModel):
    """Lines the glossary affects, for re-translating just those. No engine
    call is made; the estimates are the Translate stage's own."""
    drama_id: int
    has_glossary: bool
    terms: List[GlossaryAffectedTerm]
    selected_term_ids: List[int]
    lines: List[GlossaryAffectedLine]
    hand_edited_count: int
    preview_hash: str
    estimate: TranslateRunEstimate
    estimate_with_hand_edited: TranslateRunEstimate


class GlossaryAffectedRunStart(BaseModel):
    """Re-translate the chosen affected lines. line_ids and preview_hash come
    from the preview; the server recomputes the set and refuses a stale one."""
    model_config = ConfigDict(extra="forbid")
    line_ids: List[int] = Field(min_length=1, max_length=100000)
    preview_hash: str = Field(min_length=1, max_length=64)
    include_hand_edited: bool = False
    term_ids: Optional[List[int]] = Field(None, max_length=10000)
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=200)
    style_preset: Optional[str] = Field(None, max_length=40)
    style_note: str = Field("", max_length=4000)
    locale: str = Field("en-US", max_length=10)
    context_window: Optional[int] = Field(None, ge=0, le=100)
    context_window_ahead: Optional[int] = Field(None, ge=0, le=100)
    batch_size: Optional[int] = Field(None, ge=1, le=200)
    gemini_free_tier: Optional[bool] = None
    job_cost_cap_usd: Optional[float] = Field(None, ge=0)
    fallback_chain: Optional[List[TranslateFallbackEngine]] = Field(None, max_length=2)
    reflect: bool = False
    default_female_pronouns: Optional[bool] = None
    include_genre_notes: Optional[bool] = None


class GlossaryAffectedRunStarted(TranslateRunStarted):
    line_ids: List[int]
    skipped_hand_edited_count: int


TranslateRunStart.model_rebuild()


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
    # Parity P05: from media_inspect (the tab's media analysis).
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    subtitle_tracks: List["MediaSubtitleTrack"] = Field(default_factory=list)
    suggested_pipeline: List[str] = Field(default_factory=list)  # advisory; nothing is applied
    # A media type value (streamer_vod / asmr / audio_drama / video_drama) and
    # a one-line reason, from filename keywords and track shape; applied only
    # when the user picks "Use this content type".
    content_type_guess: Optional[str] = None
    content_type_reason: Optional[str] = None


class MediaSubtitleTrack(BaseModel):
    index: Optional[int] = None
    codec: str
    language: Optional[str] = None


MediaAnalysis.model_rebuild()


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
    # EPUB attach only: the chapters found and the range that was used (1-based, inclusive).
    epub_chapters: Optional[int] = None
    chapter_from: Optional[int] = None
    chapter_to: Optional[int] = None


class NovelAttachFromSourcesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="replace", max_length=10)


class NovelOcrResult(BaseModel):
    job_id: str


class NovelStatus(BaseModel):
    drama_id: int
    has_novel_text: bool
    char_count: int
    chapters: int
    ocr_running: bool


class ReviewJobStart(BaseModel):
    """Start a Review-stage AI job (Migration Slice 44). No keys/URLs."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=200)
    gemini_free_tier: Optional[bool] = None  # None: the saved setting
    # Parity R49: half price through Claude's/Gemini's batch API; results
    # arrive later and are applied by line id. Not for fix-flagged.
    bulk: StrictBool = False


class EmotionJobStart(ReviewJobStart):
    use_audio_cues: Optional[bool] = None


class FixFlaggedJobStart(ReviewJobStart):
    job_cost_cap_usd: Optional[float] = Field(None, ge=0)
    include_genre_notes: StrictBool = True
    default_female_pronouns: StrictBool = False
    bulk: Literal[False] = False   # there is no batch variant of fix-flagged


class ReviewJobStarted(BaseModel):
    job_id: str
    drama_id: int
    kind: str
    engine: str
    model: Optional[str] = None
    line_count: int
    bulk: bool = False


class MediaExportStarted(BaseModel):
    """Audiobook / burned-in video export job started (Migration Slices 29-30).
    Poll GET /api/jobs/{job_id}; download via GET /api/artifacts/dramas/{id}/{kind}."""
    job_id: str


class SoftsubVideoRequest(BaseModel):
    """Parity E17: which subtitles go into the muxed track."""
    model_config = ConfigDict(extra="forbid")
    field: str = Field(default="en", pattern="^(en|zh|bilingual)$")
    include_notes: StrictBool = False


class DubbedVideoRequest(BaseModel):
    """Parity E19: keep_original mixes the original audio in at -20 dB
    instead of replacing it."""
    model_config = ConfigDict(extra="forbid")
    keep_original: StrictBool = False


class MarkExportedResult(BaseModel):
    """Parity E22: the drama's status after "Mark as exported"."""
    drama_id: int
    status: str


class TranslateBulkResumeEntry(BaseModel):
    bulk_job_id: int
    state: str  # "polling" | "needs_key" | "running"


class TranslateBulkResumeResult(BaseModel):
    """Pending bulk jobs picked back up after a restart (Migration Slice 41)."""
    drama_id: int
    jobs: List[TranslateBulkResumeEntry]


# ---------------------------------------------------------------------------
# Migration Slice 45: restructure lines + version-history restore
# ---------------------------------------------------------------------------

class _RestructureBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_line_ids: List[int] = Field(
        max_length=100_000,
        description="The drama's line ids, in order, as last loaded; 409 if they differ now.")


class RestructureAddLine(_RestructureBase):
    after_line_id: Optional[int] = Field(None, ge=1, description="None = insert at the start.")
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    zh: str = Field(default="", max_length=2000)
    en: str = Field(default="", max_length=2000)
    speaker: Optional[str] = Field(None, max_length=100)


class RestructureDeleteLine(_RestructureBase):
    confirm: StrictBool = False


class RestructureMerge(_RestructureBase):
    line_ids: List[int] = Field(min_length=2, max_length=50)


class RestructureSplit(_RestructureBase):
    at_char: int = Field(ge=1)
    expected_zh: str = Field(max_length=2000)
    at_time: Optional[float] = Field(None, ge=0)
    en_at_char: Optional[int] = Field(None, ge=1)


class RestructureResult(BaseModel):
    line_ids: List[int]
    lines: List[ReviewLinesLine]


class ResegmentChange(BaseModel):
    line_id: Optional[int] = None
    idx: int
    zh: str
    pieces: List[str]


class ResegmentPreview(BaseModel):
    drama_id: int
    source_line_ids: List[int]
    line_count_before: int
    line_count_after: int
    changed: List[ResegmentChange]
    translated: int
    flagged: int
    notes: int
    needs_confirm: bool


class ResegmentStart(_RestructureBase):
    confirm: StrictBool = False
    use_llm: StrictBool = False
    # Parity R47: commit the stored LLM preview as shown (no LLM call).
    use_preview: StrictBool = False
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=200)


class ResegmentStarted(BaseModel):
    job_id: str
    drama_id: int


class ResegmentLlmPreviewStart(BaseModel):
    """Parity R47: start an LLM re-segmentation preview (writes no lines)."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=200)


class ResegmentLlmPreview(ResegmentPreview):
    engine: str


class RestoreVersionRequest(_RestructureBase):
    pass


class RestoreVersionResult(BaseModel):
    history_id: int
    line_ids: List[int]


class EngineKeySetRequest(BaseModel):
    """Write-only engine key (Migration Slice 24). `value` is a secret:
    it is never echoed back and validation errors never include it."""
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., repr=False)
    confirm: StrictBool = False


class EngineKeyClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class EngineKeyResult(BaseModel):
    engine: str
    configured: bool


class EndpointUrlSetRequest(BaseModel):
    """Ollama / LibreTranslate / GPT-SoVITS URL (settings parity G06). An
    http(s) URL with no userinfo, query or fragment."""
    model_config = ConfigDict(extra="forbid")
    url: str = Field(..., max_length=300)
    confirm: StrictBool = False


class EndpointUrlResult(BaseModel):
    name: str
    url: Optional[str] = None
    configured: bool


class LineExplainRequest(BaseModel):
    """Per-line AI helper request (Migration Slice 50). No keys/URLs."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=200)
    gemini_free_tier: Optional[bool] = None  # None: the saved setting


class LineImproveRequest(LineExplainRequest):
    issue: str = Field("", max_length=500)


class LineImprovement(BaseModel):
    line_id: int
    current_en: str
    suggestion: str
    changed: bool
    engine: str
    model: Optional[str] = None


class LineExplanation(BaseModel):
    line_id: int
    explanation: str
    engine: str
    model: Optional[str] = None


# --- Review per-line tools (review parity R17/R18/R28, R08/R43) ----------

class LineAlternative(BaseModel):
    translation: str
    approach: str
    tradeoff: str


class LineAlternatives(BaseModel):
    line_id: int
    current_en: str
    alternatives: List[LineAlternative]
    engine: str
    model: Optional[str] = None


class LineGrammarPart(BaseModel):
    word: str
    reading: str
    meaning: str
    function: str


class LineGrammar(BaseModel):
    line_id: int
    zh: str
    parts: List[LineGrammarPart]
    engine: str
    model: Optional[str] = None


class LinesShortenRequest(LineExplainRequest):
    """Auto-shorten overlong lines. line_ids: only these (still only the
    ones the pacing check calls too long); omitted = every such line.
    confirm must be true: it overwrites English."""
    line_ids: Optional[List[int]] = Field(None, max_length=1000)
    confirm: StrictBool = False


class LinesShortenedLine(BaseModel):
    id: int
    idx: int
    before: str
    after: str


class LinesShortenResult(BaseModel):
    shortened: int
    unchanged: int
    stale: int
    remaining: int
    snapshot_saved: bool
    lines: List[LinesShortenedLine]


class ReviewLinePosition(BaseModel):
    """page: in the requested filter view (None if it hides the line);
    page_all: with no filter. All None when there's no such line."""
    line_id: Optional[int] = None
    idx: Optional[int] = None
    page: Optional[int] = None
    page_all: Optional[int] = None


# --- Discover catalog (Migration Slice 55) ---------------------------------

class KnownTitle(BaseModel):
    id: int
    title_original: Optional[str] = None
    title_en: Optional[str] = None
    author: Optional[str] = None
    tags: Optional[str] = None
    summary_en: Optional[str] = None
    summary_original: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    language: Optional[str] = None
    media_type: Optional[str] = None
    created_at: Optional[str] = None


class KnownTitleList(BaseModel):
    titles: List[KnownTitle]
    total: int


class KnownTitleCreate(BaseModel):
    """Whitelisted manual-add fields; unknown fields are 422."""
    model_config = ConfigDict(extra="forbid")
    title_original: str = Field(max_length=300)
    title_en: str = Field("", max_length=300)
    author: str = Field("", max_length=300)
    tags: str = Field("", max_length=500)
    summary_en: str = Field("", max_length=5000)
    summary_original: str = Field("", max_length=5000)
    source_name: str = Field("", max_length=100)
    source_url: str = Field("", max_length=2000)
    language: str = Field(max_length=10)
    media_type: str = Field(max_length=40)


class KnownTitleDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class KnownTitleDeleted(BaseModel):
    deleted: bool
    id: int


class KnownTitleSeedResult(BaseModel):
    added: int
    total: int


class DiscoverPlatforms(BaseModel):
    platforms: List[Dict[str, Any]]


class DiscoverSearchLinks(BaseModel):
    links: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Sources registry and status (Migration Slice 56, S-1). Read-only; S-2 adds
# the write request models below. No proxy URL, path or query string is ever
# part of these shapes.
# ---------------------------------------------------------------------------

class SourceSupports(BaseModel):
    search: bool
    get_series: bool
    get_chapters: bool
    get_pages: bool
    download_page: bool
    get_chapter_text: bool
    get_audio_url: bool
    login: bool


class SourceSummary(BaseModel):
    name: str
    display_name: str
    content_types: List[str]
    languages: List[str]
    supports: SourceSupports
    import_supported: bool
    auth_supported: bool
    supports_adult_toggle: bool
    enabled: bool
    adult_enabled: bool
    health: str = Field(description="green, yellow or red.")
    has_saved_signin: bool


class SourceHealth(BaseModel):
    light: str
    consecutive_failures: int
    last_success: Optional[float] = None
    last_failure: Optional[float] = None
    last_error_type: Optional[str] = None
    last_error: Optional[str] = None
    last_latency: Optional[float] = None
    unavailable_until: Optional[float] = None
    retry_after: Optional[float] = None


class SourceTierResult(BaseModel):
    tested: bool
    ok: bool
    reason: Optional[str] = None
    detail: Optional[str] = None
    at: Optional[float] = None


class SourceDetail(SourceSummary):
    status: str
    technical_status: str
    access_method: Optional[str] = None
    content_access_status: str
    authentication_required: str
    purchase_required: str
    technical_protection: str
    automation_permission: str
    ai_ml_use: str
    tiers: Dict[str, SourceTierResult]
    technical: Dict[str, Any]
    terms: Dict[str, Any] = Field(description="Recorded findings, information only. "
                                  "Enforcement is off: never read this as permitted.")
    terms_enforced: bool
    health_detail: SourceHealth


class SourceAttempt(BaseModel):
    url: str = Field(description="scheme+host+path only.")
    created_at: Optional[float] = None
    tier: Optional[str] = None
    test_now: bool = False
    ok: Optional[bool] = None
    technical_status: Optional[str] = None
    capability_status: Optional[str] = None
    reasons: List[str] = []
    lines: List[str] = []
    handoff: Optional[Dict[str, Any]] = None


class SourceCacheStats(BaseModel):
    entries: int
    bytes: int


class SourcesSettings(BaseModel):
    pace_min_delay: float
    pace_max_delay: float
    max_concurrent: int
    max_retries: int
    session_break_min_requests: int
    session_break_max_requests: int
    session_break_min_delay: float
    session_break_max_delay: float
    cache_mode: str
    cache_max_mb: int = Field(0, description="Kept-cache size ceiling in MB; 0 = no limit.")
    check_interval_hours: int
    auto_queue_new_chapters: bool
    demo_source_enabled: bool
    extraction_diagnostics: bool
    proxy_configured: bool = Field(description="Whether a proxy is set. The URL is never returned.")
    cache_modes: List[str]
    cache: SourceCacheStats


class SourceProfileVersion(BaseModel):
    version: Optional[int] = None
    kind: Optional[str] = None
    status: Optional[str] = None
    origin: Optional[str] = None
    created_at: Optional[float] = None
    approved: bool = False
    failures: int = 0
    last_failure_reason: Optional[str] = None
    last_used: Optional[float] = None


class SourceProfileDomain(BaseModel):
    domain: str
    versions: List[SourceProfileVersion]


class TrackedSeries(BaseModel):
    source: str
    series_id: str
    title: str
    url: str
    drama_id: Optional[int] = None
    last_checked: Optional[float] = None
    last_check_error: Optional[str] = None


class SourceNotification(BaseModel):
    id: int
    source: str
    series_id: str
    chapter_id: str
    title: Optional[str] = None
    created_at: float
    dismissed: bool


# Sources config writes (Migration Slice 56, S-2).

class SourceToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class SourcesSettingsUpdate(BaseModel):
    """Partial update. `extra=forbid`: http_proxy_url, page_server_enabled and
    any unknown key are rejected (422). Ranges match the Streamlit form;
    pace_min_delay also has a floor at the built-in default (service check)."""
    model_config = ConfigDict(extra="forbid")
    pace_min_delay: Optional[float] = None
    pace_max_delay: Optional[float] = None
    max_concurrent: Optional[int] = None
    max_retries: Optional[int] = None
    session_break_min_requests: Optional[int] = None
    session_break_max_requests: Optional[int] = None
    session_break_min_delay: Optional[float] = None
    session_break_max_delay: Optional[float] = None
    cache_mode: Optional[str] = Field(None, max_length=40)
    cache_max_mb: Optional[int] = None
    check_interval_hours: Optional[int] = None
    auto_queue_new_chapters: Optional[StrictBool] = None
    demo_source_enabled: Optional[StrictBool] = None
    extraction_diagnostics: Optional[StrictBool] = None


class SourceCacheClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class SourceProfileRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class SourceTrackRequest(BaseModel):
    """Track (`tracked=true`) or untrack one series. Fetches nothing."""
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=60)
    series_id: str = Field(min_length=1, max_length=200)
    tracked: StrictBool = True
    title: str = Field("", max_length=300)
    url: str = Field("", max_length=1000)
    drama_id: Optional[int] = Field(None, ge=1)


# ---------------------------------------------------------------------------
# Migration Slice 51: pending bulk batch list + cancel
# ---------------------------------------------------------------------------

class TranslateBulkJobEntry(BaseModel):
    """One bulk batch as last recorded (Migration Slice 51). No prompts,
    provider batch id or keys."""
    bulk_job_id: int
    engine: str
    model: Optional[str] = None
    kind: str
    stage: Optional[str] = None
    pipeline_id: Optional[str] = None
    status: str  # submitting|submitted|scheduled|running|applied|cancelled|failed|auth_error
    pending: bool
    cancellable: bool
    line_count: int
    scheduled_for: Optional[str] = None
    result_summary: Optional[dict] = None
    last_error: Optional[str] = None
    submitted_at: Optional[str] = None
    updated_at: Optional[str] = None


class TranslateBulkList(BaseModel):
    drama_id: int
    jobs: List[TranslateBulkJobEntry]


class TranslateBulkCancelResult(BaseModel):
    drama_id: int
    bulk_job: TranslateBulkJobEntry
    message: str


# ---------------------------------------------------------------------------
# API batch 1: workflow progress (GET /api/workflow/dramas/{id}/progress)
# ---------------------------------------------------------------------------

class WorkflowStageState(BaseModel):
    key: str     # source | translate | review | dub | export
    state: str   # done | current | pending | optional | blocked


class WorkflowProgress(BaseModel):
    """Pipeline progress for the React stage bar. `stage_index` is the 7-tab
    scale of `compute_workspace_stage_index` (0-2 source, 3 translate,
    4 review, 6 export; 5/dub is never current). Booleans only, no paths."""
    drama_id: int
    stage_index: int
    stage: str
    line_count: int
    untranslated_count: int
    flagged_count: int
    has_audio: bool
    has_dub_track: bool
    exported: bool
    stages: List[WorkflowStageState]


# ---------------------------------------------------------------------------
# API batch 1: Live capture (spec L-1, polling) -- /api/live/sessions
# ---------------------------------------------------------------------------

class LiveSessionStart(BaseModel):
    """Keys are resolved server-side; no browser cookies over the API.
    Numbers are clamped to the service's ranges (segment 10-60 s, overlap
    0-8 s and at most half the segment, max_minutes 1-240)."""
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000)
    source_language: str = Field("zh", max_length=5)
    whisper_size: str = Field("small", max_length=10)
    segment_seconds: float = 20
    overlap_seconds: float = 3
    engine: Optional[str] = Field(None, max_length=40,
                                  description="None = the Settings default engine (checked as paid).")
    model: Optional[str] = Field(None, max_length=100)
    max_minutes: float = 60
    use_gpu: StrictBool = False


class LiveSessionStarted(BaseModel):
    session_id: str


class LiveCue(BaseModel):
    start: float
    end: float
    text: str
    translated: str


class LiveSessionStatus(BaseModel):
    session_id: str
    status: str   # queued | running | done | error | cancelled
    message: str
    progress: float
    cues: List[LiveCue]
    next_index: int


class LiveSessionSummary(BaseModel):
    session_id: str
    status: str
    engine: Optional[str] = None
    cue_count: int


class LiveSessionStopped(BaseModel):
    session_id: str
    stopping: bool


# ---------------------------------------------------------------------------
# API batch 1: Discover network helpers (spec D-2) -- /api/discover/...
# ---------------------------------------------------------------------------

class DiscoverTranslateQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(max_length=500)
    engine: Optional[str] = Field(None, max_length=40,
                                  description="None = Claude, the Discover default (checked as paid).")


class DiscoverTranslateQueryResult(BaseModel):
    query: str
    translated: str
    engine: str


class DiscoverBaihehubSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(max_length=500)


class DiscoverBaihehubHit(BaseModel):
    title: str
    url: str
    snippet: str


class DiscoverBaihehubResult(BaseModel):
    results: List[DiscoverBaihehubHit]
    fallback_url: str


class DiscoverImportSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2000)
    engine: Optional[str] = Field(None, max_length=40)


class DiscoverImportSuggestion(BaseModel):
    """A suggestion only; nothing is written. Apply it with POST /api/discover/titles."""
    suggestion: Dict[str, str]
    found: bool
    needs_manual: bool
    message: str


class DiscoverBulkExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urls: List[str] = Field(min_length=1, max_length=10)
    source_label: str = Field("", max_length=100)
    engine: Optional[str] = Field(None, max_length=40)


class DiscoverNavigationHelpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2000)
    goal: str = Field(max_length=500)
    target_language: str = Field("English", max_length=20)
    engine: Optional[str] = Field(None, max_length=40)


class DiscoverJobStarted(BaseModel):
    job_id: str
    started: bool


class DiscoverJobResult(BaseModel):
    """`result` is the job's own result once set: bulk extract
    {entries, pages, source_label}; navigation help {labels, steps,
    needs_manual, message}."""
    job_id: str
    status: Optional[str] = None
    progress: float
    message: str
    result: Optional[Dict[str, Any]] = None


class DiscoverBulkEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=300)
    author: str = Field("", max_length=300)
    tags: str = Field("", max_length=500)
    source_url: str = Field("", max_length=2000)
    has_audio_drama: StrictBool = False
    language: str = Field("zh", max_length=10)
    # From the bulk-extract result: the server stores its own full URL for
    # it and ignores source_url (which the result shows without a query).
    entry_id: Optional[str] = Field(None, max_length=40)


class DiscoverBulkCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: List[DiscoverBulkEntry] = Field(min_length=1, max_length=500)
    source_label: str = Field("", max_length=100)


class DiscoverBulkCommitResult(BaseModel):
    added: int
    skipped: int
    ids: List[int]


# ---------------------------------------------------------------------------
# API batch 1: Sources search / series (spec S-3) -- /api/sources/...
# ---------------------------------------------------------------------------

class SourcesSearchRequest(BaseModel):
    """Names and text only, never URLs (adapters build their own)."""
    model_config = ConfigDict(extra="forbid")
    query: str = Field(max_length=200)
    sources: Optional[List[str]] = Field(None, max_length=100)


class SourcesSeriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    series_id: str = Field(max_length=200)


class SourcesJobStarted(BaseModel):
    job_id: str


class SourcesJobResult(BaseModel):
    """`result` (only once done): search {kind, query, cancelled, results,
    errors, per_source_counts} or series {kind, source, series_id, info,
    chapters}. URLs are scheme+host+path only; text is scrubbed. Series
    jobs also carry `source` and `series_id` while queued/running, so a
    page can tell which series the per-source run is for."""
    job_id: str
    status: Optional[str] = None
    progress: Optional[float] = None
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    series_id: Optional[str] = None


# ---------------------------------------------------------------------------
# API batch 1: Diagnostics gaps (Streamlit retirement M1) -- /api/diagnostics/...
# ---------------------------------------------------------------------------

class DiagnosticsSetupPython(BaseModel):
    version: Optional[str] = None
    ok: bool


class DiagnosticsSetupFfmpeg(BaseModel):
    found: bool
    version: Optional[str] = None
    libass: Optional[bool] = Field(default=None, description=(
        "Built with libass (burned-in subtitles); null when ffmpeg is missing or unknown."))


class DiagnosticsSetupJsRuntime(BaseModel):
    found: bool
    name: Optional[str] = None


class DiagnosticsSetupCuda(BaseModel):
    torch_installed: bool
    cuda_available: Optional[bool] = None


class DiagnosticsSetupFiles(BaseModel):
    all_present: bool
    missing_top_level: List[str]
    missing_tabs: List[str]


class DiagnosticsSetupChecks(BaseModel):
    """Found/version/name only; never a path."""
    python: DiagnosticsSetupPython
    ffmpeg: DiagnosticsSetupFfmpeg
    js_runtime: DiagnosticsSetupJsRuntime
    cuda: DiagnosticsSetupCuda
    files: DiagnosticsSetupFiles
    library_writable: bool


class DiagnosticsHfCacheEntry(BaseModel):
    repo_id: str
    repo_type: str
    revision: str
    size_bytes: int


class DiagnosticsPiperVoice(BaseModel):
    voice: str
    size_bytes: int


class DiagnosticsModelFile(BaseModel):
    """One entry of a model folder outside the Hugging Face cache."""
    folder: Literal["torch", "audio_separator"]
    name: str
    size_bytes: int


class DiagnosticsModelCache(BaseModel):
    hf_cache: List[DiagnosticsHfCacheEntry]
    hf_total_bytes: int
    piper_voices: List[DiagnosticsPiperVoice]
    piper_total_bytes: int
    model_files: List[DiagnosticsModelFile]
    model_files_total_bytes: int


class DiagnosticsPyannoteModel(BaseModel):
    model: str
    accessible: bool


class DiagnosticsPyannoteReadiness(BaseModel):
    """Booleans only; the token is never returned."""
    pyannote_installed: bool
    hf_token_configured: bool
    models: Optional[List[DiagnosticsPyannoteModel]] = None
    ready: bool


class DiagnosticsJobHistoryItem(BaseModel):
    job_id: str
    label: str
    status: Optional[str] = None
    description: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    gpu_touching: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_seconds: Optional[float] = None


class DiagnosticsLogTail(BaseModel):
    lines: List[str]


class DiagnosticsSupportReport(BaseModel):
    report: str


class DiagnosticsAdminConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class DiagnosticsUpgradeRequest(BaseModel):
    """confirm=true, and the version the user confirmed (the last update
    check's target); 409 when that check no longer says so."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    target: Optional[StrictStr] = Field(None, max_length=64,
                                        pattern=r"^[0-9][0-9A-Za-z.+!_-]*$")


class DiagnosticsInstallResult(BaseModel):
    package: str
    ok: bool
    output_tail: List[str]
    # A plain-English next step for a known failure (pip's cache unwritable).
    hint: Optional[str] = None


class DiagnosticsPackageInfo(BaseModel):
    name: str
    dist: str
    installed: bool
    # From installed metadata (the real dist, or a known alternate like
    # opencv-python-headless); None when not installed or unreadable.
    installed_version: Optional[str] = None
    min_version: Optional[str] = None     # the app's minimum (requirements files' >=)
    below_min: bool = False
    installable: bool
    powers: str
    approx_mb: Optional[int] = None
    pulls_torch: bool
    source_url: Optional[str] = None
    not_offered_reason: Optional[str] = None
    warning: Optional[str] = None


class DiagnosticsInstallTask(BaseModel):
    id: str
    group: str
    label: str
    help: str
    packages: List[str]
    # package -> "required" | "recommended" | "optional" for this task
    roles: Dict[str, str] = {}
    installed_count: int
    required_missing: List[str] = []
    to_install: List[str]           # missing required + recommended (Install for this task)
    optional_missing: List[str] = []
    approx_mb: int


class DiagnosticsInstallPresets(BaseModel):
    """Install presets by task, plus per-package pip name, approx. size,
    PyPI link and install caveats (GET /api/diagnostics/install-presets)."""
    tasks: List[DiagnosticsInstallTask]
    packages: Dict[str, DiagnosticsPackageInfo]


class DiagnosticsPackageUpdate(BaseModel):
    name: str
    dist: str
    installed_version: Optional[str] = None
    # update | up_to_date | held_back | managed | unknown
    status: str
    latest: Optional[str] = None
    target: Optional[str] = None      # the version Upgrade installs (status "update")
    reason: Optional[str] = None      # what holds a newer release back


class DiagnosticsPackageUpdates(BaseModel):
    """POST /api/diagnostics/package-updates/check: asks PyPI (fixed URL per
    static dist name) only when called; cached in the server process."""
    checked_at: float
    packages: Dict[str, DiagnosticsPackageUpdate]


class DiagnosticsGpuTorchNvidia(BaseModel):
    found: bool
    gpu_name: Optional[str] = None
    driver_version: Optional[str] = None
    # ok | old (works, below CUDA 12.8's own requirement) | too_old | unknown
    status: str
    recommended: Optional[str] = None
    minimum: Optional[str] = None


class DiagnosticsTorchPackage(BaseModel):
    name: str
    version: Optional[str] = None
    build: Optional[str] = None      # "cuda", "cpu", or None (no build tag / not installed)


class DiagnosticsTorchVariant(BaseModel):
    variant: str
    label: str
    index_url: str
    versions: Dict[str, str]
    needs_nvidia: bool


class DiagnosticsTorchVerify(BaseModel):
    torch: Optional[str] = None
    torchvision: Optional[str] = None
    torchaudio: Optional[str] = None
    cuda_build: Optional[str] = None
    cuda_available: Optional[bool] = None
    device: Optional[str] = None
    error: Optional[str] = None


class DiagnosticsGpuTorchStatus(BaseModel):
    """GET /api/diagnostics/gpu-torch: NVIDIA GPU/driver, the installed
    torch family, mismatches and the recommended matched triple. `probe`
    only from POST /api/diagnostics/gpu-torch/check (imports torch in a
    fresh Python)."""
    nvidia: DiagnosticsGpuTorchNvidia
    installed: List[DiagnosticsTorchPackage]
    problems: List[str]
    # missing | mismatched | cpu_on_gpu | recommended | different
    state: str
    python_supported: bool
    recommended: DiagnosticsTorchVariant
    variants: List[DiagnosticsTorchVariant]
    probe: Optional[DiagnosticsTorchVerify] = None


class DiagnosticsGpuTorchSetupRequest(BaseModel):
    """confirm=true; variant is one of the server's fixed variants (omitted:
    CUDA when an NVIDIA GPU answers, else CPU). No version or index is
    accepted from the client."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    variant: Optional[Literal["cu128", "cpu"]] = None


class DiagnosticsGpuTorchSetupResult(DiagnosticsInstallResult):
    variant: str
    verify: Optional[DiagnosticsTorchVerify] = None


class DiagnosticsResetRequest(BaseModel):
    """confirm=true and confirm_text "RESET" (the word the Streamlit button
    made the user type)."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=20)


class DiagnosticsResetResult(BaseModel):
    ok: bool
    reset_at: float


# ---------------------------------------------------------------------------
# API batch 1: browser-extension bridge control (PC only) -- /api/extension/...
# ---------------------------------------------------------------------------

class ExtensionStatus(BaseModel):
    """No port and no token, ever."""
    enabled: bool
    running: bool


class ExtensionEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class ExtensionEnabledResult(BaseModel):
    """`restart_needed`: turned off, but this process still serves the
    endpoint until the API restarts (page_server has no stop)."""
    enabled: bool
    running: bool
    restart_needed: bool


class ExtensionTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class ExtensionToken(BaseModel):
    token: str


class ExtensionEngineSettings(BaseModel):
    """The extension's saved translation engine (inventory G16). `ready`:
    an engine is chosen and its key is configured. Never a key value."""
    engine: Optional[str] = None
    model: Optional[str] = None
    ready: bool
    engines: List[TranslateEngine]


class ExtensionEngineRequest(BaseModel):
    """engine null = OCR only (no translation)."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[StrictStr] = None
    model: Optional[StrictStr] = None


# ---------------------------------------------------------------------------
# Route batch 2B (M4): Reader API over services/reader_service.py
# ---------------------------------------------------------------------------

class ReaderOverview(BaseModel):
    drama_id: int
    length_display: str
    line_count: int
    percent_complete: float
    last_page: int
    last_line_idx: Optional[int] = None


class ReaderProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    chapter_size: int = Field(40, ge=10, le=200)


class ReaderProgress(BaseModel):
    drama_id: int
    last_page: int
    last_line_idx: int
    percent_complete: float


class ReaderNotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str = Field(max_length=100_000)


class ReaderNotes(BaseModel):
    drama_id: int
    notes: str


class ReaderMediaAvailability(BaseModel):
    """What the Watch / listen panel can show -- booleans and labels only.
    React plays the files through /api/media and /api/dub."""
    drama_id: int
    original: Optional[str] = None   # "video" | "audio" | None
    dub: bool
    narration: bool
    caption_tracks: List[str]
    captions_overlay: bool


class ReaderEngineFields(BaseModel):
    """Shared by every LLM request. An omitted engine means Claude (the
    Reader tab's default) and counts as paid for the engine check."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=100)


class ReaderLookupRequest(ReaderEngineFields):
    page: int = Field(ge=1)
    chapter_size: int = Field(40, ge=10, le=200)
    use_llm: StrictBool = False


class ReaderDefinition(BaseModel):
    reading: Optional[str] = None
    definitions: List[str] = []


class ReaderLookupResult(BaseModel):
    drama_id: int
    page: int
    definitions: Dict[str, ReaderDefinition]
    saved: int


class ReaderVocabWord(BaseModel):
    word: str
    reading: Optional[str] = None
    definitions: List[str] = []
    language: Optional[str] = None
    first_seen_line_idx: Optional[int] = None
    export_rich: bool


class ReaderVocabList(BaseModel):
    drama_id: int
    count: int
    words: List[ReaderVocabWord]


class ReaderRichExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    words: List[str] = Field(min_length=1, max_length=500)
    queued: StrictBool = True


class ReaderRichExportResult(BaseModel):
    drama_id: int
    updated: int
    queued: bool
    rich_count: int


class ReaderWhoRequest(ReaderEngineFields):
    name: str = Field(min_length=1, max_length=200)
    up_to_line_idx: Optional[int] = Field(None, ge=0)


class ReaderExplainRequest(ReaderEngineFields):
    phrase: str = Field(min_length=1, max_length=200)
    up_to_line_idx: Optional[int] = Field(None, ge=0)


class ReaderRecapRequest(ReaderEngineFields):
    page: int = Field(ge=1)
    chapter_size: int = Field(40, ge=10, le=200)


class ReaderScopedLlmRequest(ReaderEngineFields):
    """Relationships and wiki update: None = no spoiler limit."""
    up_to_line_idx: Optional[int] = Field(None, ge=0)


class ReaderWikiUpdateRequest(ReaderScopedLlmRequest):
    """from_line_idx: resume point (the previous call's next_line_idx)."""
    from_line_idx: int = Field(0, ge=0)


class ReaderAnswer(BaseModel):
    drama_id: int
    answer: Optional[str] = None


class ReaderRecap(BaseModel):
    drama_id: int
    summary: Optional[str] = None
    truncated: bool = False   # only the most recent lines before the page were used


class ReaderRelationshipMap(BaseModel):
    drama_id: int
    characters: List[Dict[str, Any]]
    relationships: List[Dict[str, Any]]
    mermaid: str


class ReaderWikiEntry(BaseModel):
    id: Optional[int] = None
    entry_type: Optional[str] = None
    name: Optional[str] = None
    aliases: Optional[Any] = None
    description: Optional[str] = None
    attributes: Dict[str, Any] = {}
    first_seen_line_idx: Optional[int] = None
    known_through_line_idx: Optional[int] = None


class ReaderWikiList(BaseModel):
    drama_id: int
    entry_types: List[str]
    entries: List[ReaderWikiEntry]


class ReaderWikiUpdateResult(BaseModel):
    """One bounded batch. While `remaining` > 0, call again with
    from_line_idx = next_line_idx."""
    drama_id: int
    updated: int
    remaining: int = 0
    next_line_idx: Optional[int] = None


class ReaderWikiClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class ReaderWikiClearResult(BaseModel):
    drama_id: int
    cleared: bool


class ReaderChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=20_000)


class ReaderAskRequest(ReaderEngineFields):
    question: str = Field(min_length=1, max_length=2000)
    chat_history: List[ReaderChatTurn] = Field(default_factory=list, max_length=40)


# ---------------------------------------------------------------------------
# PC-only delete routes (migration handoff "Next queue" item 2)
# ---------------------------------------------------------------------------

class DeleteConfirm(BaseModel):
    """Body of every PC-only delete: the Streamlit buttons are gated by a
    plain Confirm checkbox, so `confirm: true` (strict) is the whole bar."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class MediaRemoveResult(BaseModel):
    drama_id: int
    removed: bool
    audio_file_removed: bool
    video_file_removed: bool
    has_audio: bool
    has_video: bool


class RawNovelRemoveResult(BaseModel):
    drama_id: int
    removed: bool
    has_raw_novel_context: bool


class TranslationVersionDeleteResult(BaseModel):
    drama_id: int
    version_id: int
    deleted: bool
    was_active: bool


class SeriesCharacterDeleteResult(BaseModel):
    series_id: int
    character_id: int
    deleted: bool


class BugBundleDeleteResult(BaseModel):
    bundle_id: int
    deleted: bool


class PresetDeleteResult(BaseModel):
    preset_id: int
    deleted: bool


class VoiceBankDeleteResult(BaseModel):
    entry_id: int
    deleted: bool


# ---------------------------------------------------------------------------
# Route batch 2C: auto-tune speech splitting + glossary from novel
# (imports kept local to this section so parallel slices don't collide on
# the module's import line)
# ---------------------------------------------------------------------------

from typing import Annotated  # noqa: E402

from pydantic import StrictInt  # noqa: E402

AutotuneCandidateMs = Annotated[StrictInt, Field(ge=300, le=3000)]


class AutotuneRunRequest(BaseModel):
    """candidates default to core.DEFAULT_AUTOTUNE_CANDIDATES_MS; 1-6
    distinct values (the service rejects duplicates)."""
    model_config = ConfigDict(extra="forbid")
    candidates: Optional[List[AutotuneCandidateMs]] = Field(None, min_length=1, max_length=6)
    initial_prompt: str = Field("", max_length=1000)
    extra_names: str = Field("", max_length=1000)


class AutotuneRunResult(BaseModel):
    job_id: str
    candidates: List[int]


class AutotuneCandidateScore(BaseModel):
    candidate_ms: int
    long_lines: int
    total_lines: int


class AutotuneStatus(BaseModel):
    """This drama's auto-tune job as held in this app session. results /
    best_candidate_ms only once status is "done"."""
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    results: Optional[List[AutotuneCandidateScore]] = None
    best_candidate_ms: Optional[int] = None


class AutotuneApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ms: AutotuneCandidateMs


class NovelGlossaryRunResult(BaseModel):
    job_id: str
    engine: str
    paired: bool


class NovelGlossaryProposal(BaseModel):
    term: str
    suggested_translation: str
    category: Optional[str] = None
    policy: Optional[str] = None
    reason: str = ""
    already_in_glossary: bool


class NovelGlossaryStatus(BaseModel):
    """This drama's glossary-from-novel job as held in this app session.
    proposals only once status is "done". Never carries a key."""
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    proposals: Optional[List[NovelGlossaryProposal]] = None
    # Names this run; the apply sends it back (409 if the run was replaced).
    run_id: Optional[str] = None


class NovelGlossaryApplyRequest(BaseModel):
    """Terms are matched by their text against the finished run's
    proposals, never by position. overwrite_existing needs confirm=true."""
    model_config = ConfigDict(extra="forbid")
    terms: List[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=1000)
    overwrite_existing: StrictBool = False
    confirm: StrictBool = False


class NovelGlossaryApplyResult(BaseModel):
    added: List[str]
    overwritten: List[str]
    skipped_existing: List[str]
    unknown: List[str]


# ---------------------------------------------------------------------------
# Route batch 2A: library admin (bulk status/tags/delete/translate, export,
# backup, artifacts, restore, storage) over services/library_admin_service.py
# (imports kept local to this section so parallel slices don't collide on
# the module's import line)
# ---------------------------------------------------------------------------

from enum import Enum  # noqa: E402
from typing import Literal  # noqa: E402

import db as _db  # noqa: E402
import storage as _storage  # noqa: E402
from services.library_admin_service import MAX_BULK_IDS as _MAX_BULK_IDS  # noqa: E402
from services.library_admin_service import STATUSES as _LIBRARY_STATUSES  # noqa: E402

LibraryDramaIds = Annotated[List[Annotated[StrictInt, Field(ge=1, le=2**31 - 1)]],
                            Field(min_length=1, max_length=_MAX_BULK_IDS)]
LibraryStatus = Literal[_LIBRARY_STATUSES]
LibraryListTag = Literal[tuple(_db.ORGANIZATIONAL_TAGS)]
LibraryStoragePreset = Literal[tuple(_storage.STORAGE_QUALITY_PRESETS)]


class LibraryArtifactKind(str, Enum):
    backup = "backup"
    export = "export"
    database = "database"


class LibraryBulkStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    status: LibraryStatus


class LibraryBulkTagRequest(BaseModel):
    """Adds (present=true) or removes one organizational list tag."""
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    tag: LibraryListTag
    present: StrictBool


class LibraryBulkDeleteRequest(BaseModel):
    """Needs confirm=true and confirm_text "DELETE"."""
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=32)


class LibraryBulkTranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    # Omitted: the Settings default English variant.
    default_locale: Optional[str] = Field(None, max_length=5)
    include_genre_notes: StrictBool = True
    default_female_pronouns: StrictBool = False


class LibraryExportRequest(BaseModel):
    """drama_ids omitted: every translated/dubbed/exported drama."""
    model_config = ConfigDict(extra="forbid")
    drama_ids: Optional[LibraryDramaIds] = None


class LibraryBackupRequest(BaseModel):
    """database_only=true: the database snapshot alone (fast, small)."""
    model_config = ConfigDict(extra="forbid")
    database_only: StrictBool = False


class LibraryStorageCleanRequest(BaseModel):
    """Needs confirm=true and confirm_text "CLEAN"."""
    model_config = ConfigDict(extra="forbid")
    preset: LibraryStoragePreset
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=32)


class LibraryBulkItem(BaseModel):
    """One requested drama's outcome. error: not_found, job_running,
    delete_failed or not_translated."""
    drama_id: int
    ok: bool
    error: Optional[str] = None
    message: Optional[str] = None
    warning: Optional[str] = None
    freed_bytes: Optional[int] = None


class LibraryBulkResult(BaseModel):
    results: List[LibraryBulkItem]
    updated: int


class LibraryBulkDeleteResult(BaseModel):
    results: List[LibraryBulkItem]
    deleted: int


class LibraryBulkTranslateSkip(BaseModel):
    drama_id: int
    reason: str


class LibraryBulkTranslateStarted(BaseModel):
    job_id: str
    queued: List[int]
    skipped: List[LibraryBulkTranslateSkip]


class LibraryExportStarted(BaseModel):
    job_id: str
    drama_ids: List[int]
    results: Optional[List[LibraryBulkItem]] = None


class LibraryJobStarted(BaseModel):
    job_id: str


class LibraryArtifactInfo(BaseModel):
    """The newest finished file of one kind. Never a path."""
    kind: LibraryArtifactKind
    name: str
    size: int


class LibraryRestoreDone(BaseModel):
    restored: bool
    sessions_revoked: int


class LibraryStorageCategory(BaseModel):
    key: str
    label: str
    note: str
    bytes: int
    selected: bool


class LibraryStorageDrama(BaseModel):
    drama_id: int
    total_bytes: int
    would_free_bytes: int
    job_running: bool


class LibraryStorageScan(BaseModel):
    """Dry run: nothing is removed."""
    preset: str
    categories_to_clean: List[str]
    total_bytes: int
    reclaimable_bytes: int
    would_free_bytes: int
    categories: List[LibraryStorageCategory]
    per_drama: List[LibraryStorageDrama]


class LibraryStorageCleanResult(BaseModel):
    preset: str
    freed_bytes: int
    results: List[LibraryBulkItem]


# --- Re-transcribe one line (parity audit B1, inventory R23) ---------------

class RetranscribeLineRequest(BaseModel):
    """Optional body. Same prompt rules as TranscribeRunRequest: a non-empty
    initial_prompt replaces the automatic prompt; otherwise the server uses
    glossary names + extra_names + raw-novel excerpt."""
    model_config = ConfigDict(extra="forbid")
    initial_prompt: str = Field("", max_length=1000)
    extra_names: str = Field("", max_length=1000)


class RetranscribeLineResult(BaseModel):
    job_id: str
    drama_id: int
    line_id: int


class RetranscribeApplyRequest(BaseModel):
    """"Use this": job_id is the finished re-transcription; expected_zh and
    expected_proposed are the base_zh and proposed_zh that
    GET .../retranscribe showed. Anything else, or a line changed since the
    job started, is a 409 (nothing written)."""
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(..., min_length=1, max_length=100)
    expected_zh: str = Field(..., max_length=20000)
    expected_proposed: str = Field(..., min_length=1, max_length=2000)


class RetranscribeApplyResult(BaseModel):
    drama_id: int
    line_id: int
    zh: str


class RetranscribeResult(BaseModel):
    """A finished re-transcription's proposal for one line, raw (held in this
    API process only; gone after a restart)."""
    job_id: str
    line_id: int
    status: str
    proposed_zh: str
    base_zh: str


# ---------------------------------------------------------------------------
# Review parity R39/R10: activate a saved translation version
# (services/translation_version_service.py) and retry a content-blocked line
# (services/blocked_retry_service.py)
# ---------------------------------------------------------------------------

class TranslationVersionActivateRequest(BaseModel):
    """Overwrites the current English, so `confirm: true` (strict) is required."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class TranslationVersionActivateResult(BaseModel):
    drama_id: int
    version_id: int
    label: str
    activated: bool
    lines_changed: int
    conflicts: list[int] = []  # line ids edited meanwhile; left as they were


class BlockedRetryRequest(BaseModel):
    """Retry one content-blocked line. Only the engine name: the key, the
    model (the engine's default) and Gemini free tier come from the PC's
    saved settings, so no model id or path can be passed through."""
    model_config = ConfigDict(extra="forbid")
    engine: str = Field("ollama", min_length=1, max_length=40)


class BlockedRetryResult(BaseModel):
    drama_id: int
    line_id: int
    engine: str
    model: Optional[str] = None
    retried: bool = Field(description="True: translated, flag cleared.")
    blocked: bool = Field(description="True: this engine blocked it too; flag note updated.")
    reason: Optional[str] = None
    line: ReviewLinesLine


# ---------------------------------------------------------------------------
# Parity X02/X22: apply a workflow tier, save translate settings as a preset
# (services/translate_run_service.py apply_workflow_tier / save_translate_preset).
# ---------------------------------------------------------------------------

import translate_engines as _translate_engines  # noqa: E402

WorkflowTierKey = Literal[tuple(_translate_engines.WORKFLOW_TIERS)]


class WorkflowTierApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: WorkflowTierKey


class TranslateErrorsDismissed(BaseModel):
    """X01: the last run's failed-batch notice is cleared; lines untouched.
    `dismissed` is False when there was nothing to clear."""
    drama_id: int
    dismissed: bool


class WorkflowTierApplied(BaseModel):
    """The tier's engine is saved on the drama; the rest is for the form.
    Nothing is started."""
    drama_id: int
    tier: str
    label: str
    translation_engine: str
    engine_model: Optional[str] = None
    reflect: bool
    auto_qc: bool


class TranslatePresetApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset_id: StrictInt = Field(ge=1, le=2**31 - 1)


class TranslatePresetApplied(BaseModel):
    """Parity X03: the preset's engine (when set) is saved on the drama; the
    rest is for the form. Nothing is started."""
    drama_id: int
    preset_id: int
    name: str
    translation_engine: Optional[str] = None
    engine_model: Optional[str] = None
    style_preset: Optional[str] = None
    locale: Optional[str] = None
    default_female_pronouns: bool
    include_genre_notes: bool


class TranslatePresetSave(BaseModel):
    """The Translate form's current settings. A taken name is a 409 unless
    overwrite is true."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    translation_engine: str = Field(max_length=40)
    engine_model: Optional[str] = Field(None, max_length=100)
    style_preset: Optional[str] = Field(None, max_length=40)
    locale: Optional[str] = Field(None, max_length=10)
    default_female_pronouns: StrictBool = False
    include_genre_notes: StrictBool = True
    overwrite: StrictBool = False


class TranslatePresetSaved(BaseModel):
    preset: LibraryPreset
    replaced: bool


# --- Step 44: job notifications (Discord / ntfy) ------------------------------

NotificationChannel = Literal["discord", "ntfy"]
NotificationOutcome = Literal["sent", "failed", "refused", "not_configured"]


class NotificationStatus(BaseModel):
    """Configured booleans only: never a webhook URL, host or topic."""
    discord_configured: bool
    ntfy_configured: bool
    ntfy_allow_local: bool


class NotificationChannelSetRequest(BaseModel):
    """Write-only channel URL. `value` is a secret: never echoed back, and
    validation errors never include it."""
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., repr=False)
    confirm: StrictBool = False


class NotificationChannelClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class NotificationChannelResult(BaseModel):
    channel: NotificationChannel
    configured: bool


class NotificationTestResult(BaseModel):
    results: Dict[NotificationChannel, NotificationOutcome]


# ---------------------------------------------------------------------------
# Sources S-4 chapter import (services/sources_import_service.py). Results
# are read with GET /api/sources/jobs/{job_id}/result (SourcesJobResult).
# ---------------------------------------------------------------------------

from pydantic import StrictStr  # noqa: E402

class SourcesChapterImportRequest(BaseModel):
    """Chapter ids only: never chapter objects or URLs (the job re-fetches
    the series' chapter list and keeps these ids). The drama must exist."""
    model_config = ConfigDict(extra="forbid")
    series_id: str = Field(min_length=1, max_length=200)
    chapter_ids: List[StrictStr] = Field(min_length=1, max_length=200)
    drama_id: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Sources S-5 paste-a-URL preview and novel import
# (services/sources_url_service.py, services/sources_import_service.py).
# Results are read with GET /api/sources/jobs/{job_id}/result.
# ---------------------------------------------------------------------------

class SourcesUrlPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(min_length=1, max_length=2000)


class SourcesUrlImportRequest(BaseModel):
    """Novel text only (thin slice). The drama must be a novel drama."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(min_length=1, max_length=2000)
    drama_id: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Workspace video-URL download (services/url_media_service.py); PC-only.
# The job is read with GET /api/jobs/{job_id}.
# ---------------------------------------------------------------------------

class MediaUrlDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(min_length=1, max_length=2000)
    audio_only: StrictBool
    confirm_replace_audio: StrictBool = False


class MediaUrlDownloadStarted(BaseModel):
    job_id: str

# Voice-clone setup (parity audit blocker #7; inventory C01, C03, C09, C13):
# services/voice_clone_service.py. No path, filename or URL anywhere.
# ---------------------------------------------------------------------------

class VoiceCloneExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    max_candidates: int = Field(3, ge=1, le=5)


class VoiceCloneJobStarted(BaseModel):
    job_id: str


class VoiceCloneCandidate(BaseModel):
    """An opaque candidate id (for preview/choose), its time window in the
    drama's audio and the transcript line matched to it ("" if none)."""
    id: str
    start: float
    end: float
    duration: float
    ref_text: str


class VoiceCloneSpeakerCandidates(BaseModel):
    """skip_reason ("too_short", "too_long", "no_segments") and
    closest_duration explain an extraction that found nothing."""
    speaker_label: str
    candidates: List[VoiceCloneCandidate]
    skip_reason: Optional[str] = None
    closest_duration: Optional[float] = None


class VoiceCloneCandidates(BaseModel):
    drama_id: int
    speakers: List[VoiceCloneSpeakerCandidates]


class VoiceCloneRemoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    confirm: StrictBool = False


class VoiceCloneBankSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    notes: str = Field("", max_length=1000)


class VoiceCloneSeriesLinkRequest(BaseModel):
    """series_character_id is required; null unlinks."""
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    series_character_id: Optional[int] = Field(..., ge=1, le=2147483647)

# Parity X15-X17: add and edit a series' people
# (services/series_people_service.py). Responses reuse CharactersSeriesEntry.
# ---------------------------------------------------------------------------

class SeriesPersonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_name: str = Field(max_length=200)
    pronouns: str = Field("", max_length=40)
    aliases: str = Field("", max_length=1000)
    notes: str = Field("", max_length=2000)


class SeriesPersonUpdate(BaseModel):
    """Omitted (or null) leaves a field alone; "" clears it
    (character_name can't be blank)."""
    model_config = ConfigDict(extra="forbid")
    character_name: Optional[str] = Field(None, max_length=200)
    pronouns: Optional[str] = Field(None, max_length=40)
    aliases: Optional[str] = Field(None, max_length=1000)
    notes: Optional[str] = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# Report a problem (services/bug_report_service.py): the React header's
# "Report a problem" dialog. The report is sent as the multipart field
# `report` (JSON matching BugReportClient, max 256 KB) plus an optional
# `screenshot` file (PNG/JPEG, max 5 MB). Client buffers carry no request
# or response bodies, headers, cookies or line text.
# ---------------------------------------------------------------------------

class BugReportRouteVisit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: str = Field(max_length=300)
    at: Optional[str] = Field(None, max_length=40)


class BugReportConsoleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["error", "warn"]
    message: str = Field(max_length=2000)
    at: Optional[str] = Field(None, max_length=40)


class BugReportErrorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["error", "unhandledrejection"]
    message: str = Field(max_length=2000)
    source: Optional[str] = Field(None, max_length=500)
    at: Optional[str] = Field(None, max_length=40)


class BugReportFailedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(max_length=10)
    path: str = Field(max_length=500)
    status: int = Field(ge=0, le=999)
    code: Optional[str] = Field(None, max_length=80)
    at: Optional[str] = Field(None, max_length=40)


class BugReportViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=0, le=100000)
    height: int = Field(ge=0, le=100000)
    dpr: Optional[float] = Field(None, ge=0, le=20)


class BugReportClient(BaseModel):
    """What the browser sends. Lists are the capture module's ring buffers."""
    model_config = ConfigDict(extra="forbid")
    what_happened: str = Field(min_length=1, max_length=5000)
    expected: str = Field("", max_length=5000)
    include_server_log: StrictBool = True
    route: str = Field("", max_length=300)
    route_history: List[BugReportRouteVisit] = Field(default_factory=list, max_length=10)
    console: List[BugReportConsoleEntry] = Field(default_factory=list, max_length=30)
    errors: List[BugReportErrorEntry] = Field(default_factory=list, max_length=30)
    failed_requests: List[BugReportFailedRequest] = Field(default_factory=list, max_length=30)
    app_version: str = Field("", max_length=60)
    api_version: str = Field("", max_length=60)
    environment: str = Field("", max_length=60)
    build_id: str = Field("", max_length=120)
    user_agent: str = Field("", max_length=500)
    viewport: Optional[BugReportViewport] = None
    mode: Literal["pc", "lan", "remote", "unknown"] = "unknown"


class BugReportSaved(BaseModel):
    """`markdown` (for Copy report) includes the server section (commit,
    setup, log tail) only for a caller holding admin.diagnostics; it is
    always saved on the PC. `issue_markdown`, `what_happened`, `expected`
    and `title` are the server-scrubbed texts for the public GitHub issue
    link, which never carries the server section."""
    id: int
    stamp: str
    markdown: str
    issue_markdown: str
    what_happened: str
    expected: str
    title: str


class BugReportText(BaseModel):
    """One saved report's markdown (with the server section)."""
    id: int
    stamp: str
    markdown: str


class BugReportDeleteConfirm(BaseModel):
    """PC-only delete: `stamp` is the folder stamp from the list, so a stale
    list can't delete a different report."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    stamp: str = Field(pattern=r"^\d{8}T\d{6}Z$")


class BugReportListItem(BaseModel):
    id: int
    stamp: str
    created_at: Optional[str] = None
    summary: str
    route: Optional[str] = None
    mode: Optional[str] = None
    has_screenshot: bool
    has_server_log: bool


class BugReportDeleted(BaseModel):
    id: int
    deleted: bool


# ---------------------------------------------------------------------------
# Novel files (parity audit B1 #3/#4): the English novel translation
# reference and the raw original-language novel. Booleans and counts only;
# no filename or path is ever returned.
# ---------------------------------------------------------------------------

class NovelFileStatus(BaseModel):
    drama_id: int
    present: bool
    size_bytes: int
    char_count: int


class NovelFileUploadResult(NovelFileStatus):
    replaced: bool


class NovelReferenceRemoveResult(BaseModel):
    drama_id: int
    removed: bool
    present: bool


class NovelFileTextRequest(BaseModel):
    """Pasted text for the novel reference or raw novel. The route reads
    the body itself, capped at 32 MB, before this is validated."""
    model_config = ConfigDict(extra="forbid")
    text: str


# ---------------------------------------------------------------------------
# Library parity (react-misc-parity): Continue reading shelf, the data-driven
# "All dramas" filter choices, and the PC-only reading-history clear.
# ---------------------------------------------------------------------------

class LibraryContinueEntry(BaseModel):
    drama_id: int
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    percent_complete: Optional[float] = None
    last_page: Optional[int] = None
    last_accessed_at: Optional[str] = None
    has_cover_art: bool


class LibraryContinueResponse(BaseModel):
    items: List[LibraryContinueEntry]


class LibraryFilterOptions(BaseModel):
    studios: List[str]
    authors: List[str]
    voice_actors: List[str]
    custom_tags: List[str]


class ReadingHistoryClearResult(BaseModel):
    cleared: bool
    removed: int


# ---------------------------------------------------------------------------
# Diagnostics parity (react-misc-parity): model-cache delete (Q14) and the
# saved bug-reproduction bundles list.
# ---------------------------------------------------------------------------

class DiagnosticsCacheDeleteResult(BaseModel):
    deleted: bool
    name: str


class DiagnosticsBugBundle(BaseModel):
    """A saved "What happened here?" snapshot; the frozen input is not returned."""
    id: int
    drama_id: int
    drama_title: Optional[str] = None
    line_id: Optional[int] = None
    label: str
    engine: Optional[str] = None
    model: Optional[str] = None
    produced_output: str
    replayed: bool
    replay_output: Optional[str] = None
    reproduced: Optional[bool] = None
    created_at: Optional[str] = None



# ---------------------------------------------------------------------------
# Workspace preamble parity (react-misc-parity): romanize credits (P13) and
# cover art (P14). No filename or path is returned.
# ---------------------------------------------------------------------------

class RomanizeCreditsRequest(BaseModel):
    """`engine` defaults to the drama's translation engine."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(default=None, max_length=40)


class RomanizeCreditsResult(BaseModel):
    drama_id: int
    romanized: Dict[str, str]
    updated: bool


class CoverArtResult(BaseModel):
    drama_id: int
    has_cover_art: bool
    format: str
    width: int
    height: int
    size_bytes: int


# ---------------------------------------------------------------------------
# Characters extras (inventory C02, C08): recurring-voice suggestions and
# "remember as a known series character" (services/characters_service.py).
# ---------------------------------------------------------------------------

class CharactersVoiceSuggestion(BaseModel):
    speaker_label: str
    series_character_id: int
    character_name: str
    similarity: float


class CharactersVoiceSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=100)
    series_character_id: int = Field(ge=1, le=2147483647)


class CharactersVoiceSuggestionResult(BaseModel):
    """character: the updated speaker after an accept (null after a
    reject); suggestions: what is still offered."""
    character: Optional[CharactersEntry] = None
    suggestions: List[CharactersVoiceSuggestion]


class CharactersRememberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)


class CharactersRememberResult(BaseModel):
    character: CharactersEntry
    series_character: CharactersSeriesEntry
    created: bool

# Glossary helpers (parity X10/X28): glossary from the drama's source lines,
# and per-term edits on applying either extraction's proposals.
# ---------------------------------------------------------------------------

class LinesGlossaryRunResult(BaseModel):
    job_id: str
    engine: str
    line_count: int


class GlossaryProposalEdit(BaseModel):
    """The user's edit of one proposal in review. A field left out keeps
    the proposal's value; category/policy null means "none"."""
    model_config = ConfigDict(extra="forbid")
    translation: Optional[Annotated[str, Field(min_length=1, max_length=200)]] = None
    category: Optional[Annotated[str, Field(max_length=50)]] = None
    policy: Optional[Annotated[str, Field(max_length=50)]] = None


class GlossaryProposalsApplyRequest(NovelGlossaryApplyRequest):
    """NovelGlossaryApplyRequest plus optional edits keyed by term text
    (never by position); edits for terms not in `terms` are ignored.
    run_id: the status's run_id the user reviewed; a different held run is
    refused with 409 (optional here for older from-novel callers)."""
    overrides: Dict[Annotated[str, Field(min_length=1, max_length=200)], GlossaryProposalEdit] = Field(
        default_factory=dict, max_length=1000)
    run_id: Optional[Annotated[str, Field(min_length=1, max_length=64)]] = None


class LinesGlossaryApplyRequest(GlossaryProposalsApplyRequest):
    """GlossaryProposalsApplyRequest with run_id required (from-lines)."""
    run_id: Annotated[str, Field(min_length=1, max_length=64)]


class GlossaryRunCancelRequest(BaseModel):
    """The run_id from the extraction's status: only that run is cancelled."""
    model_config = ConfigDict(extra="forbid")
    run_id: Annotated[str, Field(min_length=1, max_length=64)]

# Review AI extras (inventory R46, R37, R35, R03): auto-merge short lines,
# learn my style, SenseVoice audio tags, burned-subtitle preview clip.
# services/review_extras_service.py; no key, URL or path is accepted or returned.
# ---------------------------------------------------------------------------

class MergeShortOptions(BaseModel):
    min_duration: float
    max_gap: float
    max_chars: int


class MergeShortGroup(BaseModel):
    line_id: int
    idx: int
    merged_line_ids: List[int]
    start: float
    end: float
    zh: str
    en: str


class MergeShortPreview(BaseModel):
    drama_id: int
    options: MergeShortOptions
    source_line_ids: List[int]
    line_count_before: int
    line_count_after: int
    groups: List[List[int]]
    merges: List[MergeShortGroup]


class MergeShortApply(_RestructureBase):
    expected_groups: List[List[int]] = Field(
        max_length=50_000,
        description="The preview's `groups`; 409 if a fresh merge would differ.")
    min_duration: Optional[float] = Field(None, ge=0.1, le=10)
    max_gap: Optional[float] = Field(None, ge=0, le=5)
    max_chars: Optional[int] = Field(None, ge=10, le=500)


class MergeShortResult(RestructureResult):
    merged_groups: int


class StyleProfileView(BaseModel):
    summary: str
    confidence: Optional[str] = None
    preferences: List[str]
    sample_count: int
    updated_at: Optional[str] = None
    applied: bool


class StyleHistoryEntry(BaseModel):
    summary: str
    preference_count: int
    updated_at: Optional[str] = None


class StyleState(BaseModel):
    drama_id: int
    scope: str
    edit_count: int
    drama_edit_count: int
    min_samples: int
    profile: Optional[StyleProfileView] = None
    history: List[StyleHistoryEntry] = []
    message: Optional[str] = None


class StyleLearnRequest(ReviewJobStart):
    pass


class StyleApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apply: StrictBool


class StyleResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class StyleRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: StrictInt = Field(0, ge=0, le=20)


class SenseVoiceStarted(BaseModel):
    job_id: str
    drama_id: int
    line_count: int


class SenseVoiceRow(BaseModel):
    line_id: Optional[int] = None
    idx: int
    text: str
    text_emotion: str
    audio_emotion: str
    audio_events: str
    disagree: bool


class SenseVoiceTags(BaseModel):
    drama_id: int
    installed: bool
    has_audio: bool
    license_note: str
    tagged: int
    disagree: int
    rows: List[SenseVoiceRow]


class BurnPreviewStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: int = Field(ge=1)
    pad_seconds: Optional[float] = Field(None, ge=0, le=5)
    preset: Optional[str] = Field(None, max_length=40)
    style: Optional[AssStyleOverrides] = None
    speaker_colors: Optional[Dict[str, str]] = None
    per_speaker_colors: bool = False
    wrap_chars_en: Optional[int] = Field(default=None, ge=0, le=200)
    wrap_chars_source: Optional[int] = Field(default=None, ge=0, le=200)


class BurnPreviewStarted(BaseModel):
    job_id: str
    drama_id: int
    line_id: int
    start: float
    end: float


class BurnPreviewClip(BaseModel):
    line_id: Optional[int] = None
    idx: Optional[int] = None
    start: Optional[float] = None
    end: Optional[float] = None
    preset: Optional[str] = None
    created_at: Optional[str] = None


class BurnPreviewInfo(BaseModel):
    drama_id: int
    has_video: bool
    ffmpeg_available: bool
    presets: List[str]
    max_clip_seconds: float
    max_pad_seconds: float
    clip: Optional[BurnPreviewClip] = None


# ---------------------------------------------------------------------------
# Sources S-6 sign-in, SO17 tier tests, S-7 check-now, tracked-series drama
# link and the SO18 proxy (services/sources_signin_service.py,
# services/sources_tracking_service.py, services/sources_registry_service.py).
# Jobs are read with GET /api/sources/jobs/{job_id}/result.
# ---------------------------------------------------------------------------

class SourceSigninOpenRequest(BaseModel):
    """`url`: a page on the source's own site ("" = its login page)."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field("", max_length=2000)


class SourceSigninForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class SourceSigninForgetResult(BaseModel):
    source: str
    forgotten: bool
    has_saved_signin: bool


class SourceTierTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Literal["static", "browser", "signed_in"]
    url: StrictStr = Field(min_length=1, max_length=2000)


class SourceTrackedDramaRequest(BaseModel):
    """Which drama a tracked series auto-imports into (null = none)."""
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=60)
    series_id: str = Field(min_length=1, max_length=200)
    drama_id: Optional[int] = Field(None, ge=1)


class SourcesProxyRequest(BaseModel):
    """"" clears it. Never returned: settings carry `proxy_configured` only."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field("", max_length=500)


# Step 80b: clean stop for the installed app (POST /api/system/shutdown).
class ShutdownResponse(BaseModel):
    status: str = Field(description="`stopping`: jobs were asked to stop and the server exits shortly.")
    cancelled_jobs: int = Field(description="How many running or queued jobs were asked to stop.")


# --- Step 115b: import with lightnovel-crawler (external program) -----------

class LncrawlStatus(BaseModel):
    """Booleans only: never the program's path."""
    installed: bool
    path_configured: bool


class LncrawlImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(..., min_length=1, max_length=2000)
    chapters: Literal["all", "first", "last"] = "all"
    count: Optional[StrictInt] = Field(None, ge=1, le=5000)
    mode: Literal["append", "replace"] = "replace"
