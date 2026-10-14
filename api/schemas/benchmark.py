"""api/schemas/benchmark.py -- Benchmark Lab shapes added with the reviewed-title
set builder and the optional LLM judge. The older Benchmark Lab models stay in
api/benchmark_schemas.py. No key, path or URL field exists on any model.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BenchmarkJudgeConfig",
    "BenchmarkJudgeScore",
    "BenchmarkJudgeSummary",
    "BenchmarkJudgeEstimate",
    "BenchmarkSetBuildRequest",
    "BenchmarkSetBuildResult",
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
