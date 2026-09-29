"""
api/metadata_research_schemas.py -- request/response models for grounded
metadata research (roadmap Step 37, api/routers/metadata_research_routes.py).
Kept out of api/schemas.py on purpose (separate owner).
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ResearchRequest(BaseModel):
    """No keys: the Gemini key is resolved server-side."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["quick", "deep", "verify"] = "quick"
    model: Optional[str] = Field(None, max_length=60)
    allow_paid: bool = False


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


class ResearchBudget(BaseModel):
    free_daily_limit: int
    used_today: int
    free_remaining: int
    paid_price_per_search_usd: float
    free_tier_key: bool
    key_configured: bool
    monthly_cap_usd: float
    month_spend_usd: float
    models: list[str]
    modes: list[str]


class ResearchResult(BaseModel):
    drama_id: int
    research_id: str
    cached: bool
    mode: str
    model: str
    retrieved_at: str
    fields: list[ResearchField]
    sources: list[ResearchSource]
    related: list[ResearchRelated]
    cost_usd: float
    budget: ResearchBudget


class ResearchApply(BaseModel):
    """Values come from the stored research result, never from the client."""
    model_config = ConfigDict(extra="forbid")
    research_id: str = Field(min_length=64, max_length=64)
    choices: dict[str, Literal["keep", "replace", "save_both"]] = Field(max_length=20)


class ResearchApplied(BaseModel):
    drama_id: int
    replaced: list[str]
    saved_alternates: list[str]
    kept: list[str]
    drama: dict


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
