"""api/schemas/shared_glossary.py -- shared glossary groups (a glossary several
series inherit) and a series' effective glossary with where each term came from."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt

__all__ = ["SharedGlossaryTerm", "SharedGlossaryTermWrite", "SharedGlossaryGroup", "SharedGlossaryGroupList", "SharedGlossaryCreateRequest",
           "SharedGlossaryDeleteResult", "SharedGlossaryTermDeleteResult",
           "EffectiveGlossaryTerm", "SeriesSharedGlossary", "SeriesSharedGlossaryRequest"]


class SharedGlossaryTerm(BaseModel):
    id: int
    term_original: str
    term_translation: str
    notes: str = ""
    category: Optional[str] = None
    policy: Optional[str] = None
    enforce_exact: bool = False
    aliases: List[str] = Field(default_factory=list)
    banned_translations: List[str] = Field(default_factory=list)


class SharedGlossaryTermWrite(BaseModel):
    """With `id` the term is updated in place (omitted fields keep their stored
    value); without it the term is keyed on term_original."""
    model_config = ConfigDict(extra="forbid")
    id: Optional[int] = None
    term_original: Optional[str] = None
    term_translation: Optional[str] = None
    notes: Optional[str] = None
    category: Optional[str] = None
    policy: Optional[str] = None
    enforce_exact: Optional[bool] = None
    aliases: Optional[List[str]] = None
    banned_translations: Optional[List[str]] = None


class SharedGlossaryGroup(BaseModel):
    id: int
    name: str
    term_count: int
    series_count: int = Field(description="Series that currently inherit it.")
    can_edit: bool = Field(description="The viewer may change its terms and delete it.")


class SharedGlossaryGroupList(BaseModel):
    items: List[SharedGlossaryGroup]


class SharedGlossaryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=1000)


class SharedGlossaryDeleteResult(BaseModel):
    deleted: bool
    group_id: int
    series_unlinked: int


class SharedGlossaryTermDeleteResult(BaseModel):
    deleted: bool


class EffectiveGlossaryTerm(SharedGlossaryTerm):
    origin: Literal["series", "shared"]
    origin_name: Optional[str] = Field(None, description="The group's name when origin is shared.")


class SeriesSharedGlossary(BaseModel):
    series_id: int
    group: Optional[SharedGlossaryGroup] = None
    terms: List[EffectiveGlossaryTerm] = Field(
        description="The glossary the series uses: its group's terms overlaid by its own.")
    can_edit: bool = Field(description="The viewer may join or leave a group for this series.")


class SeriesSharedGlossaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: Optional[StrictInt] = Field(None, ge=1, le=2**31 - 1,
                                          description="The group to join; null leaves it.")
