"""api/schemas/translate.py -- Translation shapes: the standalone translator,
translate runs, workflow tiers, presets and bulk jobs.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

import translate_engines as _translate_engines

from api.schemas.common import LibraryPreset, TranslateEngine

__all__ = [
    "TranslateEngineListResponse",
    "TranslateHistoryEntry",
    "TranslateHistoryResponse",
    "TranslateRequest",
    "TranslateResponse",
    "ClearHistoryResult",
    "TranslateRunStylePreset",
    "TranslateRunWorkflowTier",
    "TranslateRunDefaults",
    "TranslateRunConfig",
    "TranslateRunEstimate",
    "TranslateRunStart",
    "TranslateRunStarted",
    "TranslateFallbackEngine",
    "GlossaryAffectedTerm",
    "GlossaryAffectedMatch",
    "GlossaryAffectedLine",
    "GlossaryAffectedPreview",
    "GlossaryAffectedRunStart",
    "GlossaryAffectedRunStarted",
    "TranslateBulkResumeEntry",
    "TranslateBulkResumeResult",
    "TranslateBulkJobEntry",
    "TranslateBulkList",
    "TranslateBulkCancelResult",
    "WorkflowTierKey",
    "WorkflowTierApply",
    "TranslateErrorsDismissed",
    "WorkflowTierApplied",
    "TranslatePresetApply",
    "TranslatePresetApplied",
    "TranslatePresetSave",
    "TranslatePresetSaved",
]


class TranslateEngineListResponse(BaseModel):
    items: List[TranslateEngine]
    default_engine: Optional[str] = None  # Settings' default engine


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
    """Never carries an API key -- the server resolves one per engine
    itself; see services/translate_service.py's own resolve logic."""
    # Mirrored by MAX_TRANSLATE_TEXT_CHARS in frontend/src/api/translate.ts.
    text: str = Field(max_length=2_000_000)
    engine: str
    source_language: str
    target_language: str
    model: Optional[str] = None
    free_tier: Optional[bool] = None  # None: the saved Gemini free-tier setting


class TranslateResponse(BaseModel):
    translated_text: str


class ClearHistoryResult(BaseModel):
    cleared: bool


class TranslateRunStylePreset(BaseModel):
    key: str
    label: str
    guidance: str = ""   # what this style asks the translator for


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
    """Read-only Translate-stage summary. Booleans and
    numbers only -- never a key or the novel text."""
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
    # Never the URL. None when the drama's engine isn't Ollama
    # (not probed).
    ollama_reachable: Optional[bool] = None
    thinking_switch_engines: List[str] = []
    title_thinking: bool = False
    # The owner's saved choice for this title; None = never chosen (the form
    # then starts from the preset values, else genre notes on, she/her off).
    default_female_pronouns: Optional[bool] = None
    include_genre_notes: Optional[bool] = None


class TranslateRunEstimate(BaseModel):
    """Advisory pre-run cost estimate."""
    engine: str
    model: Optional[str] = None
    estimated_usd: Optional[float] = None
    target_line_count: int
    free: bool
    cap_applies: bool
    effective_cap_usd: Optional[float] = None
    monthly_refusal: bool
    estimate_above_cap: bool
    thinking: bool = False
    estimate_is_lower_bound: bool = False


class TranslateRunStart(BaseModel):
    """Start a normal translation. No keys/URLs."""
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
    reflect: bool = False  # Three-pass Reflect mode
    bulk: bool = False  # Batch API / DeepSeek off-peak, job bulk_translate_{id}
    # A preset's prompt toggles; None = the defaults (she/her off, genre notes on).
    default_female_pronouns: Optional[bool] = None
    include_genre_notes: Optional[bool] = None
    # Let DeepSeek/Ollama reason first; None = the title's remembered choice.
    thinking: Optional[bool] = None


class TranslateRunStarted(BaseModel):
    job_id: str
    drama_id: int
    engine: str
    model: Optional[str] = None
    target_line_count: int
    fallback_engines: List[str] = []
    reflect: bool = False
    bulk: bool = False
    thinking: bool = False


class TranslateFallbackEngine(BaseModel):
    """One entry of TranslateRunStart.fallback_chain."""
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
    thinking: Optional[bool] = None


class GlossaryAffectedRunStarted(TranslateRunStarted):
    line_ids: List[int]
    skipped_hand_edited_count: int


TranslateRunStart.model_rebuild()


class TranslateBulkResumeEntry(BaseModel):
    bulk_job_id: int
    state: str  # "polling" | "needs_key" | "running"


class TranslateBulkResumeResult(BaseModel):
    """Pending bulk jobs picked back up after a restart."""
    drama_id: int
    jobs: List[TranslateBulkResumeEntry]


# ---------------------------------------------------------------------------
# Pending bulk batch list + cancel
# ---------------------------------------------------------------------------
class TranslateBulkJobEntry(BaseModel):
    """One bulk batch as last recorded. No prompts,
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
# Apply a workflow tier, save translate settings as a preset
# (services/translate_run_service.py apply_workflow_tier / save_translate_preset).
# ---------------------------------------------------------------------------
WorkflowTierKey = Literal[tuple(_translate_engines.WORKFLOW_TIERS)]


class WorkflowTierApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: WorkflowTierKey


class TranslateErrorsDismissed(BaseModel):
    """The last run's failed-batch notice is cleared; lines untouched.
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
    """The preset's engine (when set) is saved on the drama; the
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
