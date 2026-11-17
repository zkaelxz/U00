"""api/schemas/benchmark.py -- request/response models for the Benchmark Lab
routes (api/routers/benchmark_routes.py). No key, path or file name field
exists on any model.
"""

from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from core import SOURCE_LANGUAGES
from services.benchmark_runs_service import MAX_CONFIGS

__all__ = [
    "Stage",
    "Tier",
    "SourceLanguage",
    "BenchmarkCase",
    "BenchmarkCaseList",
    "BenchmarkSet",
    "BenchmarkSetList",
    "BenchmarkCaseCreate",
    "BenchmarkCaseDelete",
    "BenchmarkDeleted",
    "BenchmarkImportRequest",
    "BenchmarkImportResult",
    "BenchmarkRegressionResult",
    "BenchmarkTranscribeArm",
    "BenchmarkTranslateArm",
    "BenchmarkConfig",
    "BenchmarkRunRequest",
    "BenchmarkRunStarted",
    "BenchmarkScriptCounts",
    "BenchmarkRun",
    "BenchmarkRunList",
    "BenchmarkResult",
    "BenchmarkRunDetail",
    "BenchmarkArenaRow",
    "BenchmarkArena",
    "BenchmarkEngineOption",
    "BenchmarkOptions",
    "BenchmarkJudgeConfig",
    "BenchmarkJudgeScore",
    "BenchmarkJudgeSummary",
    "BenchmarkPickTotals",
    "BenchmarkPairNext",
    "BenchmarkPickRequest",
    "BenchmarkPickSummary",
    "BenchmarkSetBuildRequest",
    "BenchmarkSetBuildResult",
    "BenchmarkTitleCorrections",
    "BenchmarkCorrectionsAddRequest",
    "BenchmarkHardSubAddRequest",
    "BenchmarkHardSubAddResult",
    "BenchmarkHardSubReadRequest",
    "BenchmarkHardSubReadEstimate",
    "BenchmarkHardSubReadStarted",
    "BenchmarkHardSubLine",
    "BenchmarkHardSubGroup",
    "BenchmarkHardSubGroupScore",
    "BenchmarkHardSubComparison",
    "BenchmarkHardSubReadOutcome",
    "BenchmarkHardSubReadResult",
    "BenchmarkHardSubApplyRequest",
    "BenchmarkHardSubCaptionSuggestion",
    "BenchmarkHardSubCaptionSuggestions",
    "BenchmarkHardSubCaptionAcceptRequest",
    "BenchmarkHardSubCaptionAcceptResult",
    "BenchmarkHardSubApplyResult",
    "BenchmarkCorrectionsAddResult",
    "BenchmarkClipRow",
    "BenchmarkClipCheckRequest",
    "BenchmarkClipNote",
    "BenchmarkClipCheck",
    "BenchmarkClipImportResult",
    "BenchmarkCaseExcluded",
    "BenchmarkCaseReference",
    "BenchmarkCaseEdit",
    "BenchmarkCaseEdits",
    "BenchmarkLeftOut",
    "BenchmarkRunDelete",
    "BenchmarkTranscribeOptions",
    "BenchmarkTranslateOptions",
    "BenchmarkTranscribeDefaults",
    "BenchmarkStylePreset",
    "BenchmarkSeriesChoice",
    "BenchmarkTranslateDefaults",
    "BenchmarkTranslateChoices",
    "BenchmarkTimingSummary",
    "BenchmarkCaseTiming",
    "BenchmarkTimingVerdict",
    "BenchmarkBackendOption",
    "BenchmarkSweepRequest",
    "BenchmarkSweepDecisionRequest",
    "BenchmarkSweepModel",
    "BenchmarkSweepKnob",
    "BenchmarkSweepEstimate",
    "BenchmarkSweepSetup",
    "BenchmarkSweepStep",
    "BenchmarkSweepTag",
    "BenchmarkSweepHoldout",
    "BenchmarkSweepRecommendation",
    "BenchmarkSweepProfile",
    "BenchmarkSweepApplies",
    "BenchmarkSweepDecision",
    "BenchmarkSweep",
    "BenchmarkSweepState",
    "BenchmarkSweepStarted",
    "BenchmarkSweepDecisionRow",
    "BenchmarkSweepDecisions",
    "BenchmarkTranslateSweepRequest",
    "BenchmarkTranslateSweepKnob",
    "BenchmarkTranslateSweepEstimate",
    "BenchmarkTranslateSweep",
    "BenchmarkTranslateSweepState",
    "BenchmarkTranslateSweepStarted",
]


