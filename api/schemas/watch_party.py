"""api/schemas/watch_party.py -- watch party room shapes."""

from pydantic import BaseModel, Field, StrictBool

__all__ = ["WatchPartyCreateRequest", "WatchPartyStateRequest", "WatchPartyRoom", "WatchPartyEnded"]


class WatchPartyCreateRequest(BaseModel):
    drama_id: int = Field(ge=1)


class WatchPartyStateRequest(BaseModel):
    playing: StrictBool
    position: float = Field(ge=0, le=24 * 3600, allow_inf_nan=False)


class WatchPartyRoom(BaseModel):
    """`position` was true at server time `at`; `now` is the server's clock when this was read, so a client can place
    itself without trusting its own clock."""
    code: str
    drama_id: int
    playing: bool
    position: float
    at: float
    now: float
    seq: int
    watching: int


class WatchPartyEnded(BaseModel):
    ended: bool
