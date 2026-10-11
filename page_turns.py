"""Which request translates an extension-bridge page, and in what order.

`page_server` translates outside its `PIPELINE_LOCK` so a slow model never
stalls other titles, which makes a stored page visible to other requests
before it is translated. Three rules keep that safe:

- A request that will translate a stored page claims it first
  (`claim_page`, under the pipeline lock). A re-capture of a page still
  being translated waits for that translation (`await_other_capture`)
  rather than paying for a second one.
- Pages of one title translate one at a time (`translate_in_chain`), so
  each page's `previous_context` is the page finished last. That is the
  order requests win the title's lock, not page order: a lock is not
  first-come-first-served, and a page captured out of order reads the
  context of whichever page finished before it. Ordering by page index
  would leave a waiter stuck on a page that is never captured. A request
  that waits longer than `CHAIN_WAIT_SECONDS` translates with no context
  rather than hold its handler thread behind one slow model call, and a
  capture with neither a drama nor a page URL has no title to chain.
- A newly added page is discarded only while the pipeline lock is still
  held (`discard_on_failure`); after release another request may already
  have answered with it, so a later failure keeps it for a re-capture.
"""

import contextlib
import hashlib
import threading
import time
from collections import OrderedDict

# Rolling per-drama translation context, so consecutive pages of the same
# book read as one conversation rather than N isolated pages -- the same
# `previous_context` the Scanlate run (services/scanlate_run_service.py)
# threads between pages. In-memory only, like `background_jobs`: a process
# restart simply starts the context fresh, which costs quality on one page
# and nothing else.
_context_lock = threading.Lock()
_contexts = OrderedDict()
_chain_locks = OrderedDict()
# Requests holding or waiting on a title's lock, so eviction never hands a
# second lock to a title whose first is still in use.
_chain_users = {}
# Every title the extension ever sent keeps an entry; bounded so a long
# reading session (or a caller inventing URLs) cannot grow them forever.
MAX_CHAINS = 256
CHAIN_WAIT_SECONDS = 30.0

# Page id -> Event set when the request translating that page finishes.
_inflight_lock = threading.Lock()
_inflight = {}
# Bounded so a slow model makes a re-capture answer "still translating"
# rather than hold the extension's request open indefinitely.
INFLIGHT_WAIT_SECONDS = 30.0
STILL_TRANSLATING_NOTE = ["warning", "an earlier capture of this page is still being "
                                     "translated; capture it again in a moment"]


def _chain_key(drama_id, source_url):
    """None when there is no title to chain. The URL is client-supplied and
    kept for the process lifetime, so only a digest of it is held."""
    if drama_id:
        return str(drama_id)
    if not source_url:
        return None
    digest = hashlib.sha256(source_url.encode("utf-8", "surrogatepass")).hexdigest()
    return "url:" + digest[:32]


def _evict_idle_chains():
    for key in list(_chain_locks):
        if len(_chain_locks) <= MAX_CHAINS:
            break
        if _chain_users.get(key) or _chain_locks[key].locked():
            continue
        del _chain_locks[key]
        _contexts.pop(key, None)


def translate_in_chain(drama_id, source_url, bubbles, engine, drama, glossary, usage_cb):
    """Translates one page as the next link of its title's context chain.
    Other titles never wait on this title's lock."""
    import scanlate
    key = _chain_key(drama_id, source_url)
    if key is None:
        scanlate.translate_page_bubbles(
            bubbles, engine, drama or {}, previous_context="",
            glossary_terms=glossary, usage_cb=usage_cb)
        return
    with _context_lock:
        chain = _chain_locks.get(key)
        if chain is None:
            chain = _chain_locks[key] = threading.Lock()
        _chain_locks.move_to_end(key)
        _chain_users[key] = _chain_users.get(key, 0) + 1
        _evict_idle_chains()
    try:
        linked = chain.acquire(timeout=CHAIN_WAIT_SECONDS)
        try:
            with _context_lock:
                previous = _contexts.get(key, "") if linked else ""
            new_context = scanlate.translate_page_bubbles(
                bubbles, engine, drama or {}, previous_context=previous,
                glossary_terms=glossary, usage_cb=usage_cb)
            if linked:
                with _context_lock:
                    _contexts[key] = new_context
                    _contexts.move_to_end(key)
        finally:
            if linked:
                chain.release()
    finally:
        with _context_lock:
            _chain_users[key] -= 1
            if not _chain_users[key]:
                del _chain_users[key]


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
    try:
        return db.load_bubbles(page_id), mine
    except BaseException:
        # The caller never receives the claim, so only this frame can free it.
        if mine is not None:
            release_page(page_id, mine)
        raise


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
