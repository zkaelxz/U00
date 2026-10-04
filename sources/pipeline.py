"""
sources/pipeline.py -- handing fetched content to the rest of the app.

The whole point of the adapter interface is that nothing downstream
needs to know where a page came from:

  * Comic pages are written exactly the way Scanlate's own "Upload page
    image(s)" does it -- `<drama>/pages/page_NNNN.<ext>` plus a
    db.create_page() row -- so OCR/translate/typeset treat them the same
    as a manual upload.
  * Novel text goes into the same `raw_novel_context.txt` Workspace's
    raw-novel upload writes, through the same core.load_novel_text_for_context()
    a pasted/uploaded .txt goes through.
  * Multi-chapter imports run as a background job: selected chapters only,
    one paced request at a time, live "Chapter 3 / 10 -- waiting before
    next request..." progress, Cancel honoured between requests.
"""

import contextlib
import io
import os
import re
import threading
import time

import background_jobs
import db

from . import ladder, registry, store
from .cache import RawCache
from .http import Cancelled
from .models import (ChallengeDetected, ChapterInfo, FailureReason, SourceError,
                     TermsProhibited)

# Scanlate's uploader accepts these as-is; anything else (webp, avif,
# gif...) is converted to PNG first so downstream code sees the same
# formats it always has.
_NATIVE_EXTS = {".png", ".jpg", ".jpeg"}

RAW_NOVEL_FILENAME = "raw_novel_context.txt"

_PAGE_FILE = re.compile(r"^page_(\d+)\.[A-Za-z0-9]+$")
_page_locks = {}
_page_locks_guard = threading.Lock()


def _page_lock(drama_id: int) -> threading.Lock:
    with _page_locks_guard:
        return _page_locks.setdefault(int(drama_id), threading.Lock())


def _next_page_index(drama_id: int, pages_dir: str) -> int:
    """MAX(idx)+1 over every page row and every page_NNNN.* file already
    on disk (never the row count: a deleted page leaves a gap). Called
    under the per-drama page lock."""
    pages = db.list_pages(drama_id)
    idx = 0
    for p in pages:
        if isinstance(p.get("idx"), int):
            idx = max(idx, p["idx"] + 1)
    for name in os.listdir(pages_dir):
        m = _PAGE_FILE.match(name)
        if m:
            idx = max(idx, int(m.group(1)) + 1)
    return idx


def _claim_page_index(pages_dir: str, idx: int):
    """Claims page index `idx` whatever the image's extension (security
    review L-2): an exclusive `page_NNNN.claim` file, then no page_NNNN.*
    file may exist already (a page another writer finished). Returns the
    claim's path (the caller removes it once the file and row exist), or
    None when the index is taken."""
    claim = os.path.join(pages_dir, f"page_{idx:04d}.claim")
    try:
        os.close(os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return None
    prefix = f"page_{idx:04d}."
    if any(n.startswith(prefix) and n != os.path.basename(claim)
           for n in os.listdir(pages_dir)):
        os.remove(claim)
        return None
    return claim


def add_page_images(drama_id: int, images, ids_out: list = None) -> int:
    """`images`: iterable of (bytes, ext). Returns how many pages were
    added (and appends each new page's id to `ids_out` when given). Same
    files and rows as Scanlate's own upload path.

    Safe against a second writer (security review MED-2): a per-drama lock
    covers the index computation and the writes in this process, and each
    index is claimed across processes whatever the extension
    (_claim_page_index; another process may be writing page_0005.jpg while
    this one writes page_0005.png), moving to the next index if it is
    taken; the file itself is still created exclusively ("xb"), so an
    existing page is never overwritten and no index gets two rows."""
    from PIL import Image
    pages_dir = os.path.join(db.drama_dir(drama_id), "pages")
    os.makedirs(pages_dir, exist_ok=True)
    added = 0
    with _page_lock(drama_id):
        idx = _next_page_index(drama_id, pages_dir)
        for content, ext in images:
            ext = (ext or "").lower()
            if not ext.startswith("."):
                ext = "." + ext if ext else ".png"
            if ext not in _NATIVE_EXTS:
                with Image.open(io.BytesIO(content)) as im:
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, "PNG")
                    content, ext = buf.getvalue(), ".png"
            while True:
                claim = _claim_page_index(pages_dir, idx)
                if claim is None:
                    idx += 1
                    continue
                try:
                    fname = f"page_{idx:04d}{ext}"
                    fpath = os.path.join(pages_dir, fname)
                    try:
                        with open(fpath, "xb") as out:
                            out.write(content)
                    except FileExistsError:  # a writer that doesn't claim (Scanlate upload)
                        idx += 1
                        continue
                    try:
                        with Image.open(fpath) as im:
                            w, h = im.size
                        pid = db.create_page(drama_id, idx, os.path.join("pages", fname), w, h)
                    except BaseException:
                        os.remove(fpath)  # no page file without its row
                        raise
                    if ids_out is not None:
                        ids_out.append(pid)
                finally:
                    os.remove(claim)
                break
            idx += 1
            added += 1
    return added


