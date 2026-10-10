"""
api/comic_schemas.py -- request/response models for the comic viewer
routes (api/routers/comic_routes.py). No path or filename field exists on
any model.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ComicPage(BaseModel):
    id: int
    ordinal: int
    width: int
    height: int
    has_rendered: bool
    has_regions: bool
    image_version: int
    chapter_id: Optional[str] = None
    chapter_page: int = 0
    hidden: bool = False


class ComicChapter(BaseModel):
    id: str
    title: str
    known: bool
    first_page: int
    page_count: int
    hidden_count: int
    # The chapter's page on its source (display-safe), "" when not known.
    url: str = ""


class ComicPageList(BaseModel):
    drama_id: int
    media_type: str
    reading_mode_default: Literal["paged", "vertical"]
    page_count: int
    pages: List[ComicPage]
    hidden_count: int = 0
    chapters: List[ComicChapter] = Field(default_factory=list)


class ComicRegion(BaseModel):
    idx: int
    x: int
    y: int
    w: int
    h: int
    translated_text: str
    source_text: str
    kind: str


class ComicPageRegions(BaseModel):
    page_id: int
    width: int
    height: int
    regions: List[ComicRegion]


class ComicProgress(BaseModel):
    last_page: int
    percent_complete: float


class ComicProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1, le=1_000_000)


class ComicVisibilityRequest(BaseModel):
    """Hide or restore pages: by page ids, or the first/last `count` pages of
    one chapter. Exactly one of the two."""
    model_config = ConfigDict(extra="forbid")
    hidden: bool
    page_ids: Optional[List[int]] = Field(None, min_length=1, max_length=2000)
    chapter_id: Optional[str] = Field(None, min_length=1, max_length=200)
    edge: Optional[Literal["first", "last", "all"]] = None
    count: int = Field(1, ge=1, le=100)


class ComicVisibilityResult(BaseModel):
    changed: int
    hidden_count: int
