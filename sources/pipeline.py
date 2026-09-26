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
import time

import background_jobs
import db

from . import ladder, registry
from .cache import RawCache
from .http import Cancelled
from .models import ChallengeDetected, ChapterInfo, SourceError, TermsProhibited

# Scanlate's uploader accepts these as-is; anything else (webp, avif,
# gif...) is converted to PNG first so downstream code sees the same
# formats it always has.
_NATIVE_EXTS = {".png", ".jpg", ".jpeg"}

RAW_NOVEL_FILENAME = "raw_novel_context.txt"


def add_page_images(drama_id: int, images) -> int:
    """`images`: iterable of (bytes, ext). Returns how many pages were
    added. Same files and rows as Scanlate's own upload path."""
    from PIL import Image
    pages_dir = os.path.join(db.drama_dir(drama_id), "pages")
    os.makedirs(pages_dir, exist_ok=True)
    next_idx = len(db.list_pages(drama_id))
    added = 0
    for content, ext in images:
        ext = (ext or "").lower()
        if not ext.startswith("."):
            ext = "." + ext if ext else ".png"
        if ext not in _NATIVE_EXTS:
            with Image.open(io.BytesIO(content)) as im:
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "PNG")
                content, ext = buf.getvalue(), ".png"
        fname = f"page_{next_idx + added:04d}{ext}"
        fpath = os.path.join(pages_dir, fname)
        with open(fpath, "wb") as out:
            out.write(content)
        with Image.open(fpath) as im:
            w, h = im.size
        db.create_page(drama_id, next_idx + added, os.path.join("pages", fname), w, h)
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

def import_job_id(source: str, series_id: str) -> str:
    return f"source_import_{source}_{series_id}"


def run_import_job(job_id: str, source: str, chapters, drama_id: int, adapter=None):
    """Background-job body. `chapters` are ChapterInfo (or their dicts) --
    only the ones the person ticked. Stores a result dict with per-chapter
    outcomes, the final Source Access stats, and a hand-off record if a
    verification page stopped it."""
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
                    results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                    "ok": True, "pages": added})
                else:
                    text = adapter.get_chapter_text(ch)
                    save_novel_text(drama_id, text, append=True, heading=ch.title)
                    results.append({"chapter_id": ch.chapter_id, "title": ch.title,
                                    "ok": True, "chars": len(text)})
            except ChallengeDetected as e:
                handoff = {"url": e.url, "reason": e.reason.value, "chapter": ch.title}
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


def start_import(source: str, series_id: str, chapters, drama_id: int) -> bool:
    chapters = list(chapters)
    job_id = import_job_id(source, series_id)
    return background_jobs.start_job(
        job_id, run_import_job, job_id, source, chapters, drama_id,
        description=f"Import {len(chapters)} chapter(s) from {source}")
