"""
services/scanlate_render_service.py -- Scanlate typeset rendering and bulk
export for the API (docs/specs/scanlate-api-spec.md S6 and S8).

- render_page: typesets one page from its regions IN THE DATABASE (never
  from a request body) with scanlate.process_page: regions with blank text
  are left as the original, SFX is skipped unless its include_sfx override
  is set, speech/thought text is fitted to the bubble shape. The output is
  `pages/typeset_id<page id>.png` (unique per page, replaced atomically)
  and recorded as the page's rendered_filename.
- start_render: a job (`scanlate_<drama_id>`, the drama's one Scanlate job)
  that re-renders one page or every page with regions.
- start_export: a job that writes a ZIP and/or a PDF of the chapter to
  artifact_service's `scanlate_zip` / `scanlate_pdf` kinds (downloaded with
  GET /api/artifacts/dramas/{id}/{kind}). A page with regions but no typeset
  image is rendered first; a page with no regions goes in as its original.
  One page failing does not stop the rest; it is recorded in that page's
  run notes.

Files are only ever read from inside the drama folder (the comic viewer's
resolver: no `..`, no symlinks, png/jpg only, size-capped). No path reaches
a response, a note or a job message. No FastAPI or Streamlit import.
"""
import os
import zipfile

import background_jobs
import db
import storage
from services import artifact_service, comic_view_service
from services import scanlate_pages_service as pages_svc
from services.service_errors import InvalidInputError, NotFoundError, UnsupportedOperationError

EXPORT_FORMATS = ("zip", "pdf")
_KIND = {"zip": "scanlate_zip", "pdf": "scanlate_pdf"}
_FILENAME = {"zip": "typeset_pages.zip", "pdf": "typeset_pages.pdf"}


def original_path(drama_id: int, page: dict) -> str:
    found = comic_view_service.safe_file(drama_id, page.get("filename"))
    if found is None:
        raise NotFoundError("This page's image is missing.")
    return found[0]


def _pages_dir(drama_id: int) -> str:
    base = os.path.realpath(db.drama_dir(drama_id))
    path = os.path.join(base, "pages")
    os.makedirs(path, exist_ok=True)
    if os.path.islink(path) or os.path.realpath(path) != path:
        raise NotFoundError("This drama's pages folder is not usable.")
    return path


def _custom_fonts(drama_id: int) -> dict:
    """The drama's own saved fonts (`fonts/<category>.ttf`), as the tab."""
    import scanlate
    folder = os.path.join(db.drama_dir(drama_id), "fonts")
    return {cat: os.path.join(folder, f"{cat}.ttf") for cat in scanlate.FONT_CATEGORIES
            if os.path.isfile(os.path.join(folder, f"{cat}.ttf"))
            and not os.path.islink(os.path.join(folder, f"{cat}.ttf"))}


def render_page(drama_id: int, page_id: int, notes: list = None) -> dict:
    """Typesets one page now (call from a job). Returns {rendered, blank}:
    rendered False when the page has no region with text to place (nothing
    is written then); blank counts regions left as the original for having
    no translation. Warnings (blank regions, an inpaint fallback) are
    appended to `notes` as (level, message)."""
    import scanlate
    notes = notes if notes is not None else []
    page = pages_svc.require_page(drama_id, page_id)
    bubbles = db.load_bubbles(page_id)
    placed = [b for b in bubbles if not b.get("skip") and not scanlate.region_excluded_from_auto(b)
              and (b.get("translated_text") or "").strip()]
    if not placed:
        return {"rendered": False, "blank": 0}
    src = original_path(drama_id, page)
    name = f"typeset_id{page_id}.png"
    out = os.path.join(_pages_dir(drama_id), name)
    render_notes = []
    with pages_svc.pipeline_lock():
        _path, skipped_blank = scanlate.process_page(
            src, bubbles, out, custom_fonts=_custom_fonts(drama_id), notes=render_notes)
    for n in render_notes:
        notes.append((n[0], "Text cleanup fell back to OpenCV: " + n[-1]))
    if skipped_blank:
        notes.append(("warning", f"{len(skipped_blank)} region(s) had no translation and were "
                                 "left as the original."))
    db.update_page(page_id, rendered_filename=f"pages/{name}")
    return {"rendered": True, "blank": len(skipped_blank)}


def append_notes(page_id: int, notes: list):
    """Adds render notes to the page's stored run notes (kept, not replaced)."""
    if not notes:
        return
    page = db.get_page(page_id) or {}
    existing = [(n["level"], n["message"]) for n in pages_svc.parse_notes(page.get("run_notes"))]
    combined = []
    for n in existing + [(n[0], pages_svc.clean_note(n[-1])) for n in notes]:
        if n not in combined:                    # a re-render repeats its notes
            combined.append(n)
    db.update_page(page_id, run_notes=pages_svc.notes_to_json(
        combined[-pages_svc.MAX_NOTES:]))       # newest kept when over the cap


def check_cancel(jid: str):
    if background_jobs.is_cancel_requested(jid):
        raise background_jobs.JobCancelled(jid)


