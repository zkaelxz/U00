"""
sources/models.py -- the shared vocabulary of the source-adapter system
(roadmap Step 23): the access-ladder tiers, the named failure reasons,
the two status scales, the result records adapters return, and the
exceptions the rest of the system catches.

Everything here is plain data. Nothing in this file touches the network.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class ContentType(str, Enum):
    MANGA = "manga"
    MANHUA = "manhua"
    MANHWA = "manhwa"
    NOVEL = "novel"
    VIDEO = "video"

    @property
    def is_image(self) -> bool:
        return self in (ContentType.MANGA, ContentType.MANHUA, ContentType.MANHWA)


class AccessTier(str, Enum):
    """The access-method ladder, simplest first (Step 23 item 2)."""
    STATIC_HTTP = "STATIC_HTTP"
    RENDERED_BROWSER = "RENDERED_BROWSER"
    AUTHENTICATED_BROWSER = "AUTHENTICATED_BROWSER"
    USER_ASSISTED_BROWSER = "USER_ASSISTED_BROWSER"
    OFFICIAL_API = "OFFICIAL_API"
    UNAVAILABLE = "UNAVAILABLE"


LADDER_ORDER = [AccessTier.STATIC_HTTP, AccessTier.RENDERED_BROWSER,
                AccessTier.AUTHENTICATED_BROWSER, AccessTier.USER_ASSISTED_BROWSER,
                AccessTier.OFFICIAL_API]


class FailureReason(str, Enum):
    """Why one tier's attempt failed (Step 23 item 2b). Never collapsed
    into a bare "blocked"."""
    HTTP_ERROR = "HTTP_ERROR"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    JAVASCRIPT_REQUIRED = "JAVASCRIPT_REQUIRED"
    EMPTY_SPA_SHELL = "EMPTY_SPA_SHELL"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    PURCHASE_REQUIRED = "PURCHASE_REQUIRED"
    COOKIE_REQUIRED = "COOKIE_REQUIRED"
    GEO_RESTRICTION = "GEO_RESTRICTION"
    CDN_RESTRICTION = "CDN_RESTRICTION"
    CLOUDFLARE_CHALLENGE = "CLOUDFLARE_CHALLENGE"
    BOT_CHALLENGE = "BOT_CHALLENGE"
    IP_REPUTATION_BLOCK = "IP_REPUTATION_BLOCK"
    ACCESS_DENIED = "ACCESS_DENIED"
    DRM_DETECTED = "DRM_DETECTED"
    ENCRYPTED_RESOURCE = "ENCRYPTED_RESOURCE"
    SIGNED_RESOURCE = "SIGNED_RESOURCE"
    NOT_INSTALLED = "NOT_INSTALLED"      # the tier's own tooling isn't set up here
    NOT_BUILT = "NOT_BUILT"              # the tier exists in the ladder but its step hasn't shipped
    UNKNOWN = "UNKNOWN"


# An active anti-automation challenge. Never retried automatically, never
# handed to an automated browser tier -- always handed to the person.
CHALLENGE_REASONS = {FailureReason.CLOUDFLARE_CHALLENGE, FailureReason.BOT_CHALLENGE}

# Ordinary, transient trouble: exponential backoff, capped retries.
RETRYABLE_REASONS = {FailureReason.RATE_LIMIT, FailureReason.TIMEOUT}

# Failures a real browser from this same machine won't fix either.
ENVIRONMENT_BLOCK_REASONS = {
    FailureReason.ACCESS_DENIED, FailureReason.IP_REPUTATION_BLOCK,
    FailureReason.GEO_RESTRICTION, FailureReason.CDN_RESTRICTION,
    FailureReason.CLOUDFLARE_CHALLENGE, FailureReason.BOT_CHALLENGE,
}

# Page needs JS/rendering -- the signal to try RENDERED_BROWSER next.
NEEDS_BROWSER_REASONS = {
    FailureReason.JAVASCRIPT_REQUIRED, FailureReason.EMPTY_SPA_SHELL,
    FailureReason.ENCRYPTED_RESOURCE,
}

# Protected content. Recorded, never circumvented.
PROTECTION_REASONS = {FailureReason.DRM_DETECTED, FailureReason.ENCRYPTED_RESOURCE,
                      FailureReason.SIGNED_RESOURCE}


class CapabilityStatus(str, Enum):
    """The graduated status on a SourceCapabilities record (Step 23 item 1)."""
    VERIFIED = "VERIFIED"
    VERIFIED_WITH_AUTH = "VERIFIED_WITH_AUTH"
    BROWSER_ASSISTED = "BROWSER_ASSISTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    MANUAL_VERIFICATION_REQUIRED = "MANUAL_VERIFICATION_REQUIRED"
    PROTECTED = "PROTECTED"
    TOS_PROHIBITED = "TOS_PROHIBITED"
    UNTESTED = "UNTESTED"


class TechnicalStatus(str, Enum):
    """Never a binary blocked/not-blocked (Step 23 item 2b)."""
    DISQUALIFIED = "DISQUALIFIED"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED_IN_CURRENT_ENVIRONMENT = "BLOCKED_IN_CURRENT_ENVIRONMENT"
    BROWSER_ACCESSIBLE = "BROWSER_ACCESSIBLE"
    AUTHENTICATED_ACCESSIBLE = "AUTHENTICATED_ACCESSIBLE"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    SUPPORTED = "SUPPORTED"


class ContentAccess(str, Enum):
    TEXT = "TEXT"
    IMAGES = "IMAGES"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    SUBTITLES = "SUBTITLES"
    METADATA = "METADATA"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


@dataclass
class TierResult:
    """One tier's untested/tested state on a SourceCapabilities record.
    `tested=False` is the honest UNTESTED default, not a failure."""
    tested: bool = False
    ok: bool = False
    reason: Optional[str] = None   # a FailureReason value when not ok
    detail: str = ""
    at: Optional[float] = None


@dataclass
class SourceCapabilities:
    """What's actually known about a source. Technical and contractual
    findings are two separate blocks, never collapsed into one verdict --
    the app states facts; whether a personal use is appropriate is the
    user's call."""
    platform: str
    content_types: list = field(default_factory=list)
    languages: list = field(default_factory=list)
    status: str = CapabilityStatus.UNTESTED.value
    technical_status: str = TechnicalStatus.UNRESOLVED.value
    access_method: Optional[str] = None        # the AccessTier that actually worked
    auth_required: bool = False
    auth_supported: bool = False
    content_access_status: str = ContentAccess.UNKNOWN.value
    tiers: dict = field(default_factory=lambda: {t.value: TierResult() for t in LADDER_ORDER})
    technical: dict = field(default_factory=dict)   # browser-accessible, extraction method, protections
    terms: dict = field(default_factory=dict)       # what was read, quoted where possible, what's unverified

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SourceCapabilities":
        d = dict(d)
        tiers = {k: TierResult(**v) if isinstance(v, dict) else v
                 for k, v in (d.pop("tiers", None) or {}).items()}
        caps = cls(**d)
        if tiers:
            caps.tiers.update(tiers)
        return caps


