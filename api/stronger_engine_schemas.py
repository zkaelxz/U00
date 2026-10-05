"""
api/stronger_engine_schemas.py -- request/response models for the
"Try with a stronger engine" routes (api/routers/stronger_engine_routes.py).
No key, URL or path field exists on any model.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class StrongerLineSuggestion(BaseModel):
    line_id: int
    reasons: List[str]
    estimate_usd: float


class StrongerEngineSuggestions(BaseModel):
    drama_id: int
    engine: str
    current_engine: str
    available: bool
    reason_labels: Dict[str, str]
    lines: List[StrongerLineSuggestion]


class StrongerLineTryRequest(BaseModel):
    """Empty on purpose: the engine is the one chosen in Settings, never the
    caller's, and the key is resolved on the PC."""
    model_config = ConfigDict(extra="forbid")


class StrongerLineResult(BaseModel):
    drama_id: int
    line_id: int
    engine: str
    model: Optional[str] = None
    text: str
    based_on_en: str
    cost_usd: float
