"""
services/sources_save_service.py -- saving a comic source's chapters to a
folder on this PC as CBZ files, for reading elsewhere, without opening a
drama or Scanlate.

`start_chapter_save` takes chapter ids only, like a chapter import: the job
re-reads the series' chapter list (the adapter builds every URL) and keeps
the requested ids, matched by id. Each chapter becomes one file:

    <data dir>/saved_comics/<source>/<series title>/<NNNN> <chapter title>.cbz

NNNN is the chapter's place in the series' reading order, so files sort in
reading order. Names come from the site, so they are reduced to safe file
names (no separators, reserved characters or Windows device names) and the
final path is checked to stay inside the save folder. A chapter whose file
already exists is skipped, so saving again only adds what's missing. A file
is written to a `.part` beside it and renamed into place only when complete,
so a cancelled or failed chapter never leaves a half CBZ behind.

The CBZ holds the pages in order (`001.webp`, ...) stored uncompressed, plus
a ComicInfo.xml (series, chapter title, number, language) that comic readers
use. One save runs at a time in this process (`sources_save`, 409
otherwise). Results carry outcomes and counts only, never paths.
"""

import os
import re
import zipfile
from xml.sax.saxutils import escape

import background_jobs
import portable
from services.service_errors import UnsupportedOperationError
from services.sources_import_service import _chapter_ids
from services.sources_registry_service import _scrub, safe_url
from services.sources_search_service import (SAVE_JOB_ID, _enabled_source, _error_view,
                                             _JobFailed, _series_id, _start)
from sources import chapter_order, ladder, registry
from sources.http import Cancelled
from sources.models import ChallengeDetected, SourceError, TermsProhibited

SAVE_DIRNAME = "saved_comics"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
MAX_NAME = 120
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}
_NOT_ATTEMPTED = "Not attempted: the save stopped before this chapter."


def save_root() -> str:
    return os.path.join(portable.data_dir(), SAVE_DIRNAME)


def safe_name(text, fallback: str) -> str:
    """One path component from site text: reserved characters become spaces,
    whitespace collapses, leading and trailing dots/spaces go (hidden files;
    Windows drops trailing ones), a device name gets a suffix, and the
    result is at most MAX_NAME chars."""
    name = " ".join(_UNSAFE_CHARS.sub(" ", str(text or "")).split())[:MAX_NAME].strip(" .")
    if not name:
        name = fallback
    if name.split(".", 1)[0].upper() in _DEVICE_NAMES:
        name = f"{name}_"
    return name


def chapter_path(root: str, source_label: str, series_title: str, number: int,
                 chapter_title: str) -> str:
    folder = os.path.join(root, safe_name(source_label, "source"), safe_name(series_title, "series"))
    path = os.path.join(folder, f"{number:04d} {safe_name(chapter_title, 'chapter')}.cbz")
    real_root = os.path.realpath(root)
    if os.path.commonpath([real_root, os.path.realpath(path)]) != real_root:
        raise SourceError("A chapter's file name would leave the save folder.")
    return path


def _comic_info(series: str, title: str, number: int, language: str) -> str:
    lang = f"  <LanguageISO>{escape(language)}</LanguageISO>\n" if language else ""
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            f"  <Series>{escape(series)}</Series>\n"
            f"  <Title>{escape(title)}</Title>\n"
            f"  <Number>{number}</Number>\n"
            f"{lang}"
            "</ComicInfo>\n")


