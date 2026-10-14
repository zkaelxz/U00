"""
services/comic_source_chapters_service.py -- where a comic title's other chapters
come from, for the reader's chapter list.

Two kinds of source: a site with an adapter (the answer names the source and
series; the page lists the chapters through the paced series job and imports one
by id with the existing chapter import), and a site only the browser extension
reaches (the list is the links the extension saw on the series page; a chapter
is opened on the site and sent with the extension). This service makes no
request to any site: listing a series' chapters is a paced job, not a GET.
"""
import comic_chapter_links
import comic_chapters
import translate_engines
from services import source_link_service
from services.sources_import_service import require_drama
from sources import extension_marker, registry, store


def _none() -> dict:
    return {"mode": "none", "source": "", "series_id": "", "imported_ids": [], "chapters": []}


def _adapter_for(link: str):
    cls = registry.adapter_class_for_url(link) if link else None
    if cls is None or not registry.is_enabled(cls.name) or extension_marker.is_marked(cls.name):
        return None
    adapter = registry.find_for_url(link)
    if adapter is None or not (adapter.supports("get_chapters") and adapter.supports("get_pages")):
        return None
    return adapter


def _series_id(adapter, link: str):
    parsed = adapter.parse_url(link)
    if parsed and parsed[0] == "series":
        return parsed[1]
    if parsed and parsed[0] == "chapter":
        return getattr(parsed[1], "series_id", None)
    return None


def _from_links(drama_id: int) -> dict:
    links = comic_chapter_links.load(drama_id)
    if not links:
        return _none()
    have = {translate_engines.display_url(c.get("url")) for c in comic_chapters.load(drama_id)["chapters"]}
    have.discard("")
    return {"mode": "links", "source": "", "series_id": "", "imported_ids": [],
            "chapters": [{"chapter_id": "", "title": l["title"], "url": l["url"],
                          "imported": l["url"] in have} for l in links]}


def list_source_chapters(drama_id: int, principal=None) -> dict:
    """Needs edit access to the title (a 404 otherwise, as for an import): a person who
    can see a title but not edit it gets no chapter list."""
    require_drama(drama_id, principal)
    link = source_link_service.source_link(drama_id)["url"]
    adapter = _adapter_for(link)
    series_id = _series_id(adapter, link) if adapter is not None else None
    if adapter is not None and series_id:
        return {"mode": "adapter", "source": adapter.name, "series_id": str(series_id),
                "imported_ids": sorted(store.imported_chapter_ids(adapter.name, str(series_id), drama_id)),
                "chapters": []}
    return _from_links(drama_id)
