"""
services/safe_fetch.py -- shared, static-only public page fetch for the API
services that must read a user-supplied URL (Discover / Sources slices).

It wraps `metadata_service._check_public_url` (http(s) only, every resolved
address must be public: private, loopback, link-local, reserved and
v4-mapped addresses are refused before any connection) and
`metadata_service._pinned_get` (connects to the validated IP, keeps SNI and
certificate checks, has a timeout) by import, so the SSRF rules live in one
place. Every redirect hop is re-validated and re-pinned, hops and bytes are
capped, and the result is visible page text via BeautifulSoup.

It never renders a browser. A page that needs JavaScript (or returns almost
no text) comes back flagged `needs_manual`, so the UI can ask the user to
paste the page text instead. Error messages are fixed strings: no URL,
exception text, path or key is ever echoed.
"""
import time
from dataclasses import dataclass
from urllib.parse import urljoin

from services import metadata_service as _ms
from services.service_errors import DependencyUnavailableError, InvalidInputError

MAX_FETCH_BYTES = 2_000_000
MAX_REDIRECTS = 3
MAX_TEXT_CHARS = 200_000
MIN_VISIBLE_CHARS = 200
_CHUNK = 65_536
_REDIRECT_CODES = (301, 302, 303, 307, 308)

FETCH_FAILED = "The page could not be fetched."
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


def _read_capped(resp, max_bytes: int, deadline: float = None) -> bytes:
    """Read at most max_bytes from the stream and stop (never the whole
    body), and stop at the wall-clock deadline: the per-read timeout alone
    restarts on every byte a slow server drips."""
    if deadline is None:
        deadline = time.monotonic() + _ms.FETCH_DEADLINE
    chunks, total = [], 0
    while total < max_bytes:
        if time.monotonic() > deadline:
            raise DependencyUnavailableError(FETCH_FAILED)
        chunk = resp.raw.read(min(_CHUNK, max_bytes - total), decode_content=True)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def fetch_public_text(url: str, max_bytes: int = MAX_FETCH_BYTES) -> FetchResult:
    """Fetch `url` statically and return its visible text (see module doc)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise DependencyUnavailableError(
            "Fetching pages needs requests and beautifulsoup4 installed.") from None
    max_bytes = max(1, min(int(max_bytes), MAX_FETCH_BYTES))
    headers = {"User-Agent": "Mozilla/5.0 (compatible; BaiheStudio/1.0)"}
    current = url
    deadline = time.monotonic() + _ms.FETCH_DEADLINE
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if time.monotonic() > deadline:
                raise DependencyUnavailableError(FETCH_FAILED)
            ip = _ms._check_public_url(current)
            resp = _ms._pinned_get(current, ip, headers)
            try:
                if resp.status_code in _REDIRECT_CODES:
                    location = resp.headers.get("Location")
                    if not location:
                        raise DependencyUnavailableError(FETCH_FAILED)
                    current = urljoin(current, location)
                    continue
                resp.raise_for_status()
                raw = _read_capped(resp, max_bytes, deadline)
                encoding = resp.encoding or "utf-8"
            finally:
                resp.close()
            soup = BeautifulSoup(raw.decode(encoding, errors="replace"), "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = "\n".join(ln.strip() for ln in soup.get_text("\n").splitlines()
                             if ln.strip())[:MAX_TEXT_CHARS]
            if len(text) < MIN_VISIBLE_CHARS:
                return FetchResult("", True, _redact(NEEDS_MANUAL_MESSAGE))
            return FetchResult(_redact(text), False)
    except (InvalidInputError, DependencyUnavailableError):
        raise
    except Exception:
        raise DependencyUnavailableError(FETCH_FAILED) from None
    raise DependencyUnavailableError(FETCH_FAILED)  # too many redirects
