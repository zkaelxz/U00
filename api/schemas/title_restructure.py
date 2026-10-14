"""api/schemas/title_restructure.py -- merging a title into another and
moving chapters between titles: the chapter list, the preview (dry run) and
the result. Ids, names and counts only; never a path or stored filename."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

__all__ = ["TitleRestructureChapter", "TitleRestructureTarget", "TitleRestructureInfo", "TitleRestructureRequest",
           "TitleRestructureApplyRequest", "TitleRestructurePreviewChapter", "TitleRestructurePreview",
           "TitleRestructureResult"]


class TitleRestructureChapter(BaseModel):
    # Comic: the chapter group's id ("unknown" for unlabelled pages); novel:
    # the chapter's number as text.
    id: str
    title: str
    pages: Optional[int] = None
    chars: Optional[int] = None


class TitleRestructureTarget(BaseModel):
    id: int
    title: str


class TitleRestructureInfo(BaseModel):
    drama_id: int
    # "comic", "novel", or null for a title without chapters.
    kind: Optional[str]
    chapters: List[TitleRestructureChapter]
    # Titles of the same kind and original language this one can move to.
    targets: List[TitleRestructureTarget]


class TitleRestructureRequest(BaseModel):
    target_id: int = Field(ge=1, le=2**31 - 1)
    kind: Literal["merge", "move"]
    # Required for "move"; ignored for "merge" (every chapter).
    chapter_ids: List[str] = Field(default_factory=list, max_length=5000)
    # Move chapters the other title already has too, as separate chapters.
    move_duplicates: bool = False


class TitleRestructureApplyRequest(TitleRestructureRequest):
    confirm: bool = False
    fingerprint: str = Field(default="", max_length=128)


class TitleRestructurePreviewChapter(TitleRestructureChapter):
    duplicate: bool
    moves: bool
    # Novel: already copied into this title's translation text.
    in_translation: bool


class TitleRestructurePreview(BaseModel):
    source_id: int
    target_id: int
    kind: str
    media: Optional[str]
    target_title: str
    chapters: List[TitleRestructurePreviewChapter]
    moving_chapters: int
    moving_pages: int
    skipped_duplicates: int
    source_empty_after: bool
    stays: List[str]
    notes: List[str]
    # Why it can't be done; empty when it can.
    blocked: List[str]
    # Pass back to apply; it refuses if anything changed since.
    fingerprint: str


class TitleRestructureResult(BaseModel):
    source_id: int
    target_id: int
    kind: str
    moved_chapters: int
    moved_pages: int
    skipped_duplicates: int
    source_empty: bool
    # The safety copy's folder name, under the library's backups/restructure.
    snapshot: str
    warnings: List[str]
