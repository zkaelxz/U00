"""
services/sources_extraction_service.py -- the pasted-URL extraction extras
for the API (SO09, SO10): the optional AI-assisted
fallback engine and the Review extraction step.

SO09, the AI fallback. The extraction ladder (sources/adaptive.py) tries a
saved site profile, then deterministic extraction, and asks an LLM only
when those come back empty or ambiguous: one call per page, cached. The
API makes it opt-in per request (`use_ai`), with the engine picked from
the reference-capable ones (`ai_engines`), defaulting to the saved default
engine (Settings > Defaults, settings_service.get_default_engine) when that
is one of them. The key is resolved here, on the PC, from .env; it is never
taken from, or returned to, the client, and a failure to build the engine
is reported without it (translate_engines.redact_secrets). The route checks
`engines.paid` for the named engine first (api.auth.require_engines_allowed).

SO10, Review extraction: a review step between the preview and the write.
When a URL import's extraction comes back unsure (low confidence), the
person asked to review first, or Sources diagnostics mode is on, the import
job writes nothing and opens a review for its drama instead
(`open_review`, one per drama, in this process only: at most MAX_REVIEWS,
each dropped after REVIEW_TTL seconds or once imported). The review keeps
the page the job already fetched; nothing here fetches anything. A comic
review's downloaded images are moved out of memory into its own folder
under the library (`<library>/source_review_tmp/`), removed when the review
ends (expiry, replacement, or once its import job finishes or fails);
folders older than the review expiry (left by an earlier process) are swept
whenever a review opens. Pages are prepared and written one at a time
(`write_pages`), so an import never holds more than one page's prepared
bytes on top of what it downloaded. The person
can then:

  * see the independent confidence per field (never the AI's own claim);
  * novel: pick the container that holds the chapter text, what to leave
    out, the chapter title, the next/previous-chapter links and where the
    chapter number comes from, and re-run (`rerun_novel`);
  * comic: mark each image's role and number the pages in reading order,
    and re-apply (`rerun_comic`);
  * save the corrections as the site's profile (`save_profile`) or approve
    a held profile candidate (`approve_profile`); both are settings-like
    writes, so their routes are PC-only;
  * import the result (`start_review_import`, the per-drama job).

A novel import that followed next-chapter links (`follow_pages`) opens a
review of every page it read: the first page is the one the corrections
above apply to; the followed pages are kept as their extracted text and
title only (no HTML), listed by id with their title, length and host, and
the person picks which to import (`pages`, ids, written in reading order).

Every choice is one of the options the review offered, named by an explicit
id (a block, link or image id, or a selector from the offered list), never
by list position, and never free text: a correction changes which parts of
the page are used, never the text or images themselves. Each change makes a
new `revision`; a request naming an older one is a 409, so a stale screen
can't import or save a result it didn't show. An image whose download
failed or that Pillow couldn't read can't be marked as a page. Text is
scrubbed and URLs reduced to scheme+host+path, with signed path segments
blanked (`display_url`). A review of a page read through the saved
signed-in browser is shown only at this PC (it can carry the account's
page chrome), and a run started from another device never auto-saves a
site profile (adaptive `hold_profiles`).
"""

import os
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field

from typing import Optional

import background_jobs
import comic_chapters
import db
import translate_engines
from services import drama_service
from services import page_import_limits as limits
from services import settings_service
from services.service_errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
)
from services.sources_registry_service import scrub, safe_url
from services.sources_search_service import JobFailed
from sources import adaptive, ai_extract, pipeline, profiles
from sources import store as src_store

_NO_ENGINE = "Pick an AI engine for the fallback."
_ENGINE_FAILED = "The AI engine could not be set up."


def ai_engines() -> list:
    """Engine names the AI fallback may use (those that take a reference
    prompt)."""
    return [e for e, cls in translate_engines.ENGINES.items()
            if getattr(cls, "supports_reference", False)]


def default_ai_engine() -> Optional[str]:
    """The saved default engine when the fallback can use it, else None."""
    name = settings_service.get_default_engine()
    return name if name in ai_engines() else None


