"""
services/safe_fetch.py -- shared, static-only public page fetch for the API
services that must read a user-supplied URL (Discover / Sources slices).

The transport, SSRF guard (every redirect hop re-validated and re-pinned),
byte cap and deadline are `lib.http.get`; this module turns the page into
visible text via BeautifulSoup.

It never renders a browser. A page that needs JavaScript (or returns almost
no text) comes back flagged `needs_manual`, so the UI can ask the user to
paste the page text instead. Error messages are fixed strings: no URL,
exception text, path or key is ever echoed.
"""
from dataclasses import dataclass

from lib import http
from services.service_errors import DependencyUnavailableError, InvalidInputError

MAX_FETCH_BYTES = 2_000_000
MAX_REDIRECTS = 3
MAX_TEXT_CHARS = 200_000
MIN_VISIBLE_CHARS = 200
FETCH_TIMEOUT = 20
FETCH_DEADLINE = 30

FETCH_FAILED = http.FETCH_FAILED
NEEDS_MANUAL_MESSAGE = ("This page needs a browser or has too little text to read "
                        "automatically. Paste the page text instead.")


@dataclass
class FetchResult:
    text: str
    needs_manual: bool
    message: str = ""


def _redact(text: str) -> str:
    from translate_engines import redact_secrets
    return redact_secrets(text)


def fetch_public_text(url: str, max_bytes: int = MAX_FETCH_BYTES) -> FetchResult:
    """Fetch `url` statically and return its visible text (see module doc)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise DependencyUnavailableError(
            "Fetching pages needs requests and beautifulsoup4 installed.") from None
    max_bytes = max(1, min(int(max_bytes), MAX_FETCH_BYTES))
    try:
        resp = http.get(url, timeout=FETCH_TIMEOUT, max_bytes=max_bytes, truncate=True,
                        deadline=FETCH_DEADLINE, max_redirects=MAX_REDIRECTS,
                        headers={"User-Agent": "Mozilla/5.0 (compatible; BaiheStudio/1.0)"})
        if not resp.ok:
            raise http.FetchError()
    except InvalidInputError:
        raise
    except http.FetchError:
        raise DependencyUnavailableError(FETCH_FAILED) from None
    soup = BeautifulSoup(resp.text(),
                         "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = "\n".join(ln.strip() for ln in soup.get_text("\n").splitlines()
                     if ln.strip())[:MAX_TEXT_CHARS]
    if len(text) < MIN_VISIBLE_CHARS:
        return FetchResult("", True, _redact(NEEDS_MANUAL_MESSAGE))
    return FetchResult(_redact(text), False)
