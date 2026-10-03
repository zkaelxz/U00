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
  * AUTHENTICATED_BROWSER (Step 23k) reads the page inside the persistent
    browser profile the person signed in to themselves. It answers "can
    this session see it" -- never "may the app extract it": a source whose
    terms restrict automated access or AI/ML use is refused by
    check_terms() before any tier runs, signed in or not.
  * OFFICIAL_API is checked before giving up. An API that only covers
    part of what was asked (e.g. metadata but not chapter text) is a real,
    partial result -- not a pass, and not UNAVAILABLE.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from . import detect, store
from .http import REDIRECT_REFUSED, ResponseRefused, UnsafeRedirect, _ascii_url
from .models import (AccessTier, AiMlUse, AttemptRecord, AutomationPermission,
                     CapabilityStatus, CHALLENGE_REASONS, ChallengeDetected, ContentAccess,
                     ENVIRONMENT_BLOCK_REASONS, FailureReason, LADDER_ORDER, PROTECTION_REASONS,
                     Requirement, SourceCapabilities, SourceError, TechnicalProtection,
                     TechnicalStatus, TermsProhibited, TierResult, explain_protection,
                     SPA_SHELL_BROWSER_NOTE, SPA_SHELL_STATIC_NOTE)

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
    stop: bool = False                              # B-25: refused address -- try no further tier


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
    resource_types: list = field(default_factory=list)  # ContentAccess values found on the page

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
        except ResponseRefused:
            # Over the size cap or past the deadline: stop the whole fetch
            # (no browser retry of the same page), fixed message.
            raise
        except UnsafeRedirect as e:
            return TierOutcome(False, reasons=[FailureReason.ACCESS_DENIED], detail=str(e),
                               evidence=_ev(e.attempt), stop=True)
        except SourceError as e:
            return TierOutcome(False, reasons=[e.reason], detail=str(e), evidence=_ev(e.attempt))
        html = resp.text
        ev = detect.evidence(resp.status_code, resp.headers, html, url, resp.url)
        if resp.reasons:
            detail = ", ".join(r.value for r in resp.reasons)
            if FailureReason.EMPTY_SPA_SHELL in resp.reasons:
                detail += " -- " + SPA_SHELL_STATIC_NOTE
            return TierOutcome(False, html=html, reasons=list(resp.reasons), detail=detail,
                               evidence=ev)
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
        return _browser_outcome(url, client, fetch, "Browser session")
    return run


def authenticated_tier(profile_dir: str, client=None, fetch_with_profile=None):
    """AUTHENTICATED_BROWSER (Step 23k): the page as the persistent
    profile at `profile_dir` sees it -- i.e. with the person's own sign-in.
    The same classification as RENDERED_BROWSER then doubles as the
    "is the target content actually visible in this session" check: a page
    still showing a login form, a purchase prompt or protection fails with
    that specific reason, and nothing is extracted from it. Only the
    rendered page comes back; the session itself never leaves the browser."""
    def fetch(url):
        fn = fetch_with_profile
        if fn is None:
            from page_fetch import fetch_with_profile as fn
        return fn(url, profile_dir)

    def run(url: str) -> TierOutcome:
        return _browser_outcome(url, client, fetch, "Signed-in browser session")
    return run