def engines_view() -> dict:
    """{engines, free, default} for the picker. Names only: no key, no key
    status. `free`: the ones that need no `engines.paid`."""
    engines = ai_engines()
    return {"engines": engines, "default": default_ai_engine(),
            "free": [e for e in engines if e in translate_engines.FREE_ENGINES]}


def resolve_ai_engine_name(use_ai, engine) -> Optional[str]:
    """The engine name a request asks for, or None when the fallback is off.
    422 for an engine the fallback can't use, or none given and no usable
    saved default."""
    if not use_ai:
        return None
    if engine is None:
        engine = default_ai_engine()
        if engine is None:
            raise InvalidInputError(_NO_ENGINE, details={"allowed": ai_engines()})
    if not isinstance(engine, str) or engine not in ai_engines():
        raise InvalidInputError("That engine can't be used for the AI fallback.",
                                details={"allowed": ai_engines()})
    return engine


def build_ai_engine(name: Optional[str]):
    """The engine object for `name` (None -> None, the fallback stays off).
    The key is resolved on this PC and stays inside the engine object.
    503 when no key is configured or the engine isn't installed."""
    if name is None:
        return None
    if name == "ollama":
        key, base_url = "local", settings_service.resolve_key("ollama_url") or None
    else:
        key, base_url = settings_service.resolve_key(name), None
        if not key:
            raise MissingKeyError(name)
    try:
        return translate_engines.get_engine(
            name, key, base_url=base_url,
            free_tier=name == "gemini" and settings_service.get_gemini_free_tier())
    except ImportError:
        raise DependencyUnavailableError(
            f"The {name} engine isn't installed on this PC.") from None
    except Exception as e:
        # Never the key: the message is redacted and the cause dropped.
        raise DependencyUnavailableError(
            translate_engines.redact_secrets(f"{_ENGINE_FAILED} ({type(e).__name__})")) from None


# ---------------------------------------------------------------------------
# SO10: Review extraction
# ---------------------------------------------------------------------------

REVIEW_TTL = 30 * 60
MAX_REVIEWS = 4
MAX_PREVIEW_CHARS = 4000
MAX_LABEL = 80
MAX_SELECTOR_LEN = 300
MAX_HEADINGS = 20
MAX_LINKS = 80
MAX_PAGE_NUMBER = ai_extract.MAX_PAGES
NUMBER_FROM = ("title", "url")
_NO_REVIEW = "There's no extraction to review for this drama. Import the link again."
_STALE = "This review changed since it was loaded. Reload it and try again."
_NOT_OFFERED = "That choice isn't one this review offered."

# Why a review was opened, as shown on the screen.
WHY_LOW_CONFIDENCE = "low_confidence"
WHY_ASKED = "asked"
WHY_DIAGNOSTICS = "diagnostics"
WHY_FOLLOWED = "follow"
WHY_RECOVERY = "recovery"     # an adapter chapter the person confirmed AI help for


@dataclass
class _Review:
    drama_id: int
    kind: str                # "novel" | "comic"
    url: str                 # the full URL: never returned
    page: object             # ai_extract.PageModel of the fetched page
    data: dict
    report: object           # adaptive.ExtractionReport
    why: str
    candidates: list = field(default_factory=list)   # comic ImageCandidates
    rules: dict = None       # the corrections, as profile rules
    revision: str = ""
    created_at: float = 0.0
    pc_only: bool = False    # read through the signed-in browser: this PC only
    tmp_dir: str = ""        # comic: the images' bytes, one file per candidate id
    sizes: dict = field(default_factory=dict)        # candidate id -> bytes on disk
    chain: list = field(default_factory=list)        # novel: novel_follow.FollowedPage after the first
    follow_stop: str = ""    # why following stopped ("" = not a followed import)
    recovery: dict = None    # {source, series_id, chapter_id, title}: an adapter chapter


_REVIEWS = {}
# The chapter page an adapter read when its layout had changed, kept so the
# person's AI-recovery confirm needn't fetch it again:
# (drama_id, source, series_id, chapter_id) -> (created_at, url, html, title).
_LAYOUT_PAGES = {}
MAX_LAYOUT_PAGE_CHARS = 2_000_000
MAX_LAYOUT_PAGES_PER_DRAMA = 3
_LOCK = threading.Lock()


def _tmp_root() -> str:
    return os.path.join(db.LIBRARY_DIR, "source_review_tmp")


