"""Schemas for Benchmark Lab "Quality" (GET /api/diagnostics/quality): the newest release check result, each score against its baseline. Numbers, fixed words and booleans only."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

__all__ = ["QualityClip", "QualityCheck"]


class QualityClip(BaseModel):
    id: str = Field(description="The clip's id from the private manifest, such as title59-transcription.")
    kind: str
    metric: Optional[str] = Field(None, description="cer or wer (scored as 1 - error), der (lower is better) or chrf.")
    score: Optional[float] = Field(None, description="None when the clip failed.")
    baseline: Optional[float] = None
    status: Literal["pass", "fail", "none"] = Field(
        description="fail: the clip failed or is worse than its baseline by more than the margin; none: no baseline.")
    failed: bool
    runs: int = Field(description="How many times the clip ran; the score is the median.")
    fixed_decoding: bool


class QualityCheck(BaseModel):
    found: bool = Field(description="False when the data folder holds no release check result.")
    unreadable: bool = Field(description="True when the newest result file can't be read.")
    when: Optional[str] = Field(None, description="When the check ran, the PC's local time (YYYY-MM-DD HH:MM).")
    age_days: Optional[int] = None
    fresh: bool = Field(description="True when the check ran within max_age_days.")
    passed: bool = Field(description="The release gate: nothing failed or fell below its baseline.")
    machine: Optional[str] = Field(None, description="The PC's name, to tell a V100 run from another PC's.")
    gpu: Optional[str] = None
    repeats: Optional[int] = None
    margin: Optional[float] = None
    max_age_days: int
    clips: List[QualityClip]
