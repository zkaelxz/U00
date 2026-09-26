"""
sources/ladder.py -- the access-method ladder (Step 23 items 2, 2b, 3b).

    STATIC_HTTP -> RENDERED_BROWSER -> AUTHENTICATED_BROWSER
                -> USER_ASSISTED_BROWSER -> OFFICIAL_API -> UNAVAILABLE

Tried simplest-first, stopping at the first tier that actually works.
Rules the ladder enforces, not just documents:

  * A static failure automatically tries RENDERED_BROWSER next and
    records both results side by side -- a failed plain request never
    marks a whole source "blocked" on its own.
  * An active challenge (Cloudflare/bot check) at ANY tier stops every
    automated tier immediately. Nothing automated is sent at it again;
    the person gets Open in Browser / Retry / Cancel, and can resume from
    the page they reached themselves (USER_ASSISTED_BROWSER).
  * Protected content (DRM, site-side decryption, signed tokens) is named
    and recorded, never decoded or worked around.
  * AUTHENTICATED_BROWSER is Step 23k's tier; until that ships it reports
    NOT_BUILT rather than silently pretending to have been tried.
  * OFFICIAL_API is checked before giving up. An API that only covers
    part of what was asked (e.g. metadata but not chapter text) is a real,
    partial result -- not a pass, and not UNAVAILABLE.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from . import detect, store
from .models import (AccessTier, AttemptRecord, CapabilityStatus, CHALLENGE_REASONS,
                     ChallengeDetected, ContentAccess, ENVIRONMENT_BLOCK_REASONS,
                     FailureReason, PROTECTION_REASONS,
                     SourceCapabilities, SourceError, TechnicalStatus, TermsProhibited,
                     TierResult)

TIER_LABELS = {
    AccessTier.STATIC_HTTP: "Static HTTP",
    AccessTier.RENDERED_BROWSER: "Browser",
    AccessTier.AUTHENTICATED_BROWSER: "Authenticated browser",
    AccessTier.USER_ASSISTED_BROWSER: "You, in your browser",
    AccessTier.OFFICIAL_API: "Official API",
    AccessTier.UNAVAILABLE: "Unavailable",
}


@dataclass
class TierOutcome:
    ok: bool
    html: str = ""
    reasons: list = field(default_factory=list)     # [FailureReason]
    detail: str = ""
    partial: bool = False                           # OFFICIAL_API covering only part of the request
    content_access: str = ContentAccess.UNKNOWN.value
    evidence: dict = field(default_factory=dict)
    data: object = None                             # tier-specific payload (e.g. API metadata)


@dataclass
class LadderResult:
    url: str
    tier: Optional[str] = None                      # the tier that worked, if any
    html: str = ""
    data: object = None
    partial: bool = False
    attempts: list = field(default_factory=list)    # [AttemptRecord]
    handoff: Optional[dict] = None                  # set when a challenge stopped automation
    technical_status: str = TechnicalStatus.UNRESOLVED.value
    capability_status: str = CapabilityStatus.UNTESTED.value
    content_access: str = ContentAccess.UNKNOWN.value
    reasons: list = field(default_factory=list)     # every FailureReason seen, first = most telling

    @property
    def ok(self) -> bool:
        return self.tier is not None

    def summary_lines(self) -> list:
        """Side-by-side per-tier lines, e.g.
        'Static HTTP: FAILED -- ACCESS_DENIED -- HTTP 403' /
        'Browser: SUCCESS -- 38 image resources'."""
        out = []
        for a in self.attempts:
            label = TIER_LABELS.get(AccessTier(a.tier), a.tier)
            out.append(a.describe().replace(a.tier, label, 1))
        if self.handoff:
            out.append(f"Stopped: {self.handoff['reason']} detected at "
                       f"{TIER_LABELS[AccessTier(self.handoff['tier'])]} -- handed to you.")
        return out


# ---------------------------------------------------------------------------
# Tier implementations
# ---------------------------------------------------------------------------

def static_tier(client):
    """STATIC_HTTP through the paced client."""
    def run(url: str) -> TierOutcome:
        try:
            resp = client.get(url, use_cache=False)
        except ChallengeDetected as e:
            return TierOutcome(False, reasons=[e.reason], detail=str(e),
                               evidence=_ev(e.attempt))
        except SourceError as e:
            return TierOutcome(False, reasons=[e.reason], detail=str(e), evidence=_ev(e.attempt))
        html = resp.text
        ev = detect.evidence(resp.status_code, resp.headers, html, url, resp.url)
        if resp.reasons:
            return TierOutcome(False, html=html, reasons=list(resp.reasons),
                               detail=", ".join(r.value for r in resp.reasons), evidence=ev)
        return TierOutcome(True, html=html, evidence=ev)
    return run


def rendered_tier(client=None, fetch_rendered=None):
    """RENDERED_BROWSER via page_fetch.fetch_rendered (Playwright). If
    Playwright isn't installed that's reported as NOT_INSTALLED -- a fact
    about this machine, not about the source."""
    def fetch(url):
        fn = fetch_rendered
        if fn is None:
            from page_fetch import fetch_rendered as fn
        return fn(url)

    def run(url: str) -> TierOutcome:
        try:
            if client is not None:
                html, _text = client.paced(fetch, url, "Browser session")
            else:
                html, _text = fetch(url)
        except ImportError as e:
            return TierOutcome(False, reasons=[FailureReason.NOT_INSTALLED],
                               detail=str(e).splitlines()[0])
        except Exception as e:
            reason = FailureReason.TIMEOUT if "timeout" in type(e).__name__.lower() \
                else FailureReason.UNKNOWN
            return TierOutcome(False, reasons=[reason], detail=f"{type(e).__name__}: {e}"[:300])
        reasons = detect.classify(200, {}, html, url, url)
        # After a real render the page's own scripts have already run, so
        # crypto/Vue markers still sitting in the markup don't by themselves
        # mean the content is missing -- only a still-empty shell does.
        if FailureReason.EMPTY_SPA_SHELL not in reasons:
            reasons = [r for r in reasons if r not in (FailureReason.ENCRYPTED_RESOURCE,
                                                       FailureReason.JAVASCRIPT_REQUIRED)]
        ev = detect.evidence(200, {}, html, url, url)
        if reasons:
            return TierOutcome(False, html=html, reasons=reasons,
                               detail=", ".join(r.value for r in reasons), evidence=ev)
        return TierOutcome(True, html=html, evidence=ev)
    return run


def not_built_tier(step: str):
    def run(url: str) -> TierOutcome:
        return TierOutcome(False, reasons=[FailureReason.NOT_BUILT],
                           detail=f"This tier arrives in Step {step}.")
    return run


def user_assisted_tier(provided_html: str):
    """USER_ASSISTED_BROWSER: the person opened the page in their own
    browser, completed whatever the site asked, and handed back what they
    see (the page source). Nothing here talks to the site."""
    def run(url: str) -> TierOutcome:
        html = provided_html or ""
        reasons = detect.classify(200, {}, html, url, url)
        if not html.strip():
            return TierOutcome(False, reasons=[FailureReason.UNKNOWN], detail="Nothing was pasted.")
        if set(reasons) & CHALLENGE_REASONS:
            return TierOutcome(False, html=html, reasons=reasons,
                               detail="The pasted page is still the verification page.")
        return TierOutcome(True, html=html, evidence=detect.evidence(200, {}, html, url, url))
    return run


def _ev(attempt: Optional[AttemptRecord]) -> dict:
    if attempt is None:
        return {}
    return {"http_status": attempt.http_status, "final_url": attempt.final_url,
            "page_title": attempt.page_title, "text_length": attempt.text_length,
            "headers": attempt.headers}


# ---------------------------------------------------------------------------
# Running the ladder
# ---------------------------------------------------------------------------

AUTOMATED_TIERS = [AccessTier.STATIC_HTTP, AccessTier.RENDERED_BROWSER,
                   AccessTier.AUTHENTICATED_BROWSER]


def run_ladder(url: str, tiers: dict, source: str = None, log: bool = True) -> LadderResult:
    """
    `tiers` maps AccessTier -> callable(url) -> TierOutcome. A tier with no
    entry is skipped (not recorded as a failure). USER_ASSISTED_BROWSER is
    only ever run when the caller supplies it -- it needs a person -- and
    OFFICIAL_API is tried last, before UNAVAILABLE.
    """
    result = LadderResult(url=url)
    order = AUTOMATED_TIERS + [AccessTier.USER_ASSISTED_BROWSER, AccessTier.OFFICIAL_API]
    for tier in order:
        fn = tiers.get(tier)
        if fn is None:
            continue
        outcome = fn(url)
        ev = dict(outcome.evidence or {})
        attempt = AttemptRecord(
            tier=tier.value, ok=outcome.ok,
            reason=None if outcome.ok else (outcome.reasons[0].value if outcome.reasons
                                            else FailureReason.UNKNOWN.value),
            detail=outcome.detail, at=time.time(),
            http_status=ev.get("http_status"), final_url=ev.get("final_url") or "",
            page_title=ev.get("page_title") or "", text_length=ev.get("text_length"),
            headers=ev.get("headers") or {})
        result.attempts.append(attempt)
        for r in outcome.reasons:
            if r not in result.reasons:
                result.reasons.append(r)
        if outcome.ok:
            result.tier = tier.value
            result.html = outcome.html
            result.data = outcome.data
            result.partial = outcome.partial
            result.content_access = outcome.content_access
            break
        if set(outcome.reasons) & CHALLENGE_REASONS:
            result.handoff = {"tier": tier.value, "reason": outcome.reasons[0].value,
                              "url": url}
            break
    _resolve_status(result)
    if log and source:
        store.log_attempt(source, url, {
            "tier": result.tier, "technical_status": result.technical_status,
            "capability_status": result.capability_status,
            "reasons": [r.value for r in result.reasons],
            "lines": result.summary_lines(), "handoff": result.handoff})
    return result


def _resolve_status(result: LadderResult):
    reasons = set(result.reasons)
    if result.ok:
        if result.partial:
            result.technical_status = TechnicalStatus.PARTIALLY_SUPPORTED.value
            result.capability_status = CapabilityStatus.PARTIALLY_SUPPORTED.value
        elif result.tier == AccessTier.STATIC_HTTP.value:
            result.technical_status = TechnicalStatus.SUPPORTED.value
            result.capability_status = CapabilityStatus.VERIFIED.value
        elif result.tier == AccessTier.RENDERED_BROWSER.value:
            result.technical_status = TechnicalStatus.BROWSER_ACCESSIBLE.value
            result.capability_status = CapabilityStatus.BROWSER_ASSISTED.value
        elif result.tier == AccessTier.AUTHENTICATED_BROWSER.value:
            result.technical_status = TechnicalStatus.AUTHENTICATED_ACCESSIBLE.value
            result.capability_status = CapabilityStatus.VERIFIED_WITH_AUTH.value
        elif result.tier == AccessTier.USER_ASSISTED_BROWSER.value:
            result.technical_status = TechnicalStatus.BROWSER_ACCESSIBLE.value
            result.capability_status = CapabilityStatus.MANUAL_VERIFICATION_REQUIRED.value
        return

    if result.handoff:
        result.technical_status = TechnicalStatus.BLOCKED_IN_CURRENT_ENVIRONMENT.value
        result.capability_status = CapabilityStatus.MANUAL_VERIFICATION_REQUIRED.value
        return

    static = next((a for a in result.attempts if a.tier == AccessTier.STATIC_HTTP.value), None)
    static_reason = FailureReason(static.reason) if static and static.reason else None
    if reasons & PROTECTION_REASONS:
        result.capability_status = CapabilityStatus.PROTECTED.value
    elif FailureReason.AUTHENTICATION_REQUIRED in reasons:
        result.capability_status = CapabilityStatus.AUTHENTICATION_REQUIRED.value
    if static_reason in ENVIRONMENT_BLOCK_REASONS:
        # Blocked from here -- not proof it's blocked for everyone, and
        # never a policy verdict. DISQUALIFIED comes only from the terms block.
        result.technical_status = TechnicalStatus.BLOCKED_IN_CURRENT_ENVIRONMENT.value
    else:
        result.technical_status = TechnicalStatus.UNRESOLVED.value


# ---------------------------------------------------------------------------
# Capabilities records and the per-tier "Test Now" buttons
# ---------------------------------------------------------------------------

def load_capabilities(source: str, default: SourceCapabilities = None) -> SourceCapabilities:
    raw = store.load_capabilities(source)
    if not raw:
        return default or SourceCapabilities(platform=source)
    caps = SourceCapabilities.from_dict(raw)
    if default is not None and default.terms.get("tos_prohibited"):
        # A stored record can't clear a prohibition the adapter's own
        # built-in default currently states -- ToS status is a property
        # of the site, not of what an earlier import happened to observe.
        caps.terms["tos_prohibited"] = True
    return caps


def save_capabilities(source: str, caps: SourceCapabilities):
    store.save_capabilities(source, caps.to_dict())


def apply_terms(caps: SourceCapabilities) -> SourceCapabilities:
    """A written, specific anti-scraping/AI-use clause (recorded in the
    terms block, quoted) is the only thing that makes a source
    DISQUALIFIED -- a technical wall never does."""
    if caps.terms.get("tos_prohibited"):
        caps.status = CapabilityStatus.TOS_PROHIBITED.value
        caps.technical_status = TechnicalStatus.DISQUALIFIED.value
    return caps


def check_terms(source: str, default: SourceCapabilities = None):
    """Raises TermsProhibited when the source's record carries a
    written ToS prohibition -- called before an import sends anything."""
    caps = apply_terms(load_capabilities(source, default))
    if caps.status == CapabilityStatus.TOS_PROHIBITED.value:
        raise TermsProhibited(
            f"{caps.platform or source}'s terms of service prohibit automated access, so the "
            "app won't import from it. Save the pages yourself and upload them manually instead.")


def test_tier(source: str, tier: AccessTier, url: str, tier_fn,
              default: SourceCapabilities = None) -> SourceCapabilities:
    """Runs exactly one tier against `url` and updates only that tier's
    field on the source's record. Other tiers -- including UNTESTED ones
    -- are left exactly as they were."""
    caps = load_capabilities(source, default)
    outcome = tier_fn(url)
    caps.tiers[tier.value] = TierResult(
        tested=True, ok=outcome.ok,
        reason=None if outcome.ok else (outcome.reasons[0].value if outcome.reasons else
                                        FailureReason.UNKNOWN.value),
        detail=outcome.detail[:300], at=time.time())
    if outcome.ok and caps.access_method is None:
        caps.access_method = tier.value
    save_capabilities(source, caps)
    store.log_attempt(source, url, {"tier": tier.value, "test_now": True, "ok": outcome.ok,
                                    "reasons": [r.value for r in outcome.reasons],
                                    "lines": [f"{TIER_LABELS[tier]}: "
                                              f"{'SUCCESS' if outcome.ok else 'FAILED'}"
                                              + (f" -- {outcome.detail}" if outcome.detail else "")]})
    return caps


def record_ladder_result(source: str, result: LadderResult,
                         default: SourceCapabilities = None) -> SourceCapabilities:
    """Folds a real import's ladder run into the source's record."""
    caps = load_capabilities(source, default)
    for a in result.attempts:
        caps.tiers[a.tier] = TierResult(tested=True, ok=a.ok, reason=a.reason,
                                        detail=a.detail[:300], at=a.at)
    caps.technical_status = result.technical_status
    caps.status = result.capability_status
    if result.ok:
        caps.access_method = result.tier
        if result.content_access != ContentAccess.UNKNOWN.value:
            caps.content_access_status = result.content_access
    protections = [r.value for r in result.reasons if r in PROTECTION_REASONS]
    if protections:
        caps.technical["known_protections"] = sorted(
            set(caps.technical.get("known_protections", [])) | set(protections))
    caps.technical["browser_accessible"] = any(
        a.ok for a in result.attempts if a.tier == AccessTier.RENDERED_BROWSER.value) or \
        caps.technical.get("browser_accessible", False)
    apply_terms(caps)
    save_capabilities(source, caps)
    return caps