def _sweep_stale(now: float) -> None:
    """Removes review folders older than the review expiry: left by an
    earlier process (or one sharing the library). A live review's folder is
    never that old, so another process's open reviews are left alone."""
    try:
        names = os.listdir(_tmp_root())
    except OSError:
        return
    for name in names:
        path = os.path.join(_tmp_root(), name)
        try:
            if now - os.path.getmtime(path) > REVIEW_TTL:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _spill(rv) -> None:
    """Moves each candidate's downloaded bytes to a file in the review's
    own folder, so an open review holds no image bytes in memory. On a
    failed write (disk full) the partly written folder is removed and the
    error re-raised."""
    _sweep_stale(time.time())
    rv.tmp_dir = os.path.join(_tmp_root(), f"{rv.drama_id}_{secrets.token_hex(6)}")
    try:
        os.makedirs(rv.tmp_dir, exist_ok=True)
        for i, c in enumerate(rv.candidates):
            if c.content:
                with open(os.path.join(rv.tmp_dir, f"{i}.img"), "wb") as f:
                    f.write(c.content)
                rv.sizes[i] = len(c.content)
            c.content = b""
    except Exception:
        _discard(rv)
        raise


def _discard(rv) -> None:
    if rv is not None and rv.tmp_dir:
        shutil.rmtree(rv.tmp_dir, ignore_errors=True)


def _read(rv, i: int) -> bytes:
    with open(os.path.join(rv.tmp_dir, f"{i}.img"), "rb") as f:
        return f.read()


def diagnostics_mode() -> bool:
    return bool(src_store.get_setting("extraction_diagnostics"))


def review_reason(report, asked: bool):
    """Why a finished extraction should be reviewed before it is written,
    or None to write it straight away."""
    if report is not None and getattr(report, "needs_review", False):
        return WHY_LOW_CONFIDENCE
    if asked:
        return WHY_ASKED
    if diagnostics_mode():
        return WHY_DIAGNOSTICS
    return None


def _new_revision() -> str:
    return secrets.token_hex(8)


def _prune(now: float):
    for did in [d for d, r in _REVIEWS.items() if now - r.created_at > REVIEW_TTL]:
        _discard(_REVIEWS.pop(did, None))
    while len(_REVIEWS) > MAX_REVIEWS:
        oldest = min(_REVIEWS, key=lambda d: _REVIEWS[d].created_at)
        _discard(_REVIEWS.pop(oldest, None))


def stash_layout_page(drama_id: int, source: str, series_id: str, chapter_id: str,
                      url, html, title: str = ""):
    """Keeps the page of a chapter whose layout changed (nothing when the
    page is unknown or over MAX_LAYOUT_PAGE_CHARS), for take_layout_page."""
    if not url or not html or len(html) > MAX_LAYOUT_PAGE_CHARS:
        return
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _LAYOUT_PAGES.items() if now - v[0] > REVIEW_TTL]:
            del _LAYOUT_PAGES[k]
        _LAYOUT_PAGES[(int(drama_id), source, str(series_id), str(chapter_id))] = (
            now, url, html, title or "")
        mine = sorted((k for k in _LAYOUT_PAGES if k[0] == int(drama_id)),
                      key=lambda k: _LAYOUT_PAGES[k][0])
        for k in mine[:-MAX_LAYOUT_PAGES_PER_DRAMA]:
            del _LAYOUT_PAGES[k]
        while len(_LAYOUT_PAGES) > MAX_REVIEWS:
            del _LAYOUT_PAGES[min(_LAYOUT_PAGES, key=lambda k: _LAYOUT_PAGES[k][0])]


def take_layout_page(drama_id: int, source: str, series_id: str, chapter_id: str):
    """(url, html, title) kept by stash_layout_page, removed as it is
    taken; None when it is gone or expired."""
    with _LOCK:
        got = _LAYOUT_PAGES.pop((int(drama_id), source, str(series_id), str(chapter_id)), None)
    if got is None or time.time() - got[0] > REVIEW_TTL:
        return None
    return got[1:]


def review_open(drama_id: int) -> bool:
    with _LOCK:
        _prune(time.time())
        return int(drama_id) in _REVIEWS


