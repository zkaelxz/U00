"""api/schemas/common.py -- Shared building blocks: the error envelope and the
API version, plus any model used by more than one domain module.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

__all__ = [
    "API_VERSION",
    "ErrorInfo",
    "ErrorResponse",
    "TranslateEngine",
    "LibraryPreset",
]


API_VERSION = "0.1"


class ErrorInfo(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. `not_found`.")
    message: str = Field(description="Human-readable explanation, safe to display.")
    details: Optional[Any] = Field(default=None, description="Optional structured extra.")


class ErrorResponse(BaseModel):
    error: ErrorInfo


class TranslateEngine(BaseModel):
    """One entry from translate_engines.ENGINES --
    key_configured is a boolean only, never a key value."""
    name: str
    label: str
    free: bool
    models: Optional[List[str]] = None
    # Label for an offered model that has no built-in entry (id -> text).
    model_labels: Dict[str, str] = Field(default_factory=dict)
    key_configured: bool


class LibraryPreset(BaseModel):
    id: int
    name: str
    translation_engine: Optional[str] = None
    engine_model: Optional[str] = None
    style_preset: Optional[str] = None
    locale: Optional[str] = None
    default_female_pronouns: Optional[int] = None
    include_genre_notes: Optional[int] = None
