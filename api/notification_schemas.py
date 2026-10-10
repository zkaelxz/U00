"""
api/notification_schemas.py -- request/response models for the
notification routes added after the first version (the category switches and
the in-app list). The channel models live in api/schemas/system.py. No
model carries a webhook URL, host, topic, path or job id.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, StrictBool

from api.schemas import NotificationStatus


class NotificationSettingsStatus(NotificationStatus):
    """Configured booleans plus which events go to Discord/ntfy."""
    send_jobs: bool
    send_chapters: bool
    send_remote: bool


class NotificationCategoriesRequest(BaseModel):
    """Omitted fields stay as they are."""
    model_config = ConfigDict(extra="forbid")
    jobs: Optional[StrictBool] = None
    chapters: Optional[StrictBool] = None
    remote: Optional[StrictBool] = None


class NotificationEvent(BaseModel):
    id: int
    at: float
    kind: Literal["job_done", "job_failed", "chapters", "remote"]
    text: str


class NotificationList(BaseModel):
    items: List[NotificationEvent]
