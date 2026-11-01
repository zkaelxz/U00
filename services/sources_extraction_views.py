"""services/sources_extraction_views.py -- What a held extraction review shows: the confidence, report and per-kind page views.

Moved out of services/sources_extraction_service.py to keep it under the size ceiling. Import these names from
`services.sources_extraction_service`, which re-exports them at its end; nothing imports this module
first, because it needs `services.sources_extraction_service` fully defined.
"""

from engine_backends import shared
from services import page_import_limits as limits
from lib.errors import InvalidInputError
from services.sources_registry_service import scrub
from sources import adaptive, ai_extract, profiles
from services.sources_extraction_service import (MAX_HEADINGS, MAX_LABEL, MAX_LINKS, MAX_PREVIEW_CHARS, MAX_SELECTOR_LEN, NUMBER_FROM, _NOT_OFFERED, _Review, _get, _image_type, _require_drama)


def _label(text) -> str:
    return (scrub(str(text or "")) or "")[:MAX_LABEL]


def _confidence_view(data: dict) -> dict:
    conf = (data or {}).get("confidence") or {}
    o = ai_extract.overall(data or {})
    fields = []
    for f in list(ai_extract.FIELDS) + ["media_resources"]:
        c = conf.get(f)
        if not c:
            continue
        value = (data or {}).get(f)
        shown = None if value is None or isinstance(value, (list, dict)) else _label(value)
        if f in ("next_url", "previous_url") and isinstance(value, str):
            shown = shared.display_url(value)
        fields.append({"field": f, "bucket": c.get("bucket"), "score": float(c.get("score") or 0),
                       "checks": [_label(x) for x in c.get("checks") or []], "value": shown})
    return {"overall": {"bucket": o.get("bucket"), "score": float(o.get("score") or 0)},
            "fields": fields}


def _report_view(report) -> dict:
    if report is None:
        return {"headline": "", "lines": [], "llm_calls": 0, "cache_hit": False,
                "profile": "", "pending_profile": None}
    pending = getattr(report, "pending_profile", None)
    bucket = (((pending or {}).get("validation") or {}).get("overall") or {}).get("bucket")
    return {"headline": scrub(report.headline()),
            "lines": [scrub(x) for x in (report.access_lines + report.lines)][:40],
            "llm_calls": int(report.llm_calls), "cache_hit": bool(report.cache_hit),
            "profile": scrub(adaptive.describe_profile(report.profile)),
            "pending_profile": {"bucket": bucket} if pending else None}


def _novel_base_rules(rv: _Review) -> dict:
    return rv.rules or profiles.infer_novel_rules(rv.page, rv.data) or {}


def _containers(rv: _Review) -> list:
    """[(selector, chars, preview)] offered for the chapter text, the
    current rule's selector first when the page's top list lacks it."""
    opts = [(sel, n, t) for sel, n, t in profiles.container_options(rv.page)
            if len(sel) <= MAX_SELECTOR_LEN]
    base = _novel_base_rules(rv).get("content_selector")
    if base and len(base) <= MAX_SELECTOR_LEN and base not in [o[0] for o in opts]:
        el = profiles.select_one(rv.page.soup, base)
        text = el.get_text(" ", strip=True) if el is not None else ""
        opts.insert(0, (base, len(text), text[:80]))
    return opts


def _exclusions(rv: _Review, selector: str) -> list:
    return [(sel, t) for sel, t in profiles.exclusion_options(rv.page, selector)
            if len(sel) <= MAX_SELECTOR_LEN]


def _headings(rv: _Review) -> list:
    return [b for b in rv.page.blocks if b.tag in ai_extract.HEADING_TAGS
            or b.id == rv.data.get("chapter_title_block")][:MAX_HEADINGS]


def _links(rv: _Review) -> list:
    return rv.page.links[:MAX_LINKS]


def _link_id_for(rv: _Review, url):
    return next((l.id for l in _links(rv) if url and l.url == url), None)


