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
from .models import ChallengeDetected, ChapterInfo, SourceError, TermsProhibited

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
                    with Image.open(fpath) as im:
                        w, h = im.size
                    pid = db.create_page(drama_id, idx, os.path.join("pages", fname), w, h)
                    if ids_out is not None:
                        ids_out.append(pid)
                finally:
                    os.remove(claim)
                break
            idx += 1
            added += 1
    return added


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
# Multi-chapter import job (Step 23 item 13)
# ---------------------------------------------------------------------------

IMPORT_JOB_PREFIX = "sourceimport_"


def import_job_id(drama_id: int) -> str:
    """One import job per drama, the same id the API's import routes use
    (background_jobs.DRAMA_JOB_PREFIXES has the prefix), so Streamlit, the
    chapter-check auto-import and the API never write one drama at once."""
    return f"{IMPORT_JOB_PREFIX}{int(drama_id)}"


def _record_imported(source: str, ch, drama_id: int):
    """Best effort: a failed bookkeeping write never fails the import."""
    try:
        store.record_imported(source, ch.series_id, ch.chapter_id, drama_id)
    except Exception:
        import applog
        applog.get_logger().warning("Could not record an imported chapter", exc_info=True)


def run_import_job(job_id: str, source: str, chapters, drama_id: int, adapter=None,
                   skip_ids=None):
    """Background-job body. `chapters` are ChapterInfo (or their dicts) --
    only the ones the person ticked. Stores a result dict with per-chapter
    outcomes, the final Source Access stats, and a hand-off record if a
    verification page stopped it.

    `skip_ids`: chapter ids (matched by id, never by position) not to fetch
    -- e.g. already imported into this drama; each gets a
    {"skipped": True} outcome. Every chapter imported is recorded in
    store.imported_chapters."""
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
                    added = add_page_images(drama_id, images)
                    _record_imported(source, ch, drama_id)
                    results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                    "ok": True, "pages": added})
                else:
                    text = adapter.get_chapter_text(ch)
                    save_novel_text(drama_id, text, append=True, heading=ch.title)
                    _record_imported(source, ch, drama_id)
                    results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                    "ok": True, "chars": len(text)})
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
    if drama_service.job_running_for_drama(drama_id):
        return False
    if skip_ids is None:
        skip_ids = store.imported_chapter_ids(source, series_id, drama_id)
    job_id = import_job_id(drama_id)
    return background_jobs.start_job(
        job_id, run_import_job, job_id, source, chapters, drama_id, skip_ids=skip_ids,
        description=f"Import {len(chapters)} chapter(s) from {source}")
