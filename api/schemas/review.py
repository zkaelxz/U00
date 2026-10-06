"""api/schemas/review.py -- Review shapes: line views and edits, records and
versions, restructure and resegment, and the Review AI extras.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

__all__ = [
    "ReviewLinesLine",
    "ReviewLinesPage",
    "ReviewLinesFindReplaceRequest",
    "ReviewLinesMatch",
    "ReviewLinesCoverageEntry",
    "ReviewLinesCoverage",
    "ReviewLinesPacingFlag",
    "ReviewLinesPacing",
    "ReviewLinesProvenance",
    "ReviewLinesOriginalText",
    "ReviewRecordsHistoryItem",
    "ReviewRecordsSnapshotLine",
    "ReviewRecordsSnapshot",
    "ReviewRecordsVersionItem",
    "ReviewRecordsVersionRef",
    "ReviewRecordsDiff",
    "ReviewRecordsCompare",
    "ReviewRecordsNote",
    "ReviewRecordsConsistencyIssue",
    "ReviewRecordsEmotionTag",
    "ReviewRecordsEmotionSummary",
    "ReviewRecordsTendencyStats",
    "ReviewRecordsStyleProfile",
    "ReviewRecordsTendencies",
    "ReviewRecordsTmSuggestion",
    "LinesPatchRequest",
    "LinesMatchIn",
    "LinesFindReplaceApplyRequest",
    "LinesFindReplaceApplyResult",
    "LinesSetLangRequest",
    "LinesSetLangResult",
    "LinesAcceptTmRequest",
    "LinesNoteCreate",
    "LinesNote",
    "LinesNoteDeleteResult",
    "ReviewJobStart",
    "EmotionJobStart",
    "FixFlaggedJobStart",
    "ReviewJobStarted",
    "RestructureAddLine",
    "RestructureDeleteLine",
    "RestructureMerge",
    "RestructureSplit",
    "RestructureResult",
    "ResegmentChange",
    "ResegmentPreview",
    "ResegmentStart",
    "ResplitStart",
    "ResplitResult",
    "ResegmentStarted",
    "ResegmentLlmPreviewStart",
    "ResegmentLlmPreview",
    "RestoreVersionRequest",
    "RestoreVersionResult",
    "LineExplainRequest",
    "LineImproveRequest",
    "LineImprovement",
    "LineExplanation",
    "LineAlternative",
    "LineAlternatives",
    "LineGrammarPart",
    "LineGrammar",
    "LinesShortenRequest",
    "LinesShortenedLine",
    "LinesShortenResult",
    "ReviewLinePosition",
    "TranslationVersionDeleteResult",
    "TranslationVersionActivateRequest",
    "TranslationVersionActivateResult",
    "BlockedRetryRequest",
    "BlockedRetryResult",
    "MergeShortOptions",
    "MergeShortGroup",
    "MergeShortPreview",
    "MergeShortApply",
    "MergeShortResult",
    "EnCleanupRule",
    "EnCleanupChange",
    "EnCleanupPreview",
    "EnCleanupApply",
    "EnCleanupResult",
    "StyleProfileView",
    "StyleHistoryEntry",
    "StyleState",
    "StyleLearnRequest",
    "StyleApplyRequest",
    "StyleResetRequest",
    "StyleRestoreRequest",
    "SenseVoiceStarted",
    "SenseVoiceRow",
    "SenseVoiceTags",
]


# --- Review read-only line views -----------------------
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
    lang: Optional[str] = Field(default=None, description=(
        "This line's spoken language (zh/ja/ko/en); null means the drama's source_language."))


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
# Review read-only records -- see
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


# --- Per-line edit writes (services/lines_service.py) ---
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
    lang: Optional[str] = Field(default=None, max_length=8,
                                description="zh/ja/ko/en; \"\" sets the drama's source_language.")
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


class LinesSetLangRequest(BaseModel):
    """Exactly one of line_ids / speaker picks the lines."""
    model_config = ConfigDict(extra="forbid")
    lang: Optional[str] = Field(max_length=8,
                                description="zh/ja/ko/en; null or \"\" sets the drama's source_language.")
    line_ids: Optional[List[int]] = Field(default=None, max_length=10000)
    speaker: Optional[str] = Field(default=None, max_length=100)


class LinesSetLangResult(BaseModel):
    updated: int = Field(description="Lines whose language changed.")
    line_ids: List[int]
    skipped_ids: List[int] = Field(description="Requested ids that are not lines of this drama.")


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


class ReviewJobStart(BaseModel):
    """Start a Review-stage AI job. No keys/URLs."""
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


# ---------------------------------------------------------------------------
# Restructure lines + version-history restore
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


class ResplitStart(_RestructureBase):
    align_to_audio: StrictBool = False
    confirm: StrictBool = False


class ResplitResult(BaseModel):
    """Either the finished summary (estimated timing) or, with align_to_audio,
    the started job (job_id; its result carries the same summary)."""
    job_id: Optional[str] = None
    drama_id: Optional[int] = None
    split_lines: Optional[int] = None
    lines_before: Optional[int] = None
    line_count: Optional[int] = None
    timing: Optional[str] = None
    aligned_lines: Optional[int] = None
    cleared_translations: Optional[int] = None
    speakers_reassigned: Optional[bool] = None
    note: Optional[str] = None


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


class LineExplainRequest(BaseModel):
    """Per-line AI helper request. No keys/URLs."""
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


class TranslationVersionDeleteResult(BaseModel):
    drama_id: int
    version_id: int
    deleted: bool
    was_active: bool


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


class EnCleanupRule(BaseModel):
    rule: str
    label: str
    lines: int


class EnCleanupChange(BaseModel):
    line_id: int
    idx: int
    before: str
    after: str
    rules: List[str]


class EnCleanupPreview(BaseModel):
    drama_id: int
    lines_scanned: int
    lines_changed: int
    lines_skipped: int = Field(
        default=0, description="Lines over the length guard, left untouched.")
    rules: List[EnCleanupRule]
    changes: List[EnCleanupChange] = Field(description="Capped; `truncated` says more exist.")
    truncated: bool
    plan_hash: str


class EnCleanupApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_plan_hash: str = Field(
        min_length=1, max_length=128,
        description="The preview's `plan_hash`; 409 if a fresh cleanup would differ.")


class EnCleanupResult(BaseModel):
    applied: int
    stale: int
    history_id: int


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
