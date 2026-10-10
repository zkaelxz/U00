"""
browser_support.py -- where the app keeps the Chromium it downloads for
JavaScript-heavy source sites, and how to tell whether it is complete.

The browser lives in its own folder inside the data directory instead of
Playwright's global cache, so an uninstall removes it with the app and a
second Playwright project on the machine never shares or deletes it.
page_fetch points Playwright at this folder when the build it wants is
there (use_app_browsers); services/browser_install_service.py fills it.
"""

import importlib
import importlib.util
import json
import os

import portable

BROWSERS_ENV = "PLAYWRIGHT_BROWSERS_PATH"
FOLDER_NAME = "playwright_browsers"
# Playwright writes this only after the download is fully unpacked.
COMPLETE_MARKER = "INSTALLATION_COMPLETE"

_BROWSER_PROGRAMS = {"chrome", "chrome.exe", "chromium", "google chrome for testing",
                     "chrome-headless-shell", "chrome-headless-shell.exe", "headless_shell"}


def app_browsers_folder() -> str:
    return os.path.join(portable.data_dir(), FOLDER_NAME)


def has_browser_program(folder: str, depth: int = 5) -> bool:
    """Whether `folder` holds a browser program file within `depth` levels
    (the unpacked layout differs by OS and Playwright release)."""
    try:
        for entry in os.scandir(folder):
            if entry.is_file():
                if entry.name.lower() in _BROWSER_PROGRAMS and os.access(entry.path, os.X_OK):
                    return True
            elif depth > 0 and entry.is_dir() and has_browser_program(entry.path, depth - 1):
                return True
    except OSError:
        pass
    return False


def wanted_browser_folders():
    """Folder names the installed Playwright launches Chromium from (full
    and headless shell), read from its bundled manifest without importing
    it. None when the package or manifest can't be read."""
    try:
        spec = importlib.util.find_spec("playwright")
        pkg = list(spec.submodule_search_locations or [])[0]
        with open(os.path.join(pkg, "driver", "package", "browsers.json"), encoding="utf-8") as f:
            browsers = json.load(f)["browsers"]
        names = [f"{b['name'].replace('-', '_')}-{b['revision']}" for b in browsers
                 if b["name"] in ("chromium", "chromium-headless-shell")]
    except (ImportError, ValueError, OSError, IndexError, KeyError, TypeError, AttributeError):
        return None
    return names or None


def app_chromium_present() -> bool:
    """Whether the build the installed Playwright wants (not an older
    leftover) is in the app folder, fully unpacked. The marker matters
    because an interrupted install (sleep, closed console, antivirus
    holding files so cleanup leaves some behind) can leave a program file
    that Playwright then fails to launch."""
    wanted = wanted_browser_folders()
    if not wanted:
        return False
    folder = app_browsers_folder()
    return all(os.path.isfile(os.path.join(folder, n, COMPLETE_MARKER))
               and has_browser_program(os.path.join(folder, n)) for n in wanted)


def use_app_browsers() -> None:
    """Point Playwright at the app folder when its Chromium is there.
    Overrides a PLAYWRIGHT_BROWSERS_PATH from the environment on purpose:
    status says the app's copy is installed, so launching must use it.
    Playwright reads the variable when its driver starts, so call this
    before sync_playwright().start()."""
    if app_chromium_present():
        os.environ[BROWSERS_ENV] = app_browsers_folder()


PACKAGE_MISSING = ("The Playwright package isn't installed: install it from Diagnostics > "
                   "Packages (the playwright row) or run `pip install playwright`, and no "
                   "browser download is needed if Google Chrome or Microsoft Edge is installed.")

BROWSER_MISSING = ("No browser found for JavaScript-only sites: install Google Chrome or "
                   "Microsoft Edge, or use Install browser support (Diagnostics > Setup).")


def package_installed() -> bool:
    # A package installed while the server runs stays invisible to find_spec
    # until the finders' cached directory listings are dropped.
    importlib.invalidate_caches()
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False
