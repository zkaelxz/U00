"""Shapes for the follow-ups after a translate run: retrying failed lines,
redoing glossary misses with the stronger engine, and the episode summary."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.translate import TranslateFallbackEngine, TranslateRunStarted


class RetryFailedLinesStart(BaseModel):
    """The Translate form's run options. The lines are the last run's failed
    ones, chosen server-side."""
    model_config = ConfigDict(extra="forbid")
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
    default_female_pronouns: Optional[bool] = None
    include_genre_notes: Optional[bool] = None
    thinking: Optional[bool] = None


class RetryFailedLinesStarted(TranslateRunStarted):
    line_ids: List[int]


class GlossaryFlaggedRedoStart(BaseModel):
    """Empty on purpose: the engine is the stronger one chosen in Settings."""
    model_config = ConfigDict(extra="forbid")


class GlossaryFlaggedRedoStarted(TranslateRunStarted):
    line_ids: List[int]


class EpisodeSummaryOptions(BaseModel):
    engine: str
    engines: List[str]


class EpisodeSummaryGenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: str = Field(max_length=40)
    model: Optional[str] = Field(None, max_length=200)


class EpisodeSummaryGenerated(BaseModel):
    drama_id: int
    summary: str
    engine: str
    cost_usd: float = 0.0
