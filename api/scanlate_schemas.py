"""
api/scanlate_schemas.py -- request/response models for the automatic
Scanlate routes (api/routers/scanlate_routes.py). No path, filename or key
field exists on any model.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ScanlateEngine(BaseModel):
    name: str
    label: str
    free: bool
    key_configured: bool


class ScanlateUploadLimits(BaseModel):
    image_types: List[str]
    pdf: bool
    max_image_mb: int
    max_image_megapixels: int
    max_pdf_mb: int
    max_pdf_pages: int
    max_files: int
    max_total_mb: int
    strip_slice_ratio: int
    slice_strips_default: bool


class ScanlateConfig(BaseModel):
    drama_id: int
    source_language: str
    engines: List[ScanlateEngine]
    default_engine: str
    detect_backends: List[str]
    ml_weights_cached: bool
    lama_weights_cached: bool
    ocr_backend: str
    ocr_backend_installed: bool
    page_count: int
    pages_with_regions: int
    pages_rendered: int
    job_id: str
    job_running: bool
    upload_limits: ScanlateUploadLimits


class ScanlateNote(BaseModel):
    level: str
    message: str


class ScanlateRegion(BaseModel):
    id: int
    idx: int
    x: int
    y: int
    w: int
    h: int
    source_text: str
    translated_text: str
    font_size: int
    font_category: str
    kind: str
    skip: bool
    include_sfx: bool
    language: Optional[str] = None
    orientation: Optional[str] = None
    # "#rrggbb" set in the editor; None lets the render choose.
    text_color: Optional[str] = None


class ScanlatePageDetail(BaseModel):
    id: int
    drama_id: int
    ordinal: int
    width: int
    height: int
    rev: int
    # Editor edits that can be undone one after another (0 after a job or
    # another writer changed the page since the latest one).
    undo_depth: int = 0
    has_rendered: bool
    regions: List[ScanlateRegion]
    run_notes: List[ScanlateNote]


class ScanlatePageNotes(BaseModel):
    page_id: int
    ordinal: int
    notes: List[ScanlateNote]


class ScanlateRunNotes(BaseModel):
    drama_id: int
    pages: List[ScanlatePageNotes]


class ScanlateUploadResult(BaseModel):
    added: int
    page_ids: List[int]
    pdf_pages_skipped: int
    strips_sliced: int


class ScanlateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["missing", "page", "all"] = "missing"
    page_id: Optional[int] = Field(None, ge=1, le=2**31 - 1)
    confirm: bool = False
    chapter_id: Optional[str] = Field(None, min_length=1, max_length=200)
    engine: Optional[str] = Field(None, min_length=1, max_length=40)
    detect_backend: Literal["auto", "cv", "ml"] = "auto"
    # Also translate the large text drawn on the art (paints it out first).
    art_captions: bool = False


class ScanlateRegionRedoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_rev: int = Field(ge=0, le=2**31 - 1)
    mode: Literal["both", "translate"] = "both"
    engine: Optional[str] = Field(None, min_length=1, max_length=40)


class ScanlateRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: Optional[int] = Field(None, ge=1, le=2**31 - 1)


class ScanlateExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formats: List[Literal["zip", "pdf"]] = Field(default_factory=lambda: ["zip", "pdf"],
                                                 min_length=1, max_length=2)
    # False writes every page as the original image, whatever has been typeset.
    typeset: bool = True


class ScanlateJobStarted(BaseModel):
    job_id: str
    engine: Optional[str] = None
    mode: Optional[str] = None
