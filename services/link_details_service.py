"""
services/link_details_service.py -- "Fill from link": what a link says about
a title (title, channel or author, stream or release date), read without
downloading anything, and the blank-only writes that use it.

`read_link` dispatches on the link, free paths only:
- a link a Sources adapter serves (a work or chapter page): that adapter's
  own series data, title, first author and summary, through its paced
  client under the pasted-URL caps and with no browser. The adapters expose
  no chapter dates, so this path never gives a date.
- a video or stream link (sources.front_door.is_video_url, or a video
  adapter): a yt-dlp metadata read (services/ytdlp_child, mode "link_info")
  in the killable child, behind the guarded egress proxy, with no cookies,
  a time limit and an output cap.
- anything else: kind "page", nothing fetched. The client may then offer the
  AI listing autofill (services/metadata_service), which costs money, as a
  separate press.
Every link first passes sources_url_service.check_public_url, the URL
download's own check. A view carries the link only as display_url (scheme,
host, path), never the raw link, a cookie path or an error's own text.

Writes never go over typed text: db.fill_blank_fields writes a column only
while it is still blank, in one statement. A series is only ever suggested,
except when the owner ticks it in `apply`.

The scan job SCAN_JOB_ID reads every title with a stored link and a blank
original title, release date, author or series, MAX_PARALLEL_PROBES at a
time with a randomized pause between starts, and writes nothing: its result
is the per-title preview the owner ticks from.
"""

import datetime
import math
import random
import shutil
import sqlite3
import threading
import time

import background_jobs
import db
import storage
from engine_backends import shared as engine_shared
from jobs import job_launch
from services import drama_service, library_service, ownership_service, ytdlp_child
from lib.errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError, ServiceError)
from services.sources_url_service import (PASTED_MAX_PAGE_BYTES, PASTED_REQUEST_DEADLINE,
                                          check_public_url)

PROBE_TIMEOUT_SECONDS = 45
PROBE_OUTPUT_CHARS = 200_000
SUMMARY_MAX = 2000
SCAN_JOB_ID = "link_scan"
MAX_PARALLEL_PROBES = 3
# Between two probe starts in a scan, so a library of links on one site does
# not look like a scraper to it.
SCAN_SPACING_SECONDS = (10.0, 20.0)
_NO_YTDLP = "Reading a video link needs yt-dlp, which isn't installed on this PC."
_FAILED = "Couldn't read that link. It may be private, region-locked or not supported."
_TIMED_OUT = "Reading the link took too long."
_NO_LINK = "This title has no link."
_PAGE_NOTE = ("This link is not a video or a supported source, so nothing was read. "
              "The AI autofill can read the page instead (it costs money).")
_SCAN_PAGE_NOTE = "Not a video or supported-source link; the scan reads only those."
_SERIES_TAKEN = ("A series with that name already exists and belongs to someone else or is shared "
                 "differently. Pick a series in Details instead.")
_sleep = time.sleep
_random = random.Random()


def _empty(kind: str, url: str) -> dict:
    return {"kind": kind, "title": "", "channel": "", "release_date": "", "is_live": False,
            "summary": "", "platform": "", "series_suggestion": "", "note": "",
            "display_url": engine_shared.display_url(url)}


def release_date_of(link: dict) -> str:
    """ISO date of the stream (its scheduled or actual start) or upload,
    "" when the link gives neither; never a guess."""
    stamp = link.get("release_timestamp")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp):
        try:
            return datetime.date.fromtimestamp(stamp).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    upload = link.get("upload_date") or ""
    if len(upload) == 8 and upload.isdigit():
        try:
            return datetime.date(int(upload[:4]), int(upload[4:6]), int(upload[6:])).isoformat()
        except ValueError:
            pass
    return ""


def _tidy(text, limit=300) -> str:
    text = " ".join("".join(c if c.isprintable() else " " for c in str(text or "")).split())
    return text[:limit]


def _video_fields(url: str, cancel) -> dict:
    """The child's {"link": ...} fields; raises a fixed-text error."""
    from services import egress_proxy
    tmp = storage.new_workdir("link_probe")
    result, end = {}, {}
    try:
        with storage.holding(tmp), egress_proxy.GuardedProxy() as proxy:
            events = ytdlp_child.run_live_probe(
                tmp, {"url": url, "proxy": proxy.url}, PROBE_TIMEOUT_SECONDS, cancel,
                PROBE_OUTPUT_CHARS, mode="link_info")
            try:
                for item in events:
                    if "event" in item:
                        result.update(item["event"])
                    else:
                        end = item
            finally:
                events.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if end.get("cancelled"):
        raise background_jobs.JobCancelled(SCAN_JOB_ID)
    if end.get("timed_out"):
        raise DependencyUnavailableError(_TIMED_OUT)
    if result.get("missing"):
        raise DependencyUnavailableError(_NO_YTDLP)
    link = result.get("link")
    if not isinstance(link, dict):
        raise DependencyUnavailableError(_FAILED)
    return link