def _browser_outcome(url, client, fetch, action) -> TierOutcome:
    try:
        if client is not None:
            html, _text = client.paced(fetch, url, action)
        else:
            html, _text = fetch(url)
    except ImportError as e:
        return TierOutcome(False, reasons=[FailureReason.NOT_INSTALLED],
                           detail=str(e).splitlines()[0])
    except Exception as e:
        from translate_engines import redact_secrets
        reason = FailureReason.TIMEOUT if "timeout" in type(e).__name__.lower() \
            else FailureReason.UNKNOWN
        return TierOutcome(False, reasons=[reason],
                           detail=redact_secrets(f"{type(e).__name__}: {e}")[:300])
    reasons = detect.classify(200, {}, html, url, url)
    # After a real render the page's own scripts have already run, so
    # crypto/Vue markers still sitting in the markup don't by themselves
    # mean the content is missing -- only a still-empty shell does.
    if FailureReason.EMPTY_SPA_SHELL not in reasons:
        reasons = [r for r in reasons if r not in (FailureReason.ENCRYPTED_RESOURCE,
                                                   FailureReason.JAVASCRIPT_REQUIRED)]
    ev = detect.evidence(200, {}, html, url, url)
    if reasons:
        notes = [SPA_SHELL_BROWSER_NOTE] if FailureReason.EMPTY_SPA_SHELL in reasons else []
        detail = " ".join([", ".join(r.value for r in reasons)] + explain_protection(reasons)
                          + notes)
        return TierOutcome(False, html=html, reasons=reasons, detail=detail, evidence=ev)
    return TierOutcome(True, html=html, evidence=ev)


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


def _refused_address(url: str):
    """True when `url` itself is not http(s) with only public addresses.
    "unresolved" when the name doesn't resolve here: the static tier may
    still run and fail normally, but the browser tiers are dropped (B-28:
    with split-horizon DNS Chromium could resolve it to a private IP)."""
    from services import url_guard
    try:
        url_guard.resolve_public(_ascii_url(url))
    except (url_guard.UnsafeURLError, UnsafeRedirect):
        return True
    except url_guard.URLResolveError:
        return "unresolved"
    return False


def run_ladder(url: str, tiers: dict, source: str = None, log: bool = True) -> LadderResult:
    """
    `tiers` maps AccessTier -> callable(url) -> TierOutcome. A tier with no
    entry is skipped (not recorded as a failure). USER_ASSISTED_BROWSER is
    only ever run when the caller supplies it -- it needs a person -- and
    OFFICIAL_API is tried last, before UNAVAILABLE.
    """
    result = LadderResult(url=url)
    refused = _refused_address(url)
    if refused == "unresolved":
        tiers = {t: fn for t, fn in tiers.items()
                 if t not in (AccessTier.RENDERED_BROWSER, AccessTier.AUTHENTICATED_BROWSER)}
    elif refused:
        # B-25: a URL that is not public is never handed to any tier at all.
        result.attempts.append(AttemptRecord(
            tier=AccessTier.STATIC_HTTP.value, ok=False,
            reason=FailureReason.ACCESS_DENIED.value, detail=REDIRECT_REFUSED,
            at=time.time()))
        result.reasons.append(FailureReason.ACCESS_DENIED)
        tiers = {}
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
        if outcome.stop:
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
    if default is not None:
        # A stored record can't clear a prohibition the adapter's own
        # built-in default currently states -- ToS status is a property
        # of the site, not of what an earlier import happened to observe
        # (nor of whether that import was signed in).
        if default.terms.get("tos_prohibited"):
            caps.terms["tos_prohibited"] = True
        for name, restricted in (("automation_permission",
                                  AutomationPermission.EXPLICITLY_RESTRICTED.value),
                                 ("ai_ml_use", AiMlUse.EXPLICITLY_RESTRICTED.value)):
            if getattr(default, name) == restricted:
                setattr(caps, name, restricted)
    return caps


def save_capabilities(source: str, caps: SourceCapabilities):
    store.save_capabilities(source, caps.to_dict())


def apply_terms(caps: SourceCapabilities) -> SourceCapabilities:
    """A written, specific anti-scraping/AI-use clause (recorded in the
    terms block, quoted, or as automation_permission / ai_ml_use =
    EXPLICITLY_RESTRICTED) is the only thing that makes a source
    DISQUALIFIED -- a technical wall never does, and a successful sign-in
    never clears it."""
    if caps.terms.get("tos_prohibited"):
        caps.automation_permission = AutomationPermission.EXPLICITLY_RESTRICTED.value
    if caps.terms_restrictions():
        caps.status = CapabilityStatus.TOS_PROHIBITED.value
        caps.technical_status = TechnicalStatus.DISQUALIFIED.value
    return caps


