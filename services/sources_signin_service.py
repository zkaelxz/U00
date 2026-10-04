"""
services/sources_signin_service.py -- the PC-only Sources actions that open
a browser or touch a saved browser profile (Discover/Sources/Live spec S-6)
and the per-tier "Test now" buttons of the Source settings (inventory SO17).

Every route over this module is `local_only()`: a sign-in window opens on
the PC's own screen, a profile holds the owner's sign-in, and a browser
tier test drives Chromium from the PC. Nothing here ever reads or returns a
cookie, storage state, profile path or the profile's contents; results are
{ok, message, lines} text (scrubbed of secrets, paths and URL queries).

The page URL a sign-in or tier test uses must be a public http(s) address
(`sources_url_service.check_public_url`) on the source's own site: a page
the adapter recognises (`matches_url`) or its login page, base URL or a
mirror (same host or a subdomain). So the window and the tests only ever go
to that site. ToS enforcement is OFF (Step 90), but `adapter.login` and
`ladder.test_tier` still call `ladder.check_terms` first.

Jobs (one per source; results via GET /api/sources/jobs/{id}/result, which
answers them only to a request from this PC). Not hidden elsewhere: like
every job they show in GET /api/jobs (status, description, message, scrubbed
error; `library.read`), and a tier test's outcome is saved in the source's
capability record (GET /api/sources/{name}). Text is scrubbed either way.
- `sources_signin_<name>`: `adapter.login(url)` opens a visible window and
  waits, with no timeout, until the person closes it; it cannot be
  cancelled from the API (the window is the way out). Then the page is read
  through the profile to confirm the content is visible.
- `sources_tiertest_<name>`: `ladder.test_tier` for one tier, which updates
  only that tier's line in the source's capability record.

`forget_signin` deletes the source's saved browser profile
(`auth_browser.forget`); it needs confirm=true and refuses (409) while the
profile is open.
"""

from urllib.parse import urlsplit

import background_jobs
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, UnsupportedOperationError)
from services.sources_registry_service import require_source, scrub
from services.sources_search_service import error_view, JobFailed, start_job
from services.sources_url_service import check_public_url, without_urls
from sources import auth_browser, ladder, registry
from sources.models import AccessTier

SIGNIN_JOB_PREFIX = "sources_signin_"
TIERTEST_JOB_PREFIX = "sources_tiertest_"
# The API's tier names -> the ladder's.
TIERS = {"static": AccessTier.STATIC_HTTP, "browser": AccessTier.RENDERED_BROWSER,
         "signed_in": AccessTier.AUTHENTICATED_BROWSER}
_NOT_ON_SITE = "That page isn't on this source's site."


def is_pc_only_job(job_id: str) -> bool:
    return any(job_id.startswith(p) and len(job_id) > len(p)
               for p in (SIGNIN_JOB_PREFIX, TIERTEST_JOB_PREFIX))


def _site_hosts(adapter) -> set:
    urls = [getattr(adapter, "login_url", ""), getattr(adapter, "base_url", "")]
    urls += list(getattr(adapter, "mirrors", None) or ())
    hosts = set()
    for u in urls:
        try:
            h = (urlsplit(str(u or "")).hostname or "").lower()
        except ValueError:
            continue
        for prefix in ("www.", "m."):
            if h.startswith(prefix):
                h = h[len(prefix):]
        if h:
            hosts.add(h)
    return hosts


def _site_url(cls, adapter, url) -> str:
    """A checked public URL on this source's own site, or 422."""
    text = check_public_url(url)
    if cls.matches_url(text):
        return text
    host = (urlsplit(text).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in _site_hosts(adapter)):
        return text
    raise InvalidInputError(_NOT_ON_SITE)


def _job_error(e) -> dict:
    from page_fetch import ProfileBusy, ProxyBypassed
    if isinstance(e, ProfileBusy):
        return {"status": 409, "code": ConflictError.code, "message": scrub(str(e)),
                "details": {"reason": "PROFILE_BUSY"}}
    if isinstance(e, ImportError):
        return {"status": 503, "code": DependencyUnavailableError.code,
                "message": "The browser add-on (Playwright) isn't installed on this PC.",
                "details": {"reason": "NOT_INSTALLED"}}
    if isinstance(e, ProxyBypassed):
        return {"status": 503, "code": DependencyUnavailableError.code,
                "message": scrub(str(e)), "details": {"reason": "PROXY_BYPASSED"}}
    return without_urls(error_view(e))


def _fail(job_id: str, kind: str, source: str, e):
    err = _job_error(e)
    background_jobs.set_result(job_id, {"kind": kind, "source": source, "error": err})
    raise JobFailed(err["message"]) from None


