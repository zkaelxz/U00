"""api/schemas/quick_video.py -- /api/quick-video: paste a video link, run the whole
pipeline with a saved translation profile. The link goes in and is never sent back."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

__all__ = ["QuickVideoProfile", "QuickVideoSettings", "QuickVideoSettingsSet", "QuickVideoStart",
           "QuickVideoStarted", "QuickVideoRun", "QuickVideoRuns"]


class QuickVideoProfile(BaseModel):
    id: int
    name: str
    # The profile has the source language a new title needs.
    ready: bool


class QuickVideoSettings(BaseModel):
    profile_id: Optional[int] = None
    profiles: List[QuickVideoProfile]


class QuickVideoSettingsSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: Optional[int] = Field(default=None, ge=1, lt=2**31)


class QuickVideoStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000)
    # Omitted = the profile chosen in Settings.
    profile_id: Optional[int] = Field(default=None, ge=1, lt=2**31)
    # The title is removed again after watching.
    watch_once: StrictBool = False


class QuickVideoStarted(BaseModel):
    drama_id: int
    watch_once: bool


class QuickVideoRun(BaseModel):
    drama_id: int
    title: str
    watch_once: bool
    state: Literal["running", "stopped", "done"]
    step: Literal["download", "transcribe", "translate", "export"]
    reason: Optional[str] = None


class QuickVideoRuns(BaseModel):
    items: List[QuickVideoRun]
