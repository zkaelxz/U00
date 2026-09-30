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
    AUDIO_DRAMA = "audio_drama"


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
    TOS_PROHIBITED = "TOS_PROHIBITED"    # refused before any request: the source's terms forbid it
    UNKNOWN = "UNKNOWN"


# An active anti-automation challenge. Never retried automatically, never
# handed to an automated browser tier -- always handed to the person.
CHALLENGE_REASONS = {FailureReason.CLOUDFLARE_CHALLENGE, FailureReason.BOT_CHALLENGE}

# Failures a real browser from this same machine won't fix either.
ENVIRONMENT_BLOCK_REASONS = {
    FailureReason.ACCESS_DENIED, FailureReason.IP_REPUTATION_BLOCK,
    FailureReason.GEO_RESTRICTION, FailureReason.CDN_RESTRICTION,
    FailureReason.CLOUDFLARE_CHALLENGE, FailureReason.BOT_CHALLENGE,
}

# Protected content. Recorded, never circumvented.
PROTECTION_REASONS = {FailureReason.DRM_DETECTED, FailureReason.ENCRYPTED_RESOURCE,
                      FailureReason.SIGNED_RESOURCE}

# Step 23k item 6: a protected resource is always reported as exactly
# that -- never collapsed into a bare "blocked" or a generic failure.
_PROTECTED = ("Protected resource could not be processed without bypassing a technical "
              "control ({what}) -- recorded, never decrypted or worked around.")
PROTECTION_EXPLANATIONS = {
    FailureReason.DRM_DETECTED: _PROTECTED.format(what="DRM / encrypted media"),
    FailureReason.ENCRYPTED_RESOURCE: _PROTECTED.format(
        what="the site decrypts this content itself, inside its own page"),
    FailureReason.SIGNED_RESOURCE: _PROTECTED.format(
        what="signed/expiring delivery tokens this session wasn't issued"),
}


def explain_protection(reasons) -> list:
    """The specific plain-language line for each protection reason in
    `reasons` (FailureReason members or their string values)."""
    out = []
    for r in reasons or ():
        try:
            r = FailureReason(r)
        except ValueError:
            continue
        if r in PROTECTION_EXPLANATIONS and PROTECTION_EXPLANATIONS[r] not in out:
            out.append(PROTECTION_EXPLANATIONS[r])
    return out


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


# --- Step 23k item 3: the granular capability fields. Each one is its own
# fact. Whether the content is technically visible to a (signed-in)
# session and whether the site permits automated extraction of it are
# never the same question -- a login answers the first, never the second.

class Requirement(str, Enum):
    """authentication_required / purchase_required. REQUIRED once any
    real attempt observed it (some content on the source needs it);
    NOT_REQUIRED only after content was reached without it."""
    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNKNOWN = "UNKNOWN"


class TechnicalProtection(str, Enum):
    NONE = "NONE"
    DETECTED = "DETECTED"
    UNKNOWN = "UNKNOWN"


class AutomationPermission(str, Enum):
    """What the site's own terms say about automated access. Only a
    directly-read clause sets EXPLICITLY_RESTRICTED; unread or unreadable
    terms stay UNKNOWN -- never PERMITTED by default."""
    PERMITTED = "PERMITTED"
    EXPLICITLY_RESTRICTED = "EXPLICITLY_RESTRICTED"
    UNKNOWN = "UNKNOWN"


class AiMlUse(str, Enum):
    ALLOWED_OR_NOT_IDENTIFIED = "ALLOWED_OR_NOT_IDENTIFIED"
    EXPLICITLY_RESTRICTED = "EXPLICITLY_RESTRICTED"
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
    auth_supported: bool = False
    content_access_status: str = ContentAccess.UNKNOWN.value
    # Step 23k item 3. `authentication_required` replaces the old
    # `auth_required: bool`, whose False default claimed "no login needed"
    # for a source nobody had tested -- UNKNOWN is the honest default.
    authentication_required: str = Requirement.UNKNOWN.value
    purchase_required: str = Requirement.UNKNOWN.value
    technical_protection: str = TechnicalProtection.UNKNOWN.value
    automation_permission: str = AutomationPermission.UNKNOWN.value
    ai_ml_use: str = AiMlUse.UNKNOWN.value
    tiers: dict = field(default_factory=lambda: {t.value: TierResult() for t in LADDER_ORDER})
    technical: dict = field(default_factory=dict)   # browser-accessible, extraction method, protections
    terms: dict = field(default_factory=dict)       # what was read, quoted where possible, what's unverified

    def terms_restrictions(self) -> list:
        """Which recorded terms findings forbid the app from extracting
        from this source at all -- whatever the session can see."""
        out = []
        if self.terms.get("tos_prohibited") or \
                self.automation_permission == AutomationPermission.EXPLICITLY_RESTRICTED.value:
            out.append("automation_permission")
        if self.ai_ml_use == AiMlUse.EXPLICITLY_RESTRICTED.value:
            out.append("ai_ml_use")
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SourceCapabilities":
        d = dict(d)
        # Records stored before Step 23k carry the old boolean. Only a True
        # was ever an observation; False was just the default.
        if "auth_required" in d:
            legacy = d.pop("auth_required")
            if legacy and "authentication_required" not in d:
                d["authentication_required"] = Requirement.REQUIRED.value
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
    # Download links a work's page posts ({"label", "url", "password"}),
    # listed for the person to open themselves. The app never fetches them.
    links: list = field(default_factory=list)


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
class AudioRef:
    """One episode's resolved, playable audio location -- roadmap Step
    94. Not necessarily a flat file: an audio-drama platform can issue a
    signed HLS manifest (`format="hls"`) instead of a direct downloadable
    file (`format="direct"`), so a caller knows whether it needs an
    HLS-aware fetch (e.g. ffmpeg) rather than a plain byte download. Both
    kinds are commonly short-lived/signed -- resolve and use promptly,
    don't cache past a session."""
    source: str
    chapter_id: str
    url: str
    format: str = "direct"
    ext: str = ""
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
    # Which browser translator, if any, had already rewritten the page
    # before this app ever saw it. Not a failure -- the page loaded --
    # but it means the text here is a translation, not the source.
    machine_translated: list = field(default_factory=list)
    # How strongly the page looked like an unrendered JS shell, and why.
    # `page_fetch.looks_like_unrendered_shell` works both out and
    # `detect.classify` used to keep only its boolean; they are the
    # closest thing here to "is the text really in the DOM?".
    shell_confidence: Optional[float] = None
    shell_reasons: list = field(default_factory=list)

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


class TermsProhibited(SourceError):
    """The source's own terms (its capability record's terms block)
    prohibit automated access. Refused before any request is sent."""

    def __init__(self, message: str):
        super().__init__(message, FailureReason.TOS_PROHIBITED)


class ContentHidden(SourceError):
    """The resource exists but is withheld behind an opt-in the app hasn't
    been given (e.g. a source's adult-content flag)."""


CHALLENGE_HANDOFF_MESSAGE = (
    "A browser verification page was detected. Automatic challenge solving is "
    "disabled. Open the page in your browser and complete whatever verification "
    "the site asks for.")
