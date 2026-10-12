"""api/schemas/source_suspect.py -- the Review "Check source text" shapes."""
from pydantic import BaseModel

__all__ = ["SourceSuspectCheckResult", "SourceSuspectItem", "SourceSuspectList"]


class SourceSuspectCheckResult(BaseModel):
    flagged_count: int
    cleared_count: int


class SourceSuspectItem(BaseModel):
    line_id: int
    idx: int
    zh: str
    note: str
    next_line_id: int | None = None
    next_zh: str = ""


class SourceSuspectList(BaseModel):
    count: int
    items: list[SourceSuspectItem]