def _video_view(url: str, cancel) -> dict:
    link = _video_fields(url, cancel)
    view = _empty("video", url)
    channel = _tidy(link.get("channel"))
    view.update(title=_tidy(link.get("title")), channel=channel,
                release_date=release_date_of(link), is_live=link.get("status") == "is_live",
                series_suggestion=channel)
    return view


def _adapter_for(url: str, cancel):
    """(adapter, series_id) for a work or chapter page a Sources adapter
    serves, else None. A video adapter's links go through yt-dlp instead."""
    from sources import registry
    from sources.models import ContentType
    cls = registry.adapter_class_for_url(url)
    if cls is None or ContentType.VIDEO.value in (cls.content_types or []):
        return None
    from services.sources_extension_service import require_url_not_extension_only
    require_url_not_extension_only(url)
    adapter = registry.find_for_url(url, cancel_check=cancel, max_page_bytes=PASTED_MAX_PAGE_BYTES,
                                    request_deadline=PASTED_REQUEST_DEADLINE)
    if adapter is None or not adapter.supports("get_series"):
        return None
    adapter.allow_browser = False
    parsed = adapter.parse_url(url)
    if parsed and parsed[0] == "series":
        return adapter, parsed[1]
    if parsed and parsed[0] == "chapter" and getattr(parsed[1], "series_id", None):
        return adapter, parsed[1].series_id
    return None


def _source_view(url: str, adapter, series_id) -> dict:
    from services.sources_registry_service import scrub
    from sources.http_transport import Cancelled
    from sources import detect
    from sources.models import SourceError
    try:
        info = adapter.get_series(series_id)
    except Cancelled:
        raise background_jobs.JobCancelled(SCAN_JOB_ID) from None
    except (SourceError, OSError, ValueError):
        # OSError covers the HTTP library's own errors; never their text.
        raise DependencyUnavailableError(_FAILED) from None
    view = _empty("source", url)
    authors = [a for a in (info.authors or []) if isinstance(a, str) and a.strip()]
    title = _tidy(scrub(detect.without_appended_translation(info.title or "")))
    view.update(title=title, channel=_tidy(scrub(authors[0])) if authors else "",
                summary=_tidy(scrub(info.description or ""), SUMMARY_MAX),
                platform=_tidy(adapter.display_name), series_suggestion=title,
                note="This source gives no release date.")
    return view


def read_link(url, cancel=lambda: False) -> dict:
    """What the link says, by the free path for its kind (module doc).
    422 a bad or private address, 503 the host doesn't resolve, yt-dlp is
    missing, or the read failed or timed out."""
    from sources import front_door
    url = check_public_url(url)
    found = _adapter_for(url, cancel)
    if found is not None:
        return _source_view(url, *found)
    if front_door.is_video_url(url) or _video_adapter(url):
        return _video_view(url, cancel)
    view = _empty("page", url)
    view["note"] = _PAGE_NOTE
    return view


def _video_adapter(url: str) -> bool:
    from sources import registry
    from sources.models import ContentType
    cls = registry.adapter_class_for_url(url)
    return cls is not None and ContentType.VIDEO.value in (cls.content_types or [])


# --- one title ---------------------------------------------------------------

def _stored_link(drama: dict) -> str:
    full = (drama.get("source_url_full") or drama.get("source_url") or "").strip()
    if not full.startswith(("http://", "https://")):
        raise InvalidInputError(_NO_LINK)
    return full


def _series_names() -> dict:
    return {s["id"]: s.get("name") or "" for s in db.list_series()}


def _current(drama: dict, series_names: dict) -> dict:
    series_name = series_names.get(drama.get("series_id"), "")
    return {"title_zh": drama.get("title_zh") or "", "author": drama.get("author") or "",
            "release_date": drama.get("release_date") or "", "series": series_name}


def _suggestions(view: dict) -> dict:
    """The link's value for each field the preview offers."""
    return {"title_zh": view["title"], "author": view["channel"],
            "release_date": view["release_date"], "series": view["series_suggestion"]}


def _editable_drama(principal, drama_id) -> dict:
    ownership_service.require_editable(principal, "drama", drama_id)
    # As the scan: the PC owner can edit everything, but another user's
    # private title is theirs to fill.
    if _others_private(principal, int(drama_id)):
        raise NotFoundError(f"No title with id {drama_id}.")
    drama = db.get_drama(int(drama_id))
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    return drama


