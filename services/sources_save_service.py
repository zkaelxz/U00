"""
services/sources_save_service.py -- saving a comic source's chapters to a
folder on this PC as CBZ files, for reading elsewhere, without opening a
drama or Scanlate.

`start_chapter_save` takes chapter ids only, like a chapter import: the job
re-reads the series' chapter list (the adapter builds every URL) and keeps
the requested ids, matched by id. Each chapter becomes one file:

    <save folder>/<source>/<series title>/<NNNN> <chapter title>.cbz

The save folder is `<data dir>/saved_comics` unless the owner picked another
one on the PC (`set_save_folder`, e.g. a folder a comic server watches); a
picked folder that has since gone missing falls back to the default.

NNNN is the chapter's place in the series' reading order, so files sort in
reading order. Names come from the site, so they are reduced to safe file
names (no separators, reserved characters or Windows device names) and the
final path is checked to stay inside the save folder. A chapter whose file
already exists (with at least one image) is skipped, so saving again only adds what's missing. A file
is written to a `.part` beside it and renamed into place only when complete,
so a cancelled or failed chapter never leaves a half CBZ behind.

The CBZ holds the pages in order (`001.webp`, ...) stored uncompressed, plus
a ComicInfo.xml (series, chapter title, number, language) that comic readers
use. One save runs at a time in this process (`sources_save`, 409
otherwise). Results carry outcomes and counts only, never paths.

`save_series_chapters` is the save itself, shared with the tracked-series
check (sources/chapter_check.py), which saves new chapters of a series whose
"Save new chapters as CBZ" is on. Two saves of the same chapter at once
each write their own `.part`, and the rename keeps one whole file.
"""

import os
import re
import subprocess
import sys
import threading
import zipfile
from xml.sax.saxutils import escape

import background_jobs
import db
import portable
from services.service_errors import InvalidInputError, UnsupportedOperationError
from services.sources_import_service import parse_chapter_ids
from services.sources_registry_service import scrub, safe_url
from services.sources_search_service import (SAVE_JOB_ID, enabled_source, error_view,
                                             JobFailed, clean_series_id, start_job)
from sources import chapter_order, ladder, registry
from sources.http import Cancelled
from sources.models import ChallengeDetected, FailureReason, SourceError, TermsProhibited

# The reader refuses a chapter with more pages than this, so saving one would
# only produce a file that never opens.
MAX_CHAPTER_PAGES = 2000

SAVE_DIRNAME = "saved_comics"
SETTING = "comic_save"
MAX_FOLDER_LEN = 1000
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
MAX_NAME = 120
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}
_NOT_ATTEMPTED = "Not attempted: the save stopped before this chapter."


def default_root() -> str:
    return os.path.join(portable.data_dir(), SAVE_DIRNAME)


def _picked_folder():
    saved = db.get_app_setting(SETTING) or {}
    folder = saved.get("folder") if isinstance(saved, dict) else None
    return folder if isinstance(folder, str) and folder else None


def save_root() -> str:
    folder = _picked_folder()
    return folder if folder and os.path.isdir(folder) else default_root()


def get_save_folder() -> dict:
    """PC only (it is a path on this PC): the folder saves go to, whether
    the owner picked it, and whether a picked folder is missing."""
    picked = _picked_folder()
    return {"folder": save_root(), "custom": bool(picked and os.path.isdir(picked)),
            "picked_missing": bool(picked and not os.path.isdir(picked))}


def set_save_folder(folder) -> dict:
    """An existing folder given as a full path; "" goes back to the default."""
    if not isinstance(folder, str):
        raise InvalidInputError("Enter a folder.")
    folder = folder.strip()
    if not folder:
        db.set_app_setting(SETTING, {})
        return get_save_folder()
    if len(folder) > MAX_FOLDER_LEN or any(ord(c) < 32 for c in folder):
        raise InvalidInputError("That folder path is not valid.")
    if not os.path.isabs(folder):
        raise InvalidInputError("Use the full path of the folder, starting with its drive.")
    if not os.path.isdir(folder):
        raise InvalidInputError("That folder does not exist on this PC.")
    db.set_app_setting(SETTING, {"folder": os.path.realpath(folder)})
    return get_save_folder()


def _launch(folder: str):
    if sys.platform.startswith("win"):
        os.startfile(folder)  # noqa: S606 -- the owner's own folder, on the PC
    else:
        # Popen without a shell; the file manager outlives this call.
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


