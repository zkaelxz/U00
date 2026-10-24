"""
api/security_headers.py -- the headers each listener adds to every reply
(`api.auth.HouseholdGate` on the household listener, `api.auth.LoopbackOnlyGate`
on the PC listener). The reasoning is in docs/remote-access-listeners.md.
"""
from starlette.datastructures import MutableHeaders

# The Reader shows each page in a sandboxed srcdoc iframe, which inherits
# this policy and runs inline scripts and handlers, and plays audio and shows
# images from data: URIs; index.html has one inline theme script. So scripts
# can't be limited to 'self' until the Reader page is served from a URL.
# frame-src is only for the Live page's optional stream video (YouTube, Twitch).
HOUSEHOLD_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                 "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                 "media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
                 "object-src 'none'; base-uri 'self'; form-action 'self'; "
                 "frame-src https://www.youtube-nocookie.com https://player.twitch.tv; "
                 "frame-ancestors 'none'")
HOUSEHOLD_SECURITY_HEADERS = (
    ("Content-Security-Policy", HOUSEHOLD_CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)
HSTS_VALUE = "max-age=31536000"
# The PC listener gets only the anti-framing part: the household CSP's
# frame-src and script rules were never checked against the PC-only pages.
ADMIN_SECURITY_HEADERS = (
    ("Content-Security-Policy", "frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
)


def with_default_headers(send, extra):
    """`send` that adds each of `extra` to the response unless the route
    set that header itself (a stricter CSP is kept)."""
    async def send_with_headers(message):
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            for name, value in extra:
                headers.setdefault(name, value)
        await send(message)
    return send_with_headers
