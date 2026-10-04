"""
sources/auth_browser.py -- signed-in browser sessions (roadmap Step 23k
items 1, 2 and 4).

    1. The capability check runs first. A source whose terms restrict
       automated access (or AI/ML use) is refused here -- before a window
       is ever opened -- whatever the person's account can see.
    2. A visible browser window opens on that source's persistent profile
       (`profiles/<source>/`). The person signs in the normal way and
       opens the chapter they want; the app waits, with no timeout, until
       they close the window. It never types a password, solves a CAPTCHA,
       passes MFA, forges a session or touches a purchase check.
    3. The same profile then reads the page headlessly and checks the
       target content is actually visible in that session before anything
       is extracted -- a login page, a purchase prompt or a protected
       resource comes back as exactly that.

Later imports from the same source reuse the profile (generic_import.
fetch_page), so the person isn't asked to sign in again.

The session itself never leaves the browser: this module (and page_fetch
under it) never reads cookies or storage state, never logs them, and
never imports the LLM layer -- only the rendered page is handed on, and
only its extracted content can ever reach translate_engines.call_llm_json.
"""

import os
import shutil
from dataclasses import dataclass, field

from . import ladder, registry, store
from .models import AccessTier

LOGIN_PROMPT = ("Authentication required — a browser window will open. Log in normally and "
                "open the chapter you want.")

GENERIC = "generic"   # == generic_import.GENERIC_SOURCE (not imported: generic_import imports us)


def source_for(url: str) -> str:
    cls = registry.adapter_class_for_url(url)
    return cls.name if cls else GENERIC


def profile_key(url: str, source: str = None) -> str:
    """The adapter's name, or -- for a pasted URL no adapter covers -- the
    site's own host, so two unrelated sites never share a sign-in."""
    source = source or source_for(url)
    if source != GENERIC:
        return source
    from .profiles import domain_of
    return domain_of(url) or GENERIC


def profile_dir(url: str, source: str = None) -> str:
    return store.browser_profile_dir(profile_key(url, source))


def has_profile(url: str, source: str = None) -> bool:
    """True once the person has opened this source's sign-in window --
    which is what makes later imports read through the signed-in session."""
    d = profile_dir(url, source)
    return os.path.isdir(d) and bool(os.listdir(d))


def forget(url: str, source: str = None) -> bool:
    """Deletes this source's saved browser profile (sign-in included).
    Later imports go back to the ordinary, signed-out tiers."""
    from page_fetch import ProfileBusy, profile_lock
    d = profile_dir(url, source)
    lock = profile_lock(d)
    if not lock.acquire(blocking=False):
        raise ProfileBusy("This site's browser profile is in use right now -- try again when "
                          "its window is closed and no import is running.")
    try:
        existed = os.path.isdir(d)
        shutil.rmtree(d, ignore_errors=True)
        return existed
    finally:
        lock.release()


def _default(source: str):
    cls = registry.adapter_classes().get(source)
    return cls().capabilities() if cls else None


@dataclass
class LoginCheck:
    """The result of checking the signed-in session against the target
    page -- what the sign-in flow shows the person."""
    url: str
    ok: bool
    message: str
    facts: dict = field(default_factory=dict)
    lines: list = field(default_factory=list)


def manual_login(url: str, source: str = None, default=None, launcher=None,
                 fetch_with_profile=None) -> LoginCheck:
    """Item 2's flow, end to end. Blocks until the person closes the
    window. Raises TermsProhibited (nothing opened) for a restricted
    source; page_fetch.ProfileBusy if the profile is already open."""
    from page_fetch import open_login_window
    source = source or source_for(url)
    default = default if default is not None else _default(source)
    ladder.check_terms(source, default, url=url)
    open_login_window(url, profile_dir(url, source), launcher=launcher)
    return verify(url, source, default, launcher=launcher, fetch_with_profile=fetch_with_profile)


def verify(url: str, source: str = None, default=None, launcher=None,
           fetch_with_profile=None) -> LoginCheck:
    """Reads `url` through the saved profile and says whether the target
    content is really visible to it. Nothing is extracted here."""
    source = source or source_for(url)
    default = default if default is not None else _default(source)
    ladder.check_terms(source, default, url=url)
    if fetch_with_profile is None:
        from page_fetch import fetch_with_profile as _fetch

        def fetch_with_profile(u, d):
            return _fetch(u, d, launcher=launcher)
    tier = ladder.authenticated_tier(profile_dir(url, source), fetch_with_profile=fetch_with_profile)
    result = ladder.run_ladder(url, {AccessTier.AUTHENTICATED_BROWSER: tier}, source=source)
    ladder.record_ladder_result(source, result, default)
    facts = ladder.access_facts(result)
    if result.ok:
        message = ("Signed in -- this page is visible in your saved browser session. Import it "
                   "now; later imports from this site reuse the session instead of asking you "
                   "to sign in again.")
    elif result.handoff:
        message = ("The page is still showing a browser verification check. Complete it "
                   "yourself in the browser window, then close the window again.")
    elif facts["protection_detail"]:
        message = " ".join(facts["protection_detail"])
    elif facts["purchase_required"]:
        message = facts["entitlement"]
    else:
        message = facts["authentication"]
    return LoginCheck(url=url, ok=result.ok, message=message, facts=facts,
                      lines=result.summary_lines())