def open_save_folder() -> dict:
    """Opens the save folder in the PC's file manager (created if needed)."""
    folder = save_root()
    os.makedirs(folder, exist_ok=True)
    try:
        _launch(folder)
    except OSError:
        raise UnsupportedOperationError("This PC has no file manager the app can open.") from None
    return {"opened": True}


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


def series_folder_name(series_title, series_id) -> str:
    """"Title [id]": the title is for people and Jellyfin, the id keeps two
    series whose titles clean up to the same name in separate folders."""
    return f"{safe_name(series_title, 'series')} [{safe_name(series_id, 'id')}]"


def chapter_path(root: str, source_label: str, series_title: str, series_id, number: int,
                 chapter_title: str) -> str:
    folder = os.path.join(root, safe_name(source_label, "source"),
                          series_folder_name(series_title, series_id))
    path = os.path.join(folder, f"{number:04d} {safe_name(chapter_title, 'chapter')}.cbz")
    real_root = os.path.realpath(root)
    if os.path.commonpath([real_root, os.path.realpath(path)]) != real_root:
        raise SourceError("A chapter's file name would leave the save folder.")
    return path


def _comic_info(series: str, title: str, number: int, language: str, note: str = "") -> str:
    notes = f"  <Notes>{escape(note)}</Notes>\n" if note else ""
    lang = f"  <LanguageISO>{escape(language)}</LanguageISO>\n" if language else ""
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            f"  <Series>{escape(series)}</Series>\n"
            f"  <Title>{escape(title)}</Title>\n"
            f"  <Number>{number}</Number>\n"
            f"{lang}"
            f"{notes}"
            "</ComicInfo>\n")


def image_ext(data) -> str:
    """The extension for image bytes, from their signature; "" when the
    bytes aren't a page image (an HTML error page sent with a 200, say)."""
    head = bytes(data[:16]) if isinstance(data, (bytes, bytearray)) else b""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis"):
        return ".avif"
    return ""