def _refusal(caps: SourceCapabilities, name: str) -> TermsProhibited:
    what = []
    if "automation_permission" in caps.terms_restrictions():
        what.append("prohibit automated access")
    if "ai_ml_use" in caps.terms_restrictions():
        what.append("restrict AI/ML use of its content")
    return TermsProhibited(
        f"{caps.platform or name}'s terms of service {' and '.join(what)}, so the app won't "
        "import from it -- signing in doesn't change that. Save the pages yourself and upload "
        "them manually instead.")


def check_terms(source: str, default: SourceCapabilities = None, url: str = None):
    """Raises TermsProhibited when the source's record -- or, with `url`,
    that site's own entry in sources/site_terms -- carries a written
    restriction. Called before an import sends anything, and before a
    sign-in window is opened. Never depends on authentication status."""
    # ToS/robots.txt enforcement intentionally deactivated 2026-09-27, per
    # explicit user decision (Kae) -- see step-90-deactivate-tos-enforcement.
    # To restore: uncomment the block below and remove the `pass`.
    pass
    # caps = apply_terms(load_capabilities(source, default))
    # if caps.status == CapabilityStatus.TOS_PROHIBITED.value:
    #     raise _refusal(caps, source)
    # if url:
    #     from . import site_terms
    #     site = site_terms.capabilities_for(url)
    #     if site is not None and apply_terms(site).status == CapabilityStatus.TOS_PROHIBITED.value:
    #         raise _refusal(site, url)


def test_tier(source: str, tier: AccessTier, url: str, tier_fn,
              default: SourceCapabilities = None) -> SourceCapabilities:
    """Runs exactly one tier against `url` and updates that tier's field,
    plus the aggregate `technical_status`/`access_method` those tiers
    roll up into. Other tiers -- including UNTESTED ones -- are left
    exactly as they were. Raises TermsProhibited, before anything is
    sent, for a source whose terms restrict automated access -- the same
    check every other network-touching action path already makes."""
    check_terms(source, default, url=url)
    caps = load_capabilities(source, default)
    outcome = tier_fn(url)
    caps.tiers[tier.value] = TierResult(
        tested=True, ok=outcome.ok,
        reason=None if outcome.ok else (outcome.reasons[0].value if outcome.reasons else
                                        FailureReason.UNKNOWN.value),
        detail=outcome.detail[:300], at=time.time())
    # Step 86: a manual "Test Now" click used to leave technical_status
    # exactly as it was, even on success -- reproducing "STATIC_HTTP OK
    # but the aggregate status still UNRESOLVED/higher-tier". Recompute
    # it from this one tier's outcome the same way a full ladder run
    # would if it had stopped here, via the same _resolve_status logic.
    # Only on success: a single failing tier says nothing about whatever
    # status an earlier, fuller ladder run already correctly established
    # (a different tier may have already succeeded), so a failure here
    # must not overwrite/downgrade it.
    if outcome.ok:
        result = LadderResult(url=url, tier=tier.value, partial=outcome.partial,
                              reasons=list(outcome.reasons),
                              attempts=[AttemptRecord(tier=tier.value, ok=True,
                                                      reason=None, detail=outcome.detail[:300])])
        _resolve_status(result)
        caps.technical_status = result.technical_status
        caps.status = result.capability_status
    # A tier the source's own built-in default merely *presets* (a
    # declared expectation, never itself tested=True) must not permanently
    # block a real confirmed result from updating access_method -- several
    # adapters preset a non-None default here (bilibili_manga.py,
    # mangaz.py, manhuaku.py), which the old `is None` check could never
    # overwrite. Update it when there's no confirmed access_method yet,
    # when the one on record was never actually tested, or when this
    # tier is strictly preferred (earlier in the ladder) over it.
    if outcome.ok:
        current_tested = (caps.access_method is not None
                          and caps.tiers.get(caps.access_method, TierResult()).tested)
        prefers_new = (caps.access_method is None or not current_tested
                      or (caps.access_method in [t.value for t in LADDER_ORDER]
                          and LADDER_ORDER.index(tier) <
                          LADDER_ORDER.index(AccessTier(caps.access_method))))
        if prefers_new:
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
    _record_access_facts(caps, access_facts(result))
    apply_terms(caps)
    save_capabilities(source, caps)
    return caps


