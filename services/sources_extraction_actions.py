"""services/sources_extraction_actions.py -- Re-running a held extraction review and saving or approving its profile.

Moved out of services/sources_extraction_service.py to keep it under the size ceiling. Import these names from
`services.sources_extraction_service`, which re-exports them at its end; nothing imports this module
first, because it needs `services.sources_extraction_service` fully defined.
"""

from services.service_errors import ConflictError, InvalidInputError
from services.sources_registry_service import scrub
from sources import adaptive, ai_extract, ai_extract_comic as axc, ai_extract_novel as axn, profiles
from services.sources_extraction_service import (MAX_PAGE_NUMBER, NUMBER_FROM, _LOCK, _NOT_OFFERED, _Review, _STALE, _get, _new_revision, _require_drama)
from services.sources_extraction_views import (_containers, _exclusions, _headings, _links, _comic_items, _usable, review_view, _offered)


def rerun_novel(drama_id: int, revision: str, content_selector, exclude_selectors=(),
                title_block=None, next_link=None, previous_link=None, number_from="title",
                principal=None, local: bool = True) -> dict:
    """Re-runs the extraction on the kept page with the chosen parts. 422
    for a choice the review didn't offer, or when the chosen parts give no
    text; 409 stale revision."""
    _require_drama(drama_id, principal)
    rv = _get(drama_id, revision, local)
    if rv.kind != "novel":
        raise InvalidInputError("This review is for comic pages.")
    sel = _offered(content_selector, {c[0] for c in _containers(rv)}, "content_selector")
    if sel is None:
        raise InvalidInputError("Pick the part of the page that holds the chapter text.")
    allowed_ex = {s for s, _t in _exclusions(rv, sel)}
    exclude = []
    for x in exclude_selectors or []:
        x = _offered(x, allowed_ex, "exclude_selectors")
        if x not in exclude:
            exclude.append(x)
    title_id = _offered(title_block, {b.id for b in _headings(rv)}, "title_block")
    link_ids = {l.id for l in _links(rv)}
    next_id = _offered(next_link, link_ids, "next_link")
    prev_id = _offered(previous_link, link_ids, "previous_link")
    if number_from not in NUMBER_FROM:
        raise InvalidInputError(_NOT_OFFERED, details={"field": "number_from"})
    rules = profiles.novel_rules_from_choices(rv.page, sel, exclude, title_id, next_id, prev_id,
                                              number_from)
    new, why = profiles.apply_novel_rules(rv.page, rules)
    if new is None:
        raise InvalidInputError(scrub(why) or "Those choices found no chapter text.")
    axn.validate_novel(new, rv.page)
    if not (new.get("content") or "").strip():
        raise InvalidInputError("Those choices found no chapter text.")
    with _LOCK:
        if rv.revision != revision:
            raise ConflictError(_STALE, details={"revision": rv.revision})
        rv.data, rv.rules, rv.revision = new, rules, _new_revision()
    return review_view(drama_id, principal, local)


def rerun_comic(drama_id: int, revision: str, images, principal=None, local: bool = True) -> dict:
    """Applies the person's roles and page numbers, each keyed by the image
    id the review gave it. Images not named keep their current role and
    page. 422 for an unknown id or role, a page number out of range, or an
    image that can't be a page (not downloaded, or not readable)."""
    _require_drama(drama_id, principal)
    rv = _get(drama_id, revision, local)
    if rv.kind != "comic":
        raise InvalidInputError("This review is for novel text.")
    current = {it["id"]: it for it in _comic_items(rv)}
    chosen = {}
    for item in images or []:
        cid, role, page = item.get("id"), item.get("role"), item.get("page", 0)
        if isinstance(cid, bool) or not isinstance(cid, int) or cid not in current:
            raise InvalidInputError(_NOT_OFFERED, details={"field": "images.id"})
        if role not in ai_extract.COMIC_ROLES:
            raise InvalidInputError(_NOT_OFFERED, details={"field": "images.role"})
        if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page <= MAX_PAGE_NUMBER:
            raise InvalidInputError(f"Page numbers go from 0 (not a page) to {MAX_PAGE_NUMBER}.")
        if role == "content" and not _usable(rv, cid):
            raise InvalidInputError("That image couldn't be downloaded or read, so it can't be a page.",
                                    details={"field": "images.role", "id": cid})
        chosen[cid] = (role, page)
    roles, order = {}, {}
    for cid, it in current.items():
        role, page = chosen.get(cid, (it["role"], it["page"]))
        if role == "content" and not _usable(rv, cid):
            role, page = "other", 0
        url = rv.candidates[cid].url
        roles[url] = role
        if role == "content" and page > 0:
            order[url] = page
    new = profiles.comic_data_from_roles(rv.page, rv.candidates, roles, order)
    axc.validate_comic(new, rv.page, adaptive.measured(rv.candidates))
    rules = profiles.infer_comic_rules(rv.candidates, new)
    with _LOCK:
        if rv.revision != revision:
            raise ConflictError(_STALE, details={"revision": rv.revision})
        rv.data, rv.rules, rv.revision = new, rules, _new_revision()
    return review_view(drama_id, principal, local)


def _snapshot(rv: _Review, revision: str) -> tuple:
    """(data, rules) as of `revision`, read under the lock so a re-run from
    another tab can't swap them in between (409 when it already has)."""
    with _LOCK:
        if rv.revision != revision:
            raise ConflictError(_STALE, details={"revision": rv.revision})
        return rv.data, rv.rules


def save_profile(drama_id: int, revision: str, principal=None) -> dict:
    """Saves the corrections as the site's profile (a new version; the
    previous one is kept for rollback). 422 when there are no corrections
    yet or the profile doesn't validate. PC-only route."""
    _require_drama(drama_id, principal)
    rv = _get(drama_id, revision)
    data, rules = _snapshot(rv, revision)
    if not rules:
        raise InvalidInputError("Re-run with your corrections first; those are what's saved.")
    try:
        v = profiles.save_version(profiles.domain_of(rv.url), rv.kind, rules, data,
                                  origin="correction", approved=True)
    except profiles.ProfileRejected as e:
        raise InvalidInputError(scrub(str(e))) from None
    return {"domain": profiles.domain_of(rv.url), "kind": rv.kind, "version": int(v["version"]),
            "replaces": v.get("replaces")}


def approve_profile(drama_id: int, revision: str, principal=None) -> dict:
    """Saves the profile candidate the extraction held for approval."""
    _require_drama(drama_id, principal)
    rv = _get(drama_id, revision)
    with _LOCK:                       # taken once: a second approve finds nothing
        pending = getattr(rv.report, "pending_profile", None)
        if pending:
            rv.report.pending_profile = None
    if not pending:
        raise InvalidInputError("There's no suggested profile waiting for approval.")
    try:
        v = adaptive.approve_pending(pending)
    except profiles.ProfileRejected as e:
        with _LOCK:
            rv.report.pending_profile = pending
        raise InvalidInputError(scrub(str(e))) from None
    return {"domain": profiles.domain_of(rv.url), "kind": rv.kind, "version": int(v["version"]),
            "replaces": v.get("replaces")}
