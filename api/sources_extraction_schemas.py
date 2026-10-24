"""
api/sources_extraction_schemas.py -- request/response models for the
pasted-URL extraction extras: the AI fallback opt-in on the URL imports,
the engine list, the comic import and the Review extraction step. The
shared SourcesUrlImportRequest lives in api/schemas/sources.py. No model
carries a key, and no response carries one.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from api.schemas import SourcesUrlImportRequest
from services.sources_import_service import FOLLOW_STOPS, MAX_FOLLOW_PAGES


class SourcesUrlImportAiRequest(SourcesUrlImportRequest):
    """POST /api/sources/url/import. `use_ai` turns on the LLM fallback
    (off by default; it is only asked when deterministic extraction comes
    back empty or ambiguous). `engine` omitted = the saved default engine.
    `review`: open a Review extraction instead of writing, even when the
    result looks sure."""
    use_ai: StrictBool = False
    engine: Optional[StrictStr] = Field(default=None, min_length=1, max_length=40)
    review: StrictBool = False


class SourcesUrlImportFollowRequest(SourcesUrlImportAiRequest):
    """POST /api/sources/url/import. `follow_pages` above 1: also follow
    each page's next-chapter link, up to that many pages in all, on the
    same site; the pages read open as a review (nothing is written until
    the person imports the ones they keep). 1 = the one page, as before."""
    follow_pages: StrictInt = Field(default=1, ge=1, le=MAX_FOLLOW_PAGES)


class SourcesAiRecoverRequest(BaseModel):
    """POST /api/sources/{name}/import/{chapter_id}/ai-recover. The person's
    confirm for one AI call on a chapter whose page layout changed; `engine`
    is required and `confirm` must be true."""
    model_config = ConfigDict(extra="forbid")
    series_id: StrictStr = Field(min_length=1, max_length=200)
    drama_id: StrictInt = Field(ge=1)
    engine: StrictStr = Field(min_length=1, max_length=40)
    confirm: StrictBool


class SourcesAiEngines(BaseModel):
    """Engine names the AI fallback can use, and the saved default when it
    is one of them. Names only: no key and no key status."""
    engines: List[str]
    default: Optional[str] = None
    # The engines that need no `engines.paid` (the rest may be paid).
    free: List[str] = []


class SourcesComicUrlImportRequest(SourcesUrlImportAiRequest):
    """POST /api/sources/url/import-comic. The drama must be a manhua,
    manga or manhwa drama."""


# ---------------------------------------------------------------------------
# Review extraction (GET/POST /api/sources/dramas/{drama_id}/extraction...)
# ---------------------------------------------------------------------------

class ExtractionFieldConfidence(BaseModel):
    field: str
    bucket: Optional[str] = None
    score: float
    checks: List[str]
    # The field's value when it is short text (a URL as scheme+host+path).
    value: Optional[str] = None


class ExtractionOverall(BaseModel):
    bucket: Optional[str] = None
    score: float


class ExtractionConfidence(BaseModel):
    """Checked independently, never taken from the AI's own claim."""
    overall: ExtractionOverall
    fields: List[ExtractionFieldConfidence]


class ExtractionPendingProfile(BaseModel):
    bucket: Optional[str] = None


class ExtractionReport(BaseModel):
    headline: str
    lines: List[str]
    llm_calls: int
    cache_hit: bool
    profile: str
    # A validated site-profile candidate waiting for approval.
    pending_profile: Optional[ExtractionPendingProfile] = None


class ExtractionOption(BaseModel):
    selector: str
    preview: str


class ExtractionContainer(BaseModel):
    selector: str
    chars: int
    preview: str
    # What can be left out when this container holds the text.
    exclusions: List[ExtractionOption]


class ExtractionIdText(BaseModel):
    id: str
    text: str


class ExtractionLink(BaseModel):
    id: str
    text: str
    url: Optional[str] = None


class ExtractionNovel(BaseModel):
    text_preview: str
    char_count: int
    chapter_title: str
    containers: List[ExtractionContainer]
    content_selector: Optional[str] = None
    exclude_selectors: List[str]
    headings: List[ExtractionIdText]
    title_block: Optional[str] = None
    links: List[ExtractionLink]
    next_link: Optional[str] = None
    previous_link: Optional[str] = None
    number_from: Literal["title", "url"]


class ExtractionImage(BaseModel):
    id: int
    display_url: Optional[str] = None
    attr: str
    width: int
    height: int
    role: str
    # Reading order from 1; 0 = not a page.
    page: int
    reason: str
    # A thumbnail can be fetched from .../extraction/images/{id}.
    has_image: bool


class ExtractionComic(BaseModel):
    images: List[ExtractionImage]
    roles: List[str]
    page_count: int


class ExtractionFollowedPage(BaseModel):
    """One page of a followed import. id 0 is the reviewed first page."""
    id: int
    title: str
    char_count: int
    # The page's host name only.
    host: str


class ExtractionFollow(BaseModel):
    """The pages a followed import read, in reading order, and why it
    stopped following."""
    pages: List[ExtractionFollowedPage]
    stop: Literal[FOLLOW_STOPS]


class ExtractionReview(BaseModel):
    kind: Literal["extraction_review"]
    drama_id: int
    revision: str
    content_type: Literal["novel", "comic"]
    why: Literal["low_confidence", "asked", "diagnostics", "follow", "recovery"]
    display_url: Optional[str] = None
    confidence: ExtractionConfidence
    report: ExtractionReport
    can_save_profile: bool
    novel: Optional[ExtractionNovel] = None
    comic: Optional[ExtractionComic] = None
    # Set for a novel import that followed next-chapter links.
    follow: Optional[ExtractionFollow] = None


_REVISION = Field(min_length=1, max_length=64)
_SELECTOR = Field(min_length=1, max_length=300)
_ID = Field(default=None, min_length=1, max_length=20)


class ExtractionRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictStr = _REVISION


class ExtractionImportRequest(BaseModel):
    """`pages`: for a followed novel import, the ids of the pages to import
    (omitted = all); written in reading order."""
    model_config = ConfigDict(extra="forbid")
    revision: StrictStr = _REVISION
    pages: Optional[List[StrictInt]] = Field(default=None, max_length=MAX_FOLLOW_PAGES)


class ExtractionNovelRerunRequest(BaseModel):
    """Every value is one the review offered (by selector or id)."""
    model_config = ConfigDict(extra="forbid")
    revision: StrictStr = _REVISION
    content_selector: StrictStr = _SELECTOR
    exclude_selectors: List[StrictStr] = Field(default_factory=list, max_length=15)
    title_block: Optional[StrictStr] = _ID
    next_link: Optional[StrictStr] = _ID
    previous_link: Optional[StrictStr] = _ID
    number_from: Literal["title", "url"] = "title"


class ExtractionImageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictInt = Field(ge=0, le=10_000)
    role: StrictStr = Field(min_length=1, max_length=20)
    page: StrictInt = Field(default=0, ge=0, le=500)


class ExtractionComicRerunRequest(BaseModel):
    """Roles and page numbers keyed by the image id the review gave."""
    model_config = ConfigDict(extra="forbid")
    revision: StrictStr = _REVISION
    images: List[ExtractionImageChoice] = Field(max_length=400)


class ExtractionProfileSaved(BaseModel):
    domain: str
    kind: str
    version: int
    replaces: Optional[int] = None