def _discard_pages(drama_id: int, page_ids):
    """Removes pages this import just added (rows, then files), so a
    chapter that could not be committed leaves nothing behind."""
    rows = [p for p in (db.get_page(pid, drama_id) for pid in page_ids) if p]
    with contextlib.closing(db.get_conn()) as conn:
        conn.executemany("DELETE FROM pages WHERE id = ? AND drama_id = ?",
                         [(p["id"], int(drama_id)) for p in rows])
        conn.commit()
    for p in rows:
        path = os.path.join(db.drama_dir(drama_id), p["filename"])
        if os.path.exists(path):
            os.remove(path)


_fsync = os.fsync   # module-level so a test can fail this call alone


def _as_written(text: str) -> bytes:
    """The bytes a text-mode write of `text` puts on disk."""
    return text.replace("\n", os.linesep).encode("utf-8")


def save_novel_text(drama_id: int, text: str, append: bool = False, heading: str = "") -> str:
    """Writes fetched novel text into the drama's raw-novel file, through
    the same loader an uploaded .txt goes through. Returns the path."""
    import core
    loaded = core.load_novel_text_for_context(text.encode("utf-8"), "imported.txt")
    if heading:
        loaded = f"{heading}\n\n{loaded}"
    path = os.path.join(db.drama_dir(drama_id), RAW_NOVEL_FILENAME)
    mode = "a" if append and os.path.exists(path) else "w"
    with open(path, mode, encoding="utf-8") as f:
        if mode == "a":
            f.write("\n\n")
        f.write(loaded)
    return path


# ---------------------------------------------------------------------------
# Multi-chapter import job
# ---------------------------------------------------------------------------

IMPORT_JOB_PREFIX = "sourceimport_"


def import_job_id(drama_id: int) -> str:
    """One import job per drama, the same id the API's import routes use
    (background_jobs.DRAMA_JOB_PREFIXES has the prefix), so the
    chapter-check auto-import and the API never write one drama at once."""
    return f"{IMPORT_JOB_PREFIX}{int(drama_id)}"


# Fixed texts (no exception detail: it may hold a path or a URL).
_IN_FLIGHT_TEXT = ("Interrupted while saving this chapter; retry it, then check the novel "
                   "text if other chapters were imported since.")
_IN_FLIGHT_PAGES = ("Interrupted while saving this chapter; some of its pages may be in the "
                    "drama -- check the drama before retrying it.")
_NO_BOOKKEEPING = "Could not update the import records; nothing was saved for this chapter."
_NOT_RECORDED = "The chapter could not be recorded as imported; retry it."
_NOT_SAVED = "Could not save the chapter text; retry it."
_ROLLED_BACK = "Could not save this chapter's pages; none were kept. Retry it."
NEEDS_AI_TEXT = ("The page loaded but this source's layout has changed. "
                 "Needs AI help: confirm.")


def _warn(message: str):
    import applog
    applog.get_logger().warning(message, exc_info=True)


