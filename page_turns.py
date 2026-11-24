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
  Quick read (nothing saved) skips the lock and reads whatever summary the
  title has when its page starts, trading some continuity for speed.
- A newly added page is discarded only while the pipeline lock is still
  held (`discard_on_failure`); after release another request may already
  have answered with it, so a later failure keeps it for a re-capture.

Within one request the pages overlap (`PageOrder`): the next page is read
while one translates, but each still reads after the page before it read
and translates after it translated, so the library's page order and each
page's previous-page context are what a one-at-a-time loop gave.
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
# Quick read's names per title ({source: English}), so pages translated side by side, which
# cannot wait for each other's summary, still spell a character one way.
_chapter_names = {}
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
        _chapter_names.pop(key, None)


def translate_in_chain(drama_id, source_url, bubbles, engine, drama, glossary, usage_cb,
                       wait_for_turn: bool = True, **extras):
    """Translates one page as the next link of its title's context chain.
    Other titles never wait on this title's lock. `extras` (character_notes,
    page_image) go to `translate_page_bubbles` as given.

    `wait_for_turn=False` (Quick read, which keeps nothing) skips the title's
    lock: the page reads the summary of whichever page finished last and
    leaves its own, so pages translate side by side at the cost of a looser
    story link between pages in flight together."""
    from scanlate import scanlate_translate
    key = _chain_key(drama_id, source_url)
    if key is not None and not wait_for_turn:
        with _context_lock:
            previous = _contexts.get(key, "")
            names = dict(_chapter_names.get(key, {}))
        try:
            new_context = scanlate_translate.translate_page_bubbles(
                bubbles, engine, drama or {}, previous_context=previous,
                glossary_terms=glossary, usage_cb=usage_cb, chapter_names=names, **extras)
        finally:
            # Names from an answer that applied only in part are still the model's spelling.
            with _context_lock:
                if names:
                    kept = _chapter_names.setdefault(key, {})
                    for source, english in names.items():
                        kept.setdefault(source, english)
                    while len(_chapter_names) > MAX_CHAINS:
                        _chapter_names.pop(next(iter(_chapter_names)))
        with _context_lock:
            _contexts[key] = new_context
            _contexts.move_to_end(key)
            # This path makes no lock, so the lock count that bounds the chains never sees it.
            while len(_contexts) > MAX_CHAINS:
                evicted, _ = _contexts.popitem(last=False)
                _chapter_names.pop(evicted, None)
        return
    if key is None:
        scanlate_translate.translate_page_bubbles(
            bubbles, engine, drama or {}, previous_context="",
            glossary_terms=glossary, usage_cb=usage_cb, **extras)
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
            new_context = scanlate_translate.translate_page_bubbles(
                bubbles, engine, drama or {}, previous_context=previous,
                glossary_terms=glossary, usage_cb=usage_cb, **extras)
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


# One page's worth of the page before it is context, not a second page to read.
PREVIOUS_LINES_MAX = 12
PREVIOUS_LINE_CHARS = 160
PREVIOUS_LINES_WAIT_SECONDS = 5.0


class Stopped(Exception):
    """The request stopped (cancelled, or a page failed in a way every later one would)
    before this page's turn to read came."""


class _Unordered:
    """The turn of a page that waits for no other page."""

    def keep(self, bubbles):
        pass

    def previous_lines(self):
        return []

    def reading(self):
        return contextlib.nullcontext()

    def translating(self):
        return contextlib.nullcontext()


UNORDERED = _Unordered()


class PageOrder:
    """Turns for the pages of one request, by their index in it. `should_stop` is asked when
    a page's turn to read comes; True raises `Stopped` there, before anything is stored.
    `finish(index)` must run for every page, however it ended, or the next page waits for ever.
    `ordered_translation=False` lets a page translate without waiting for the one before it
    (Quick read); reading stays in page order either way."""

    def __init__(self, count: int, should_stop=lambda: False, ordered_translation: bool = True):
        self._read = [threading.Event() for _ in range(count)]
        self._translated = [threading.Event() for _ in range(count)]
        self._known = [threading.Event() for _ in range(count)]
        self._bubbles = [None] * count
        self._should_stop = should_stop
        self.ordered_translation = ordered_translation

    def turn(self, index: int) -> "_PageTurn":
        return _PageTurn(self, index)

    def finish(self, index: int) -> None:
        self._read[index].set()
        self._translated[index].set()
        self._known[index].set()


class _PageTurn:
    def __init__(self, order: PageOrder, index: int):
        self._order = order
        self._index = index

    def keep(self, bubbles) -> None:
        """Makes this page's bubbles, read but maybe not yet translated, visible to the next
        page. The list is shared, so the English that arrives later shows too."""
        self._order._bubbles[self._index] = bubbles
        self._order._known[self._index].set()

    def previous_lines(self) -> list:
        """[{"source", "english"}] for the page before this one in the request: what it said
        and, if its translation already came back, the English. Pages translate side by side,
        so the English is often not there yet and the source alone has to carry the story."""
        if not self._index:
            return []
        before = self._index - 1
        # A page that never reads or never translates still ends with finish(), which sets this.
        self._order._known[before].wait(PREVIOUS_LINES_WAIT_SECONDS)
        lines = []
        for b in list(self._order._bubbles[before] or ()):
            source = (b.get("source_text") or "").strip()
            if b.get("skip") or b.get("kind") == "sfx" or not source:
                continue
            lines.append({"source": source[:PREVIOUS_LINE_CHARS],
                          "english": (b.get("translated_text") or "").strip()[:PREVIOUS_LINE_CHARS]})
        return lines[:PREVIOUS_LINES_MAX]

    @contextlib.contextmanager
    def reading(self):
        if self._index:
            self._order._read[self._index - 1].wait()
        if self._order._should_stop():
            raise Stopped()
        yield
        # Not on failure: the caller records why first, then `finish` lets the next page check
        # `should_stop`, so a failure that stops the request is seen before the next read starts.
        self._order._read[self._index].set()

    @contextlib.contextmanager
    def translating(self):
        if self._index and self._order.ordered_translation:
            self._order._translated[self._index - 1].wait()
        try:
            yield
        finally:
            self._order._translated[self._index].set()
