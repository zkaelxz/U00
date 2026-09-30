"""
api/jellyfin_schemas.py -- request/response models for the optional Jellyfin
connector (roadmap Step 39, api/routers/jellyfin_routes.py). Kept out of
api/schemas.py on purpose (separate owner).
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class JellyfinConfig(BaseModel):
    """The key is write-only: only `key_configured` comes back."""
    enabled: bool
    server_url: Optional[str] = None
    library_dir: Optional[str] = None
    key_configured: bool


class JellyfinConfigUpdate(BaseModel):
    """Omitted fields keep their saved value; "" clears the URL or folder."""
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    server_url: Optional[str] = Field(None, max_length=2000)
    library_dir: Optional[str] = Field(None, max_length=1000)
    # Changing the address moves where the key is sent: needs confirm and the
    # key-write gate, like the engine endpoint URLs.
    confirm: bool = False


class JellyfinKeySet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(max_length=200)
    confirm: bool = False


class JellyfinKeyClear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class JellyfinTestResult(BaseModel):
    ok: bool
    server_name: str
    version: str


class JellyfinScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["en", "zh", "ja", "ko"] = "en"


class JellyfinScanItem(BaseModel):
    id: str
    name: str
    series: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    type: Literal["movie", "episode"]
    writable: bool


class JellyfinScanReport(BaseModel):
    language: str
    total: int
    with_subtitles: int
    missing: int
    items: list[JellyfinScanItem]
    truncated: bool


class JellyfinSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: Optional[str] = Field(None, max_length=64)
    format: Literal["srt", "ass"] = "srt"
    field: Literal["en", "zh", "bilingual"] = "en"
    language: Optional[Literal["en", "zh", "ja", "ko"]] = None
    media: Literal["none", "source", "dubbed"] = "none"
    overwrite: bool = False
    refresh: bool = True


class JellyfinSendResult(BaseModel):
    drama_id: int
    files: list[str]
    refresh: Literal["done", "failed", "skipped"]