class BenchmarkJudgeConfig(BaseModel):
    """The engine that judges a translation run's outputs. allow_same_model
    must be true to use the same engine and model as one being tested."""
    model_config = ConfigDict(extra="forbid")
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(default=None, max_length=100)
    allow_same_model: bool = False


class BenchmarkJudgeScore(BaseModel):
    accuracy: float
    tone: float
    naturalness: float
    overall: float


class BenchmarkJudgeSummary(BaseModel):
    engine: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    same_as_tested: bool = False
    cost_usd: float = 0.0
    scored: int = 0
    average: Dict[str, Optional[float]] = Field(default_factory=dict)
    note: Optional[str] = None


class BenchmarkPickTotals(BaseModel):
    won: int = 0
    lost: int = 0
    tie: int = 0


class BenchmarkPairNext(BaseModel):
    """Two outputs for one case, in random order; nothing names the run behind either."""
    total: int
    remaining: int
    done: bool
    case_id: Optional[int] = None
    label: Optional[str] = None
    source_text: Optional[str] = None
    reference_text: Optional[str] = None
    left_text: Optional[str] = None
    right_text: Optional[str] = None
    ticket: Optional[str] = None


class BenchmarkPickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(max_length=100)
    choice: Literal["left", "right", "tie"]


class BenchmarkPickSummary(BaseModel):
    run_a: int
    run_b: int
    total: int
    picked: int
    a_wins: int
    b_wins: int
    ties: int
    judge_agrees: int
    judge_disagrees: int
    judge_undecided: int


class BenchmarkSetBuildRequest(BaseModel):
    """Which of a title's lines become cases. line_start/line_end are 1-based
    line numbers; scene_count keeps that many evenly spaced scenes."""
    model_config = ConfigDict(extra="forbid")
    drama_id: int = Field(ge=1)
    set_name: str = Field(min_length=1, max_length=60)
    include: Literal["reviewed", "all"] = "reviewed"
    line_start: Optional[int] = Field(default=None, ge=1)
    line_end: Optional[int] = Field(default=None, ge=1)
    scene_count: Optional[int] = Field(default=None, ge=1, le=500)
    lines_per_case: int = Field(default=4, ge=1, le=10)
    dry_run: bool = False


class BenchmarkSetBuildResult(BaseModel):
    set_name: str
    tier: str
    include: str
    lines_in_range: int
    reviewed_lines: int
    lines_used: int
    case_count: int
    added: int
    skipped: int
    dry_run: bool


class BenchmarkTitleCorrections(BaseModel):
    """How far a title's raw transcript is from the owner's corrections. The
    rate is None until at least one line has been corrected."""
    drama_id: int
    raw_available: bool
    lines_compared: int
    lines_corrected: int
    raw_to_edited_cer: Optional[float] = None


class BenchmarkCorrectionsAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    set_name: str = Field(min_length=1, max_length=60)
    dry_run: bool = False
    # Also add lines whose text was never changed, taking their current text as the reference.
    include_all: bool = False


class BenchmarkHardSubAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_ids: List[int] = Field(min_length=1, max_length=10)
    set_name: str = Field(default="hard-sub", min_length=1, max_length=60)
    # The kept band of the picture as fractions of its height from the top; omitted = the bottom 30%.
    band_top: Optional[float] = Field(default=None, ge=0, le=1)
    band_bottom: Optional[float] = Field(default=None, ge=0, le=1)


class BenchmarkHardSubAddResult(BaseModel):
    set_name: str
    added: int
    skipped: int
    # Lines none of whose pictures was clear enough to read.
    unclear: int


