"""
lib/url_guard.py -- the one public-address check for server-side
fetches of a user- or site-supplied URL.

`resolve_public(url)` accepts http(s) URLs only (no userinfo) whose host
resolves, and whose EVERY resolved address is global: private, loopback,
link-local, reserved, multicast and v4-mapped non-global addresses, and the
home's own public addresses (`set_home_addresses`), are refused before any
connection. It returns the first validated address so
the caller can pin its connection to it.

Callers: `lib.http` (and through it `services.safe_fetch`,
`services.metadata_service`), `services.metadata_service.check_public_url`, `sources.http_transport._requests_transport` and
`page_fetch.fetch_static`, which re-validate every redirect hop, and
`services.egress_proxy`, which checks every connection ffmpeg and yt-dlp
make during live capture. Error
messages are fixed strings with no URL, host or IP in them.

`cross_origin_headers` and `referer_origin` limit what a request tells
another host about the page it came from.

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
_SITE_LOCAL = ipaddress.ip_network("fec0::/10")


# This home's own public addresses (set_home_addresses). A router that serves
# its admin page to hairpin traffic would hand that page to a fetch of the
# home's WAN address, so these are refused like private ones.
_home_networks: tuple = ()


def set_home_addresses(addresses) -> None:
    """Replaces the home's own public addresses. An IPv6 address stands for
    its /64, which is the home network, so other devices there are refused
    too. Non-global addresses are ignored: they are refused anyway."""
    global _home_networks
    networks = set()
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_global:
            networks.add(ipaddress.ip_network(f"{ip}/{64 if ip.version == 6 else 32}",
                                              strict=False))
    _home_networks = tuple(networks)


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
        raw_ip = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            raise UnsafeURLError(NOT_PUBLIC) from None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        # Python reports both tunnel prefixes as global, yet they reach
        # arbitrary IPv4 hosts (including private ones) through a gateway; it
        # also reports most multicast ranges, the deprecated IPv4-compatible
        # ::/96 (inside reserved ::/8, so ::7f00:1 names 127.0.0.1 on a stack
        # that still maps it) and deprecated site-local fec0::/10 as global.
        if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip in _NAT64
                or ip in _SIXTOFOUR or ip in _SITE_LOCAL
                or any(ip in net for net in _home_networks)):
            raise UnsafeURLError(NOT_PUBLIC)
        if pinned is None:
            pinned = raw_ip
    if pinned is None:
        raise URLResolveError(RESOLVE_FAILED)
    return pinned


def referer_origin(url: str) -> str:
    """`url` cut to scheme, host and port (no credentials, path, query or
    fragment), or "" when it has no http(s) host."""
    parts = urlsplit(url or "")
    try:
        port = parts.port
    except ValueError:                              # a malformed port
        port = None
    host = parts.hostname or ""
    if not host or parts.scheme not in ("http", "https"):
        return ""
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}{f':{port}' if port else ''}/"


def cross_origin_headers(headers: dict) -> dict:
    """`headers` for a redirect to another origin: no Authorization or
    Cookie, and a Referer cut to its origin (dropped when it has none). The
    Referer was chosen for the first URL's host -- often a whole chapter
    URL whose query carries a token -- and a same-site link can redirect to
    a third party."""
    out = {}
    for k, v in headers.items():
        low = k.lower()
        if low in ("authorization", "cookie"):
            continue
        if low == "referer":
            v = referer_origin(v)
            if not v:
                continue
        out[k] = v
    return out
