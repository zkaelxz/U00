"""
api/metadata_research_schemas.py -- request/response models for grounded
metadata research (api/routers/metadata_research_routes.py).
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.schemas import DramaDetail


class ResearchRequest(BaseModel):
    """No keys: the Gemini key is resolved server-side."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["quick", "deep", "verify"] = "quick"
    model: Optional[str] = Field(None, max_length=60)
    allow_paid: bool = False
    refresh: bool = False


class ResearchSource(BaseModel):
    title: Optional[str] = None
    url: str


class ResearchField(BaseModel):
    field: str
    value: str
    current: Optional[str] = None
    status: Literal["new", "same", "conflict"]
    sources: list[ResearchSource]
    confidence: Optional[float] = None


class ResearchRelated(BaseModel):
    title: str
    relation: str = ""


class ResearchModelBudget(BaseModel):
    free_daily_limit: int
    requests_today: int
    free_remaining: int
    used_up: bool  # Google said this model's free requests are used up today


class ResearchBudget(BaseModel):
    # The top-level limit and free_remaining are the default model's.
    free_daily_limit: int
    used_today: int
    free_monthly_limit: int
    used_this_month: int
    free_remaining: int
    per_model: dict[str, ResearchModelBudget] = {}
    paid_price_per_search_usd: float
    free_lookup_min: int  # below this many free searches left, a lookup is paid
    free_tier_key: bool
    key_configured: bool
    monthly_cap_usd: float
    month_spend_usd: float
    models: list[str]
    modes: list[str]
    estimates_usd: dict[str, dict[str, float]]


class ResearchResult(BaseModel):
    drama_id: int
    # None for a cached read, which stores nothing; Apply then names lookup_mode, lookup_model and retrieved_at.
    research_id: Optional[str] = None
    cached: bool
    mode: str
    model: str
    retrieved_at: str
    fields: list[ResearchField]
    sources: list[ResearchSource]
    related: list[ResearchRelated]
    search_queries: list[str] = []
    cost_usd: float
    budget: ResearchBudget


class ResearchApply(BaseModel):
    """Values come from the stored research result, never from the client."""
    model_config = ConfigDict(extra="forbid")
    research_id: Optional[str] = Field(default=None, min_length=64, max_length=64)
    # Which cached lookup was shown (a lookup, not a model to call).
    lookup_mode: Optional[str] = Field(default=None, max_length=20)
    lookup_model: Optional[str] = Field(default=None, max_length=100)
    retrieved_at: Optional[str] = Field(default=None, max_length=40)
    choices: dict[str, Literal["keep", "replace", "save_both", "confirm"]] = Field(max_length=20)
    # The drama's value the user was shown for each chosen field (null = empty).
    seen: dict[str, Optional[str]] = Field(default_factory=dict, max_length=20)


class ResearchApplied(BaseModel):
    drama_id: int
    replaced: list[str]
    saved_alternates: list[str]
    confirmed: list[str]
    kept: list[str]
    drama: DramaDetail


class ProvenanceRow(BaseModel):
    id: int
    field: str
    value: str
    source: Optional[str] = None
    source_url: Optional[str] = None
    sources: list[ResearchSource]
    retrieved_at: Optional[str] = None
    confidence: Optional[float] = None
    last_verified: Optional[str] = None
    status: str


class ProvenanceList(BaseModel):
    drama_id: int
    fields: list[ProvenanceRow]
