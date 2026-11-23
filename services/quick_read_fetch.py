"""Quick read's PC-side download of the pictures a phone names by address, so the phone need not upload them."""

import concurrent.futures
import threading
from urllib.parse import urlsplit

from lib import http
from lib.errors import InvalidInputError

FETCH_TIMEOUT = (5, 20)
# One send names at most MAX_IMAGES_PER_REQUEST pictures; fetching them together keeps the
# first page from waiting on the slowest download.
FETCHES_AT_ONCE = 6
# Per request and per member: a small request can name 12 pictures of 12 MB each, and none of it
# reaches an engine (so no spending cap) when the addresses are not images.
MAX_TOTAL_BYTES = 48 * 1024 * 1024
REQUESTS_AT_ONCE_PER_MEMBER = 2
MAX_URL_CHARS = 2048

# A CDN that checks the browser (rawkuma's does) refuses the default python-requests agent.
FALLBACK_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

BLOCKED, MISSING, UNREACHABLE = "blocked", "missing", "unreachable"
TOO_LARGE, NOT_IMAGE, UNSAFE = "too_large", "not_image", "unsafe_address"


def _same_site(picture_url: str, chapter_url: str) -> bool:
    """A cookie from the chapter's site goes only to that site or its subdomains (a CDN on a
    sibling domain would otherwise be handed the person's login)."""
    try:
        picture_parts, chapter_parts = urlsplit(picture_url), urlsplit(chapter_url)
        picture, chapter = (picture_parts.hostname or ""), (chapter_parts.hostname or "")
    except ValueError:
        return False
    # Never over plain http: the browser would have upgraded the picture, the PC would not.
    if not picture or not chapter or picture_parts.scheme != "https":
        return False
    return picture == chapter or picture.endswith("." + chapter)


def request_headers(fetch_url: str, options: dict) -> dict:
    referer = options.get("referer") or ""
    headers = {"User-Agent": options.get("user_agent") or FALLBACK_USER_AGENT,
               "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    if options.get("cookie") and _same_site(fetch_url, referer):
        headers["Cookie"] = options["cookie"]
    return headers


def fetch_one(fetch_url: str, options: dict, max_bytes: int) -> tuple[bytes | None, str]:
    """(bytes, "") or (None, reason). The address is checked as public before every hop and the
    redirects are followed by lib.http, which drops the cookie once a hop leaves the site."""
    try:
        if not isinstance(fetch_url, str) or len(fetch_url) > MAX_URL_CHARS or \
                urlsplit(fetch_url).scheme not in ("http", "https"):
            return None, UNSAFE
        response = http.get(fetch_url, timeout=FETCH_TIMEOUT, max_bytes=max_bytes,
                            headers=request_headers(fetch_url, options), max_error_bytes=1024)
    except (InvalidInputError, ValueError):   # ValueError: a malformed port or bracketed host, or a header the codec refuses
        return None, UNSAFE
    except http.ResponseTooLarge:
        return None, TOO_LARGE
    except http.FetchError:
        return None, UNREACHABLE
    if response.status in (401, 403, 429):
        return None, BLOCKED
    if response.status in (404, 410):
        return None, MISSING
    if response.status >= 400:
        return None, UNREACHABLE
    if not response.body:
        return None, NOT_IMAGE
    return response.body, ""


class _Budget:
    def __init__(self, total: int):
        self._left, self._lock = total, threading.Lock()

    def left(self) -> int:
        with self._lock:
            return self._left

    def spend(self, n: int) -> None:
        with self._lock:
            self._left -= n


_member_slots: dict = {}
_member_slots_lock = threading.Lock()


def _slots(member) -> threading.BoundedSemaphore:
    with _member_slots_lock:
        return _member_slots.setdefault(member, threading.BoundedSemaphore(REQUESTS_AT_ONCE_PER_MEMBER))


def fetch_all(wanted: dict, options: dict, max_bytes: int, member=None) -> dict:
    """`wanted` maps a key to its picture address; the answer maps each key to (bytes, reason).
    `member` (None for the PC's owner) holds one of REQUESTS_AT_ONCE_PER_MEMBER slots while it
    downloads, so one account cannot take the household's bandwidth with parallel requests."""
    if not wanted:
        return {}
    with _slots(member):
        return _fetch_all(wanted, options, max_bytes)


def _fetch_all(wanted: dict, options: dict, max_bytes: int) -> dict:
    budget = _Budget(MAX_TOTAL_BYTES)

    def one(url):
        room = min(max_bytes, budget.left())
        if room <= 0:
            return None, TOO_LARGE
        content, reason = fetch_one(url, options, room)
        if content is not None:
            budget.spend(len(content))
        return content, reason

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(FETCHES_AT_ONCE, len(wanted)),
                                               thread_name_prefix="quick-read-fetch") as pool:
        futures = {key: pool.submit(one, url) for key, url in wanted.items()}
        return {key: future.result() for key, future in futures.items()}
