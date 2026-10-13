"""api/schemas/link_details.py -- "Fill from link" shapes: what a link says
about a title, the blank-only apply, and the read-only scan of old titles
(services/link_details_service.py)."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictStr

__all__ = [
    "MediaLinkProbeRequest",
    "MediaLinkProbe",
    "LinkDetailFields",
    "TitleLinkProbe",
    "LinkDetailsApplyRequest",
    "LinkDetailsApplyResult",
    "LinkScanStarted",
    "LinkScanRow",
    "LinkScanResult",
]


class MediaLinkProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(min_length=1, max_length=2000)


class MediaLinkProbe(BaseModel):
    """`kind` "page": nothing was read (not a video or supported-source
    link); the AI listing autofill is a separate, paid step. Never the
    fetched link itself: `display_url` is scheme, host and path only."""
    kind: Literal["video", "source", "page"]
    title: str = ""
    channel: str = ""
    release_date: str = ""          # YYYY-MM-DD, "" = the link gives none
    is_live: bool = False
    summary: str = ""
    platform: str = ""
    series_suggestion: str = ""
    note: str = ""
    display_url: str = ""


class LinkDetailFields(BaseModel):
    title_zh: str = ""
    author: str = ""
    release_date: str = ""
    series: str = ""


class TitleLinkProbe(BaseModel):
    drama_id: int
    link: MediaLinkProbe
    current: LinkDetailFields
    suggested: LinkDetailFields


class LinkDetailsApplyRequest(BaseModel):
    """Only the ticked fields; each is written only while still blank."""
    model_config = ConfigDict(extra="forbid")
    title_zh: Optional[StrictStr] = Field(default=None, max_length=300)
    author: Optional[StrictStr] = Field(default=None, max_length=300)
    release_date: Optional[StrictStr] = Field(default=None, max_length=10)
    series: Optional[StrictStr] = Field(default=None, max_length=300)


class LinkDetailsApplyResult(BaseModel):
    applied: List[str]
    kept: List[str]          # had text by the time of the write, left as it was


class LinkScanStarted(BaseModel):
    job_id: str
    total: int


class LinkScanRow(BaseModel):
    drama_id: int
    name: str = ""
    display_url: str = ""
    blank: List[str] = []
    current: Optional[LinkDetailFields] = None
    suggested: Optional[LinkDetailFields] = None
    note: str = ""


class LinkScanResult(BaseModel):
    job_id: str
    status: str
    total: int = 0
    titles: List[LinkScanRow] = []