# ---------------------------------------------------------------------------
# Sign-in (S-6)
# ---------------------------------------------------------------------------

def _signin_job(job_id: str, name: str, url: str):
    adapter = registry.get_adapter(name)
    background_jobs.update_progress(job_id, 0.1, "Waiting for you in the browser window on the "
                                                 "PC. Close it once the page is open.")
    try:
        check = adapter.login(url)
    except Exception as e:
        _fail(job_id, "signin", name, e)
    background_jobs.set_result(job_id, {
        "kind": "signin", "source": name, "ok": bool(check.ok),
        "message": scrub(check.message) or "",
        "lines": [scrub(line) for line in (check.lines or [])][:20],
        "has_saved_signin": bool(auth_browser.has_profile("", name)),
    })


def start_signin(name: str, url: str = "") -> dict:
    """{job_id}. 400 when the source has no sign-in; 422 a URL off its site
    (or none, for a source with no login page); 409 while one runs."""
    name = str(name or "")
    cls = require_source(name)
    if not cls.auth_supported:
        raise UnsupportedOperationError("This source has no sign-in.",
                                        details={"reason": "NOT_SUPPORTED"})
    adapter = cls()
    url = (url or "").strip()
    if url:
        url = _site_url(cls, adapter, url)
    elif not cls.login_url:
        raise InvalidInputError("Paste a page from this source to sign in there.")
    else:
        url = cls.login_url
    job_id = SIGNIN_JOB_PREFIX + name
    return start_job(job_id, _signin_job, job_id, name, url,
                  description=f"Sign-in window ({name})")


def forget_signin(name: str, confirm: bool) -> dict:
    """{source, forgotten, has_saved_signin}. 422 without confirm=true, 409
    while the profile is open (a sign-in window or an import using it)."""
    from page_fetch import ProfileBusy
    name = str(name or "")
    cls = require_source(name)
    if confirm is not True:
        raise InvalidInputError("Forgetting a sign-in needs confirm=true.")
    if not cls.auth_supported:
        raise UnsupportedOperationError("This source has no sign-in.",
                                        details={"reason": "NOT_SUPPORTED"})
    try:
        existed = auth_browser.forget("", name)
    except ProfileBusy:
        raise ConflictError("This site's browser profile is in use. Close its window, or wait "
                            "for the import using it, then try again.",
                            details={"reason": "PROFILE_BUSY"}) from None
    return {"source": name, "forgotten": bool(existed),
            "has_saved_signin": bool(auth_browser.has_profile("", name))}


# ---------------------------------------------------------------------------
# Test one access tier (SO17)
# ---------------------------------------------------------------------------

def _tier_fn(tier: AccessTier, adapter, name: str, url: str):
    if tier == AccessTier.STATIC_HTTP:
        return ladder.static_tier(adapter.client)
    if tier == AccessTier.RENDERED_BROWSER:
        return ladder.rendered_tier(adapter.client)
    return ladder.authenticated_tier(auth_browser.profile_dir(url, name), adapter.client)


def _tier_job(job_id: str, name: str, tier_key: str, url: str):
    adapter = registry.get_adapter(name)
    tier = TIERS[tier_key]
    background_jobs.update_progress(job_id, 0.1, "Testing...")
    try:
        caps = ladder.test_tier(name, tier, url, _tier_fn(tier, adapter, name, url),
                                adapter.capabilities())
    except Exception as e:
        _fail(job_id, "tier_test", name, e)
    res = caps.tiers.get(tier.value)
    background_jobs.set_result(job_id, {
        "kind": "tier_test", "source": name, "tier": tier_key,
        "ok": bool(res and res.ok),
        "reason": scrub(res.reason) if res else None,
        "detail": scrub(res.detail) if res else None,
    })


def start_tier_test(name: str, tier: str, url: str) -> dict:
    """{job_id}. 422 unknown tier, URL off the site, or the signed-in tier
    with no saved sign-in (testing it would create an empty profile)."""
    name = str(name or "")
    cls = require_source(name)
    if tier not in TIERS:
        raise InvalidInputError("tier must be one of: " + ", ".join(TIERS))
    url = _site_url(cls, cls(), url)
    if TIERS[tier] == AccessTier.AUTHENTICATED_BROWSER and not auth_browser.has_profile(url, name):
        raise InvalidInputError("Sign in to this source first.")
    job_id = TIERTEST_JOB_PREFIX + name
    return start_job(job_id, _tier_job, job_id, name, tier, url,
                  description=f"Test access ({name})")
