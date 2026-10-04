"""
services/capped_body.py -- the one byte-capped, time-capped read of a
streamed HTTP response body.

`read_capped` refuses a declared Content-Length over the cap before reading,
reads in chunks, stops at the cap and at a total monotonic deadline (the
per-read timeout alone restarts on every chunk a slow server drips), and
always closes the response. The caller supplies `make_error`, so each caller
keeps its own user-facing message and error class; this module never puts a
URL, header or other response detail in an error. Standard library only.
"""
import time
from typing import Callable

DEFAULT_CHUNK = 64 * 1024


def declared_length(resp):
    """The Content-Length header as an int, or None when absent or malformed."""
    raw = (getattr(resp, "headers", None) or {}).get("Content-Length") or ""
    return int(raw) if raw.isdigit() else None


def read_capped(resp, cap_bytes: int, deadline_seconds: float,
                make_error: Callable[[], Exception],
                chunk_size: int = DEFAULT_CHUNK, clock=None) -> bytes:
    """The whole body, or `make_error()` raised when it is over `cap_bytes`
    (declared or actual) or takes longer than `deadline_seconds` in all."""
    try:
        declared = declared_length(resp)
        if declared is not None and declared > cap_bytes:
            raise make_error()
        clock = clock or time.monotonic
        started = clock()
        body = bytearray()
        for chunk in resp.iter_content(chunk_size):
            body.extend(chunk)
            if len(body) > cap_bytes or clock() - started > deadline_seconds:
                raise make_error()
        return bytes(body)
    finally:
        resp.close()
