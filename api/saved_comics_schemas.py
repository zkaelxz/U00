"""
api/saved_comics_schemas.py -- models for api/routers/saved_comics_routes.py
(the save folder for chapters saved as CBZ, and reading them in the app).
Series and chapters are named by their folder and file names only; the one
path anywhere here is the PC-only save folder.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SavedComicsFolder(BaseModel):
    """PC only: where chapters saved as CBZ go. `custom`: the owner picked
    it; `picked_missing`: a picked folder is gone, so the default is used."""
    folder: str
    custom: bool
    picked_missing: bool


class SavedComicsFolderUpdate(BaseModel):
    """A full path to an existing folder; "" goes back to the default."""
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(max_length=1000)


class SavedComicsOpened(BaseModel):
    opened: bool


class SavedSeries(BaseModel):
    source: str
    series: str
    chapter_count: int
    updated_at: Optional[float] = None


class SavedChapter(BaseModel):
    # The file name without .cbz: the chapter's id in this API.
    chapter: str
    title: str
    # Its place in the series' reading order, when the name carries one.
    number: Optional[int] = None


class SavedChapterList(BaseModel):
    source: str
    series: str
    chapters: List[SavedChapter]


class SavedPage(BaseModel):
    page: int
    width: Optional[int] = None
    height: Optional[int] = None


class SavedChapterPages(SavedChapter):
    source: str
    series: str
    pages: List[SavedPage]
    prev_chapter: Optional[str] = None
    next_chapter: Optional[str] = None
