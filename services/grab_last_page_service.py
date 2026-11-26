"""services/grab_last_page_service.py -- Keeps the page an account last read on each site, for Grab on any of its phones."""

from urllib.parse import urlsplit, urlunsplit

from lib.errors import InvalidInputError
from sources import store_last_page

MAX_URL = 500
MAX_TITLE = 120


def _clean_url(url) -> tuple[str, str]:
    """(url, host) for a public https page. Only scheme, host and path are kept: the query can hold a
    signed or session token, and this table goes into library backups and back to every phone."""
    refusal = InvalidInputError("That page address can't be remembered.")
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL or any(ord(c) < 32 for c in url):
        raise refusal
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise refusal from None
    host = (parts.hostname or "").lower()
    private = (not host or "." not in host or ":" in host or host.replace(".", "").isdigit()
               or host.endswith((".local", ".lan", ".internal")))
    if parts.scheme != "https" or private:
        raise refusal
    netloc = host + (f":{port}" if port else "")
    return urlunsplit(("https", netloc, parts.path, "", "")), host


def remember(user_id: int, url, title) -> dict:
    """Saves `url` as this account's place on its site. 422 for an address that isn't a public https page."""
    clean, host = _clean_url(url)
    name = " ".join(str(title or "").split())[:MAX_TITLE]
    store_last_page.save(user_id, host, clean, name)
    return {"host": host}


def places(user_id: int) -> dict:
    """{pages: [{host, url, title}]}, most recent first."""
    return {"pages": [{"host": r["host"], "url": r["url"], "title": r["title"]}
                      for r in store_last_page.list_for(user_id)]}
