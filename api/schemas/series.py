"""api/schemas/series.py -- /api/series: the Series page (titles and
counts), rename, delete and copying glossary terms between series. Ids,
names and counts only; no owner ids, paths or term translations."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

__all__ = ["SeriesAutoRunState", "SeriesAutoRunOn", "SeriesAutoRunCeiling", "SeriesListItem", "SeriesListResponse", "SeriesTitle", "SeriesPage",
           "SeriesRenameRequest", "SeriesRenameResult", "SeriesDeletePreview",
           "SeriesDeleteRequest", "SeriesDeleteResult", "SeriesCopyTermsRequest",
           "SeriesCopyTermsPreview", "SeriesCopyTermsResult", "SeriesDefaultsField",
           "SeriesDefaults", "SeriesDefaultsUpdate", "SeriesDefaultsTitle",
           "SeriesDefaultsPreview", "SeriesDefaultsApplyRequest", "SeriesDefaultsApplyResult",
           "SeriesTmxImportRequest", "SeriesTmxImportResult", "SeriesSpeakerModeRequest",
           "SeriesSpeakerModeResult", "ConsistencyTitleLines", "ConsistencyExample",
           "ConsistencyRendering", "ConsistencyTerm", "ConsistencySummary", "SeriesConsistencyReport"]

_ID: dict[str, Any] = dict(ge=1, le=2**31 - 1)


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
    release_date: Optional[str] = None
    line_count: int
    translated_count: int = 0


class SeriesPage(BaseModel):
    id: int
    name: str
    is_private: bool
    owned_by_me: bool
    can_edit: bool = Field(description="The viewer may rename it and copy terms into it.")
    has_instructions: bool
    glossary_term_count: int
    character_count: int
    speaker_mode: Literal["auto", "solo"] = Field(
        description="solo: one creator, so speaker detection keeps one speaker.")
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


class SeriesDefaultsField(BaseModel):
    key: str
    label: str
    group: str = Field(description="translate or transcribe")
    type: str = Field(description="bool, int, number or choice")
    choices: Optional[List[str]] = None
    min: Optional[float] = None
    max: Optional[float] = None


class SeriesDefaults(BaseModel):
    series_id: int
    # Any: a bool must stay a bool and a whole number a number (no coercion).
    settings: Dict[str, Any]
    fields: List[SeriesDefaultsField]


class SeriesDefaultsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: Dict[str, Any] = Field(description="setting: value; {} clears every default. "
                                                 "The service checks keys, types and bounds.")


class SeriesDefaultsTitle(BaseModel):
    id: int
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    changes: Dict[str, Dict[str, Any]] = Field(description='setting: {"from": ..., "to": ...}')
    unsuited: List[str] = Field(description="Defaults skipped: not for this title's language.")


class SeriesDefaultsPreview(BaseModel):
    series_id: int
    mode: str
    title_count: int
    changed_titles: int
    fields_changed: int
    job_running: bool
    titles: List[SeriesDefaultsTitle]


class SeriesDefaultsApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["fill", "overwrite"] = "fill"


class SeriesDefaultsApplyResult(BaseModel):
    series_id: int
    mode: str
    title_count: int
    titles_changed: int
    fields_changed: int


class SeriesTmxImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tmx: str = Field(min_length=1, max_length=5_000_000, description="The TMX file's text.")


class SeriesTmxImportResult(BaseModel):
    series_id: int
    added: int
    skipped: int = Field(description="Sources the series already had; their stored translation is kept.")


class SeriesSpeakerModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "solo"]


class SeriesSpeakerModeResult(BaseModel):
    series_id: int
    speaker_mode: Literal["auto", "solo"]


class SeriesAutoRunState(BaseModel):
    series_id: int
    on: bool
    ceiling_usd: float = Field(description="What the auto-run may spend on its episodes in all.")
    spent_usd: float
    left_usd: float
    reviewed: bool = Field(description="The first episode was checked and Continue pressed.")
    hold: str = Field(description="Why it is waiting for the owner; empty when it is going.")
    hold_kind: Literal["", "review", "stopped", "ceiling", "queue"]
    current_drama_id: Optional[int] = None
    episodes_run: int


class SeriesAutoRunOn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ceiling_usd: float = Field(gt=0, le=1000, description="Required: a paid run always starts from a figure the owner saw.")


class SeriesAutoRunCeiling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ceiling_usd: float = Field(gt=0, le=1000)


class ConsistencyTitleLines(BaseModel):
    drama_id: int
    line_count: int
    line_ids: List[int] = Field(description="Empty unless the rendering is flagged; capped.")


class ConsistencyExample(BaseModel):
    drama_id: int
    line_id: int
    idx: int
    en: str


class ConsistencyRendering(BaseModel):
    text: str
    kind: Literal["glossary", "other", "unnamed"] = Field(
        description="glossary: the term's required English. other: a different name. "
                    "unnamed: no name in the line (never flagged).")
    count: int
    flagged: bool
    titles: List[ConsistencyTitleLines]
    examples: List[ConsistencyExample]


class ConsistencyTerm(BaseModel):
    term_id: int
    term_original: str
    term_translation: str
    renderings: List[ConsistencyRendering]
    flagged_lines: int


class ConsistencySummary(BaseModel):
    titles_scanned: int
    lines_scanned: int
    terms_checked: int
    terms_found: int
    names_with_several_renderings: int
    flagged_lines: int
    glossary_missing_lines: int


class SeriesConsistencyReport(BaseModel):
    series_id: int
    truncated: bool
    summary: ConsistencySummary
    terms: List[ConsistencyTerm]