def open_review(drama_id: int, kind: str, url: str, html: str, data: dict, report,
                why: str, candidates=(), pc_only: bool = False, chain=(),
                follow_stop: str = "", recovery: dict = None) -> bool:
    """Called by an import job instead of writing: keeps what it extracted
    for review. Replaces any earlier review for the drama. False when there
    is nothing to review (no result). `chain`: the pages read after this
    one by following next links; `follow_stop`: why following stopped."""
    if not data or kind not in ("novel", "comic"):
        return False
    rv = _Review(int(drama_id), kind, url, ai_extract.PageModel(html or "", url), data, report,
                 why, list(candidates), None, _new_revision(), time.time(), bool(pc_only),
                 chain=list(chain) if kind == "novel" else [], follow_stop=str(follow_stop or ""),
                 recovery=recovery)
    if kind == "comic":
        try:
            _spill(rv)
        except OSError:
            return False              # nothing kept; the job reports needs_review
    with _LOCK:
        _discard(_REVIEWS.get(rv.drama_id))
        _REVIEWS[rv.drama_id] = rv
        _prune(time.time())
    return rv.drama_id in _REVIEWS


def _get(drama_id: int, revision=None, local: bool = True) -> _Review:
    with _LOCK:
        _prune(time.time())
        rv = _REVIEWS.get(int(drama_id))
    if rv is None or (rv.pc_only and not local):
        raise NotFoundError(_NO_REVIEW)
    if revision is not None and revision != rv.revision:
        raise ConflictError(_STALE, details={"revision": rv.revision})
    return rv


def _require_drama(drama_id, principal):
    from services import sources_import_service as imp
    return imp.require_drama(drama_id, principal)


def drop_review(drama_id: int):
    with _LOCK:
        _discard(_REVIEWS.pop(int(drama_id), None))


def display_url(url):
    """scheme+host+path, with credential-shaped path segments blanked (a
    signed CDN path is a credential, as for the AI prompt)."""
    shown = safe_url(url)
    return ai_extract.SENSITIVE_PATH_SEGMENT.sub(r"\1[REDACTED]", shown) if shown else shown


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
            shown = display_url(value)
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


# ----- novel ---------------------------------------------------------------

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
        "links": [{"id": l.id, "text": _label(l.text), "url": display_url(l.url)}
                  for l in _links(rv)],
        "next_link": _link_id_for(rv, data.get("next_url")),
        "previous_link": _link_id_for(rv, data.get("previous_url")),
        "number_from": base.get("number_from") if base.get("number_from") in NUMBER_FROM else "title",
    }


# ----- comic ---------------------------------------------------------------