class BenchmarkHardSubReadRequest(BaseModel):
    """Read a title's burned-in subtitles speaker by speaker. confirm is for the run, not the estimate."""
    model_config = ConfigDict(extra="forbid")
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(default=None, max_length=100)
    speaker_groups: Optional[int] = Field(default=None, ge=1, le=6)
    band_top: Optional[float] = Field(default=None, ge=0, le=1)
    band_bottom: Optional[float] = Field(default=None, ge=0, le=1)
    # Dollars; omitted = no more than the estimate.
    max_cost_usd: Optional[float] = Field(default=None, ge=0)
    confirm: bool = False


class BenchmarkHardSubReadEstimate(BaseModel):
    engine: str
    model: Optional[str] = None
    windows: int
    speaker_groups: int
    reads_at_most: int
    estimated_usd: float
    cap_applies: bool
    monthly_cap_usd: Optional[float] = None
    cap_left_usd: Optional[float] = None
    fits_cap: bool


class BenchmarkHardSubReadStarted(BaseModel):
    job_id: str
    windows: int
    estimated_usd: float


class BenchmarkHardSubLine(BaseModel):
    start: float
    end: float
    text: str


class BenchmarkHardSubGroup(BaseModel):
    key: str
    lines: List[BenchmarkHardSubLine]


class BenchmarkHardSubGroupScore(BaseModel):
    key: str
    characters: int
    recall: Optional[float] = None


class BenchmarkHardSubComparison(BaseModel):
    groups: List[BenchmarkHardSubGroupScore]
    recall: Optional[float] = None
    precision: Optional[float] = None
    label_overlap: Dict[str, Dict[str, float]]
    label_match: Optional[float] = None


class BenchmarkHardSubReadOutcome(BaseModel):
    groups: List[BenchmarkHardSubGroup]
    windows_read: int
    windows_total: int
    unreadable: int
    unclear_pictures: int
    failed_reads: int
    cost_usd: float
    # "cap", "cancelled" or "failed" when the run ended early.
    stopped: Optional[str] = None
    note: Optional[str] = None
    comparison: BenchmarkHardSubComparison


class BenchmarkHardSubReadResult(BaseModel):
    status: str
    progress: Optional[float] = None
    message: Optional[str] = None
    # None while the reading is still going.
    result: Optional[BenchmarkHardSubReadOutcome] = None


class BenchmarkHardSubApplyRequest(BaseModel):
    """Optional colour-to-person names ({"yellow-right": "Mei"}); remembered for the title's series."""
    model_config = ConfigDict(extra="forbid")
    names: Optional[Dict[str, str]] = None


class BenchmarkHardSubCaptionSuggestion(BaseModel):
    line_id: int
    start: float
    end: float
    current: str
    caption: str
    group: str
    person: Optional[str] = None
    similarity: float


class BenchmarkHardSubCaptionSuggestions(BaseModel):
    suggestions: List[BenchmarkHardSubCaptionSuggestion]


class BenchmarkHardSubCaptionAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_ids: List[int] = Field(min_length=1, max_length=2000)


class BenchmarkHardSubCaptionAcceptResult(BaseModel):
    changed: int
    skipped: int


class BenchmarkHardSubApplyResult(BaseModel):
    labelled: int
    kept_manual: int
    unplaced: int
    speakers: List[str]


class BenchmarkCorrectionsAddResult(BaseModel):
    set_name: str
    lines_compared: int
    lines_corrected: int
    unedited_skipped: int
    unclippable_skipped: int
    include_all: bool = False
    case_count: int
    # Lines left for another add: one call adds at most 100.
    remaining: int = 0
    added: int
    skipped: int
    dry_run: bool


class BenchmarkClipRow(BaseModel):
    """One clip as typed in the table (no audio: that is a file part)."""
    model_config = ConfigDict(extra="forbid")
    filename: Optional[str] = Field(default=None, max_length=300)
    language: Literal["zh", "ja", "ko"]
    transcript: str = Field(max_length=4000)
    translation: Optional[str] = Field(default=None, max_length=4000)
    title: str = Field(default="", max_length=80)
    tags: List[str] = Field(default_factory=list, max_length=10)
    scene_order: Optional[int] = Field(default=None, ge=0)


class BenchmarkClipCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clips: List[BenchmarkClipRow] = Field(max_length=250)


class BenchmarkClipNote(BaseModel):
    clip: str
    message: str


