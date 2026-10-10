"""
services/source_link_service.py -- a title's "where it came from" link in
its two stored forms.

`dramas.source_url` holds the display-safe form (scheme, host, path; see
translate_engines.display_url), which every API response, page and report
may show. Cleaning can break a link that needs its query (a video id in
`?v=`), so when cleaning changes the link the raw one is also kept in
`dramas.source_url_full`, empty otherwise. That column can hold a signed
token, so it is read only by `source_link` for the PC-only route
GET /api/dramas/{id}/source-link; nothing logs it and no other response
model, report, page or manifest carries it.
"""

from urllib.parse import urlsplit

import db
from services import library_service
from services.service_errors import NotFoundError


def split_source_url(raw) -> tuple:
    """(clean, full) for storing a link: full is the raw link only when
    cleaning changed it and left something usable, else ""."""
    raw = str(raw or "").strip()
    clean = library_service.display_source_url(raw)
    return clean, (raw if clean and raw != clean else "")


def _is_http(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


def source_link(drama_id) -> dict:
    """{"url": the raw link when one was kept, else the cleaned one}. A
    title saved before the raw form had its own column may still hold the
    typed link in source_url, which is the owner's original too."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    full = (drama.get("source_url_full") or drama.get("source_url") or "").strip()
    if _is_http(full):
        return {"url": full}
    return {"url": library_service.display_source_url(drama.get("source_url"))}
