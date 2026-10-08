"""
services/comic_chapters_service.py -- marking comic pages "not part of the
story" (credit and promo pages) and resolving which pages a Translate or
Scanlate run covers. The data lives in comic_chapters' manifest; this module
only decides which pages a request means. No FastAPI import.

Hidden pages stay on disk and in the page list (flagged `hidden`), so older
clients still see every page and a hide is always reversible. Automatic runs
skip them.
"""
import comic_chapters
import db
from services.service_errors import ConflictError, InvalidInputError, NotFoundError


def _groups(drama_id: int):
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    pages = db.list_pages(drama_id)
    return pages, comic_chapters.group_pages(pages, comic_chapters.load(drama_id))


def _chapter(groups, chapter_id: str) -> dict:
    for g in groups:
        if g["id"] == chapter_id:
            return g
    raise NotFoundError("No such chapter in this drama.")


def set_visibility(drama_id: int, hidden: bool, page_ids=None, chapter_id=None, edge=None,
                   count: int = 1) -> dict:
    """Hides or restores pages. Either `page_ids` (each must belong to this
    drama) or `chapter_id` with `edge` "first"/"last" (that many pages from
    that end of the chapter) or "all"."""
    if (page_ids is None) == (chapter_id is None):
        raise InvalidInputError("Send page_ids, or chapter_id with edge.")
    pages, groups = _groups(drama_id)
    if page_ids is not None:
        by_id = {p["id"]: p["filename"] for p in pages}
        if any(pid not in by_id for pid in page_ids):
            raise NotFoundError("No such page in this drama.")
        names = [by_id[pid] for pid in page_ids]
    else:
        if edge not in ("first", "last", "all"):
            raise InvalidInputError("edge must be 'first', 'last' or 'all'.")
        files = _chapter(groups, chapter_id)["filenames"]
        names = files if edge == "all" else files[:count] if edge == "first" else files[-count:]
    try:
        comic_chapters.set_hidden(drama_id, names, hidden)
    except comic_chapters.ManifestUnreadable:
        raise ConflictError("The chapter data could not be updated, so no pages were changed.")
    now_hidden = set(comic_chapters.load(drama_id)["hidden"])
    live = {p["filename"] for p in pages}
    return {"changed": len(set(names)), "hidden_count": len(now_hidden & live)}


def run_page_ids(drama_id: int, chapter_id=None) -> list:
    """Ids of the pages an automatic run covers, in reading order: every
    visible page, or only the visible pages of `chapter_id`."""
    pages, groups = _groups(drama_id)
    hidden = set(comic_chapters.load(drama_id)["hidden"])
    wanted = None if chapter_id is None else set(_chapter(groups, chapter_id)["filenames"])
    return [p["id"] for p in pages
            if p["filename"] not in hidden and (wanted is None or p["filename"] in wanted)]