def _mark_in_flight(source: str, ch, drama_id: int):
    """Written before any of a comic chapter's pages: pages a crash leaves
    behind can't be told apart, so the chapter is "partial" (shown, never
    retried automatically) until it is recorded or its pages are removed."""
    from translate_engines import redact_secrets
    store.record_import_retry(
        source, ch.series_id, drama_id,
        [(ch.chapter_id, redact_secrets(ch.title or ""), "partial", _IN_FLIGHT_PAGES)])


def _mark_retryable(source: str, ch, drama_id: int, error: str):
    """Best effort: stores why a chapter failed, as retryable (a comic one
    only once its pages were removed; a text one keeps its text_offset).
    If this fails, the in-flight marker stays."""
    from translate_engines import redact_secrets
    try:
        store.record_import_retry(
            source, ch.series_id, drama_id,
            [(ch.chapter_id, redact_secrets(ch.title or ""), "failed", error)])
    except Exception:
        _warn("Could not mark a chapter retryable")


def _clear_in_flight(source: str, ch, drama_id: int):
    """Best effort: import_retry_rows already hides a recorded chapter."""
    try:
        store.record_import_retry(source, ch.series_id, drama_id, [],
                                  done_ids=[ch.chapter_id])
    except Exception:
        _warn("Could not clear an import retry marker")


def _record_imported(source: str, ch, drama_id: int) -> bool:
    """False (logged) when the record could not be written; the caller
    must then not report the chapter as imported."""
    try:
        store.record_imported(source, ch.series_id, ch.chapter_id, drama_id)
        return True
    except Exception:
        _warn("Could not record an imported chapter")
        return False


def _append_chapter_text(source: str, ch, drama_id: int, text: str) -> str:
    """Appends one chapter the way save_novel_text(append=True,
    heading=title) does, writing only the new block. The file's length
    before the append (-1: no file) goes into the chapter's retry marker
    first. A later attempt at a chapter with a marker looks at that offset:
    its whole block is not written again, a torn prefix of it (a crash
    mid-write) is cut off and written again. A chapter without a marker
    always appends, even when another chapter's identical text is there.
    Returns "" once the text is in the file, else the error to report
    (none of the chapter's text left in the file). Raises only if a failed
    write could not be cut off again, so nothing is appended after it."""
    import core
    from translate_engines import redact_secrets
    loaded = core.load_novel_text_for_context(text.encode("utf-8"), "imported.txt")
    if ch.title:
        loaded = f"{ch.title}\n\n{loaded}"
    path = os.path.join(db.drama_dir(drama_id), RAW_NOVEL_FILENAME)
    key = (source, ch.series_id, drama_id, ch.chapter_id)

    def payload(offset):
        return (_as_written("\n\n") if offset >= 0 else b"") + _as_written(loaded)

    with _page_lock(drama_id):
        try:
            earlier = store.import_text_offset(*key)
        except Exception:
            _warn("Could not read a chapter's retry marker")
            return _NO_BOOKKEEPING
        try:
            size = os.path.getsize(path) if os.path.exists(path) else -1
            offset = size
            if earlier is not None and size >= max(earlier, 0):
                want = payload(earlier)
                with open(path, "rb") as f:
                    f.seek(max(earlier, 0))
                    tail = f.read(len(want) + 1)
                if tail[:len(want)] == want:
                    return ""   # the interrupted attempt wrote all of it
                if len(tail) < len(want) and want.startswith(tail):
                    os.truncate(path, max(earlier, 0))
                    offset = earlier
        except OSError:
            _warn("Could not check a chapter's text")
            return _NOT_SAVED
        if offset != earlier:
            try:
                store.mark_text_in_flight(*key, redact_secrets(ch.title or ""),
                                          _IN_FLIGHT_TEXT, offset)
            except Exception:
                _warn("Could not mark a chapter in flight")
                return _NO_BOOKKEEPING
        try:
            with open(path, "ab") as f:
                f.write(payload(offset))
                f.flush()
                _fsync(f.fileno())
        except OSError:
            _warn("Could not save a chapter's text")
            if offset < 0:
                if os.path.exists(path):
                    os.remove(path)
            else:
                os.truncate(path, offset)
            return _NOT_SAVED
    return ""


