"""
api/notion_schemas.py -- request/response models for the Notion export
(roadmap item 112, api/routers/notion_routes.py). Kept out of api/schemas.py
on purpose, like api/jellyfin_schemas.py.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class NotionConfig(BaseModel):
    """The token is write-only: only `token_configured` comes back."""
    target_type: Optional[Literal["database", "page"]] = None
    target_id: Optional[str] = None
    token_configured: bool


class NotionConfigUpdate(BaseModel):
    """Omitted fields keep their saved value; target_id "" clears it. The
    target is a Notion link or id; the token only ever goes to
    api.notion.com, so changing the target does not move the token."""
    model_config = ConfigDict(extra="forbid")
    target_type: Optional[Literal["database", "page"]] = None
    target_id: Optional[str] = Field(None, max_length=2000)


class NotionTokenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(max_length=300)
    confirm: bool = False


class NotionTokenClear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class NotionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotionTestResult(BaseModel):
    ok: bool
    bot_name: str
    target_title: str
    target_type: Literal["database", "page"]


class NotionExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["en", "zh", "bilingual"] = "en"


class NotionExportStarted(BaseModel):
    job_id: str


class NotionExportStatus(BaseModel):
    """The page this drama was last exported to (None before the first)."""
    drama_id: int
    page_id: Optional[str] = None
    page_url: Optional[str] = None