def write_cbz(path: str, images, series: str, title: str, number: int, language: str = "",
              note: str = ""):
    """`images` are (bytes, ext) in reading order. Written to `<path>.part`
    and renamed into place, so `path` only ever holds a whole CBZ."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = f"{path}.{os.getpid()}-{threading.get_ident()}.part"
    width = max(3, len(str(len(images))))
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as zf:
            for i, (data, ext) in enumerate(images, start=1):
                ext = str(ext or "").lower()
                zf.writestr(f"{i:0{width}d}{ext if ext in IMAGE_EXTS else '.jpg'}", data)
            zf.writestr("ComicInfo.xml", _comic_info(series, title, number, language, note),
                        compress_type=zipfile.ZIP_DEFLATED)
        os.replace(part, path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise


def _has_images(path: str) -> bool:
    """True when the CBZ at `path` holds at least one image. A leftover file
    with none (or an unreadable one) counts as missing so it is fetched again."""
    try:
        with zipfile.ZipFile(path) as zf:
            return any(n.lower().endswith(IMAGE_EXTS) for n in zf.namelist())
    except (OSError, zipfile.BadZipFile, ValueError):
        return False


def _row(ch, outcome: str, **extra) -> dict:
    return {"chapter_id": str(ch.chapter_id), "title": scrub(ch.title or ""),
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


def save_series_chapters(adapter, name: str, series_id: str, chapter_ids, root: str = None,
                         progress=None):
    """Saves the chapters of one series whose ids are in `chapter_ids`.
    Returns (rows, cancelled): one outcome row per id (reading order, then
    ids the list doesn't have as "not_found"). `progress(fraction, message)`
    is told as it goes. Errors before any chapter (terms, the chapter list,
    a cancel) raise; a chapter's own error is its row. A browser check, the
    terms or a cancel stop the run: the chapters after it are
    "not_attempted", and a browser check's row carries `handoff`."""
    root = root or save_root()
    progress = progress or (lambda fraction, message: None)
    ladder.check_terms(name, adapter.capabilities())
    series_title, language = series_id, ""
    if adapter.supports("get_series"):
        info = adapter.get_series(series_id)
        series_title, language = info.title or series_id, info.language or ""
    listed = chapter_order.reading_order(adapter, adapter.get_chapters(series_id))
    numbers, by_id = {}, {}
    for i, ch in enumerate(listed, start=1):
        numbers.setdefault(str(ch.chapter_id), i)
        by_id.setdefault(str(ch.chapter_id), ch)
    requested = {str(c) for c in chapter_ids}
    wanted = [ch for ch in by_id.values() if str(ch.chapter_id) in requested]
    label = adapter.display_name or name
    rows, total = [], len(wanted)
    stopped = cancelled = False
    for i, ch in enumerate(wanted, start=1):
        number = numbers[str(ch.chapter_id)]
        try:
            path = chapter_path(root, label, series_title, series_id, number, ch.title or ch.chapter_id)
            if os.path.exists(path) and _has_images(path):
                rows.append(_row(ch, "skipped"))
                continue
            ladder.check_terms(name, adapter.capabilities())
            progress((i - 1) / total, f"Chapter {i} / {total} -- loading pages")
            pages = adapter.get_pages(ch)
            if not pages:
                raise SourceError("The site returned no pages for this chapter.",
                                  FailureReason.LAYOUT_CHANGED)
            if len(pages) > MAX_CHAPTER_PAGES:
                raise SourceError("This chapter has too many pages to save.")
            images = []
            for j, page in enumerate(pages, start=1):
                progress(((i - 1) + j / max(len(pages), 1)) / total,
                         f"Chapter {i} / {total} -- page {j} / {len(pages)}")
                data, _ = adapter.download_page(page)
                ext = image_ext(data)
                if not ext:
                    # Checked before writing: a saved chapter is never re-fetched.
                    raise SourceError(f"Page {j} isn't an image (the site sent something else, "
                                      "such as an error page).", FailureReason.HTTP_ERROR)
                images.append((data, ext))
            write_cbz(path, images, series_title, ch.title or ch.chapter_id, number, language,
                      note=f"{label} series {series_id}")
            rows.append(_row(ch, "saved", pages=len(images)))
        except ChallengeDetected as e:
            rows.append(_row(ch, "failed", error=scrub(str(e)),
                             handoff={"reason": e.reason.value, "handoff": True,
                                      "open_url": safe_url(e.url),
                                      "chapter_id": str(ch.chapter_id)}))
            stopped = True
            break
        except TermsProhibited as e:
            rows.append(_row(ch, "failed", error=scrub(str(e))))
            stopped = True
            break
        except Cancelled:
            stopped = cancelled = True
            break
        except SourceError as e:
            rows.append(_row(ch, "failed", error=scrub(str(e)) or "Save failed."))
        except OSError as e:
            # str(e) carries the full file path; only the OS reason is safe to show.
            rows.append(_row(ch, "failed", error=scrub(e.strerror or "") or "Save failed."))
    done = {r["chapter_id"] for r in rows}
    if stopped:
        rows += [_row(ch, "not_attempted", error=_NOT_ATTEMPTED) for ch in wanted
                 if str(ch.chapter_id) not in done]
    rows += [{"chapter_id": c, "title": "", "outcome": "not_found"}
             for c in chapter_ids if str(c) not in by_id]
    return rows, cancelled


def _chapter_save_job(job_id: str, name: str, series_id: str, chapter_ids: list, root: str = None):
    cancel_check = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    adapter = registry.get_adapter(name, cancel_check=cancel_check)
    background_jobs.update_progress(job_id, 0.01, "Loading the chapter list...")

    def progress(fraction, message):
        background_jobs.update_progress(job_id, max(0.01, min(fraction, 0.999)), message)

    try:
        rows, cancelled = save_series_chapters(adapter, name, series_id, chapter_ids, root,
                                               progress)
    except Cancelled:
        background_jobs.set_result(job_id, _save_result(
            [{"chapter_id": c, "title": "", "outcome": "not_attempted", "error": _NOT_ATTEMPTED}
             for c in chapter_ids], True, None))
        return
    except Exception as e:
        err = error_view(e, name)
        background_jobs.set_result(job_id, {"kind": "chapter_save", "error": err})
        raise JobFailed(err["message"]) from None
    handoff = next((r.pop("handoff") for r in rows if "handoff" in r), None)
    background_jobs.set_result(job_id, _save_result(rows, cancelled, handoff))


def start_chapter_save(name, series_id, chapter_ids) -> dict:
    """Starts `sources_save`. 404 unknown source; 400 source off or not a
    comic source; 422 bad ids; 409 while another save runs."""
    name = str(name or "")
    cls = enabled_source(name)
    series_id = clean_series_id(series_id)
    ids = parse_chapter_ids(chapter_ids)
    adapter = cls()
    if not adapter.supports("get_pages") or not adapter.supports("get_chapters"):
        raise UnsupportedOperationError("Only comic sources can save chapters as CBZ files.",
                                        details={"reason": "NOT_SUPPORTED"})
    return start_job(SAVE_JOB_ID, _chapter_save_job, SAVE_JOB_ID, name, series_id, ids,
                  description=f"Save {len(ids)} chapter(s) from {name} as CBZ")
