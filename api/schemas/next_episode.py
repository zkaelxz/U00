"""api/schemas/next_episode.py -- the title that follows an episode on Watch, and how ready it is."""

from typing import Literal, Optional

from pydantic import BaseModel

__all__ = ["NextEpisode"]


class NextEpisode(BaseModel):
    drama_id: Optional[int]
    title: str
    state: Literal["ready", "working", "idle", "no_media"]
    free: bool
