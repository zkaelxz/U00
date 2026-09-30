"""
api/sharing_schemas.py -- request/response models for who can see each
drama and series (api/routers/sharing_routes.py). No email, path or
secret is part of any of them.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class SharingItem(BaseModel):
    kind: Literal["series", "drama"]
    id: int
    title: str
    owner_name: str
    # No owner: made at the PC (or with sign-in off). Stored private since
    # new items are private by default, so the household can't see it yet.
    created_at_pc: bool
    is_private: bool
    # Dramas only: a drama in a series follows the series' flag.
    series_id: Optional[int] = None
    series_name: Optional[str] = None
    series_is_private: Optional[bool] = None


class SharingList(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[SharingItem]


class SetPrivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    private: bool


class SetPrivateResult(BaseModel):
    kind: Literal["series", "drama"]
    id: int
    is_private: bool


class ShareByDefault(BaseModel):
    model_config = ConfigDict(extra="forbid")
    share_by_default: bool