def write_cbz(path: str, images, series: str, title: str, number: int, language: str = ""):
    """`images` are (bytes, ext) in reading order. Written to `<path>.part`
    and renamed into place, so `path` only ever holds a whole CBZ."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    width = max(3, len(str(len(images))))
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as zf:
            for i, (data, ext) in enumerate(images, start=1):
                ext = str(ext or "").lower()
                zf.writestr(f"{i:0{width}d}{ext if ext in IMAGE_EXTS else '.jpg'}", data)
            zf.writestr("ComicInfo.xml", _comic_info(series, title, number, language),
                        compress_type=zipfile.ZIP_DEFLATED)
        os.replace(part, path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise


def _row(ch, outcome: str, **extra) -> dict:
    return {"chapter_id": str(ch.chapter_id), "title": _scrub(ch.title or ""),
            "outcome": outcome, **extra}


def _save_result(chapters: list, cancelled: bool, handoff) -> dict:
    counts = {k: sum(1 for c in chapters if c["outcome"] == k)
              for k in ("saved", "skipped", "failed", "not_attempted", "not_found")}
    return {"kind": "chapter_save", "chapters": chapters,
            "saved_count": counts["saved"], "skipped_count": counts["skipped"],
            "failed_count": counts["failed"], "not_attempted_count": counts["not_attempted"],
            "not_found_count": counts["not_found"],
            "partial": bool(counts["failed"] or counts["not_attempted"] or counts["not_found"]),
            "cancelled": bool(cancelled), "handoff": handoff}


def _chapter_save_job(job_id: str, name: str, series_id: str, chapter_ids: list, root: str = None):
    root = root or save_root()
    cancel_check = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    adapter = registry.get_adapter(name, cancel_check=cancel_check)
    try:
        ladder.check_terms(name, adapter.capabilities())
        background_jobs.update_progress(job_id, 0.01, "Loading the chapter list...")
        series_title, language = series_id, ""
        if adapter.supports("get_series"):
            info = adapter.get_series(series_id)
            series_title, language = info.title or series_id, info.language or ""
        listed = chapter_order.reading_order(adapter, adapter.get_chapters(series_id))
    except Cancelled:
        background_jobs.set_result(job_id, _save_result([], True, None))
        return
    except Exception as e:
        err = _error_view(e, name)
        background_jobs.set_result(job_id, {"kind": "chapter_save", "error": err})
        raise _JobFailed(err["message"]) from None

    numbers, by_id = {}, {}
    for i, ch in enumerate(listed, start=1):
        numbers.setdefault(str(ch.chapter_id), i)
        by_id.setdefault(str(ch.chapter_id), ch)
    requested = set(chapter_ids)
    wanted = [ch for ch in by_id.values() if str(ch.chapter_id) in requested]
    label = adapter.display_name or name
    rows, handoff, cancelled = [], None, False
    total = len(wanted)
    try:
        for i, ch in enumerate(wanted, start=1):
            number = numbers[str(ch.chapter_id)]
            try:
                path = chapter_path(root, label, series_title, number, ch.title or ch.chapter_id)
                if os.path.exists(path):
                    rows.append(_row(ch, "skipped"))
                    continue
                ladder.check_terms(name, adapter.capabilities())
                background_jobs.update_progress(job_id, (i - 1) / total,
                                                f"Chapter {i} / {total} -- loading pages")
                pages = adapter.get_pages(ch)
                images = []
                for j, page in enumerate(pages, start=1):
                    background_jobs.update_progress(
                        job_id, ((i - 1) + j / max(len(pages), 1)) / total,
                        f"Chapter {i} / {total} -- page {j} / {len(pages)}")
                    images.append(adapter.download_page(page))
                write_cbz(path, images, series_title, ch.title or ch.chapter_id, number, language)
                rows.append(_row(ch, "saved", pages=len(images)))
            except ChallengeDetected as e:
                handoff = {"reason": e.reason.value, "handoff": True, "open_url": safe_url(e.url),
                           "chapter_id": str(ch.chapter_id)}
                rows.append(_row(ch, "failed", error=_scrub(str(e))))
                break
            except TermsProhibited as e:
                rows.append(_row(ch, "failed", error=_scrub(str(e))))
                break
            except (SourceError, OSError) as e:
                rows.append(_row(ch, "failed", error=_scrub(str(e)) or "Save failed."))
    except Cancelled:
        cancelled = True
    done = {r["chapter_id"] for r in rows}
    rows += [_row(ch, "not_attempted", error=_NOT_ATTEMPTED) for ch in wanted
             if str(ch.chapter_id) not in done]
    rows += [{"chapter_id": c, "title": "", "outcome": "not_found"}
             for c in chapter_ids if c not in by_id]
    background_jobs.set_result(job_id, _save_result(rows, cancelled, handoff))


def start_chapter_save(name, series_id, chapter_ids) -> dict:
    """Starts `sources_save`. 404 unknown source; 400 source off or not a
    comic source; 422 bad ids; 409 while another save runs."""
    name = str(name or "")
    cls = _enabled_source(name)
    series_id = _series_id(series_id)
    ids = _chapter_ids(chapter_ids)
    adapter = cls()
    if not adapter.supports("get_pages") or not adapter.supports("get_chapters"):
        raise UnsupportedOperationError("Only comic sources can save chapters as CBZ files.",
                                        details={"reason": "NOT_SUPPORTED"})
    return _start(SAVE_JOB_ID, _chapter_save_job, SAVE_JOB_ID, name, series_id, ids,
                  description=f"Save {len(ids)} chapter(s) from {name} as CBZ")