class BenchmarkClipCheck(BaseModel):
    problems: List[BenchmarkClipNote]
    warnings: List[BenchmarkClipNote]


class BenchmarkClipImportResult(BaseModel):
    set_name: str
    clips: int
    transcription_cases: int
    translation_cases: int
    case_ids: List[int]
    warnings: List[BenchmarkClipNote]


class BenchmarkCaseExcluded(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excluded: bool


class BenchmarkCaseReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_text: str = Field(max_length=4000)


class BenchmarkCaseEdit(BaseModel):
    field: str
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    version: int
    edited_at: Optional[str] = None


class BenchmarkCaseEdits(BaseModel):
    edits: List[BenchmarkCaseEdit]


class BenchmarkLeftOut(BaseModel):
    """Cases not compared: deleted, excluded, edited between the runs, or missing from a run."""
    total: int
    removed: int
    excluded: int
    edited: int
    not_in_every_run: int


class BenchmarkRunDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class BenchmarkTranscribeOptions(BaseModel):
    """A Lab transcription arm's per-title options (services/transcribe_options_service.py
    checks the values); a field left out takes the app's default."""
    model_config = ConfigDict(extra="forbid")

    # Strict: "5" or "yes" is refused, not coerced.
    beam_size: Optional[StrictInt] = None
    min_silence_ms: Optional[StrictInt] = None
    vad_threshold: Optional[Union[StrictInt, StrictFloat]] = None
    sensitivity_preset: Optional[str] = Field(default=None, max_length=20)
    hallucination_silence_sec: Optional[Union[StrictInt, StrictFloat]] = None
    whisper_repeat_guard: Optional[StrictBool] = None
    difficult_audio: Optional[StrictBool] = None
    separate_vocals_first: Optional[StrictBool] = None
    decode_randomness: Optional[str] = Field(default=None, max_length=20)
    initial_prompt: Optional[str] = Field(default=None, max_length=1000)
    # Timing settings only the Lab varies.
    speech_pad_ms: Optional[StrictInt] = None
    refine_timing: Optional[StrictBool] = None
    split_max_seconds: Optional[Union[StrictInt, StrictFloat]] = None
    split_max_chars: Optional[StrictInt] = None


class BenchmarkTranslateOptions(BaseModel):
    """A Lab translation arm's options: its cases run as batches in order, the way a title's
    Translate runs them (services/benchmark_scene_service.py checks the values); a field left out
    takes the app's default."""
    model_config = ConfigDict(extra="forbid")

    batch_size: Optional[StrictInt] = None
    context_window: Optional[StrictInt] = None
    context_window_ahead: Optional[StrictInt] = None
    reflect: Optional[StrictBool] = None
    style_preset: Optional[str] = Field(default=None, max_length=40)
    # A series whose glossary, learned style and character hints the arm translates with.
    series_id: Optional[StrictInt] = None
    memory_examples: Optional[StrictBool] = None
    temperature: Optional[Union[StrictInt, StrictFloat]] = None


class BenchmarkTranscribeDefaults(BaseModel):
    """What a title with no saved options transcribes with: every field of
    BenchmarkTranscribeOptions, always present (the options model is all-optional because
    it is a request body)."""
    beam_size: int
    min_silence_ms: int
    vad_threshold: float
    sensitivity_preset: str
    hallucination_silence_sec: float
    whisper_repeat_guard: bool
    difficult_audio: bool
    separate_vocals_first: bool
    decode_randomness: str
    initial_prompt: str
    speech_pad_ms: int
    refine_timing: bool
    split_max_seconds: float
    split_max_chars: int


class BenchmarkStylePreset(BaseModel):
    key: str
    label: str


class BenchmarkSeriesChoice(BaseModel):
    id: int
    name: str


class BenchmarkTranslateDefaults(BaseModel):
    context_window: int
    context_window_ahead: int
    batch_size: int
    reflect: bool
    style_preset: str
    memory_examples: bool


class BenchmarkTranslateChoices(BaseModel):
    style_presets: List[BenchmarkStylePreset]
    series: List[BenchmarkSeriesChoice]
    defaults: BenchmarkTranslateDefaults
    batch_size_defaults: Dict[str, Dict[str, int]]
    temperature_engines: List[str]


class BenchmarkTimingSummary(BaseModel):
    """A run's line timing over the cases that have reference times; only `cases` when none do."""
    cases: int = 0
    start_median_ms: Optional[int] = None
    end_median_ms: Optional[int] = None
    within: Optional[float] = None
    merged: Optional[int] = None
    split: Optional[int] = None
    missed: Optional[int] = None
    silence: Optional[int] = None


class BenchmarkCaseTiming(BaseModel):
    lines: int
    missed: bool
    split: bool
    merged: bool
    start_error_s: Optional[float] = None
    end_error_s: Optional[float] = None
    silence: Optional[int] = None


class BenchmarkTimingVerdict(BaseModel):
    """Arena only: whether a run's timing beat the first run's by more than the measured noise
    without a worse text score. state: baseline, repeat, untimed, no_margin, no_gain, cer_worse, gain."""
    state: str
    text: str
    margin_within: Optional[float] = None
    margin_score: Optional[float] = None
    gain: Optional[float] = None
    score_change: Optional[float] = None


class BenchmarkBackendOption(BaseModel):
    name: str
    label: str
    # What it still needs on this PC; None when it can run.
    problem: Optional[str] = None


# --- The settings sweep (services/benchmark_sweep_service.py) ---

_SweepValue = Union[StrictBool, StrictInt, StrictFloat]


class BenchmarkSweepRequest(BaseModel):
    """One golden set and one language; use_gpu None takes Settings' value."""
    model_config = ConfigDict(extra="forbid")
    set_name: str = Field(min_length=1, max_length=60)
    language: Literal["zh", "ja", "ko"]
    use_gpu: Optional[bool] = None
    confirm: bool = False


class BenchmarkSweepDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    reason: Optional[str] = Field(default=None, max_length=300)
    confirm: bool = False


class BenchmarkSweepModel(BaseModel):
    engine: str
    model: Optional[str] = None
    label: str
    included: bool
    reason: Optional[str] = None


class BenchmarkSweepKnob(BaseModel):
    key: str
    label: str
    values: List[_SweepValue]
    included: bool
    reason: Optional[str] = None


class BenchmarkSweepEstimate(BaseModel):
    set_name: str
    language: str
    use_gpu: bool
    tuning_cases: int
    holdout_cases: int
    tuning_titles: int
    holdout_titles: int
    audio_seconds: float
    durations_guessed: bool
    small_set: bool
    min_set_cases: int
    models: List[BenchmarkSweepModel]
    knobs: List[BenchmarkSweepKnob]
    not_swept: List[str]
    passes_done: int
    passes_to_run: int
    estimated_seconds: int
    estimated_cost_usd: float
    monthly_cap_usd: float = 0.0
    monthly_refusal: Optional[str] = None


class BenchmarkSweepSetup(BaseModel):
    engine: str
    model: Optional[str] = None
    changes: Dict[str, _SweepValue] = {}
    label: str


class BenchmarkSweepStep(BaseModel):
    step: str
    value: Optional[Union[StrictBool, StrictInt, StrictFloat, str]] = None
    half: Literal["tuning", "holdout"]
    setup: str
    run_id: Optional[int] = None
    score: Optional[float] = None
    errors: Optional[int] = None
    kept: Optional[bool] = None


class BenchmarkSweepTag(BaseModel):
    tag: str
    cases: int
    default_score: Optional[float] = None
    winner_score: Optional[float] = None
    delta: Optional[float] = None
    too_small: bool


class BenchmarkSweepHoldout(BaseModel):
    default_score: Optional[float] = None
    winner_score: Optional[float] = None
    delta: Optional[float] = None
    default_run_id: int
    winner_run_id: int
    default_errors: int
    winner_errors: int


class BenchmarkSweepRecommendation(BaseModel):
    state: Literal["keep_default", "adopt", "not_confirmed"]
    text: str
    noise: Optional[float] = None
    margin: float
    margin_rule: str
    default: BenchmarkSweepSetup
    winner: BenchmarkSweepSetup
    tuning: Dict[str, Optional[float]]
    holdout: Optional[BenchmarkSweepHoldout] = None
    tags: List[BenchmarkSweepTag]
    steps: List[BenchmarkSweepStep]
    case_counts: Dict[str, int]
    small_set: bool


class BenchmarkSweepProfile(BaseModel):
    id: int
    name: str


class BenchmarkSweepApplies(BaseModel):
    """One title setting the approved profile writes when it is applied to a title."""
    key: str
    label: str
    value: str


class BenchmarkSweepDecision(BaseModel):
    decision: Literal["promoted", "rejected"]
    reason: str = ""
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    profile: Optional[BenchmarkSweepProfile] = None
    applies: Optional[List[BenchmarkSweepApplies]] = None


class BenchmarkSweep(BaseModel):
    id: Optional[str] = None
    set_name: str
    language: str
    use_gpu: Optional[bool] = None
    status: Literal["queued", "running", "done", "cancelled", "failed", "interrupted"]
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    note: Optional[str] = None
    passes_run: int = 0
    passes_reused: int = 0
    recommendation: Optional[BenchmarkSweepRecommendation] = None
    decision: Optional[BenchmarkSweepDecision] = None
    applies: Optional[List[BenchmarkSweepApplies]] = None
    estimate: Optional[Dict[str, int]] = None


class BenchmarkSweepState(BaseModel):
    sweep: Optional[BenchmarkSweep] = None


class BenchmarkSweepStarted(BaseModel):
    job_id: str
    sweep: BenchmarkSweep


class BenchmarkSweepDecisionRow(BaseModel):
    decision: str
    reason: str = ""
    decided_at: Optional[str] = None
    language: Optional[str] = None
    setup: str


class BenchmarkSweepDecisions(BaseModel):
    decisions: List[BenchmarkSweepDecisionRow]


# --- The translation settings sweep (services/benchmark_translate_sweep_service.py) ---

# None is a knob's "the engine's default" (temperature), a float a temperature.
_TranslateSweepValue = Optional[Union[StrictBool, StrictInt, StrictFloat, str]]


class BenchmarkTranslateSweepRequest(BaseModel):
    """One golden set, one language and one engine; model None is the engine's default, series_id
    None translates without a glossary."""
    model_config = ConfigDict(extra="forbid")
    set_name: str = Field(min_length=1, max_length=60)
    language: Literal["zh", "ja", "ko"]
    engine: str = Field(min_length=1, max_length=40)
    model: Optional[str] = Field(default=None, max_length=100)
    series_id: Optional[StrictInt] = Field(default=None, ge=1)
    confirm: bool = False


class BenchmarkTranslateSweepKnob(BaseModel):
    key: str
    label: str
    values: List[_TranslateSweepValue]
    included: bool
    reason: Optional[str] = None


class BenchmarkTranslateSweepEstimate(BaseModel):
    set_name: str
    language: str
    engine: str
    model: Optional[str] = None
    series_id: Optional[int] = None
    tuning_cases: int
    holdout_cases: int
    tuning_titles: int
    holdout_titles: int
    small_set: bool
    min_set_cases: int
    models: List[BenchmarkSweepModel]
    knobs: List[BenchmarkTranslateSweepKnob]
    not_swept: List[str]
    passes_done: int
    passes_to_run: int
    estimated_cost_usd: float
    cap_applies: bool
    monthly_cap_usd: float = 0.0
    month_spend_usd: float = 0.0
    remaining_usd: Optional[float] = None
    monthly_refusal: Optional[str] = None
    estimate_above_cap: bool


class _TranslateSweepSetup(BaseModel):
    engine: str
    model: Optional[str] = None
    changes: Dict[str, _TranslateSweepValue] = {}
    label: str


class _TranslateSweepRecommendation(BenchmarkSweepRecommendation):
    default: _TranslateSweepSetup
    winner: _TranslateSweepSetup


class BenchmarkTranslateSweep(BenchmarkSweep):
    engine: str
    model: Optional[str] = None
    series_id: Optional[int] = None
    recommendation: Optional[_TranslateSweepRecommendation] = None


class BenchmarkTranslateSweepState(BaseModel):
    sweep: Optional[BenchmarkTranslateSweep] = None


class BenchmarkTranslateSweepStarted(BaseModel):
    job_id: str
    sweep: BenchmarkTranslateSweep


Stage = Literal["translation", "transcription", "ocr"]
Tier = Literal["public", "application", "regression"]
# The choices come from a runtime tuple, which a type checker can't read as a Literal.
if TYPE_CHECKING:
    SourceLanguage = str
else:
    SourceLanguage = Literal[SOURCE_LANGUAGES]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchmarkCase(BaseModel):
    id: int
    label: str
    stage: Optional[str] = None
    tier: str
    set_name: str
    source_language: str
    source_text: Optional[str] = None
    reference_text: Optional[str] = None
    has_reference: bool
    has_input_file: bool
    hard_sub: bool = False
    origin_drama_id: Optional[int] = None
    origin_line_id: Optional[int] = None
    title: str = ""
    tags: List[str] = Field(default_factory=list)
    scene_order: Optional[int] = None
    excluded: bool = False
    ref_version: int = 1
    has_ref_times: bool = False
    created_at: Optional[str] = None


class BenchmarkCaseList(BaseModel):
    cases: List[BenchmarkCase]


class BenchmarkSet(BaseModel):
    stage: Optional[str] = None
    tier: str
    set_name: str
    case_count: int
    with_reference: int
    excluded: int = 0


class BenchmarkSetList(BaseModel):
    sets: List[BenchmarkSet]


class BenchmarkCaseCreate(_Strict):
    label: str = Field(max_length=120)
    source_text: str = Field(max_length=4000)
    reference_text: Optional[str] = Field(default=None, max_length=4000)
    source_language: SourceLanguage = "zh"
    tier: Tier = "application"
    set_name: str = Field(default="", max_length=60)


class BenchmarkCaseDelete(_Strict):
    confirm: bool = False


class BenchmarkDeleted(BaseModel):
    deleted: bool
    id: int


class BenchmarkImportRequest(_Strict):
    set_name: str = Field(max_length=60)
    text: str = Field(max_length=2_000_000)
    format: Literal["jsonl", "tsv"] = "jsonl"
    tier: Tier = "public"
    source_language: SourceLanguage = "zh"


class BenchmarkImportResult(BaseModel):
    set_name: str
    tier: str
    added: int
    skipped: int


class BenchmarkRegressionResult(BaseModel):
    case: BenchmarkCase
    replaced: bool


class BenchmarkTranscribeArm(_Strict):
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(default=None, max_length=100)
    options: Optional[BenchmarkTranscribeOptions] = None


class BenchmarkTranslateArm(_Strict):
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(default=None, max_length=100)


class BenchmarkConfig(_Strict):
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(default=None, max_length=100)
    # Translation arms only: translate what this recogniser hears in each clip, not the reference transcript.
    transcribe: Optional[BenchmarkTranscribeArm] = None
    # The per-title options this arm runs with: transcription's, or a translation arm's batch,
    # context, Reflect and style settings (which make it translate cases as batches in order).
    options: Optional[Union[BenchmarkTranscribeOptions, BenchmarkTranslateOptions]] = None
    # Vision OCR arms only: translate what the arm reads and score the translation too.
    translate: Optional[BenchmarkTranslateArm] = None


class BenchmarkRunRequest(_Strict):
    stage: Stage = "translation"
    configs: List[BenchmarkConfig] = Field(min_length=1, max_length=MAX_CONFIGS)
    tier: Optional[Tier] = None
    set_name: Optional[str] = Field(default=None, max_length=60)
    case_ids: Optional[List[int]] = Field(default=None, max_length=1000)
    label: str = Field(default="", max_length=120)
    prompt_version: str = Field(default="", max_length=60)
    # Translation runs only: also have this engine score each output (see
    # services/benchmark_judge_service.py).
    judge: Optional[BenchmarkJudgeConfig] = None
    # Starting a run needs confirm=true: the client shows the estimate first.
    confirm: bool = False


class BenchmarkRunStarted(BaseModel):
    job_id: str
    session_ids: List[int]
    arena_group: Optional[str] = None
    estimated_cost_usd: float


class BenchmarkScriptCounts(BaseModel):
    """Traditional vs Simplified in a Chinese transcript; shown beside CER, which ignores script."""
    traditional_lines: int = 0
    simplified_lines: int = 0
    mixed_lines: int = 0
    neutral_lines: int = 0
    traditional_chars: int = 0
    simplified_chars: int = 0
    neutral_chars: int = 0


class BenchmarkRun(BaseModel):
    id: int
    label: Optional[str] = ""
    stage: Optional[str] = None
    engine: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = ""
    arena_group: Optional[str] = None
    status: Optional[str] = None
    case_count: int = 0
    scored_count: int = 0
    passed_count: int = 0
    error_count: int = 0
    aggregate_score: Optional[float] = None
    avg_latency_seconds: Optional[float] = None
    total_cost_usd: Optional[float] = 0.0
    peak_vram_mb: Optional[float] = None
    note: Optional[str] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    context_settings: dict = Field(default_factory=dict)
    case_filter: dict = Field(default_factory=dict)
    delta_vs_first: Optional[float] = None
    # Arena only: the run's score over the compared cases, and its stored total over all of them.
    stored_aggregate_score: Optional[float] = None
    judge: Optional[BenchmarkJudgeSummary] = None
    picks: Optional[BenchmarkPickTotals] = None
    script_counts: Optional[BenchmarkScriptCounts] = None
    timing: BenchmarkTimingSummary = Field(default_factory=BenchmarkTimingSummary)
    timing_verdict: Optional[BenchmarkTimingVerdict] = None


class BenchmarkRunList(BaseModel):
    runs: List[BenchmarkRun]


class BenchmarkResult(BaseModel):
    case_id: Optional[int] = None
    case_label: str
    output_text: str
    score: Optional[float] = None
    metric: Optional[str] = None
    # "jiwer" or "builtin" for CER/WER, "sacrebleu" or "builtin" for translation; None on
    # results from before it was recorded (those were scored by the built-in scorer).
    scorer: Optional[str] = None
    passed: Optional[bool] = None
    duration_seconds: Optional[float] = None
    cost_usd: float = 0.0
    error: Optional[str] = None
    judge: Optional[BenchmarkJudgeScore] = None
    picks: Optional[BenchmarkPickTotals] = None
    script_counts: Optional[BenchmarkScriptCounts] = None
    ref_version: int = 1
    timing: Optional[BenchmarkCaseTiming] = None
    # What the recogniser heard, on an end-to-end result; output_text is its translation.
    recognised_text: Optional[str] = None
    # A read-then-translate result: how well the picture was read, beside the translation's own score.
    read_score: Optional[float] = None


class BenchmarkRunDetail(BaseModel):
    run: BenchmarkRun
    results: List[BenchmarkResult]


class BenchmarkArenaRow(BaseModel):
    case_id: Optional[int] = None
    label: str
    source_text: Optional[str] = None
    reference_text: Optional[str] = None
    tier: Optional[str] = None
    ref_version: int = 1
    results: List[Optional[BenchmarkResult]]


class BenchmarkArena(BaseModel):
    runs: List[BenchmarkRun]
    rows: List[BenchmarkArenaRow]
    left_out: BenchmarkLeftOut


class BenchmarkEngineOption(BaseModel):
    name: str
    label: str
    free: bool
    models: Optional[List[str]] = None
    # Label for an offered model that has no built-in entry (id -> text).
    model_labels: Dict[str, str] = Field(default_factory=dict)
    key_configured: bool
    # False when the engine's SDK is missing: the run then fails for a reason a key cannot fix.
    package_installed: bool = True


class BenchmarkOptions(BaseModel):
    stages: List[Stage]
    tiers: List[Tier]
    source_languages: List[SourceLanguage]
    translation_engines: List[BenchmarkEngineOption]
    whisper_sizes: List[str]
    whisper_exclusions: Dict[str, Dict[str, str]]
    transcription_backends: List[BenchmarkBackendOption]
    transcribe_defaults: BenchmarkTranscribeDefaults
    # Per recogniser, the options it does not read (shown disabled in the form).
    transcribe_options_ignored: Dict[str, List[str]]
    translate_choices: BenchmarkTranslateChoices
    ocr_backends: List[str]
    vision_engines: List[str] = []
    pass_threshold: float
    chrf_pass_threshold: float
    max_configs: int
