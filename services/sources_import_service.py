"""
services/sources_import_service.py -- importing from a source into an
existing drama for the API (Discover/Sources/Live spec S-4, chapter import).

`start_chapter_import` takes chapter IDS only, never chapter objects or
URLs from the client: the job re-fetches the series' chapter list itself
(the adapter builds every URL) and keeps the requested ids, matched by id,
never by position. An id the list doesn't have is "not_found"; an id
already imported into this drama (sources.store.imported_chapters) is
"skipped", so re-importing is idempotent per (chapter, drama).

It never creates a drama (POST /api/dramas is `admin.library`; the client
creates one first). Writes are the pipeline's append-only ones: comic pages
into `<drama>/pages/` plus page rows, novel text appended to the raw-novel
file. No `db.save_lines`. The job id is per drama (`sourceimport_<id>`),
which serialises the pipeline's unlocked page-index and append writes, and
is in background_jobs.DRAMA_JOB_PREFIXES so a delete refuses while it runs.

Results live in this process only; read them with
sources_search_service.get_job_result (GET /api/sources/jobs/{id}/result).
Text is scrubbed, URLs reduced to scheme+host+path.
"""

import background_jobs
import db
from services import drama_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from services.sources_registry_service import _import_supported, _scrub, safe_url
from services.sources_search_service import (IMPORT_JOB_PREFIX, MAX_ID_LEN, _enabled_source,
                                             _error_view, _JobFailed, _plain_text, _series_id,
                                             _start)
from sources import chapter_order, ladder, pipeline, registry, store
from sources.http import Cancelled

MAX_CHAPTERS = 200
COMIC_MEDIA_TYPES = ("manhua", "manga", "manhwa")
NOVEL_MEDIA_TYPES = ("novel",)
_BUSY = "A job is running for this drama. Wait for it to finish or cancel it."


def import_job_id(drama_id: int) -> str:
    return f"{IMPORT_JOB_PREFIX}{int(drama_id)}"


def _plain_id(value, what: str) -> str:
    """Same rule as a series id: no URL, slash, backslash, "@", ":", ".."
    or whitespace, so an id can never steer an adapter to another host."""
    text = _plain_text(value, what, MAX_ID_LEN)
    if (text[0] in "/\\" or any(c in text for c in "\\@:") or ".." in text
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text)):
        raise InvalidInputError(f"{what} must be a plain id.")
    return text


def _chapter_ids(values) -> list:
    if not isinstance(values, (list, tuple)):
        raise InvalidInputError("chapter_ids must be a list of chapter ids.")
    out, seen = [], set()
    for v in values:
        if not isinstance(v, str):
            raise InvalidInputError("chapter_ids must be a list of chapter ids.")
        cid = _plain_id(v, "chapter_id")
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    if not out:
        raise InvalidInputError("Pick at least one chapter.")
    if len(out) > MAX_CHAPTERS:
        raise InvalidInputError(f"At most {MAX_CHAPTERS} chapters per import.")
    return out


def _require_drama(drama_id) -> dict:
    if isinstance(drama_id, bool) or not isinstance(drama_id, int) or drama_id < 1:
        raise InvalidInputError("drama_id must be a positive integer.")
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _require_idle(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY, details={"job_id": import_job_id(drama_id)})


# ---------------------------------------------------------------------------
# Chapter import (S-4)
# ---------------------------------------------------------------------------

def _outcome(row: dict) -> dict:
    out = {"chapter_id": str(row.get("chapter_id")), "title": _scrub(row.get("title") or "")}
    if row.get("skipped"):
        out["outcome"] = "skipped"
    elif row.get("ok"):
        out["outcome"] = "imported"
        if "pages" in row:
            out["pages"] = int(row["pages"])
        if "chars" in row:
            out["chars"] = int(row["chars"])
    else:
        out["outcome"] = "failed"
        out["error"] = _scrub(row.get("error") or "") or "Import failed."
    return out


