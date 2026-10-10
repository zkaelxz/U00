"""
lib/capped_body.py -- the one byte-capped, time-capped read of a
streamed HTTP response body.

`read_capped` refuses a declared Content-Length over the cap before reading,
reads in chunks, stops at the cap and at a total monotonic deadline (the
per-read timeout alone restarts on every chunk a slow server drips), and
always closes the response. The deadline is also enforced by a timer that
shuts the connection, because a read blocked inside one chunk never gets
back to the check between chunks. The caller supplies `make_error`, so each caller
keeps its own user-facing message and error class; this module never puts a
URL, header or other response detail in an error. Standard library only.
"""
import threading
import time
from typing import Callable

DEFAULT_CHUNK = 64 * 1024


def declared_length(resp):
    """The Content-Length header as an int, or None when absent or malformed."""
    raw = ((getattr(resp, "headers", None) or {}).get("Content-Length") or "").strip()
    # isascii: str.isdigit() also accepts characters like "\u00b2" that int() rejects.
    return int(raw) if raw.isascii() and raw.isdigit() else None


def _interrupt(resp, fired: threading.Event) -> None:
    fired.set()
    # close() alone doesn't wake a thread blocked in recv() on a requests
    # (urllib3) body; urllib3's shutdown() does. httpx's close() is enough.
    try:
        shutdown = getattr(getattr(resp, "raw", None), "shutdown", None)
        if callable(shutdown):
            shutdown()
        resp.close()
    except Exception:
        pass


def read_capped(resp, cap_bytes: int, deadline_seconds: float,
                make_error: Callable[[], Exception],
                chunk_size: int = DEFAULT_CHUNK, clock=None,
                make_deadline_error: Callable[[], Exception] = None) -> bytes:
    """The whole body, or `make_error()` raised when it is over `cap_bytes`
    (declared or actual); `make_deadline_error()` (default: `make_error`) when
    it takes longer than `deadline_seconds` in all, so a slow link can be told
    apart from an oversized body."""
    deadline_error = make_deadline_error or make_error
    fired = threading.Event()
    timer = None
    try:
        declared = declared_length(resp)
        if declared is not None and declared > cap_bytes:
            raise make_error()
        clock = clock or time.monotonic
        started = clock()
        if deadline_seconds < threading.TIMEOUT_MAX:
            timer = threading.Timer(max(deadline_seconds, 0), _interrupt, (resp, fired))
            timer.daemon = True
            timer.start()
        body = bytearray()
        try:
            # requests names it iter_content, httpx iter_bytes; both yield decoded bytes.
            chunks = (resp.iter_content(chunk_size) if hasattr(resp, "iter_content")
                      else resp.iter_bytes(chunk_size))
            for chunk in chunks:
                body.extend(chunk)
                if len(body) > cap_bytes:
                    raise make_error()
                if clock() - started > deadline_seconds:
                    raise deadline_error()
        except Exception:
            if fired.is_set():
                raise deadline_error() from None
            raise
        # A shut connection can also end the body early without an error.
        if fired.is_set():
            raise deadline_error()
        return bytes(body)
    finally:
        if timer is not None:
            timer.cancel()
        resp.close()
