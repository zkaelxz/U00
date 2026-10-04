"""
api/llm_slots.py -- the shared concurrency cap for SYNCHRONOUS long work
in a request handler (an LLM call, ffmpeg): the Reader's LLM tools and rich
export (api/routers/reader_routes.py) and the Review blocked-line retry
(api/routers/blocked_retry_routes.py), POST /api/translate, the line-ai
improve/explain routes, Discover translate-query and Romanize credits.

Each such request holds a server worker thread for as long as the engine
takes, so at most LLM_MAX_IN_FLIGHT run at once server-wide (one pool shared
by every route that uses it) and at most one per caller (user id, or
"local" with auth off). A request over either cap gets 429 `rate_limited`
at once rather than queueing.
"""

import threading
from contextlib import contextmanager

from fastapi import Request

from services.service_errors import RateLimitedError

LLM_MAX_IN_FLIGHT = 2
SLOTS = threading.BoundedSemaphore(LLM_MAX_IN_FLIGHT)
ACTIVE_CALLERS = set()
ACTIVE_LOCK = threading.Lock()
DEFAULT_BUSY = "The AI tools are busy; try again in a moment."


def caller_key(request: Request) -> str:
    principal = getattr(request.state, "principal", None) or {}
    user_id = principal.get("user_id")
    return f"user:{user_id}" if user_id is not None else "local"


@contextmanager
def llm_slot(request: Request, busy_message: str = DEFAULT_BUSY):
    """Non-blocking: 429 when this caller already has one running or the
    server-wide cap is reached. Always released, even on an exception."""
    key = caller_key(request)
    with ACTIVE_LOCK:
        if key in ACTIVE_CALLERS:
            raise RateLimitedError(busy_message)
        if not SLOTS.acquire(blocking=False):
            raise RateLimitedError(busy_message)
        ACTIVE_CALLERS.add(key)
    try:
        yield
    finally:
        with ACTIVE_LOCK:
            ACTIVE_CALLERS.discard(key)
            SLOTS.release()