def probe_title(drama_id, principal=None) -> dict:
    """"Refresh details from link" for one title: what is there beside what
    its stored link says. Writes nothing."""
    drama = _editable_drama(principal, drama_id)
    view = read_link(_stored_link(drama))
    return {"drama_id": int(drama_id), "link": view, "current": _current(drama, _series_names()),
            "suggested": _suggestions(view)}


_APPLY_FIELDS = ("title_zh", "author", "release_date", "series")


def apply(drama_id, values: dict, principal=None) -> dict:
    """Writes the ticked values, each only where the field is still blank
    (a series only while the title is in none). {"applied": [...],
    "kept": [...]}: kept fields had text by the time of the write."""
    if not isinstance(values, dict) or not set(values) <= set(_APPLY_FIELDS):
        raise InvalidInputError("Only title_zh, author, release_date and series can be filled.")
    clean = {}
    for name, value in values.items():
        if not isinstance(value, str):
            raise InvalidInputError(f"{name} must be text.")
        value = value.strip()
        if name == "release_date":
            value = drama_service.check_release_date(value)
        elif len(value) > drama_service.MAX_NAME_LEN or "\x00" in value:
            raise InvalidInputError(f"{name} is too long or not plain text.")
        if value:
            clean[name] = value
    _editable_drama(principal, drama_id)
    if clean.get("series"):
        series_id = _series_for(int(drama_id), clean["series"])
    applied = db.dramas.fill_blank_fields(int(drama_id), clean)
    if clean.get("series") and series_id is not None \
            and db.dramas.assign_series_if_none(int(drama_id), series_id):
        applied.append("series")
        drama_service.inherit_series_defaults_on_join(int(drama_id), series_id)
    return {"applied": applied, "kept": sorted(set(clean) - set(applied))}


def _series_for(drama_id: int, name: str):
    """The series a ticked suggestion puts the title in, or None when the
    title is already in one. The name comes from the site (a channel or
    work title), so it never joins a series someone else owns or one more
    or less private than the title: either it is new, made with the title's
    own owner and privacy so nothing becomes visible that was not, or it is
    the owner's own series with the same privacy; anything else is 409."""
    row = db.get_item_ownership("drama", drama_id) or {}
    if row.get("series_id") is not None:
        return None
    owner, private = row.get("owner_user_id"), bool(row.get("is_private"))
    existing = db.get_series_id_by_name(name)
    if existing is not None:
        series = db.get_item_ownership("series", existing) or {}
        if series.get("owner_user_id") != owner or bool(series.get("is_private")) != private:
            raise ConflictError(_SERIES_TAKEN)
        return existing
    try:
        return db.create_series(name, owner_user_id=owner, is_private=private)
    except sqlite3.IntegrityError:
        raise ConflictError(_SERIES_TAKEN) from None


def fill_after_download(drama_id: int, link: dict) -> list:
    """Blank author and release date from a downloaded video's own metadata
    (the title already follows the download's own rule)."""
    if not isinstance(link, dict):
        return []
    return db.dramas.fill_blank_fields(drama_id, {"author": _tidy(link.get("channel")),
                                           "release_date": release_date_of(link)})


def title_for_new(url, at_pc: bool) -> tuple:
    """(title, how) for a title created from a link with no name typed: the
    link's own title read by a free path ("link"), else the link's host as
    a placeholder ("placeholder"). Only a request at the PC reads the link."""
    url = check_public_url(url)
    if at_pc:
        try:
            title = read_link(url)["title"]
        except (ServiceError, background_jobs.JobCancelled):
            title = ""
        if title:
            return title, "link"
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return (host or "Untitled link")[:drama_service.MAX_NAME_LEN], "placeholder"


def create_with_link(fields: dict, url, at_pc: bool, principal=None) -> dict:
    """drama_service.create_drama, then the link stored as any import's.
    With no name typed the title is title_for_new's, and the detail's
    "title_from_link" says where it came from ("link" or "placeholder")."""
    url = check_public_url(url)
    how = None
    if not (str(fields.get("title_en") or "").strip() or str(fields.get("title_zh") or "").strip()):
        fields["title_zh"], how = title_for_new(url, at_pc)
    detail = drama_service.create_drama(**fields, principal=principal)
    drama_service.set_source_url_once(detail["id"], url)
    detail = dict(library_service.get_library_drama(detail["id"]),
                  preset_defaults=detail.get("preset_defaults"))
    detail["title_from_link"] = how
    return detail


# --- the scan ----------------------------------------------------------------

_scan_lock = threading.Lock()