def _import_result(chapters: list, cancelled: bool, handoff) -> dict:
    counts = {k: sum(1 for c in chapters if c["outcome"] == k)
              for k in ("imported", "skipped", "failed")}
    return {"kind": "chapter_import", "chapters": chapters,
            "imported_count": counts["imported"], "skipped_count": counts["skipped"],
            "failed_count": counts["failed"], "partial": counts["failed"] > 0,
            "cancelled": bool(cancelled), "handoff": handoff}


def _chapter_import_job(job_id: str, name: str, series_id: str, chapter_ids: list,
                        drama_id: int):
    cancel_check = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    adapter = registry.get_adapter(name, cancel_check=cancel_check)
    try:
        ladder.check_terms(name, adapter.capabilities())
        background_jobs.update_progress(job_id, 0.01, "Loading the chapter list...")
        listed = adapter.get_chapters(series_id)
    except Cancelled:
        background_jobs.set_result(job_id, _import_result([], True, None))
        return
    except Exception as e:
        err = _error_view(e, name)
        background_jobs.set_result(job_id, {"kind": "chapter_import", "error": err})
        raise _JobFailed(err["message"]) from None
    by_id = {}
    for ch in listed:
        by_id.setdefault(str(ch.chapter_id), ch)
    requested = set(chapter_ids)
    wanted = [ch for ch in chapter_order.sort_chapters_grouped(list(by_id.values()))
              if str(ch.chapter_id) in requested]
    already = store.imported_chapter_ids(name, series_id, drama_id)
    skip = {c for c in chapter_ids if c in already}
    pipeline.run_import_job(job_id, name, wanted, drama_id, adapter=adapter, skip_ids=skip)
    raw = (background_jobs.get_status(job_id) or {}).get("result") or {}
    rows = {}
    for row in raw.get("chapters") or []:
        rows.setdefault(str(row.get("chapter_id")), row)
    chapters = []
    for ch in wanted:  # matched by chapter_id; a chapter never reached is left out
        row = rows.get(str(ch.chapter_id))
        if row is not None:
            chapters.append(_outcome(row))
    chapters += [{"chapter_id": c, "title": "", "outcome": "not_found"}
                 for c in chapter_ids if c not in by_id]
    handoff = None
    if raw.get("handoff"):
        h = raw["handoff"]
        handoff = {"reason": str(h.get("reason") or ""), "handoff": True,
                   "open_url": safe_url(h.get("url")),
                   "chapter_id": str(h.get("chapter_id") or "") or None}
    background_jobs.set_result(job_id, _import_result(chapters, raw.get("cancelled"), handoff))


def start_chapter_import(name, series_id, chapter_ids, drama_id) -> dict:
    """Starts `sourceimport_<drama_id>`. 404 unknown source or drama; 400
    source off or unable to import; 422 bad ids or the drama's media type
    doesn't match (comic sources need manhua/manga/manhwa, text sources a
    novel); 409 while any job runs for the drama."""
    name = str(name or "")
    cls = _enabled_source(name)
    series_id = _series_id(series_id)
    ids = _chapter_ids(chapter_ids)
    adapter = cls()
    if not _import_supported(adapter) or not adapter.supports("get_chapters"):
        raise UnsupportedOperationError("This source can't import chapters.",
                                        details={"reason": "NOT_SUPPORTED"})
    drama = _require_drama(drama_id)
    media = (drama.get("media_type") or "").lower()
    if adapter.supports("get_pages"):
        if media not in COMIC_MEDIA_TYPES:
            raise InvalidInputError("Comic chapters import into a manhua, manga or manhwa "
                                    "drama. Pick one of those, or create one first.")
    elif media not in NOVEL_MEDIA_TYPES:
        raise InvalidInputError("Novel chapters import into a novel drama. Pick one, "
                                "or create one first.")
    _require_idle(drama_id)
    job_id = import_job_id(drama_id)
    return _start(job_id, _chapter_import_job, job_id, name, series_id, ids, drama_id,
                  description=f"Import {len(ids)} chapter(s) from {name}")
