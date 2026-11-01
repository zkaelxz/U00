"""
page_fetch_profile.py -- reads and sign-in windows on a persistent browser
profile, so a site the person already signed in to is fetched signed in.
"""

import os
import threading
import time

import storage
from page_fetch import _visible_lines
from page_fetch_browser import _SHUTDOWN, _launch_chromium, _require_playwright
from page_fetch_guard import (
    _BYPASSED, _PinningProxy, ProxyBypassed, _goto, _guard_context)
from page_scroll import scroll_through_and_settle

LOGIN_POLL_MS = 1000
# A sign-in window nobody closes (walked away, forgot it) would hold the
# source's browser profile lock and a job slot until the app quits; ten
# minutes is long enough for a CAPTCHA or MFA round and short enough to
# free the profile the same session.
LOGIN_MAX_WAIT_SECONDS = 600



# id(persistent context) -> its _PinningProxy (stopped in _shut)
_PROXIES = {}


# ---------------------------------------------------------------------------
# Persistent browser profiles
# ---------------------------------------------------------------------------
#
# One Chromium profile directory per source, opened with Playwright's
# launch_persistent_context: whatever the person's own sign-in left in
# that profile (cookies, local storage) is there again on the next visit,
# so they aren't asked to log in on every import.
#
# What persists is the profile directory -- the browser process itself is
# started per call and closed after. Playwright's sync objects only work
# on the thread that created them (each background job runs on its own
# thread), a Chromium profile can only be open in one browser at a time,
# and the visible sign-in window and the headless reads need separate
# launches anyway. Login state survives all of that because it lives in
# the profile, which is exactly what a persistent context is for.
#
# The session data never leaves Chromium: nothing here reads cookies or
# storage state, and the only thing handed back is the page as rendered.

_PROFILE_LOCKS = {}
_PROFILE_LOCKS_GUARD = threading.Lock()
PROFILE_BUSY_WAIT = 120  # seconds a read waits for another use of the same profile


class ProfileBusy(RuntimeError):
    """The profile is already open (e.g. its sign-in window is still up)."""


def profile_lock(profile_dir: str) -> threading.Lock:
    key = os.path.abspath(profile_dir)
    with _PROFILE_LOCKS_GUARD:
        return _PROFILE_LOCKS.setdefault(key, threading.Lock())


def _launch_persistent(profile_dir: str, headless: bool):
    """(playwright, context) for one persistent-profile launch. The
    browser's own user agent is kept -- the same browser the person signed
    in with, not a disguised one."""
    sync_playwright = _require_playwright()
    pw, release = storage.playwright_start(sync_playwright)
    proxy = None
    try:
        proxy = _PinningProxy()
        context = _launch_chromium(pw.chromium.launch_persistent_context, profile_dir,
                                   headless=headless, service_workers="block",
                                   **proxy.launch_kwargs())
    except Exception:
        if proxy is not None:
            proxy.stop()
        try:
            pw.stop()
        finally:
            release()
        raise
    _PROXIES[id(context)] = proxy
    _TEMP_RELEASES[id(context)] = release
    return pw, context


# id(persistent context) -> removes the driver's temp folder (run in _shut)
_TEMP_RELEASES = {}


def _shut(pw, context):
    for fn in (getattr(context, "close", None), getattr(pw, "stop", None)):
        try:
            if fn is not None:
                fn()
        except Exception:
            pass
    release = _TEMP_RELEASES.pop(id(context), None)
    if release is not None:
        release()
    proxy = _PROXIES.pop(id(context), None)
    if proxy is not None:
        proxy.stop()


def fetch_with_profile(url: str, profile_dir: str, timeout: int = 30, wait_selector: str | None = None,
                       wait_ms: int = 2500, launcher=None):
    """Like fetch_rendered, but inside the persistent profile at
    `profile_dir`, so a site the person already signed in to sees that
    same signed-in browser. Returns (html, text). `launcher(profile_dir,
    headless)` -> (playwright, context) is injectable for tests."""
    lock = profile_lock(profile_dir)
    if not lock.acquire(timeout=PROFILE_BUSY_WAIT):
        raise ProfileBusy("This site's browser profile is still in use (is its sign-in "
                          "window still open?). Finish there and close it first.")
    try:
        os.makedirs(profile_dir, exist_ok=True)
        pw, context = (launcher or _launch_persistent)(profile_dir, True)
        try:
            _guard_context(context)
            page = context.new_page()
            _goto(page, url, _PROXIES.get(id(context)), allow_unguarded=launcher is not None,
                  timeout=timeout * 1000, wait_until="networkidle")
            scroll_through_and_settle(page, timeout * 1000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except Exception:
                    pass
            else:
                page.wait_for_timeout(wait_ms)
            html = page.content()
        finally:
            _shut(pw, context)
    finally:
        lock.release()
    return html, _visible_lines(html)


def _playwright_timeout_error():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        return PlaywrightTimeout
    except ImportError:
        class _NoTimeout(Exception):
            pass
        return _NoTimeout


class LoginWindowTimeout(RuntimeError):
    """The sign-in window stayed open past LOGIN_MAX_WAIT_SECONDS."""


def _wait_for_close(context, max_wait: float | None = None) -> None:
    """Waits for the person to close the window, up to LOGIN_MAX_WAIT_SECONDS --
    in short steps, so an app shutdown ends the wait too (and _shut then
    closes the browser on this, its own, thread)."""
    timeout_error = _playwright_timeout_error()
    give_up_at = time.monotonic() + (LOGIN_MAX_WAIT_SECONDS if max_wait is None else max_wait)
    while not _SHUTDOWN.is_set():
        if time.monotonic() >= give_up_at:
            raise LoginWindowTimeout("The sign-in window was left open too long, so it was closed.")
        try:
            context.wait_for_event("close", timeout=LOGIN_POLL_MS)
            return
        except timeout_error:
            continue


def open_login_window(url: str, profile_dir: str, launcher=None):
    """Opens a visible browser window on the persistent profile at `url`
    and waits until the person closes it (or LOGIN_MAX_WAIT_SECONDS passes). They sign
    in (and pass any CAPTCHA/MFA the site asks for) themselves, the normal
    way; nothing here types, clicks, solves or reads anything."""
    lock = profile_lock(profile_dir)
    if not lock.acquire(blocking=False):
        raise ProfileBusy("This site's browser profile is already open -- finish in that "
                          "window (or wait for the import using it) first.")
    try:
        os.makedirs(profile_dir, exist_ok=True)
        pw, context = (launcher or _launch_persistent)(profile_dir, False)
        try:
            _guard_context(context)
            page = context.pages[0] if context.pages else context.new_page()
            proxy = _PROXIES.get(id(context))
            try:
                _goto(page, url, proxy, allow_unguarded=launcher is not None,
                      wait_until="domcontentloaded")
            except ProxyBypassed:
                raise
            except Exception:
                # The window stays open for the person to navigate -- but only
                # if the proxy demonstrably carries this browser's traffic.
                if proxy is not None and proxy.proxied == 0:
                    raise ProxyBypassed(_BYPASSED) from None  # the window is still open; the person can navigate there themselves
            _wait_for_close(context)
        finally:
            _shut(pw, context)
    finally:
        lock.release()
