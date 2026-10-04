"""
api/benchmark_schemas.py -- request/response models for the Benchmark Lab
routes (api/routers/benchmark_routes.py, Step 38). No key, path or file
name field exists on any model.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core import SOURCE_LANGUAGES

Stage = Literal["translation", "transcription", "ocr"]
Tier = Literal["public", "application", "regression"]
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
    origin_drama_id: Optional[int] = None
    origin_line_id: Optional[int] = None
    created_at: Optional[str] = None


class BenchmarkCaseList(BaseModel):
    cases: List[BenchmarkCase]


class BenchmarkSet(BaseModel):
    stage: Optional[str] = None
    tier: str
    set_name: str
    case_count: int
    with_reference: int


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


class BenchmarkConfig(_Strict):
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(default=None, max_length=100)


class BenchmarkRunRequest(_Strict):
    stage: Stage = "translation"
    configs: List[BenchmarkConfig] = Field(min_length=1, max_length=4)
    tier: Optional[Tier] = None
    set_name: Optional[str] = Field(default=None, max_length=60)
    case_ids: Optional[List[int]] = Field(default=None, max_length=1000)
    label: str = Field(default="", max_length=120)
    prompt_version: str = Field(default="", max_length=60)
    # Starting a run needs confirm=true: the client shows the estimate first.
    confirm: bool = False


class BenchmarkConfigEstimate(BaseModel):
    engine: str
    model: Optional[str] = None
    estimated_cost_usd: float
    cap_applies: bool


class BenchmarkEstimate(BaseModel):
    stage: str
    case_count: int
    configs: List[BenchmarkConfigEstimate]
    estimated_cost_usd: float
    monthly_cap_usd: float
    month_spend_usd: float
    remaining_usd: Optional[float] = None
    monthly_refusal: Optional[str] = None
    estimate_above_cap: bool


class BenchmarkRunStarted(BaseModel):
    job_id: str
    session_ids: List[int]
    arena_group: Optional[str] = None
    estimated_cost_usd: float


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


class BenchmarkRunList(BaseModel):
    runs: List[BenchmarkRun]


class BenchmarkResult(BaseModel):
    case_id: Optional[int] = None
    case_label: str
    output_text: str
    score: Optional[float] = None
    metric: Optional[str] = None
    # "jiwer" or "builtin" for CER/WER; None on results from before it was recorded
    # (those were scored by the built-in scorer).
    scorer: Optional[str] = None
    passed: Optional[bool] = None
    duration_seconds: Optional[float] = None
    cost_usd: float = 0.0
    error: Optional[str] = None


class BenchmarkRunDetail(BaseModel):
    run: BenchmarkRun
    results: List[BenchmarkResult]


class BenchmarkArenaRow(BaseModel):
    case_id: Optional[int] = None
    label: str
    source_text: Optional[str] = None
    reference_text: Optional[str] = None
    tier: Optional[str] = None
    results: List[Optional[BenchmarkResult]]


class BenchmarkArena(BaseModel):
    runs: List[BenchmarkRun]
    rows: List[BenchmarkArenaRow]


class BenchmarkEngineOption(BaseModel):
    name: str
    label: str
    free: bool
    models: Optional[List[str]] = None
    # Label for an offered model that has no built-in entry (id -> text).
    model_labels: Dict[str, str] = Field(default_factory=dict)
    key_configured: bool


class BenchmarkOptions(BaseModel):
    stages: List[str]
    tiers: List[str]
    source_languages: List[str]
    translation_engines: List[BenchmarkEngineOption]
    whisper_sizes: List[str]
    ocr_backends: List[str]
    pass_threshold: float
    max_configs: int
