"""api/schemas/extension_access.py -- Browser-extension bridge control, device tokens, member keys
and job notifications.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from api.schemas.common import ConfirmRequest, TranslateEngine

__all__ = [
    "ExtensionStatus",
    "ExtensionEnabledRequest",
    "ExtensionEnabledResult",
    "ExtensionTokenRequest",
    "ExtensionToken",
    "ExtensionEngineSettings",
    "ExtensionEngineRequest",
    "DeviceTokenStatus",
    "DeviceToken",
    "DeviceTokenList",
    "DeviceTokenCreateRequest",
    "DeviceTokenCreated",
    "DeviceTokenRevoked",
    "AdminDeviceToken",
    "AdminDeviceTokenList",
    "MemberKeyStatus",
    "MemberKeySaveRequest",
    "MemberKeyRemoved",
    "NotificationChannel",
    "NotificationOutcome",
    "NotificationStatus",
    "NotificationChannelSetRequest",
    "NotificationChannelClearRequest",
    "NotificationChannelResult",
    "NotificationTestResult",
]


# ---------------------------------------------------------------------------
# Browser-extension bridge control (PC only) -- /api/extension/...
# ---------------------------------------------------------------------------
class ExtensionStatus(BaseModel):
    """No port and no token, ever."""
    enabled: bool
    running: bool


class ExtensionEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class ExtensionEnabledResult(BaseModel):
    """`restart_needed`: turned off, but this process could not stop the
    endpoint, so it serves until the API restarts. Normally False."""
    enabled: bool
    running: bool
    restart_needed: bool


class ExtensionTokenRequest(ConfirmRequest):
    pass


class ExtensionToken(BaseModel):
    token: str


class ExtensionEngineSettings(BaseModel):
    """The extension's saved translation engine. `ready`:
    an engine is chosen and its key is configured. Never a key value."""
    engine: Optional[str] = None
    model: Optional[str] = None
    ready: bool
    engines: List[TranslateEngine]


class ExtensionEngineRequest(BaseModel):
    """engine null = OCR only (no translation)."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[StrictStr] = None
    model: Optional[StrictStr] = None


# --- Extension device tokens (services/device_token_service.py) ----------------
DeviceTokenStatus = Literal["active", "revoked", "expired"]


class DeviceToken(BaseModel):
    """One extension device token. Never the token or its hash."""
    id: int
    label: str
    created_at: float                     # epoch seconds
    last_used_at: Optional[float] = None
    last_used_ip_prefix: str              # IPv4 /24 ("203.0.113") or IPv6 /48; "" if unused
    expires_at: Optional[float] = None    # None: never expires
    revoked_at: Optional[float] = None
    status: DeviceTokenStatus


class DeviceTokenList(BaseModel):
    tokens: List[DeviceToken]
    max_active: int


class DeviceTokenCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: StrictStr = Field(..., min_length=1, max_length=40)
    # Omitted means 90 days; an explicit null means the token never expires.
    expires_in_days: Optional[StrictInt] = Field(90, ge=1, le=365)


class DeviceTokenCreated(BaseModel):
    """`token` is shown this once and can't be read again."""
    token: str
    device_token: DeviceToken


class DeviceTokenRevoked(BaseModel):
    revoked: int


class AdminDeviceToken(DeviceToken):
    user_id: int
    user_name: str                        # display name, else a masked email


class AdminDeviceTokenList(BaseModel):
    tokens: List[AdminDeviceToken]


# --- A member's own Ollama key (services/member_key_service.py) ----------------
class MemberKeyStatus(BaseModel):
    """Never the key: its last four characters at most. `unreadable`: a key
    is saved but can't be decrypted on this server any more (paste it again)."""
    has_key: bool
    last4: Optional[str] = None
    added_at: Optional[float] = None      # epoch seconds
    unreadable: bool


class MemberKeySaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: StrictStr = Field(..., min_length=1, max_length=512)


class MemberKeyRemoved(BaseModel):
    user_id: int
    removed: bool

# --- Job notifications (Discord / ntfy) ---------------------------------------
NotificationChannel = Literal["discord", "ntfy"]


NotificationOutcome = Literal["sent", "failed", "refused", "not_configured"]


class NotificationStatus(BaseModel):
    """Configured booleans only: never a webhook URL, host or topic."""
    discord_configured: bool
    ntfy_configured: bool
    ntfy_allow_local: bool


class NotificationChannelSetRequest(BaseModel):
    """Write-only channel URL. `value` is a secret: never echoed back, and
    validation errors never include it."""
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., repr=False)
    confirm: StrictBool = False


class NotificationChannelClearRequest(ConfirmRequest):
    pass


class NotificationChannelResult(BaseModel):
    channel: NotificationChannel
    configured: bool


class NotificationTestResult(BaseModel):
    results: Dict[NotificationChannel, NotificationOutcome]