def _blank_fields(drama: dict) -> list:
    blanks = [f for f in ("title_zh", "author", "release_date")
              if not str(drama.get(f) or "").strip()]
    if drama.get("series_id") is None:
        blanks.append("series")
    return blanks


def _others_private(principal, drama_id) -> bool:
    row = db.get_item_ownership("drama", drama_id) or {}
    owner = row.get("owner_user_id")
    private = row.get("is_private") or row.get("series_is_private")
    return bool(private) and owner is not None and owner != ownership_service.user_id(principal)


def scan_candidates(principal=None) -> list:
    """Ids of the titles the scan reads: a stored link, at least one blank
    field, editable by `principal`, and not another user's private title."""
    ids = []
    for drama in db.list_dramas():
        if not str(drama.get("source_url") or "").strip() or not _blank_fields(drama):
            continue
        if _others_private(principal, drama["id"]) \
                or not ownership_service.can_edit_drama(principal, drama["id"]):
            continue
        ids.append(drama["id"])
    return ids


def _scan_row(drama_id: int, cancel, series_names: dict) -> dict:
    drama = db.get_drama(drama_id) or {}
    row = {"drama_id": drama_id, "name": drama.get("title_en") or drama.get("title_zh") or "",
           "display_url": engine_shared.display_url(drama.get("source_url")),
           "blank": _blank_fields(drama), "current": _current(drama, series_names), "suggested": None,
           "note": ""}
    try:
        url = check_public_url(_stored_link(drama))
        from sources import front_door
        found = _adapter_for(url, cancel)
        if found is not None:
            view = _source_view(url, *found)
        elif front_door.is_video_url(url) or _video_adapter(url):
            view = _video_view(url, cancel)
        else:
            row["note"] = _SCAN_PAGE_NOTE
            return row
    except ServiceError as exc:
        row["note"] = exc.message
        return row
    row["suggested"] = _suggestions(view)
    row["note"] = view["note"]
    return row


def _scan_job(job_id: str, ids: list):
    cancel = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    rows, lock = {}, threading.Lock()
    series_names = _series_names()
    slots = threading.Semaphore(MAX_PARALLEL_PROBES)
    threads = []

    def work(drama_id):
        try:
            if cancel():
                return
            try:
                row = _scan_row(drama_id, cancel, series_names)
            except background_jobs.JobCancelled:
                return
            except Exception:
                row = {"drama_id": drama_id, "suggested": None, "note": _FAILED}
            with lock:
                rows[drama_id] = row
                done = len(rows)
            background_jobs.update_progress(job_id, done / max(len(ids), 1),
                                            f"Checked {done} of {len(ids)} titles")
        finally:
            slots.release()

    background_jobs.update_progress(job_id, 0.0, f"Checking {len(ids)} titles...")
    for n, drama_id in enumerate(ids):
        if n:
            _wait(job_id, _random.uniform(*SCAN_SPACING_SECONDS))
        while not slots.acquire(timeout=0.5):
            if cancel():
                break
        if cancel():
            break
        t = threading.Thread(target=work, args=(drama_id,), daemon=True, name="link-scan")
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    background_jobs.set_result(job_id, {"kind": "link_scan", "total": len(ids),
                                        "titles": [rows[i] for i in ids if i in rows]})
    if cancel():
        raise background_jobs.JobCancelled(job_id)
    background_jobs.update_progress(job_id, 1.0, f"Checked {len(rows)} titles.")


def _wait(job_id: str, seconds: float) -> None:
    """Sleeps in short steps so Cancel ends the pause too."""
    end = time.monotonic() + seconds
    while not background_jobs.is_cancel_requested(job_id):
        left = end - time.monotonic()
        if left <= 0:
            return
        _sleep(min(left, 0.5))


def start_scan(principal=None) -> dict:
    """Starts SCAN_JOB_ID over scan_candidates; 409 while one runs."""
    with _scan_lock:
        if background_jobs.is_running(SCAN_JOB_ID):
            raise ConflictError("A link check is already running.")
        ids = scan_candidates(principal)
        # No clear_job first: start_job replaces a finished entry under its
        # own lock, and clearing a force-stopped scan whose thread still runs
        # would drop its cancel flag.
        if not job_launch.start_job(SCAN_JOB_ID, _scan_job, SCAN_JOB_ID, ids,
                                    description="Check old titles from links", line_fields=()):
            raise ConflictError("A link check is already running.")
    return {"job_id": SCAN_JOB_ID, "total": len(ids)}


def scan_result() -> dict:
    """The last scan's state and per-title preview (in this process only)."""
    status = background_jobs.get_status(SCAN_JOB_ID) or {}
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    return {"job_id": SCAN_JOB_ID, "status": status.get("status") or "none",
            "total": result.get("total", 0), "titles": result.get("titles") or []}
