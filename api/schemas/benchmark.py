"""api/schemas/benchmark.py -- Benchmark Lab shapes added with the reviewed-title
set builder and the optional LLM judge. The older Benchmark Lab models stay in
api/benchmark_schemas.py. No key, path or URL field exists on any model.
"""

from typing import Dict, List, Literal, Optional

from typing import Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

__all__ = [
    "BenchmarkTranscribeOptions",
    "BenchmarkTranslateOptions",
    "BenchmarkTranslateChoices",
    "BenchmarkTimingSummary",
    "BenchmarkCaseTiming",
    "BenchmarkTimingVerdict",
    "BenchmarkBackendOption",
    "BenchmarkJudgeConfig",
    "BenchmarkJudgeScore",
    "BenchmarkJudgeSummary",
    "BenchmarkJudgeEstimate",
    "BenchmarkPickTotals",
    "BenchmarkPairNext",
    "BenchmarkPickRequest",
    "BenchmarkPickSummary",
    "BenchmarkSetBuildRequest",
    "BenchmarkSetBuildResult",
    "BenchmarkTitleCorrections",
    "BenchmarkCorrectionsAddRequest",
    "BenchmarkCorrectionsAddResult",
    "BenchmarkHardSubAddRequest",
    "BenchmarkHardSubAddResult",
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
    "BenchmarkSweepApplies", "BenchmarkSweepDecision",
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


class BenchmarkJudgeEstimate(BaseModel):
    engine: str
    model: Optional[str] = None
    estimated_cost_usd: float
    cap_applies: bool
    same_as_tested: List[str] = Field(default_factory=list)
    warning: Optional[str] = None


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


class BenchmarkTranslateChoices(BaseModel):
    style_presets: List[Dict[str, str]]
    series: List[Dict[str, Union[int, str]]]
    defaults: Dict[str, Union[StrictBool, StrictInt, str]]
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

_TranslateSweepValue = Union[StrictBool, StrictInt, str]


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