def _render_job(jid: str, drama_id: int, page_ids: list):
    done = failed = 0
    total = len(page_ids)
    for n, pid in enumerate(page_ids, start=1):
        check_cancel(jid)
        background_jobs.update_progress(jid, (n - 1) / total, f"Rendering page {n} of {total}")
        notes = []
        try:
            if render_page(drama_id, pid, notes)["rendered"]:
                done += 1
            append_notes(pid, notes)
        except (NotFoundError, InvalidInputError) as exc:
            failed += 1
            append_notes(pid, [("error", f"Render failed: {exc}")])
        except Exception as exc:
            failed += 1
            append_notes(pid, [("error", f"Render failed: {type(exc).__name__}: {exc}")])
    msg = f"Rendered {done} page(s)" + (f", {failed} failed (see page notes)" if failed else "")
    background_jobs.update_progress(jid, 1.0, msg + ".")


def start_render(drama_id: int, page_id: int = None) -> dict:
    """Re-renders one page, or every page that has regions. 409 while the
    drama's Scanlate job or an import runs."""
    pages_svc.require_drama(drama_id)
    if page_id is not None:
        pages_svc.require_page(drama_id, page_id)
        ids = [page_id]
    else:
        with_regions = {b["page_id"] for b in db.list_bubbles_for_drama(drama_id)}
        ids = [p["id"] for p in db.list_pages(drama_id) if p["id"] in with_regions]
        if not ids:
            raise UnsupportedOperationError("No page has text regions yet. Translate first.")
    return pages_svc.start_drama_job(drama_id, _render_job, pages_svc.job_id(drama_id),
                                     drama_id, ids, description=f"Scanlate render (drama {drama_id})")


# --- S8: export -------------------------------------------------------------

MAX_PDF_EXPORT_PAGES = 200      # PIL builds a PDF with every page in memory
_PDF_TOO_MANY = (f"A PDF export holds every page in memory, so it is limited to "
                 f"{MAX_PDF_EXPORT_PAGES} pages. Export a ZIP instead.")


def _rendered_path(drama_id: int, page: dict):
    found = comic_view_service.safe_file(drama_id, page.get("rendered_filename"))
    return found[0] if found else None


def _tmp_beside(dest: str) -> str:
    """A partial file in the library temp folder, so an unfinished export
    can never be served as the artifact and a crash's leftover is swept."""
    return storage.new_partial_file("export")


def _write_zip(dest: str, files: list):
    tmp = _tmp_beside(dest)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
            for ordinal, path in files:
                zf.write(path, f"page_{ordinal:04d}{os.path.splitext(path)[1].lower()}")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_pdf(dest: str, files: list):
    if len(files) > MAX_PDF_EXPORT_PAGES:
        raise InvalidInputError(_PDF_TOO_MANY)
    from PIL import Image
    tmp = _tmp_beside(dest)
    images = []
    try:
        for _ordinal, path in files:
            im = Image.open(path)
            images.append(im if im.mode in ("RGB", "L") else im.convert("RGB"))
        images[0].save(tmp, "PDF", save_all=True, append_images=images[1:])
        os.replace(tmp, dest)
    finally:
        for im in images:
            im.close()
        if os.path.exists(tmp):
            os.remove(tmp)


def _export_job(jid: str, drama_id: int, formats: list):
    pages = db.list_pages(drama_id)
    with_regions = {b["page_id"] for b in db.list_bubbles_for_drama(drama_id)}
    files, originals, failed = [], 0, 0
    total = len(pages)
    for n, page in enumerate(pages, start=1):
        check_cancel(jid)
        background_jobs.update_progress(jid, 0.8 * (n - 1) / total,
                                        f"Preparing page {n} of {total}")
        try:
            path = _rendered_path(drama_id, page)
            if path is None and page["id"] in with_regions:
                notes = []
                if render_page(drama_id, page["id"], notes)["rendered"]:
                    path = _rendered_path(drama_id, db.get_page(page["id"]))
                append_notes(page["id"], notes)
            if path is None:
                path = original_path(drama_id, page)
                originals += 1
            files.append((n, path))
        except Exception as exc:
            failed += 1
            append_notes(page["id"], [("error", f"Export skipped this page: "
                                                 f"{type(exc).__name__}: {exc}")])
    if not files:
        raise RuntimeError("No page could be exported.")
    for fmt in formats:
        check_cancel(jid)
        background_jobs.update_progress(jid, 0.9, f"Writing the {fmt.upper()}")
        dest = artifact_service.output_path(drama_id, _KIND[fmt], _FILENAME[fmt])
        (_write_zip if fmt == "zip" else _write_pdf)(dest, files)
    msg = f"Exported {len(files)} page(s)"
    if originals:
        msg += f" ({originals} without typeset text, as the original)"
    if failed:
        msg += f"; {failed} failed (see page notes)"
    background_jobs.update_progress(jid, 1.0, msg + ".")


def start_export(drama_id: int, formats=EXPORT_FORMATS) -> dict:
    """Bulk export job. formats: any of "zip", "pdf" (both by default)."""
    pages_svc.require_drama(drama_id)
    formats = list(dict.fromkeys(formats or ()))
    if not formats or any(f not in EXPORT_FORMATS for f in formats):
        raise InvalidInputError("formats must be 'zip' and/or 'pdf'.")
    page_count = len(db.list_pages(drama_id))
    if not page_count:
        raise UnsupportedOperationError("This drama has no pages to export.")
    if "pdf" in formats and page_count > MAX_PDF_EXPORT_PAGES:
        raise InvalidInputError(_PDF_TOO_MANY)
    return pages_svc.start_drama_job(drama_id, _export_job, pages_svc.job_id(drama_id),
                                     drama_id, formats,
                                     description=f"Scanlate export (drama {drama_id})")