def _comic_items(rv: _Review) -> list:
    roles = {p["resource_url"]: p for p in rv.data.get("pages") or []}
    out = []
    for i, c in enumerate(rv.candidates):
        p = roles.get(c.url) or {}
        role = p.get("role") or "other"
        page = (p.get("index") + 1) if role == "content" and isinstance(p.get("index"), int) else 0
        out.append({"id": i, "display_url": display_url(c.url), "attr": _label(c.attr or "src"),
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


def write_pages(drama_id: int, items, job_id: str = None, chapter: dict = None) -> tuple:
    """Prepares and writes the pages one image at a time, in order, under
    the page rules (EXIF orientation applied, webtoon strips cut into
    pages): (pages added, [skipped candidates]). Each image's bytes are
    dropped as soon as its pages are written, so no more than one image's
    prepared pages are held at once. An image over a cap, or damaged, is
    skipped with its reason set, never failing the rest. `items`:
    (candidate, bytes or a callable returning them). With `job_id`, a
    cancel is honoured between images (pages already written stay).
    `chapter` (comic_chapters.chapter_ref) labels every page written."""
    added, skipped = 0, []
    for c, content in items:
        if job_id and background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        try:
            pages = limits.prepare_page(content() if callable(content) else content)
        except limits.ImageRejected as e:
            c.reject_reason = str(e)
            skipped.append(c)
            continue
        except OSError:
            c.reject_reason = "couldn't be read back for import"
            skipped.append(c)
            continue
        finally:
            c.content = b""          # the download is no longer needed
            content = None
        added += pipeline.add_page_images(drama_id, pages, chapter=chapter)
        del pages
    return added, skipped


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
        "content_type": rv.kind, "why": rv.why, "display_url": display_url(rv.url),
        "confidence": _confidence_view(rv.data), "report": _report_view(rv.report),
        "can_save_profile": bool(rv.rules), "novel": None, "comic": None,
        "follow": _follow_view(rv),
    }
    out[rv.kind] = _novel_view(rv) if rv.kind == "novel" else _comic_view(rv)
    return out


# ----- corrections ---------------------------------------------------------

def _offered(value, allowed, what: str):
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise InvalidInputError(_NOT_OFFERED, details={"field": what})
    return value


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
    ai_extract.validate_novel(new, rv.page)
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
    ai_extract.validate_comic(new, rv.page, adaptive.measured(rv.candidates))
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


# ----- import --------------------------------------------------------------

def _recovered_import(job_id: str, drama_id: int, text: str, heading: str, rv: _Review):
    """A reviewed adapter chapter goes in the way the adapter's own import
    writes one (recorded as imported, retry marker cleared)."""
    from sources.models import ChapterInfo
    rec = rv.recovery
    ch = ChapterInfo(rec["source"], rec["series_id"], rec["chapter_id"],
                     rec["title"] or heading, rv.url)
    error = pipeline.append_recovered_chapter(rec["source"], ch, drama_id, text)
    if error:
        err = {"status": 500, "code": "import_failed", "message": error}
        background_jobs.set_result(job_id, {"kind": "review_import", "content_type": "novel",
                                            "error": err})
        raise JobFailed(error)
    background_jobs.set_result(job_id, {"kind": "review_import", "content_type": "novel",
                                        "char_count": len(text), "pages_imported": 1})


def _review_import_job(job_id: str, drama_id: int, kind: str, snapshot, rv: _Review):
    try:
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        background_jobs.update_progress(job_id, 0.5, "Saving the reviewed result...")
        if rv.recovery:
            _recovered_import(job_id, drama_id, *snapshot[0][:2], rv)
            return
        if kind == "novel":
            chars = 0
            for n, (text, heading, url) in enumerate(snapshot):
                # Between pages only: a page is appended whole or not at all.
                if n and background_jobs.is_cancel_requested(job_id):
                    # The review is gone; say what is already in the drama.
                    background_jobs.set_result(job_id, {
                        "kind": "review_import", "content_type": "novel", "char_count": chars,
                        "pages_imported": n, "cancelled": True})
                    background_jobs.update_progress(
                        job_id, 0.5 + 0.45 * n / len(snapshot),
                        f"Cancelled after appending {n} of {len(snapshot)} pages.")
                    raise background_jobs.JobCancelled(job_id)
                if len(snapshot) > 1:
                    background_jobs.update_progress(job_id, 0.5 + 0.45 * n / len(snapshot),
                                                    f"Saving page {n + 1} of {len(snapshot)}...")
                pipeline.save_novel_text(drama_id, text, append=True, heading=heading, url=url)
                chars += len(text)
            result = {"kind": "review_import", "content_type": "novel", "char_count": chars,
                      "pages_imported": len(snapshot)}
        else:
            from services import sources_import_service as imp
            label = _first_heading(rv, rv.data) or adaptive.title_from_url(rv.url)
            n, skipped = write_pages(
                drama_id, ((rv.candidates[i], (lambda i=i: _read(rv, i))) for i in snapshot),
                job_id, chapter=comic_chapters.chapter_ref(None, label, "", rv.url))
            result = {"kind": "review_import", "content_type": "comic", "pages_added": n,
                      "skipped": imp.skipped_view(skipped), "skipped_count": len(skipped)}
        if kind == "novel" or result["pages_added"]:
            drama_service.set_source_url_once(drama_id, rv.url)
        background_jobs.set_result(job_id, result)
    finally:
        _discard(rv)                  # the review ended when its import started


def _chosen_pages(rv: _Review, pages) -> list:
    """The page ids to import, in reading order (None = every page). 422
    for an id the review didn't list, or none chosen."""
    count = 1 + len(rv.chain)
    if pages is None:
        return list(range(count))
    if not isinstance(pages, (list, tuple)):
        raise InvalidInputError(_NOT_OFFERED, details={"field": "pages"})
    for p in pages:
        if isinstance(p, bool) or not isinstance(p, int) or not 0 <= p < count:
            raise InvalidInputError(_NOT_OFFERED, details={"field": "pages"})
    if not pages:
        raise InvalidInputError("Pick at least one page to import.")
    return sorted(set(pages))


def start_review_import(drama_id: int, revision: str, principal=None,
                        local: bool = True, pages=None) -> dict:
    """Starts `sourceimport_<drama_id>`: writes the reviewed result (novel
    text appended to the raw-novel text, or the content images, in page
    order, added as pages under the page rules). `pages`: for a novel
    review, the ids of the pages to import (None = all), each appended
    under its own title in reading order whatever order they were sent in.
    The review closes once its import starts. 422 nothing to import, a page
    id the review didn't list, or the drama's media type no longer fits;
    409 stale revision or a job running for the drama."""
    from services import sources_import_service as imp
    drama = _require_drama(drama_id, principal)
    rv = _get(drama_id, revision, local)
    data, _rules = _snapshot(rv, revision)
    media = (drama.get("media_type") or "").lower()
    if rv.kind == "novel":
        if media not in imp.NOVEL_MEDIA_TYPES:
            raise InvalidInputError("Novel text imports into a novel drama.")
        if rv.recovery and rv.recovery["chapter_id"] in src_store.imported_chapter_ids(
                rv.recovery["source"], rv.recovery["series_id"], drama_id):
            raise ConflictError("That chapter was imported since this review opened.")
        parts = [(data.get("content") or "", _first_heading(rv, data) or adaptive.title_from_url(rv.url),
                  rv.url)]
        parts += [(p.text or "", p.title or adaptive.title_from_url(p.url), p.url)
                  for p in rv.chain]
        snapshot = [parts[i] for i in _chosen_pages(rv, pages)]
        if not all(text.strip() for text, _h, _u in snapshot):
            raise InvalidInputError("There's no chapter text to import.")
    else:
        if pages is not None:
            raise InvalidInputError(_NOT_OFFERED, details={"field": "pages"})
        if media not in imp.COMIC_MEDIA_TYPES:
            raise InvalidInputError("Comic pages import into a manhua, manga or manhwa drama.")
        kept = _kept_ids(rv, data)
        if not kept:
            raise InvalidInputError("No image is marked as a page.")
        snapshot = kept
    imp.require_idle(drama_id)
    job_id = imp.import_job_id(drama_id)
    start = imp.start_comic_job if rv.kind == "comic" else imp.start_job
    # The review ends with its import: it can't be imported (appended) twice.
    # Taken out first so an expiry can't delete its images under the job.
    with _LOCK:
        if _REVIEWS.get(int(drama_id)) is not rv:
            raise NotFoundError(_NO_REVIEW)
        if rv.revision != revision:
            raise ConflictError(_STALE, details={"revision": rv.revision})
        _REVIEWS.pop(int(drama_id), None)
    try:
        return start(job_id, _review_import_job, job_id, int(drama_id), rv.kind, snapshot, rv,
                     description="Import a reviewed extraction")
    except Exception:
        with _LOCK:                   # didn't start: the review stays open
            _REVIEWS.setdefault(int(drama_id), rv)
        raise


# ----- image previews ------------------------------------------------------

# The accepted page types (limits.ALLOWED_IMAGE_TYPES), as _measure names them.
_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}


def _image_type(c):
    return _MEDIA_TYPES.get((c.ext or "").lower())


def review_image(drama_id: int, candidate_id: int, principal=None, local: bool = True) -> tuple:
    """(bytes, media type) of one downloaded image in a comic review, for
    its thumbnail. Raster types Pillow read only (never SVG or HTML)."""
    _require_drama(drama_id, principal)
    rv = _get(drama_id, local=local)
    if rv.kind != "comic" or not 0 <= candidate_id < len(rv.candidates):
        raise NotFoundError("No such image in this review.")
    if not _usable(rv, candidate_id):
        raise NotFoundError("No such image in this review.")
    try:
        return _read(rv, candidate_id), _image_type(rv.candidates[candidate_id])
    except OSError:
        raise NotFoundError("No such image in this review.") from None
