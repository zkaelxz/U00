"""
api/sources_tools_schemas.py -- request/response models for the Sources
tools routes (api/routers/sources_tools_routes.py) and the Discover pasted
listing route. Kept out of api/schemas.py so this batch could be built
alongside another branch editing that file (precedent: api/comic_schemas.py).
The shared ErrorResponse and SourcesJobStarted still live there.

Pasted page source and pasted listing text are capped by the request body
size in the router (413), then by these models (422).
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from services.discover_lookup_service import MAX_PASTED_LISTING_CHARS
from services.sources_tools_service import MAX_PASTED_HTML_BYTES

_URL = Field(min_length=1, max_length=2000)


class SourcesPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = _URL


class SourcesPastedPreviewRequest(BaseModel):
    """`url` is the page the verification stopped at; `html` is its source
    as the person copied it from their browser."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = _URL
    html: StrictStr = Field(min_length=1, max_length=MAX_PASTED_HTML_BYTES)


class SourcesPastedImportRequest(SourcesPastedPreviewRequest):
    drama_id: int = Field(ge=1)


class SourcesIdentifyMediaRequest(BaseModel):
    """Without `html` the page is fetched; with it nothing is requested."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = _URL
    html: Optional[StrictStr] = Field(default=None, min_length=1,
                                      max_length=MAX_PASTED_HTML_BYTES)


class SourcesMediaResource(BaseModel):
    """PC only: the full address of one identified resource, for the video
    download (POST /api/media/dramas/{drama_id}/download-url)."""
    run_id: str
    index: int
    resource_url: str


class SourcesPastedPreview(BaseModel):
    """The URL preview's result shape (kind "url_preview"), from pasted
    page source. `pasted` is always true."""
    kind: str
    content_type: str
    route: str
    platform: str
    title: str
    chapter: str
    chapter_id: Optional[str] = None
    language: str
    chapter_count: Optional[int] = None
    adapter: Optional[str] = None
    series_id: Optional[str] = None
    text_length: Optional[int] = None
    image_count: Optional[int] = None
    notes: List[str]
    display_url: str
    pasted: bool


class SourceExtractionAccess(BaseModel):
    authentication: Optional[str] = None
    entitlement: Optional[str] = None
    technical_protection: Optional[str] = None
    protection_detail: List[str]


class SourceExtraction(BaseModel):
    """One pasted-URL attempt. `url` is scheme+host+path only."""
    url: str
    created_at: Optional[float] = None
    content_type: str
    headline: str
    tier: Optional[str] = None
    extraction_tier: Optional[str] = None
    llm_calls: int
    cache_hit: bool
    profile: str
    confidence: Optional[str] = None
    access: Optional[SourceExtractionAccess] = None
    resource_types: List[str]
    reason: str
    lines: List[str]


class DiscoverBulkPastedRequest(BaseModel):
    """Listing text copied from the browser (select all, copy)."""
    model_config = ConfigDict(extra="forbid")
    text: StrictStr = Field(min_length=1, max_length=MAX_PASTED_LISTING_CHARS)
    source_label: StrictStr = Field(default="", max_length=100)
    engine: Optional[StrictStr] = Field(default=None, max_length=40)