def _novel_view(rv: _Review) -> dict:
    data, base = rv.data, _novel_base_rules(rv)
    containers = _containers(rv)
    sels = [c[0] for c in containers]
    current = base.get("content_selector") if base.get("content_selector") in sels else \
        (sels[0] if sels else None)
    exclusions = {sel: _exclusions(rv, sel) for sel in sels}
    ex_now = {s for s, _t in exclusions.get(current, [])}
    title_id = data.get("chapter_title_block")
    heads = _headings(rv)
    text = data.get("content") or ""
    return {
        "text_preview": text[:MAX_PREVIEW_CHARS],
        "char_count": len(text),
        "chapter_title": _label(data.get("chapter_title")),
        "containers": [{"selector": sel, "chars": int(n), "preview": _label(t),
                        "exclusions": [{"selector": es, "preview": _label(et)}
                                       for es, et in exclusions[sel]]}
                       for sel, n, t in containers],
        "content_selector": current,
        "exclude_selectors": [x for x in base.get("exclude_selectors") or [] if x in ex_now],
        "headings": [{"id": b.id, "text": _label(b.text)} for b in heads],
        "title_block": title_id if title_id in {b.id for b in heads} else None,
        "links": [{"id": l.id, "text": _label(l.text), "url": shared.display_url(l.url)}
                  for l in _links(rv)],
        "next_link": _link_id_for(rv, data.get("next_url")),
        "previous_link": _link_id_for(rv, data.get("previous_url")),
        "number_from": base.get("number_from") if base.get("number_from") in NUMBER_FROM else "title",
    }


def _comic_items(rv: _Review) -> list:
    roles = {p["resource_url"]: p for p in rv.data.get("pages") or []}
    out = []
    for i, c in enumerate(rv.candidates):
        p = roles.get(c.url) or {}
        role = p.get("role") or "other"
        page = (p.get("index") + 1) if role == "content" and isinstance(p.get("index"), int) else 0
        out.append({"id": i, "display_url": shared.display_url(c.url), "attr": _label(c.attr or "src"),
                    "width": int(c.width or 0), "height": int(c.height or 0),
                    "role": role if role in ai_extract.COMIC_ROLES else "other", "page": page,
                    "reason": _label(c.reject_reason or p.get("reason") or ""),
                    "has_image": _usable(rv, i)})
    return out


def _usable(rv: _Review, i: int) -> bool:
    """Downloaded, an accepted type (PNG/JPEG/WebP) with a size within the
    page rules (services/page_import_limits.py, header only): only such an
    image can become a page."""
    c = rv.candidates[i]
    size = rv.sizes.get(i, 0)
    return (size > 0 and _image_type(c) is not None and c.width > 0 and c.height > 0
            and c.width * c.height <= limits.MAX_IMAGE_PIXELS
            and size <= limits.MAX_IMAGE_BYTES)


def _kept_ids(rv: _Review, data: dict) -> list:
    """Candidate ids of the images marked as pages, in page order, usable only."""
    by_url = {c.url: i for i, c in enumerate(rv.candidates)}
    ids = [by_url[p["resource_url"]] for p in sorted(
        (p for p in data.get("pages") or [] if p.get("role") == "content"),
        key=lambda p: p["index"]) if p["resource_url"] in by_url]
    return [i for i in ids if _usable(rv, i)]


def _kept_count(rv: _Review) -> int:
    return len(_kept_ids(rv, rv.data))


def _comic_view(rv: _Review) -> dict:
    return {"images": _comic_items(rv), "roles": list(ai_extract.COMIC_ROLES),
            "page_count": _kept_count(rv)}


def _first_heading(rv: _Review, data: dict) -> str:
    """The reviewed page's heading, as a direct import would write it: the
    chapter title, else the page's <title>, capped."""
    title = (data or {}).get("chapter_title") or rv.page.page_title or ""
    return title[:adaptive.MAX_TITLE_CHARS]


def _follow_view(rv: _Review):
    """The pages of a followed import, by id in reading order (0 = the
    reviewed first page, as last re-run): title, length and host only."""
    if not rv.follow_stop:
        return None
    pages = [{"id": 0, "title": _label(_first_heading(rv, rv.data)),
              "char_count": len(rv.data.get("content") or ""),
              "host": profiles.domain_of(rv.url)}]
    pages += [{"id": i, "title": _label(p.title), "char_count": len(p.text or ""),
               "host": profiles.domain_of(p.url)} for i, p in enumerate(rv.chain, 1)]
    return {"pages": pages, "stop": rv.follow_stop}


def review_view(drama_id: int, principal=None, local: bool = True) -> dict:
    """The review for a drama: 404 when there is none (or it expired, or
    it is PC-only and the request isn't from this PC)."""
    _require_drama(drama_id, principal)
    rv = _get(drama_id, local=local)
    out = {
        "kind": "extraction_review", "drama_id": rv.drama_id, "revision": rv.revision,
        "content_type": rv.kind, "why": rv.why, "display_url": shared.display_url(rv.url),
        "confidence": _confidence_view(rv.data), "report": _report_view(rv.report),
        "can_save_profile": bool(rv.rules), "novel": None, "comic": None,
        "follow": _follow_view(rv),
    }
    out[rv.kind] = _novel_view(rv) if rv.kind == "novel" else _comic_view(rv)
    return out


def _offered(value, allowed, what: str):
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise InvalidInputError(_NOT_OFFERED, details={"field": what})
    return value
