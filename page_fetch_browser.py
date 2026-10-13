"""
page_fetch_browser.py -- which browser program page_fetch launches, and the
shutdown flag that stops new launches.

Playwright pins one exact Chromium build per release, so the launch order
is BAIHE_BROWSER_PATH, then Playwright's own build, then an installed
Chrome or Edge. Nothing is downloaded here.
"""

import os
import threading

import browser_support
from browser_support import BROWSER_MISSING, PACKAGE_MISSING


# Set when the app is shutting down (services/shutdown_service.py): no new
# browser starts, and a sign-in window closes on its own thread (Playwright's
# sync objects can't be closed from another one). Anything still running
# after the grace period ends with the server's Job Object (process_guard).
_SHUTDOWN = threading.Event()


def request_shutdown() -> None:
    _SHUTDOWN.set()


def _require_playwright():
    if _SHUTDOWN.is_set():
        raise RuntimeError("Baihe Studio is shutting down; no new browser is started.")
    import importlib
    importlib.invalidate_caches()  # a just-installed package must import without a restart
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(PACKAGE_MISSING) from None
    browser_support.use_app_browsers()
    return sync_playwright


# ---------------------------------------------------------------------------
# Which browser program to launch
# ---------------------------------------------------------------------------
#
# Playwright pins one exact Chromium build per release, so upgrading the
# package makes the build it downloaded earlier "missing". Rather than ask
# for another download, the launch order is: BAIHE_BROWSER_PATH if that
# file exists, then Playwright's own build, then an installed Chrome or
# Edge. Nothing is downloaded here.

BROWSER_ENV = "BAIHE_BROWSER_PATH"

class BrowserNotFound(RuntimeError):
    """No usable browser program was found. The message is fixed text with
    no filesystem path, so it is safe to show."""


def _system_browser_candidates():
    """(name, path) in preference order for this OS."""
    import shutil
    import sys
    out = []
    if sys.platform == "win32":
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
        for root in (r for r in roots if r):
            out.append(("Chrome", os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")))
            out.append(("Edge", os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe")))
    elif sys.platform == "darwin":
        for base in ("/Applications", os.path.expanduser("~/Applications")):
            out.append(("Chrome", base + "/Google Chrome.app/Contents/MacOS/Google Chrome"))
            out.append(("Edge", base + "/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"))
    else:
        for exe, name in (("google-chrome", "Chrome"), ("google-chrome-stable", "Chrome"),
                          ("microsoft-edge", "Edge"), ("microsoft-edge-stable", "Edge"),
                          ("chromium", "Chromium"), ("chromium-browser", "Chromium")):
            found = shutil.which(exe)
            if found:
                out.append((name, found))
    return out


def find_system_browser():
    """(name, path) of an installed Chrome, Edge or Chromium, or None."""
    return next(((n, p) for n, p in _system_browser_candidates() if os.path.isfile(p)), None)


def _explicit_browser():
    path = (os.environ.get(BROWSER_ENV) or "").strip().strip('"')
    return path if path and os.path.isfile(path) else None


def _bundled_browser_present() -> bool:
    """Whether the Chromium build the installed Playwright wants (not just
    any older download) is on disk with its program file. False when
    Playwright or its manifest can't be read."""
    wanted = browser_support.wanted_browser_folders()
    if not wanted:
        return False
    dirs = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        dirs.append(env)
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(os.path.join(os.environ["LOCALAPPDATA"], "ms-playwright"))
    home = os.path.expanduser("~")
    dirs += [os.path.join(home, "Library", "Caches", "ms-playwright"),
             os.path.join(home, ".cache", "ms-playwright")]
    # The app folder goes through the completeness marker so an interrupted
    # install doesn't read as a usable browser.
    return browser_support.app_chromium_present() or any(
        all(browser_support.has_browser_program(os.path.join(d, n)) for n in wanted) for d in dirs)


def browser_status() -> dict:
    """{found, name}: what a browser launch would use, without launching.
    Never includes a path."""
    if _explicit_browser():
        return {"found": True, "name": "custom"}
    if _bundled_browser_present():
        return {"found": True, "name": "Playwright Chromium"}
    system = find_system_browser()
    if system:
        return {"found": True, "name": system[0]}
    return {"found": False, "name": None}


def _launch_chromium(launch, *args, **kwargs):
    """`launch(*args, **kwargs)` (chromium.launch or launch_persistent_context),
    falling back to an installed Chrome/Edge when Playwright's own build is
    missing. Raises BrowserNotFound when there is none."""
    explicit = _explicit_browser()
    if explicit:
        return launch(*args, executable_path=explicit, **kwargs)
    try:
        return launch(*args, **kwargs)
    except Exception as e:
        if "Executable doesn't exist" not in str(e) and "playwright install" not in str(e):
            raise
    system = find_system_browser()
    if system is None:
        raise BrowserNotFound(BROWSER_MISSING) from None
    return launch(*args, executable_path=system[1], **kwargs)
