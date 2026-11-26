"""api/schemas/cast.py -- casting a title's subtitled copy to a TV."""

from typing import Literal

from pydantic import BaseModel

__all__ = ["CastStatus", "CastLink", "ShareLinkRequest", "ShareEnded"]


class CastStatus(BaseModel):
    state: Literal["ready", "working", "missing", "unsupported"]


class CastLink(BaseModel):
    """`path` is relative to the address the caller is on; the TV is given that address plus this."""
    path: str
    expires_in: int


class ShareLinkRequest(BaseModel):
    days: Literal[1, 3, 7] = 1


class ShareEnded(BaseModel):
    ended: int
