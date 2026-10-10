"""Subtitle-file import: what a file contains, what importing it would do, and what it did."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["SubtitleProblem", "SubtitleSampleCue", "SubtitleImportPreview", "SubtitleUndoHandle",
           "SubtitleImportResult", "SidecarMatchRequest", "SidecarCandidateOut", "SidecarMatchResult"]


class SubtitleProblem(BaseModel):
    code: str
    severity: str  # "warning" or "error"; an error blocks the import
    message: str
    count: int


class SubtitleSampleCue(BaseModel):
    start: float
    end: float
    text: str


class SubtitleImportPreview(BaseModel):
    format: str
    encoding: str
    encoding_guessed: bool
    cue_count: int
    duration_seconds: float
    detected_language: Optional[str] = None
    bilingual_suspected: bool
    problems: List[SubtitleProblem]
    blocking: bool
    sample: List[SubtitleSampleCue]
    mode: str
    existing_line_count: int
    replaces_lines: int
    matched_lines: int
    unmatched_cues: int
    overwrites: int
    unsplit_cues: int
    blocked_reason: Optional[str] = None


class SubtitleUndoHandle(BaseModel):
    history_id: int
    lines_fingerprint: str


class SubtitleImportResult(BaseModel):
    mode: str
    format: str
    encoding: str
    lines_written: int
    line_ids: List[int]
    replaced_lines: int
    matched_lines: int
    unmatched_cues: int
    undo: Optional[SubtitleUndoHandle] = None


class SidecarMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_name: str = Field(min_length=1, max_length=255)
    names: List[str] = Field(max_length=500)


class SidecarCandidateOut(BaseModel):
    name: str
    format: str
    language_token: Optional[str] = None
    language: Optional[str] = None
    exact: bool


class SidecarMatchResult(BaseModel):
    candidates: List[SidecarCandidateOut]
    ambiguous: bool
