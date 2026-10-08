"""api/schemas/novel_chapters.py -- the chapters saved in a drama's raw
novel: a bounded list page and a bounded slice of one chapter's text.
Counts, titles and text only: never a filename, path or source URL."""

from typing import List, Optional

from pydantic import BaseModel

__all__ = ["NovelChapterRow", "NovelChapterList", "NovelChapterText"]


class NovelChapterRow(BaseModel):
    number: int
    title: str
    chars: int
    source: str
    imported_at: str
    unsplit: bool
    in_translation: bool


class NovelChapterList(BaseModel):
    drama_id: int
    present: bool
    size_bytes: int
    # False: no usable chapter manifest, so the whole file is one block.
    split: bool
    total: int
    char_count: int
    # How many of `total` are already in the text used for translation,
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
    offset: int
    text: str
    # Character offset to ask for next; null at the end of the chapter.
    next_offset: Optional[int]
