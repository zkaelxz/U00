"""
lib/url_guard.py -- the one public-address check for server-side
fetches of a user- or site-supplied URL.

`resolve_public(url)` accepts http(s) URLs only (no userinfo) whose host
resolves, and whose EVERY resolved address is global: private, loopback,
link-local, reserved, multicast and v4-mapped non-global addresses are
refused before any connection. It returns the first validated address so
the caller can pin its connection to it.

Callers: `services.metadata_service._check_public_url` (and through it
`services.safe_fetch`), `sources.http._requests_transport` and
`page_fetch.fetch_static`, which re-validate every redirect hop, and
`services.egress_proxy`, which checks every connection ffmpeg and yt-dlp
make during live capture. Error
messages are fixed strings with no URL, host or IP in them.

Standard library only, so `sources/` can import it without pulling in the
service layer.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

NOT_PUBLIC = "The address is not a public web address."
BAD_URL = "The address must be a valid http:// or https:// URL."
RESOLVE_FAILED = "The address could not be resolved."
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIXTOFOUR = ipaddress.ip_network("2002::/16")


class UnsafeURLError(ValueError):
    """The URL is malformed, not http(s), or points at a non-public host."""


class URLResolveError(OSError):
    """The host did not resolve (an ordinary network failure)."""


def resolve_public(url: str) -> str:
    """Validate `url` and return the first resolved (global) address."""
    if not isinstance(url, str):
        raise UnsafeURLError(BAD_URL)
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise UnsafeURLError(BAD_URL) from None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise UnsafeURLError(BAD_URL)
    try:
        infos = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except (UnicodeError, OSError):
        raise URLResolveError(RESOLVE_FAILED) from None
    if not infos:
        raise URLResolveError(RESOLVE_FAILED)
    pinned = None
    for info in infos:
        raw_ip = info[4][0].split("%")[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            raise UnsafeURLError(NOT_PUBLIC) from None
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        # Python reports both as global, yet they tunnel to arbitrary IPv4
        # hosts (including private ones) through a gateway.
        if not ip.is_global or ip in _NAT64 or ip in _SIXTOFOUR:
            raise UnsafeURLError(NOT_PUBLIC)
        if pinned is None:
            pinned = raw_ip
    return pinned
