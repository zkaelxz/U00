"""
api/security_headers.py -- the headers each listener adds to every reply
(`api.auth.HouseholdGate` on the household listener, `api.auth.LoopbackOnlyGate`
on the PC listener). The reasoning is in docs/remote-access-listeners.md.
"""
from starlette.datastructures import MutableHeaders

# Scripts come only from the app's own files: index.html's before-first-paint
# theme read is /boot.js, and the Reader page, which runs inline scripts,
# is a document the browser loads from its own URL (READER_PAGE_HEADERS
# below), not a srcdoc frame, which would inherit this policy. Styles keep
# 'unsafe-inline' for React's style attributes and index.html's boot note.
# frame-src: the Reader's own page (same origin) and the Live page's
# optional stream video (YouTube, Twitch).
HOUSEHOLD_CSP = ("default-src 'self'; script-src 'self'; "
                 "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                 "media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
                 "object-src 'none'; base-uri 'self'; form-action 'self'; "
                 "frame-src 'self' https://www.youtube-nocookie.com https://player.twitch.tv; "
                 "frame-ancestors 'none'")
HOUSEHOLD_SECURITY_HEADERS = (
    ("Content-Security-Policy", HOUSEHOLD_CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)
HSTS_VALUE = "max-age=31536000"
# The Reader page (GET /api/reader/dramas/{id}/page.html) in its sandboxed
# iframe: self-contained, so it needs no network at all, only its own inline
# scripts and styles and data: images and audio. Framed by the app only.
READER_PAGE_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                   "img-src data:; media-src data: blob:; font-src data:; "
                   "frame-ancestors 'self'")
READER_PAGE_HEADERS = (
    ("Content-Security-Policy", READER_PAGE_CSP),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cache-Control", "no-store"),
)
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
