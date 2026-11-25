"""
api/schemas/admin_users.py -- response models for user administration and
the audit log view (api/routers/admin_users_routes.py). No model carries a
session token, hash, full IP address, user agent or filesystem path.
"""

from typing import List, Optional

from pydantic import BaseModel

__all__ = ["AdminUser", "AdminDevice", "AdminDeviceList", "AdminUserList", "AdminSessionsRevoked", "AdminUserDeleted", "AuditEvent", "AuditPage"]


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
    has_own_ollama_key: bool = False
    # Logged paid-engine spending for this account this month (counted like the
    # monthly cap), and a member's own monthly limit (None: none; always None for an admin).
    month_paid_spend_usd: float = 0.0
    monthly_paid_cap_usd: Optional[float] = None
    # Epoch seconds of the newest request from any of their devices; None with no sign-in on record.
    last_seen_at: Optional[float] = None
    # A member's own limit on the background jobs they start in a day (None: none; always None for an
    # admin), and how many they started today.
    daily_job_limit: Optional[int] = None
    jobs_today: int = 0


class AdminDevice(BaseModel):
    id: int
    device: str                 # "Chrome on Android"; never the user agent
    created_at: float
    last_seen_at: float
    expires_at: float
    ip_prefix: str              # IPv4 /24 or IPv6 /48; "" if unknown


class AdminDeviceList(BaseModel):
    devices: List[AdminDevice]


class AdminUserList(BaseModel):
    users: List[AdminUser]
    # The rights an admin can give or take on a member account, in display order.
    member_permissions: List[str] = []


class AdminSessionsRevoked(BaseModel):
    user_id: int
    revoked: int


class AdminUserDeleted(BaseModel):
    user_id: int
    deleted: bool


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
