"""
api/comic_schemas.py -- request/response models for the comic viewer
routes (api/routers/comic_routes.py). Kept out of api/schemas.py so the
slice could be built alongside another branch editing that file; the
shared ErrorResponse still lives there. No path or filename field exists
on any model.
"""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class ComicPage(BaseModel):
    id: int
    ordinal: int
    width: int
    height: int
    has_rendered: bool
    has_regions: bool
    image_version: int


class ComicPageList(BaseModel):
    drama_id: int
    media_type: str
    reading_mode_default: Literal["paged", "vertical"]
    page_count: int
    pages: List[ComicPage]
    chapters: List[dict] = Field(default_factory=list)


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
