"""Plain-words status for rendering JavaScript-only pages.

Two separate facts: the Playwright Python package, and a browser program to
drive. An installed Chrome or Edge is enough, so a missing package never
means a browser download."""
import importlib
import importlib.util

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

