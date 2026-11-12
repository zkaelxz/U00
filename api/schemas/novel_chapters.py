"""api/schemas/novel_chapters.py -- the chapters saved in a drama's raw
novel: a bounded list page and a bounded slice of one chapter's text.
Counts, titles and text, plus each chapter's display-safe source page link
(scheme, host and path only); never a filename or path."""

from typing import List, Optional

from pydantic import BaseModel, Field

__all__ = ["NovelChapterRow", "NovelChapterList", "NovelChapterText", "NovelLineChapterRow",
           "NovelLineChapters", "NovelChapterRenameRequest",
           "NovelChapterRenamed", "NovelChapterMoveRequest", "NovelChapterMoved"]


class NovelChapterRow(BaseModel):
    number: int
    title: str
    chars: int
    source: str
    imported_at: str
    unsplit: bool
    in_translation: bool
    url: str = ""


class NovelChapterList(BaseModel):
    drama_id: int
    present: bool
    size_bytes: int
    # False: no usable chapter manifest, so the whole file is one block.
    split: bool
    total: int
    char_count: int
    # How many of the rows in `chapters` (this page, not all `total`) are
    # already in the text used for translation,
    # and how long that text is (0: none attached).
    in_translation: int
    translation_chars: int
    offset: int
    limit: int
    chapters: List[NovelChapterRow]


class NovelChapterText(BaseModel):
    drama_id: int
    number: int
    title: str
    source: str
    imported_at: str
    unsplit: bool
    chars: int
    in_translation: bool
    url: str = ""
    offset: int
    text: str
    # Character offset to ask for next; null at the end of the chapter.
    next_offset: Optional[int]


class NovelLineChapterRow(BaseModel):
    # The saved chapter's number in the raw-novel chapter list (1-based), or a
    # running count when chapters were found from headings in the lines.
    number: int
    title: str
    first_idx: int
    line_count: int
    translated: int
    reviewed: int


class NovelLineChapters(BaseModel):
    drama_id: int
    total_lines: int
    # "saved": from the saved chapters' titles; "headings": from chapter
    # headings in the lines; "": no chapters could be told apart.
    source: str
    chapters: List[NovelLineChapterRow]


class NovelChapterRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=1000)


class NovelChapterRenamed(BaseModel):
    drama_id: int
    number: int
    title: str
    # False when no line of the title held the old chapter title (nothing to change there).
    line_updated: bool
    # True when the translation text was a copy of the saved chapters and was updated too.
    translation_text_updated: bool = False


class NovelChapterMoveRequest(BaseModel):
    # The position (1-based) the chapter should have afterwards.
    to_number: int = Field(ge=1, le=100000)


class NovelChapterMoved(BaseModel):
    drama_id: int
    number: int
    to_number: int
    lines_moved: int
    translation_text_updated: bool = False
    translation_text_stale: bool = False
