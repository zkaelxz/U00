"""api/schemas/comic_editor.py -- the comic editor's writes to one text box
(PATCH .../pages/{page_id}/regions/{region_id}), a new box (POST
.../pages/{page_id}/regions; DELETE of one takes only a query rev) and its
undo (POST .../pages/{page_id}/undo) under /api/scanlate. All answer with
the page detail (api.scanlate_schemas.ScanlatePageDetail)."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ComicRegionFields", "ComicRegionEditRequest", "ComicRegionUndoRequest",
           "ComicRegionNew", "ComicRegionCreateRequest", "MAX_REGION_TEXT_CHARS"]

MAX_REGION_TEXT_CHARS = 2000
_PIXELS = 100_000


class ComicRegionFields(BaseModel):
    """The fields the editor may change, all optional: only the ones sent
    count. text_color null puts the automatic colour back."""
    model_config = ConfigDict(extra="forbid")
    translated_text: Optional[str] = Field(None, max_length=MAX_REGION_TEXT_CHARS)
    x: Optional[int] = Field(None, ge=0, le=_PIXELS)
    y: Optional[int] = Field(None, ge=0, le=_PIXELS)
    w: Optional[int] = Field(None, ge=1, le=_PIXELS)
    h: Optional[int] = Field(None, ge=1, le=_PIXELS)
    font_size: Optional[int] = Field(None, ge=6, le=200)
    font_category: Optional[str] = Field(None, min_length=1, max_length=20)
    kind: Optional[str] = Field(None, min_length=1, max_length=20)
    text_color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class ComicRegionEditRequest(BaseModel):
    """expected_rev: the page rev the client read (409 if it moved on).
    expected: the box's values the client saw for any fields it wants
    checked as well, usually the ones it changes."""
    model_config = ConfigDict(extra="forbid")
    expected_rev: int = Field(ge=0, le=2**31 - 1)
    expected: Optional[ComicRegionFields] = None
    changes: ComicRegionFields


class ComicRegionUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_rev: int = Field(ge=0, le=2**31 - 1)


class ComicRegionNew(BaseModel):
    """A text box the user drew: x, y, w, h required, the rest optional
    (empty English, size 18, regular style, a bubble, automatic colour)."""
    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0, le=_PIXELS)
    y: int = Field(ge=0, le=_PIXELS)
    w: int = Field(ge=1, le=_PIXELS)
    h: int = Field(ge=1, le=_PIXELS)
    translated_text: Optional[str] = Field(None, max_length=MAX_REGION_TEXT_CHARS)
    font_size: Optional[int] = Field(None, ge=6, le=200)
    font_category: Optional[str] = Field(None, min_length=1, max_length=20)
    kind: Optional[str] = Field(None, min_length=1, max_length=20)
    text_color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class ComicRegionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_rev: int = Field(ge=0, le=2**31 - 1)
    region: ComicRegionNew
