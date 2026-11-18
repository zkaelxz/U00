"""api/schemas/misheard.py -- Review's "May be mis-heard" suggestions and applying one."""
from pydantic import BaseModel, Field

__all__ = ["MisheardApplyBody", "MisheardApplyResult", "MisheardLine", "MisheardList",
           "MisheardRememberBody", "MisheardRememberResult", "MisheardSuggestion"]


class MisheardLine(BaseModel):
    idx: int
    line_id: int | None = None
    # The line's text when the check read it: "Use it" sends it back as expected_zh.
    zh: str
    start: float
    end: float


class MisheardSuggestion(BaseModel):
    heard: str
    suggested: str
    source: str
    heard_count: int
    suggested_count: int
    # Lines that hold the heard spelling now: what "Use everywhere" would change.
    line_count: int
    # The series glossary term this suggestion matches, when one can take a remembered spelling.
    term_id: int | None = None
    lines: list[MisheardLine]


class MisheardList(BaseModel):
    available: bool
    reason: str
    lines_scanned: int
    truncated: bool
    suggestions: list[MisheardSuggestion]


class MisheardApplyBody(BaseModel):
    heard: str = Field(min_length=1, max_length=40)
    suggested: str = Field(min_length=1, max_length=40)
    # One line (the person pressed "Use it") or every line holding the spelling.
    line_id: int | None = Field(default=None, ge=1)
    # The text the person saw on that line; a line edited since is skipped, not overwritten.
    expected_zh: str | None = Field(default=None, max_length=5000)


class MisheardApplyResult(BaseModel):
    applied: int
    skipped: int


class MisheardRememberBody(BaseModel):
    heard: str = Field(min_length=1, max_length=40)
    suggested: str = Field(min_length=1, max_length=40)
    term_id: int = Field(ge=1)


class MisheardRememberResult(BaseModel):
    applied: int
    skipped: int
    aliases_added: int