def append_recovered_chapter(source: str, ch, drama_id: int, text: str) -> str:
    """Writes a chapter the adapter could not read (text an AI-assisted
    extraction recovered, confirmed by the person) the way run_import_job
    writes one: appended, recorded as imported, then its retry marker
    cleared. Returns "" on success, else the error (the chapter is marked
    retryable)."""
    error = _append_chapter_text(source, ch, drama_id, text)
    if not error and not _record_imported(source, ch, drama_id):
        error = _NOT_RECORDED
    if error:
        _mark_retryable(source, ch, drama_id, error)
        return error
    _clear_in_flight(source, ch, drama_id)
    return ""


def run_import_job(job_id: str, source: str, chapters, drama_id: int, adapter=None,
                   skip_ids=None, on_layout_changed=None):
    """Background-job body. `chapters` are ChapterInfo (or their dicts) --
    only the ones the person ticked. Stores a result dict with per-chapter
    outcomes, the final Source Access stats, and a hand-off record if a
    verification page stopped it.

    `skip_ids`: chapter ids (matched by id, never by position) not to fetch
    -- e.g. already imported into this drama; each gets a
    {"skipped": True} outcome. Every chapter imported is recorded in
    store.imported_chapters.

    A text chapter whose page loaded but no longer matches the adapter
    (LAYOUT_CHANGED) is recorded as "needs_ai" and ends the run: the rest
    are not attempted. `on_layout_changed(ch, url, html)` is told the page
    the adapter read (both None when it is not known). This job never
    holds an engine."""
    if db.get_drama(drama_id) is None:
        raise SourceError("The drama to import into no longer exists.")
    skip_ids = {str(i) for i in (skip_ids or ())}
    chapters = [c if isinstance(c, ChapterInfo) else ChapterInfo(**c) for c in chapters]
    total = len(chapters)
    cache = RawCache()
    state = {"chapter": 0, "page": 0, "pages": 0, "stats": {}}
    results = []

    def publish(stats=None):
        if stats is not None:
            state["stats"] = stats
        s = state["stats"]
        where = f"Chapter {state['chapter']} / {total}"
        if state["pages"]:
            where += f" -- page {state['page']} / {state['pages']}"
        action = s.get("current_action", "")
        frac = ((state["chapter"] - 1) + (state["page"] / state["pages"] if state["pages"] else 0)) \
            / total if total and state["chapter"] else 0.0
        background_jobs.update_progress(job_id, max(0.0, min(frac, 0.999)),
                                        f"{where} -- {action}" if action else where)
        background_jobs.set_result(job_id, {"stats": dict(s), "partial": True,
                                            "chapters": results})

    if adapter is None:
        adapter = registry.get_adapter(
            source, cache=cache, status_cb=publish,
            cancel_check=lambda: background_jobs.is_cancel_requested(job_id))
    else:
        adapter.client.cache = cache
        adapter.client.status_cb = publish
        adapter.client.cancel_check = lambda: background_jobs.is_cancel_requested(job_id)

    from translate_engines import redact_secrets
    handoff = None
    cancelled = False
    try:
        for i, ch in enumerate(chapters, start=1):
            state.update(chapter=i, page=0, pages=0)
            publish(adapter.client.snapshot())
            if str(ch.chapter_id) in skip_ids:
                results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                "ok": True, "skipped": True})
                continue
            try:
                ladder.check_terms(source, adapter.capabilities())
                if adapter.supports("get_pages"):
                    pages = adapter.get_pages(ch)
                    state["pages"] = len(pages)
                    images = []
                    for j, page in enumerate(pages, start=1):
                        state["page"] = j
                        publish(adapter.client.snapshot())
                        images.append(adapter.download_page(page))
                    try:
                        _mark_in_flight(source, ch, drama_id)
                    except Exception:
                        _warn("Could not mark a chapter in flight")
                        results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                        "ok": False, "error": _NO_BOOKKEEPING})
                        continue
                    page_ids = []
                    try:
                        outcome = {"pages": add_page_images(drama_id, images, ids_out=page_ids)}
                    except Exception:
                        _warn("Could not add a chapter's pages")
                        # If this raises, pages may remain: the chapter stays "partial".
                        _discard_pages(drama_id, page_ids)
                        _mark_retryable(source, ch, drama_id, _ROLLED_BACK)
                        results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                        "ok": False, "error": _ROLLED_BACK})
                        break
                    except BaseException:
                        try:
                            _discard_pages(drama_id, page_ids)
                        except Exception:
                            _warn("Could not remove a failed chapter's pages")
                        raise
                    recorded = _record_imported(source, ch, drama_id)
                    if not recorded:
                        # Unrecorded pages would be imported again by a retry.
                        _discard_pages(drama_id, page_ids)
                else:
                    adapter.client.last_page = None
                    try:
                        text = adapter.get_chapter_text(ch)
                    except SourceError as e:
                        if e.reason != FailureReason.LAYOUT_CHANGED:
                            raise
                        try:
                            store.record_import_retry(
                                source, ch.series_id, drama_id,
                                [(ch.chapter_id, redact_secrets(ch.title or ""), "needs_ai",
                                  NEEDS_AI_TEXT)])
                        except Exception:
                            _warn("Could not record a chapter that needs AI help")
                        results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                        "ok": False, "needs_ai": True, "error": NEEDS_AI_TEXT})
                        if on_layout_changed is not None:
                            url, html = adapter.client.last_page or (None, None)
                            on_layout_changed(ch, url, html)
                        break
                    error = _append_chapter_text(source, ch, drama_id, text)
                    if error:
                        _mark_retryable(source, ch, drama_id, error)
                        results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                        "ok": False, "error": error})
                        continue
                    outcome = {"chars": len(text)}
                    recorded = _record_imported(source, ch, drama_id)
                if not recorded:
                    _mark_retryable(source, ch, drama_id, _NOT_RECORDED)
                    results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                    "ok": False, "error": _NOT_RECORDED})
                    continue
                _clear_in_flight(source, ch, drama_id)
                results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                "ok": True, **outcome})
            except ChallengeDetected as e:
                handoff = {"url": e.url, "reason": e.reason.value, "chapter": ch.title,
                           "chapter_id": ch.chapter_id}
                results.append({"chapter_id": ch.chapter_id, "title": ch.title, "ok": False,
                                "error": str(e)})
                break
            except TermsProhibited as e:
                results.append({"chapter_id": ch.chapter_id, "title": ch.title, "ok": False,
                                "error": f"{e.reason.value}: {e}"})
                break
            except SourceError as e:
                results.append({"chapter_id": ch.chapter_id, "title": ch.title, "ok": False,
                                "error": f"{e.reason.value}: {e}"})
    except Cancelled:
        cancelled = True
    finally:
        cache.release()
        try:
            cache.enforce_ceiling()
        except Exception:   # a cache trim must never lose the import's result
            import applog
            applog.get_logger().warning("Could not trim the source cache", exc_info=True)
        background_jobs.set_result(job_id, {"stats": adapter.client.snapshot(),
                                            "chapters": results, "handoff": handoff,
                                            "cancelled": cancelled, "partial": False,
                                            "finished_at": time.time()})


def start_import(source: str, series_id: str, chapters, drama_id: int, skip_ids=None) -> bool:
    """Claims `sourceimport_<drama_id>`. False, starting nothing, while any
    job for the drama runs here or (per job_records) in the other process.
    Chapters already imported into the drama are skipped unless `skip_ids`
    is given."""
    from services import drama_service
    chapters = list(chapters)
    if db.get_drama(drama_id) is None:
        return False
    if drama_service.job_running_for_drama(drama_id):
        return False
    if skip_ids is None:
        skip_ids = store.imported_chapter_ids(source, series_id, drama_id)
    job_id = import_job_id(drama_id)
    return background_jobs.start_job(
        job_id, run_import_job, job_id, source, chapters, drama_id, skip_ids=skip_ids,
        description=f"Import {len(chapters)} chapter(s) from {source}")