@dataclass
class SearchResult:
    source: str
    series_id: str
    title: str
    url: str = ""
    cover_url: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class SeriesInfo:
    source: str
    series_id: str
    title: str
    url: str = ""
    cover_url: str = ""
    authors: list = field(default_factory=list)
    description: str = ""
    genres: list = field(default_factory=list)
    status: str = "unknown"          # ongoing / completed / unknown
    content_type: str = ""
    language: str = ""


@dataclass
class ChapterInfo:
    source: str
    series_id: str
    chapter_id: str
    title: str
    url: str = ""
    group: str = ""                  # e.g. a volume/section heading on the source
    sort_key: tuple = ()             # filled by chapter_order.sort_chapters


@dataclass
class PageRef:
    """One image page of a comic chapter, not yet downloaded."""
    source: str
    chapter_id: str
    index: int
    url: str
    headers: dict = field(default_factory=dict)


@dataclass
class AttemptRecord:
    """One attempt at one tier, with the evidence actually observed --
    what diagnostics show instead of a bare pass/fail."""
    tier: str
    ok: bool
    reason: Optional[str] = None
    detail: str = ""
    http_status: Optional[int] = None
    final_url: str = ""
    page_title: str = ""
    text_length: Optional[int] = None
    headers: dict = field(default_factory=dict)
    at: Optional[float] = None

    def describe(self) -> str:
        if self.ok:
            return f"{self.tier}: SUCCESS" + (f" -- {self.detail}" if self.detail else "")
        bits = [self.reason or FailureReason.UNKNOWN.value]
        if self.http_status:
            bits.append(f"HTTP {self.http_status}")
        if self.detail:
            bits.append(self.detail)
        return f"{self.tier}: FAILED -- " + " -- ".join(bits)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SourceError(Exception):
    """Base class. Carries a FailureReason and the evidence behind it so
    the UI can explain the failure rather than just report it."""

    def __init__(self, message: str, reason: FailureReason = FailureReason.UNKNOWN,
                 attempt: Optional[AttemptRecord] = None):
        super().__init__(message)
        self.reason = reason
        self.attempt = attempt


class NotSupportedError(SourceError):
    """An adapter doesn't implement an optional method. Fails cleanly --
    the UI shows "this source doesn't offer X" instead of a traceback."""

    def __init__(self, message: str):
        super().__init__(message, FailureReason.UNKNOWN)


class ChallengeDetected(SourceError):
    """An active anti-automation challenge. Automated requests stop here;
    the person is offered Open in Browser / Retry / Cancel."""

    def __init__(self, message: str, url: str, reason: FailureReason,
                 attempt: Optional[AttemptRecord] = None):
        super().__init__(message, reason, attempt)
        self.url = url


class SourceUnavailable(SourceError):
    """The source is 🔴 and still inside its backoff window, or every
    configured mirror failed."""

    def __init__(self, message: str, reason: FailureReason = FailureReason.UNKNOWN,
                 retry_after: Optional[float] = None, attempt: Optional[AttemptRecord] = None):
        super().__init__(message, reason, attempt)
        self.retry_after = retry_after


class FetchFailed(SourceError):
    """A single request failed for a reason that isn't a challenge."""


class ContentHidden(SourceError):
    """The resource exists but is withheld behind an opt-in the app hasn't
    been given (e.g. a source's adult-content flag)."""


CHALLENGE_HANDOFF_MESSAGE = (
    "A browser verification page was detected. Automatic challenge solving is "
    "disabled. Open the page in your browser and complete whatever verification "
    "the site asks for.")
