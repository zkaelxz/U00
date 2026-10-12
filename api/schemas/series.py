"""api/schemas/series.py -- /api/series: the Series page (titles and
counts), rename, delete and copying glossary terms between series. Ids,
names and counts only; no owner ids, paths or term translations."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

__all__ = ["SeriesListItem", "SeriesListResponse", "SeriesTitle", "SeriesPage",
           "SeriesRenameRequest", "SeriesRenameResult", "SeriesDeletePreview",
           "SeriesDeleteRequest", "SeriesDeleteResult", "SeriesCopyTermsRequest",
           "SeriesCopyTermsPreview", "SeriesCopyTermsResult"]

_ID = dict(ge=1, le=2**31 - 1)


class SeriesListItem(BaseModel):
    id: int
    name: str
    is_private: bool
    owned_by_me: bool = Field(description="The signed-in viewer created it.")
    title_count: int
    glossary_term_count: int


class SeriesListResponse(BaseModel):
    items: List[SeriesListItem]


class SeriesTitle(BaseModel):
    id: int
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    media_type: Optional[str] = None
    status: Optional[str] = None
    episode_number: Optional[int] = None
    line_count: int


class SeriesPage(BaseModel):
    id: int
    name: str
    is_private: bool
    owned_by_me: bool
    can_edit: bool = Field(description="The viewer may rename it and copy terms into it.")
    has_instructions: bool
    glossary_term_count: int
    character_count: int
    titles: List[SeriesTitle]


class SeriesRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=1000)


class SeriesRenameResult(BaseModel):
    id: int
    name: str


class SeriesDeletePreview(BaseModel):
    series_id: int
    name: str
    title_count: int
    glossary_term_count: int
    character_count: int
    translation_memory_count: int
    dismissal_count: int
    style_profile_count: int = Field(description="The learned style profile (0 or 1).")
    has_instructions: bool
    needs_typed_confirm: bool = Field(description="It holds something: confirm_text DELETE.")
    job_running: bool


class SeriesDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=20)


class SeriesDeleteResult(BaseModel):
    deleted: bool
    series_id: int
    titles_kept: int
    glossary_terms_deleted: int
    characters_deleted: int


class SeriesCopyTermsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_series_id: StrictInt = Field(**_ID)


class SeriesCopyTermsPreview(BaseModel):
    series_id: int
    source_series_id: int
    new_count: int
    skipped_count: int
    new_terms: List[str] = Field(description="The first 50 terms that would be added.")
    skipped_terms: List[str] = Field(description="The first 50 already there.")


class SeriesCopyTermsResult(BaseModel):
    series_id: int
    source_series_id: int
    added: int
    skipped: int
