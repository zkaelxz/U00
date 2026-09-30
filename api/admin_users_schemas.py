"""
api/admin_users_schemas.py -- response models for user administration and
the audit log view (api/routers/admin_users_routes.py). Kept out of
api/schemas.py. No model carries a session token, hash, full IP address,
user agent or filesystem path.
"""

from typing import List, Optional

from pydantic import BaseModel


class AdminUser(BaseModel):
    id: int
    email: str
    display_name: str = ""
    is_admin: bool
    is_active: bool
    has_google_binding: bool
    created_at: Optional[str] = None
    permissions: List[str]
    active_sessions: int
    is_self: bool


class AdminUserList(BaseModel):
    users: List[AdminUser]


class AdminSessionsRevoked(BaseModel):
    user_id: int
    revoked: int


class AuditEvent(BaseModel):
    id: int
    ts: str
    user_id: Optional[int] = None
    action: str
    detail: str = ""


class AuditPage(BaseModel):
    events: List[AuditEvent]
    # Pass as `before_id` for the next (older) page; None at the end.
    next_before_id: Optional[int] = None
    # Every action name in the log, for the filter.
    actions: List[str]
