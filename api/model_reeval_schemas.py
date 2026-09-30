"""
api/model_reeval_schemas.py -- request/response models for scheduled model
re-evaluation and promotion (api/routers/model_reeval_routes.py, Step 40b).
Kept out of api/schemas.py; no key field exists on any model.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.benchmark_schemas import BenchmarkEstimate


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelDecision(BaseModel):
    id: int
    candidate_id: int
    decision: str
    reason: str = ""
    scores: Dict = Field(default_factory=dict)
    decided_at: Optional[str] = None
    summary: str


class ModelCandidate(BaseModel):
    id: int
    capability: str
    engine: str
    model: Optional[str] = None
    note: Optional[str] = ""
    status: str
    created_at: Optional[str] = None
    last_decision: Optional[ModelDecision] = None


class ProductionModel(BaseModel):
    engine: str
    model: Optional[str] = None
    source: str
    promoted_at: Optional[str] = None


class ReevalSettings(BaseModel):
    schedule_enabled: bool
    interval_days: int
    tier: Optional[str] = None
    set_name: Optional[str] = None
    max_cost_usd: Optional[float] = None
    # When the schedule was turned on; the first scheduled run is one
    # interval later.
    enabled_at: Optional[str] = None


class ReevalRow(BaseModel):
    candidate: ModelCandidate
    run_id: int
    status: Optional[str] = None
    aggregate_score: Optional[float] = None
    quality_delta: Optional[float] = None
    cost_delta_usd: Optional[float] = None
    latency_delta_seconds: Optional[float] = None
    vram_delta_mb: Optional[float] = None
    total_cost_usd: Optional[float] = None
    avg_latency_seconds: Optional[float] = None


class ReevalProductionRun(BaseModel):
    id: int
    status: Optional[str] = None
    aggregate_score: Optional[float] = None
    total_cost_usd: Optional[float] = None
    avg_latency_seconds: Optional[float] = None
    peak_vram_mb: Optional[float] = None
    engine: Optional[str] = None
    model: Optional[str] = None


class ReevalReport(BaseModel):
    production: ProductionModel
    production_run: Optional[ReevalProductionRun] = None
    started_at: Optional[str] = None
    scheduled: bool = False
    arena_group: Optional[str] = None
    error: Optional[str] = None
    error_at: Optional[str] = None
    rows: List[ReevalRow] = Field(default_factory=list)


class ReevalOverview(BaseModel):
    capability: str
    production: ProductionModel
    settings: ReevalSettings
    next_due_at: Optional[str] = None
    candidates: List[ModelCandidate]
    report: ReevalReport


class ReevalSettingsSaved(ReevalOverview):
    # What one scheduled run would cost now (enabled schedule only), or why
    # it can't be estimated yet (e.g. no candidate).
    schedule_estimate: Optional[BenchmarkEstimate] = None
    schedule_estimate_error: Optional[str] = None


class ReevalDecisionList(BaseModel):
    decisions: List[ModelDecision]


class ReevalSettingsRequest(_Strict):
    schedule_enabled: bool
    interval_days: int = Field(ge=1, le=365)
    tier: Optional[str] = Field(default=None, max_length=20)
    set_name: Optional[str] = Field(default=None, max_length=60)
    max_cost_usd: Optional[float] = Field(default=None, ge=0, le=10000)


class CandidateAddRequest(_Strict):
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=200)


class CandidateAddResult(BaseModel):
    candidate: ModelCandidate
    already_registered: bool


class CandidateResult(BaseModel):
    candidate: ModelCandidate


class RejectRequest(_Strict):
    reason: str = Field(default="", max_length=300)


class PromoteRequest(_Strict):
    confirm: bool = False
    reason: str = Field(default="", max_length=300)


class PromoteResult(BaseModel):
    production: ProductionModel
    previous: ProductionModel
    default_engine_changed: bool
    candidate: ModelCandidate


class ReevalRunRequest(_Strict):
    confirm: bool = False


class ReevalRunStarted(BaseModel):
    job_id: str
    session_ids: List[int]
    arena_group: Optional[str] = None
    estimated_cost_usd: float
    candidate_ids: List[int]


ReevalEstimate = BenchmarkEstimate
