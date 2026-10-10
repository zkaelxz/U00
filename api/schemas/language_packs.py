"""api/schemas/language_packs.py -- /api/language-packs: the built-in
language packs, a title's choice of packs, and the per-language default.
Pack text only; no file paths.
"""

from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field

__all__ = ["LanguagePackStyles", "LanguagePackSummary", "LanguagePackEntry", "LanguagePack",
           "TitleLanguagePack", "TitleLanguagePacks", "LanguagePackChoice", "LanguagePackDefaultResult"]


class LanguagePackStyles(BaseModel):
    # Empty default and no options for a pack with a single rendering.
    default: str
    options: Dict[str, str]


class LanguagePackSummary(BaseModel):
    id: str
    version: int
    language: str  # zh, ja, ko, or any
    title: str
    description: str
    entry_count: int
    styles: LanguagePackStyles


class LanguagePackEntry(BaseModel):
    source: str
    # Text, or one rendering per style id.
    en: Union[str, Dict[str, str]]
    category: str
    note: str
    context: str


class LanguagePack(LanguagePackSummary):
    entries: List[LanguagePackEntry]


class TitleLanguagePack(LanguagePackSummary):
    enabled: bool
    style: Optional[str] = None


class TitleLanguagePacks(BaseModel):
    source_language: str
    # True while the title follows the default for its language.
    uses_default: bool
    packs: List[TitleLanguagePack]


class LanguagePackChoice(BaseModel):
    # Pack id -> style id (null for the pack's default). Packs left out are off.
    packs: Dict[str, Optional[str]] = Field(max_length=50)


class LanguagePackDefaultResult(BaseModel):
    language: str
    packs: List[str]