# ---------------------------------------------------------------------------
# Step 23k: per-attempt access facts (Source Diagnostics item 6)
# ---------------------------------------------------------------------------

_UNAUTHENTICATED = (AccessTier.STATIC_HTTP.value, AccessTier.RENDERED_BROWSER.value)


def access_facts(result: LadderResult) -> dict:
    """What one ladder run showed about sign-in, entitlement and
    protection -- each its own fact, each with its specific wording."""
    reasons = set(result.reasons)
    auth_attempt = next((a for a in result.attempts
                         if a.tier == AccessTier.AUTHENTICATED_BROWSER.value), None)
    if auth_attempt is not None:
        if auth_attempt.ok:
            authentication = "Signed in: the target content is visible in your saved browser session."
        elif auth_attempt.reason == FailureReason.AUTHENTICATION_REQUIRED.value:
            authentication = ("Not signed in: the saved browser session still sees a login "
                              "page. Sign in again with the browser window.")
        else:
            authentication = "Tried with your saved browser session."
    elif FailureReason.AUTHENTICATION_REQUIRED in reasons:
        authentication = "This page asks for a login -- no signed-in session was used."
    elif result.ok and result.tier in _UNAUTHENTICATED:
        authentication = "Not needed: reached without signing in."
    else:
        authentication = "Unknown."
    if FailureReason.PURCHASE_REQUIRED in reasons:
        entitlement = ("Purchase/entitlement required: the page asks for a purchase or unlock "
                       "this session doesn't have. The app never works around that.")
    elif result.ok:
        entitlement = "No purchase prompt on the page reached."
    else:
        entitlement = "Unknown."
    protections = [r for r in result.reasons if r in PROTECTION_REASONS]
    if protections:
        protection = TechnicalProtection.DETECTED.value
    elif result.ok:
        protection = TechnicalProtection.NONE.value
    else:
        protection = TechnicalProtection.UNKNOWN.value
    return {
        "authenticated_session": auth_attempt is not None,
        "authentication": authentication,
        # Only an observed login wall. A signed-in success alone doesn't
        # show a sign-in was *needed* -- the signed-out tiers weren't run.
        "authentication_required": FailureReason.AUTHENTICATION_REQUIRED in reasons,
        "reached_without_auth": result.ok and result.tier in _UNAUTHENTICATED,
        "entitlement": entitlement,
        "purchase_required": FailureReason.PURCHASE_REQUIRED in reasons,
        "technical_protection": protection,
        "protection_detail": explain_protection(protections),
    }


def _record_access_facts(caps: SourceCapabilities, facts: dict):
    """REQUIRED/DETECTED stick once seen (some content on the source
    needed it); NOT_REQUIRED/NONE only fill an UNKNOWN."""
    if facts["authentication_required"]:
        caps.authentication_required = Requirement.REQUIRED.value
    elif facts["reached_without_auth"] and \
            caps.authentication_required == Requirement.UNKNOWN.value:
        caps.authentication_required = Requirement.NOT_REQUIRED.value
    if facts["purchase_required"]:
        caps.purchase_required = Requirement.REQUIRED.value
    if facts["technical_protection"] == TechnicalProtection.DETECTED.value:
        caps.technical_protection = TechnicalProtection.DETECTED.value
    elif facts["technical_protection"] == TechnicalProtection.NONE.value and \
            caps.technical_protection == TechnicalProtection.UNKNOWN.value:
        caps.technical_protection = TechnicalProtection.NONE.value
