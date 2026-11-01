"""
services/sources_extraction_service.py -- the pasted-URL extraction extras
for the API (SO09, SO10): the optional AI-assisted
fallback engine and the Review extraction step.

SO09, the AI fallback. The extraction ladder (sources/adaptive.py) tries a
saved site profile, then deterministic extraction, and asks an LLM only
when those come back empty or ambiguous: one call per page, cached. The
API makes it opt-in per request (`use_ai`), with the engine picked from
the reference-capable ones (`ai_engines`), defaulting to the saved default
engine (Settings > Defaults, settings_service.get("default_engine")) when that
is one of them. The key is resolved here, on the PC, from .env; it is never
taken from, or returned to, the client, and a failure to build the engine
is reported without it (shared.redact_secrets). The route checks
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
scrubbed and URLs reduced to scheme+host+path, or the bare host when a
path segment looks like a credential (`shared.display_url`). A review of a page read through the saved
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
from engine_backends import shared, engine_registry
from services import drama_service
from services import page_import_limits as limits
from services import settings_service
from lib.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
)
from services.sources_search_service import JobFailed
from sources import adaptive, ai_extract, pipeline
from sources import novel_follow
from sources import store as src_store

_NO_ENGINE = "Pick an AI engine for the fallback."
_ENGINE_FAILED = "The AI engine could not be set up."


def ai_engines() -> list:
    """Engine names the AI fallback may use (those that take a reference
    prompt)."""
    return [e for e, cls in engine_registry.ENGINES.items()
            if getattr(cls, "supports_reference", False)]


def default_ai_engine() -> Optional[str]:
    """The saved default engine when the fallback can use it, else None."""
    name = settings_service.get("default_engine")
    return name if name in ai_engines() else None


def engines_view() -> dict:
    """{engines, free, default} for the picker. Names only: no key, no key
    status. `free`: the ones that need no `engines.paid`."""
    engines = ai_engines()
    return {"engines": engines, "default": default_ai_engine(),
            "free": [e for e in engines if e in engine_registry.FREE_ENGINES]}


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
        key, base_url = "local", settings_service.ollama_url()
    else:
        key, base_url = settings_service.resolve_key(name), None
        if not key:
            raise MissingKeyError(name)
    try:
        return engine_registry.get_engine(
            name, key, base_url=base_url,
            free_tier=name == "gemini" and settings_service.get("gemini_free_tier"))
    except DependencyUnavailableError:
        raise  # a missing package keeps its own 503 and package name
    except Exception as e:
        # Never the key: the message is redacted and the cause dropped.
        raise DependencyUnavailableError(
            shared.redact_secrets(f"{_ENGINE_FAILED} ({type(e).__name__})")) from None


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
_NO_REVIEW = "There's no extraction to review for this title. Import the link again."
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


def open_review(drama_id: int, kind: str, url: str, html: str, data: dict | None, report,
                why: str, candidates=(), pc_only: bool = False, chain=(),
                follow_stop: str = "", recovery: dict | None = None) -> bool:
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


# ----- novel ---------------------------------------------------------------


# ----- comic ---------------------------------------------------------------


def write_pages(drama_id: int, items, job_id: str | None = None, chapter: dict | None = None) -> tuple:
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


# ----- corrections ---------------------------------------------------------


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
            chars, done = 0, 0
            total = sum(n_pages for *_rest, n_pages in snapshot)
            for n, (text, heading, url, n_pages) in enumerate(snapshot):
                # Between chapters only: a chapter is appended whole or not at all.
                if n and background_jobs.is_cancel_requested(job_id):
                    # The review is gone; say what is already in the drama.
                    background_jobs.set_result(job_id, {
                        "kind": "review_import", "content_type": "novel", "char_count": chars,
                        "pages_imported": done, "cancelled": True})
                    background_jobs.update_progress(
                        job_id, 0.5 + 0.45 * done / total,
                        f"Cancelled after appending {done} of {total} pages.")
                    raise background_jobs.JobCancelled(job_id)
                if total > 1:
                    background_jobs.update_progress(job_id, 0.5 + 0.45 * done / total,
                                                    f"Saving page {done + 1} of {total}...")
                pipeline.save_novel_text(drama_id, text, append=True, heading=heading, url=url)
                chars += len(text)
                done += n_pages
            result = {"kind": "review_import", "content_type": "novel", "char_count": chars,
                      "pages_imported": total}
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
            raise InvalidInputError("Novel text imports into a novel title.")
        if rv.recovery and rv.recovery["chapter_id"] in src_store.imported_chapter_ids(
                rv.recovery["source"], rv.recovery["series_id"], drama_id):
            raise ConflictError("That chapter was imported since this review opened.")
        parts = [(data.get("content") or "", _first_heading(rv, data) or adaptive.title_from_url(rv.url),
                  rv.url, False)]
        parts += [(p.text or "", p.title or adaptive.title_from_url(p.url), p.url, p.part)
                  for p in rv.chain]
        chosen = set(_chosen_pages(rv, pages))
        if not all(parts[i][0].strip() for i in chosen):
            raise InvalidInputError("There's no chapter text to import.")
        # A chapter's later pages go in with it, as one chapter.
        snapshot = novel_follow.joined_chapters(
            [p if i in chosen else None for i, p in enumerate(parts)])
    else:
        if pages is not None:
            raise InvalidInputError(_NOT_OFFERED, details={"field": "pages"})
        if media not in imp.COMIC_MEDIA_TYPES:
            raise InvalidInputError("Comic pages import into a manhua, manga or manhwa title.")
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


# Defined in the modules below; re-exported so `services.sources_extraction_service.<name>` keeps working.
from services.sources_extraction_views import (  # noqa: E402,F401
    _label, _confidence_view, _report_view, _novel_base_rules, _containers, _exclusions,
    _headings, _links, _link_id_for, _novel_view, _comic_items, _usable, _kept_ids, _kept_count,
    _comic_view, _first_heading, _follow_view, review_view, _offered)
from services.sources_extraction_actions import (  # noqa: E402,F401
    rerun_novel, rerun_comic, _snapshot, save_profile, approve_profile)
