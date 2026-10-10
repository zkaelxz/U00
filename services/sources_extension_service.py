"""
services/sources_extension_service.py -- the early stop every import entry point takes for a
source the person marked as working only through the browser extension.

The marker itself (sources/extension_marker.py) is the person's own record. It sits beside the
capability record and never edits it, so the Static and Browser results stay as tested. The
write that sets it is `sources_registry_service.set_extension_only`, next to the other
per-source switches.
"""

from urllib.parse import urlsplit

from services.service_errors import ExtensionOnlyError
from sources import extension_marker, registry

EXTENSION_ONLY_MESSAGE = ("This site only works through the browser extension. Open the chapter "
                          "in Chrome and use the extension.")
# Shown on the tracked-series line after a scheduled check passes the series by.
SKIPPED_STATUS = "extension only: skipped"
# The app page holding the extension's help (Settings > Browser extension).
HELP_ROUTE = "settings"


def extension_only_error() -> ExtensionOnlyError:
    return ExtensionOnlyError(EXTENSION_ONLY_MESSAGE, details={"reason": "EXTENSION_ONLY",
                                                               "help": HELP_ROUTE})


def require_not_extension_only(name) -> None:
    """Stops an import, series load, search or tracking request for a marked source."""
    if extension_marker.is_marked(str(name or "")):
        raise extension_only_error()


def require_url_not_extension_only(url) -> None:
    """The same stop for a pasted link whose address belongs to a marked source."""
    try:
        has_host = bool(urlsplit(str(url or "")).hostname)
    except ValueError:
        has_host = False
    cls = registry.adapter_class_for_url(url) if has_host else None
    if cls is not None:
        require_not_extension_only(cls.name)
