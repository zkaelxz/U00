"""api/schemas/word_replacements.py -- a series' word replacements, applied to every line a translation run writes."""

from typing import List

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["WordReplacement", "WordReplacementWrite", "WordReplacementDeleteResult"]


class WordReplacement(BaseModel):
    id: int
    find_text: str
    replace_text: str


class WordReplacementWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    find_text: str = Field(min_length=1, max_length=200)
    replace_text: str = Field(max_length=200)


class WordReplacementDeleteResult(BaseModel):
    deleted: List[int]
