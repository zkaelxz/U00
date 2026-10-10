"""Which request translates an extension-bridge page, and in what order.

`page_server` translates outside its `PIPELINE_LOCK` so a slow model never
stalls other titles, which makes a stored page visible to other requests
before it is translated. Three rules keep that safe:

- A request that will translate a stored page claims it first
  (`claim_page`, under the pipeline lock). A re-capture of a page still
  being translated waits for that translation (`await_other_capture`)
  rather than paying for a second one.
- Pages of one title translate one at a time (`translate_in_chain`), so
  each page's `previous_context` is the page finished last.
- A newly added page is discarded only while the pipeline lock is still
  held (`discard_on_failure`); after release another request may already
  have answered with it, so a later failure keeps it for a re-capture.
"""

import contextlib
import threading
import time

# Rolling per-drama translation context, so consecutive pages of the same
# book read as one conversation rather than N isolated pages -- the same
# `previous_context` the Scanlate run (services/scanlate_run_service.py)
# threads between pages. In-memory only, like `background_jobs`: a process
# restart simply starts the context fresh, which costs quality on one page
# and nothing else.
_context_lock = threading.Lock()
_contexts = {}
_chain_locks = {}

# Page id -> Event set when the request translating that page finishes.
_inflight_lock = threading.Lock()
_inflight = {}
# Bounded so a slow model makes a re-capture answer "still translating"
# rather than hold the extension's request open indefinitely.
INFLIGHT_WAIT_SECONDS = 30.0
STILL_TRANSLATING_NOTE = ["warning", "an earlier capture of this page is still being "
                                     "translated; capture it again in a moment"]


def translate_in_chain(drama_id, source_url, bubbles, engine, drama, glossary, usage_cb):
    """Translates one page as the next link of its title's context chain.
    Other titles never wait on this title's lock."""
    import scanlate
    key = str(drama_id) if drama_id else f"url:{source_url}"
    with _context_lock:
        chain = _chain_locks.setdefault(key, threading.Lock())
    with chain:
        with _context_lock:
            previous = _contexts.get(key, "")
        new_context = scanlate.translate_page_bubbles(
            bubbles, engine, drama or {}, previous_context=previous,
            glossary_terms=glossary, usage_cb=usage_cb)
        with _context_lock:
            _contexts[key] = new_context


def claim_page(page_id):
    """`(None, mine)` when this request now owns the page's translation,
    else `(theirs, None)` with the owner's Event."""
    with _inflight_lock:
        theirs = _inflight.get(page_id)
        if theirs is not None:
            return theirs, None
        mine = _inflight[page_id] = threading.Event()
        return None, mine


def release_page(page_id, mine):
    with _inflight_lock:
        if _inflight.get(page_id) is mine:
            del _inflight[page_id]
    mine.set()


def await_other_capture(page_id, theirs):
    """Waits for another request's translation of this page, then claims
    it. Returns the page's bubbles as saved by then and this request's
    claim, or no claim when the wait ran out."""
    import db
    deadline = time.monotonic() + INFLIGHT_WAIT_SECONDS
    mine = None
    while theirs is not None:
        if not theirs.wait(max(0.0, deadline - time.monotonic())):
            return db.load_bubbles(page_id), None
        theirs, mine = claim_page(page_id)
    return db.load_bubbles(page_id), mine


@contextlib.contextmanager
def discard_on_failure(drama_id):
    """Discards the page ids appended to the yielded list if the block
    fails: a page whose reading failed must not stay behind empty, since
    the caller reports it as not delivered and a retry would add it twice."""
    added = []
    try:
        yield added
    except BaseException:
        if added:
            from sources import pipeline
            try:
                pipeline._discard_pages(int(drama_id), added)
            except Exception:
                pass
        raise
