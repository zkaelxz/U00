"""
services/extension_bridge_service.py -- what the extension's requests do once they are
accepted, shared by the PC-only bridge (services/page_server.py, the shared loopback
token, the local owner) and the device-token routes
(api/routers/extension_bridge_routes.py, one household member).

`principal` is None for the PC's own token. A member's principal limits the
title list to what they can see and sends only into a title they can edit.
"""

import contextlib
import contextvars
import copy
import hashlib
import json
import os
import queue
import re
import threading
import time
from collections import OrderedDict

import db
import page_capture_checks
from services import page_server
import page_turns
from services import ownership_service, quick_read_cache, quick_read_fetch
from lib.errors import ServiceError


def parse_json(body: bytes) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise page_server.EndpointError(400, "the request body was not valid JSON") from None
    if not isinstance(payload, dict):
        raise page_server.EndpointError(400, "the request body must be a JSON object")
    return payload


def engine_name_if_ready():
    """The configured engine's name when it can run, else None."""
    config = page_server.get_translation_config()
    return (config.get("engine") or "").strip() if page_server._build_engine(config) is not None else None


def _user_id(principal):
    return (principal or {}).get("user_id")


def require_drama_access(principal, drama_id) -> None:
    """A device token's member sends only into a title they can edit; the
    refusal is the same 404 as a missing title so ids are not probed."""
    if principal is None or not drama_id:
        return
    try:
        allowed = ownership_service.can_edit_drama(principal, int(drama_id))
    except (TypeError, ValueError, OverflowError, ServiceError):
        # ServiceError: an id past sqlite's range (ownership_service._item_id);
        # OverflowError: a JSON 1e999 parses as inf.
        allowed = False
    if not allowed:
        raise page_server.EndpointError(404, "no such drama")


_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "extension", "manifest.json")


def bundled_extension_version() -> str | None:
    """Version of the extension files this app ships, so a copy loaded from an
    older download can say it is stale (a browser never updates an unpacked one)."""
    try:
        with open(_MANIFEST, encoding="utf-8") as f:
            return str(json.load(f)["version"])
    except (OSError, ValueError, KeyError):
        return None


def health_payload(principal=None, app_port=None) -> dict:
    import db
    dramas = db.list_dramas()
    if principal is not None:
        visible = set(ownership_service.filter_visible_drama_ids(
            principal, [d["id"] for d in dramas]))
        dramas = [d for d in dramas if d["id"] in visible]
    return {
        "ok": True,
        "app": "Baihe Studio",
        "extension_version": bundled_extension_version(),
        # The PC's "Open in Baihe" link must follow BAIHE_API_PORT; a member
        # opens the household address they typed, so the PC passes its port and a member's call does not.
        **({"app_port": app_port} if app_port is not None else {}),
        "engine_configured": page_server._build_engine(page_server.get_translation_config()) is not None,
        # The extension batches a whole chapter to this, so the
        # number is stated once, here, and cannot drift from its copy.
        "max_images_per_request": page_server.MAX_IMAGES_PER_REQUEST,
        # `title_en or title_zh` is the app's own display-title
        # convention, not a new one.
        "dramas": [{"id": d["id"],
                    "title": (d.get("title_en") or d.get("title_zh")
                              or f"drama #{d['id']}"),
                    "media_type": d.get("media_type") or "",
                    "source_language": d.get("source_language") or ""}
                   for d in dramas],
    }


def validate_image_batch(route_is_single: bool, images) -> None:
    if not isinstance(images, list) or not images:
        raise page_server.EndpointError(400, "at least one image is required")
    if route_is_single and len(images) != 1:
        raise page_server.EndpointError(400, "/page takes exactly one image; use /pages for more")
    if len(images) > page_server.MAX_IMAGES_PER_REQUEST:
        raise page_server.EndpointError(413, f"at most {page_server.MAX_IMAGES_PER_REQUEST} images per request")


def run_text(payload, principal=None) -> dict:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise page_server.EndpointError(400, "text is required")
    if len(text) > page_server.MAX_TEXT_CHARS:
        raise page_server.EndpointError(413, f"text is longer than {page_server.MAX_TEXT_CHARS} characters")
    source_language = str(payload.get("source_language") or "zh")
    target_language = str(payload.get("target_language") or "en")
    if (source_language not in page_server.ALLOWED_TEXT_LANGUAGES
            or target_language not in page_server.ALLOWED_TEXT_LANGUAGES):
        raise page_server.EndpointError(400, "source/target language must be one of zh, ja, ko, en")
    if source_language == target_language:
        raise page_server.EndpointError(400, "source and target language must differ")
    if "en" not in (source_language, target_language):
        raise page_server.EndpointError(400, "one of source/target language must be English -- the same "
                                 "limit the Translate page has")
    store = bool(payload.get("store", True))
    return page_server.translate_text_block(text, source_language, target_language, store=store,
                                user_id=_user_id(principal), member=principal is not None)


def _record_chapter(drama_id, results: list, source_url: str, run_id) -> None:
    """Files the pages this capture newly saved under their chapter, so the reader's chapter list
    shows them instead of "Chapter unknown". Never raises: a label is a convenience and must
    not fail the import."""
    import db
    import comic_chapters
    from services import extension_title_service
    try:
        page_ids = [r["page_id"] for r in results
                    if r.get("stored") and r.get("page_id") and not r.get("already_stored")]
        ref = extension_title_service.capture_chapter_ref(source_url, run_id) if page_ids else None
        if ref is None:
            return
        pages = [db.get_page(pid, int(drama_id)) for pid in page_ids]
        comic_chapters.record_pages(int(drama_id), ref, [p["filename"] for p in pages if p])
    except Exception as exc:
        page_capture_checks.log_page_failure(exc)


def _record_run_notes(result: dict) -> None:
    """Keeps on a newly saved page what the capture said about it, so a page that came back with
    no text (an OCR miss, or art with no lettering) can be told apart from one never reached."""
    from services import scanlate_pages_service as pages_svc
    import db
    page_id = result.get("page_id")
    if not (result.get("stored") and page_id) or result.get("already_stored"):
        return
    notes = [(n[0], n[-1]) for n in result.get("notes") or []]
    seconds = result.get("seconds") or {}
    if seconds:
        notes.append(("info", "Time: read text " + f"{seconds.get('read', 0)}s, translate "
                      f"{seconds.get('translate', 0)}s" +
                      (f", typeset {seconds['typeset']}s." if "typeset" in seconds else ".")))
    if not result.get("regions"):
        notes.append(("warning", "No text was found on this page when the extension saved it."))
    if notes:
        db.update_page(int(page_id), run_notes=pages_svc.notes_to_json(notes))


# A phone screen, from a small one to a tablet; anything else is ignored rather than refused, so
# an odd value only costs the smaller download.
_TYPESET_WIDTHS = range(320, 4097)


def _typeset_max_width(raw) -> int | None:
    """The width the redrawn page is scaled down to: the phone's screen width, which is all it
    can show, so the rest of a full-size page is only download time."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw in _TYPESET_WIDTHS:
        return raw
    return None


def _typeset_style(raw):
    """The look settings the popup sent, or None for the plain render. A bad value falls back
    to its default rather than refusing the page: it is a preference, not data."""
    if not isinstance(raw, dict):
        return None
    from scanlate import scanlate_fonts
    return scanlate_fonts.TypesetStyle(
        min_font=raw.get("min_font"), stroke=raw.get("stroke") is True,
        align=str(raw.get("align") or "auto"), erase_grow=raw.get("erase_grow"),
        bubble_fill=raw.get("bubble_fill"), font=str(raw.get("font") or "default"))


def _typeset_preview(drama_id: int, page: dict) -> str:
    """The page's typeset image as a data: URL, or "" when it cannot be sent."""
    from services import comic_view_service
    found = comic_view_service.safe_file(drama_id, page.get("rendered_filename"))
    if found is None:
        return ""
    return page_capture_checks.jpeg_data_url(found[0])


def _typeset_stored_page(result: dict, drama_id, member: bool) -> None:
    """Cleans and typesets a page the capture just saved, so Typeset is already on when the
    title opens, and attaches the result for the extension to draw. A page that already has a
    typeset image is left alone: it was drawn from the same regions, and a re-capture must not
    redo work or replace what was fixed in Scanlate. A failure keeps the saved page and its
    text and says so in the notes."""
    import db
    from services import scanlate_render_service as render_svc
    page_id = result.get("page_id")
    if not (result.get("stored") and page_id and drama_id):
        return
    page = db.get_page(int(page_id), int(drama_id))
    if page is None:
        return
    notes = []
    if not page.get("rendered_filename"):
        try:
            rendered = bool(render_svc.render_page(int(drama_id), int(page_id), notes)["rendered"])
        except Exception as e:
            page_capture_checks.log_page_failure(e)
            result["typeset"] = False
            # The detail (a path, a model name) stays in the log; only the PC's owner sees it.
            result.setdefault("notes", []).append(
                ["warning", "typeset failed: " + ("the page was kept" if member else
                 f"{type(e).__name__}; the page was kept")])
            return
        result["typeset"] = rendered
        page = db.get_page(int(page_id), int(drama_id)) or page
    else:
        result["typeset"] = True
    if result["typeset"]:
        try:
            preview = _typeset_preview(int(drama_id), page)
        except Exception as e:
            page_capture_checks.log_page_failure(e)
            preview = ""
        if preview:
            result["typeset_image"] = preview
    result.setdefault("notes", []).extend([n[0], n[-1]] for n in notes if not member)


# A cancelled batch keeps working on the app's side after the extension drops its connection, so
# the extension names the run (a random id it made up) and says when to stop. Ids are short-lived
# and unguessable, so remembering them for a while costs nothing and cannot be used to stop
# anyone else's work.
_RUN_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_CANCELLED_TTL_SECONDS = 3600
_MAX_CANCELLED_RUNS = 1000
_cancelled_runs: dict[str, float] = {}
_cancelled_lock = threading.Lock()


def _run_id(payload) -> str:
    value = (payload or {}).get("run_id")
    return value if isinstance(value, str) and _RUN_ID.match(value) else ""


def cancel_run(payload) -> dict:
    """POST /cancel: the remaining pages of a batch carrying this run id are not worked on."""
    run_id = _run_id(payload)
    if not run_id:
        raise page_server.EndpointError(400, "run_id must be 8 to 64 letters, digits or dashes")
    now = time.monotonic()
    with _cancelled_lock:
        for key in [k for k, at in _cancelled_runs.items() if now - at > _CANCELLED_TTL_SECONDS]:
            del _cancelled_runs[key]
        if len(_cancelled_runs) >= _MAX_CANCELLED_RUNS:
            del _cancelled_runs[min(_cancelled_runs, key=lambda k: _cancelled_runs[k])]
        _cancelled_runs[run_id] = now
    return {"cancelled": True}


MAX_SUMMARY_CHARS = 2000
_URL_IN_SUMMARY = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


def record_capture_summary(payload, principal=None) -> dict:
    """POST /capture-summary: the closing line of a chapter capture, written to the app log so it
    outlives the popup message. Paths, link queries and key-like text are removed first (the
    line can carry a failure reason), and only the title's id is logged, never its name."""
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        raise page_server.EndpointError(400, "summary must be text")
    drama_id = payload.get("drama_id")
    require_drama_access(principal, drama_id)
    from applog import get_logger
    from services import scanlate_pages_service
    from engine_backends.shared import display_url
    # A failure reason can quote a link whose path holds a token: keep only what
    # display_url allows (scheme, host and a path that doesn't look like a secret).
    one_line = _URL_IN_SUMMARY.sub(lambda m: display_url(m.group(0)) or "[url]", " ".join(summary.split()))
    text = scanlate_pages_service.clean_note(one_line)[:MAX_SUMMARY_CHARS]
    get_logger().info("Extension capture (title %s%s): %s", int(drama_id) if isinstance(drama_id, int) else "?",
                      ", member" if principal is not None else "", text)
    if isinstance(drama_id, int) and not isinstance(drama_id, bool):
        try:
            if db.get_drama(drama_id):
                db.add_capture_note(drama_id, text)
        except Exception:
            # The note on the title is a nicety: the capture itself is already saved and logged.
            get_logger().warning("Could not keep the capture note for title %s", drama_id, exc_info=True)
    return {"logged": True}


def _run_cancelled(run_id: str) -> bool:
    with _cancelled_lock:
        return bool(run_id) and run_id in _cancelled_runs


def _decode_images(images, allow_fetch: bool = False) -> list:
    import base64
    decoded = []
    for image in images:
        if not isinstance(image, dict):
            raise page_server.EndpointError(400, "each image must be a JSON object")
        # Checked before decoding so an oversized payload never allocates
        # the full decoded copy.
        data = image.get("data") or ""
        fetch_url = image.get("fetch_url")
        if not data and fetch_url:
            if not allow_fetch:
                # A saved send is an import, which a member needs sources.import for: the PC only
                # downloads the pictures of a no-save Quick read.
                raise page_server.EndpointError(400, "picture addresses are only read for Quick read, "
                                                     "so send the picture itself")
            # The phone sent the address instead of the picture: the PC downloads it (see _prefetch).
            decoded.append({"key": str(image.get("key") or ""),
                            "url": page_capture_checks.short_url(image.get("url") or fetch_url),
                            "content_type": "", "content": None, "fetch_url": str(fetch_url)})
            continue
        if isinstance(data, (str, bytes)) and len(data) * 3 // 4 > page_server.MAX_IMAGE_BYTES:
            raise page_server.EndpointError(413, f"image is larger than {page_server.MAX_IMAGE_BYTES // (1024 * 1024)}MB")
        try:
            content = base64.b64decode(data, validate=True)
        except Exception:
            raise page_server.EndpointError(400, "an image's data was not valid base64") from None
        decoded.append({
            "key": str(image.get("key") or ""),
            # A `data:` or `blob:` image's "URL" is the image itself,
            # which would otherwise be echoed back whole in every
            # response (a real 12-image send measured megabytes of
            # pure echo). It's only ever shown to a person, so it is
            # capped here rather than carried in full.
            "url": page_capture_checks.short_url(image.get("url")),
            "content_type": str(image.get("content_type") or ""),
            "content": content,
        })
    return decoded


# Quick read answers by image bytes: going back a page, or a phone sending the same chapter again,
# answers at once instead of paying for the page twice. In memory only, and small, because each
# answer carries its redrawn page (up to about 1.5 MB).
QUICK_READ_CACHE_PAGES = 20
_quick_read_cache: "OrderedDict[str, dict]" = OrderedDict()
_quick_read_cache_lock = threading.Lock()


def _glossary_version(drama_id) -> str:
    """Changes when the terms a page is translated with change (the title's glossary and the
    comic glossary), so an edited glossary is never answered from the cache."""
    from services import comic_glossary_service
    drama = db.get_drama(int(drama_id)) if drama_id else None
    terms = db.list_run_glossary_terms(drama) if drama else []
    return quick_read_cache.make_key(sorted(terms, key=lambda t: str(t.get("term_original"))),
                                     comic_glossary_service.version())[:16]


def _quick_read_key(image: dict, job: dict) -> str | None:
    """None for a saved send: the library is that send's memory, and it must store the page."""
    if job["store"]:
        return None
    config = page_server.get_translation_config()
    # No member in the key: a page one household member read is the same answer for the next.
    return quick_read_cache.make_key("bytes", hashlib.sha256(image["content"]).hexdigest(),
                                     *_key_settings(job, config))


def _key_settings(job: dict, config: dict) -> list:
    return [job["drama_id"], job["source_language"], job["typeset"],
            json.dumps(job["typeset_style"], sort_keys=True, default=str), job["typeset_max_width"],
            job["glossary_version"], config.get("engine"), config.get("model")]


def _quick_read_url_key(image: dict, job: dict) -> str | None:
    """The key a picture address answers to before anything is downloaded."""
    if job["store"] or not image.get("fetch_url") or image.get("fetch_cookie"):
        # A page fetched with someone's cookie may be one only they can see, so it is never
        # looked up or remembered by address; the bytes key still serves a later upload.
        return None
    return quick_read_cache.make_key("url", image["fetch_url"], image.get("fetch_origin", ""),
                                     *_key_settings(job, page_server.get_translation_config()))


def _disk_cached(key: str | None) -> dict | None:
    hit = quick_read_cache.get(key)
    if hit and "alias" in hit:
        hit = quick_read_cache.get(hit["alias"])
    if not hit or "alias" in hit:
        return None
    return {**hit, "cached": True, "seconds": {}}


def _cached_quick_read(key: str | None) -> dict | None:
    if key is None:
        return None
    with _quick_read_cache_lock:
        hit = _quick_read_cache.get(key)
        if hit is None:
            return None
        _quick_read_cache.move_to_end(key)
        return {**copy.deepcopy(hit), "cached": True, "seconds": {}}


def _remember_quick_read(key: str | None, result: dict) -> None:
    """Only a complete answer is kept, so a page that failed or came back partly empty is tried
    again next time rather than repeating its failure."""
    if key is None or result.get("untranslated_lines") or any(
            n and n[0] == "warning" for n in result.get("notes") or []):
        return
    with _quick_read_cache_lock:
        _quick_read_cache[key] = copy.deepcopy(result)
        _quick_read_cache.move_to_end(key)
        while len(_quick_read_cache) > QUICK_READ_CACHE_PAGES:
            _quick_read_cache.popitem(last=False)


def _remember_on_disk(key: str | None, url_key: str | None, result: dict) -> None:
    """Same completeness rule as the in-memory cache; the address key only points at the bytes key,
    so the redrawn page is written once."""
    if key is None or result.get("untranslated_lines") or any(
            n and n[0] == "warning" for n in result.get("notes") or []):
        return
    quick_read_cache.put(key, result)
    if url_key:
        quick_read_cache.put(url_key, {"alias": key})


def _prefetch(decoded: list, payload: dict, job: dict, member=None) -> list:
    """Fills in the pictures the phone named by address. A picture whose answer is already cached is
    never downloaded; one that cannot be downloaded is dropped from the run and returned as
    {"key","url","reason"}, so the phone uploads just those."""
    from lib import url_guard
    referer = str(payload.get("fetch_referer") or "")
    wanted = {}
    for index, image in enumerate(decoded):
        if image["content"] is not None:
            continue
        # Part of the address key: a hotlink-protected CDN answers differently by referer.
        image["fetch_origin"] = url_guard.referer_origin(referer)
        image["fetch_cookie"] = bool(payload.get("fetch_cookie"))
        hit = _disk_cached(_quick_read_url_key(image, job))
        if hit is not None:
            image["cached_result"] = hit
        else:
            wanted[index] = image["fetch_url"]
    options = {"referer": str(payload.get("fetch_referer") or ""),
               "user_agent": str(payload.get("fetch_user_agent") or ""),
               "cookie": str(payload.get("fetch_cookie") or "")}
    fetched = quick_read_fetch.fetch_all(wanted, options, page_server.MAX_IMAGE_BYTES, member=member)
    unfetched = []
    for index, (content, reason) in fetched.items():
        image = decoded[index]
        if content is None:
            unfetched.append({"key": image["key"], "url": image["url"], "reason": reason})
            image["dropped"] = True
            continue
        image["content"] = content
        sniffed, _ = page_capture_checks.sniff_image(content)
        if sniffed is None:
            unfetched.append({"key": image["key"], "url": image["url"], "reason": quick_read_fetch.NOT_IMAGE})
            image["dropped"] = True
    decoded[:] = [image for image in decoded if not image.get("dropped")]
    return unfetched


def _log_page_time(result: dict, job: dict, started: float) -> None:
    from applog import get_logger
    seconds = result.get("seconds") or {}
    get_logger().info("Extension page (%s%s): %.2fs in all; read %ss, translate %ss, typeset %ss; "
                      "%d regions", "saved" if job["store"] else "quick read",
                      ", from cache" if result.get("cached") else "", time.monotonic() - started,
                      seconds.get("read", "-"), seconds.get("translate", "-"),
                      seconds.get("typeset", "-"), len(result.get("regions") or []))


def _translate_one(image: dict, job: dict, turn):
    """One page of a request as (outcome, value): ("page", result), ("failed", entry),
    ("fatal", entry) when every later page would fail the same way, or ("stopped", None)."""
    started = time.monotonic()
    cached = image.get("cached_result")
    cache_key = None
    if cached is None:
        cache_key = _quick_read_key(image, job)
        cached = _cached_quick_read(cache_key) or _disk_cached(cache_key)
    if cached is not None:
        _log_page_time(cached, job, started)
        return "page", {**cached, "key": image["key"], "url": image["url"]}
    try:
        if page_capture_checks.looks_blank(image["content"]):
            raise page_server.EndpointError(422, "the page was blank (the reader had not drawn it yet)")
        result = page_server.translate_image(
            image["content"], image["content_type"], drama_id=job["drama_id"],
            source_url=job["source_url"], source_language=job["source_language"], store=job["store"],
            member=job["member"], typeset=job["typeset"] and not job["store"],
            typeset_style=job["typeset_style"], turn=turn, typeset_max_width=job["typeset_max_width"])
    except page_turns.Stopped:
        return "stopped", None
    except page_server.EndpointError as e:
        if job["single"] and e.status in (404, 413, 415):
            raise
        return "failed", {"key": image["key"], "url": image["url"], "error": e.message}
    except Exception as e:
        # The raw text can carry the OS user name or library path
        # and the extension shows it on the page being read, so
        # only a fixed message leaves; the detail stays in the log.
        page_capture_checks.log_page_failure(e)
        entry = {"key": image["key"], "url": image["url"], "error": page_capture_checks.page_failure_message(e)}
        # Every remaining page would fail the same way and each would leave a stored, empty page behind.
        return ("fatal" if page_capture_checks.is_request_fatal(e) else "failed"), entry
    if job["typeset"] and job["store"]:
        typeset_started = time.monotonic()
        _typeset_stored_page(result, job["drama_id"], member=job["member"])
        result.setdefault("seconds", {})["typeset"] = round(time.monotonic() - typeset_started, 2)
    _record_run_notes(result)
    _remember_quick_read(cache_key, result)
    _remember_on_disk(cache_key, _quick_read_url_key(image, job), result)
    _log_page_time(result, job, started)
    result["key"] = image["key"]
    result["url"] = image["url"]
    return "page", result


# Two pages at a time: the model call is most of a page's time, so the next page is read (and the
# last one lettered) while one translates. Pages still read and translate in page order
# (page_turns.PageOrder), so a third worker would only wait behind the same turns.
PAGES_AT_ONCE = 2
# Quick read translates pages without waiting for the page before (nothing is saved, so speed
# wins over the tighter story link), so more pages can be at the model at once. Reading still
# takes turns, so this adds model calls in flight, not GPU work.
QUICK_READ_PAGES_AT_ONCE = 4


def _translate_all(decoded: list, job: dict, on_page=None) -> list:
    """Each image's (outcome, value), in the images' order. `on_page(result)` is called as each
    page finishes, from a worker thread, one call at a time."""
    halted = threading.Event()
    order = page_turns.PageOrder(len(decoded), lambda: halted.is_set() or _run_cancelled(job["run_id"]),
                                 ordered_translation=job["store"])
    outcomes: list[tuple[str, dict | None]] = [("stopped", None)] * len(decoded)
    report_lock = threading.Lock()

    def work(index):
        try:
            outcome = _translate_one(decoded[index], job, order.turn(index))
            outcomes[index] = outcome
            if outcome[0] == "fatal":
                halted.set()
            elif outcome[0] == "page" and on_page is not None:
                with report_lock, contextlib.suppress(Exception):
                    # A listener that has gone (the extension closed the connection) must not stop
                    # the run: the app keeps working on a dropped batch, as it always has.
                    on_page(outcome[1])
        finally:
            order.finish(index)

    if len(decoded) == 1:
        work(0)
        return outcomes
    waiting = queue.SimpleQueue()
    for index in range(len(decoded)):
        waiting.put(index)
    errors = []

    def worker():
        while True:
            try:
                index = waiting.get_nowait()
            except queue.Empty:
                return
            try:
                work(index)
            except BaseException as e:   # re-raised below, on the request's own thread
                errors.append(e)

    # Daemon threads, like the bridge's own handler threads: the app's shutdown never waits
    # for a page still being worked on. Each runs in a copy of the request's context, which
    # carries the household member the pages are for (their key, spend and monthly limit); a
    # new thread would otherwise start with an empty one and bill the owner.
    workers = [threading.Thread(target=contextvars.copy_context().run, args=(worker,), daemon=True,
                                name="bridge-page")
               for _ in range(min(PAGES_AT_ONCE if job["store"] else QUICK_READ_PAGES_AT_ONCE,
                                  len(decoded)))]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    if errors:
        raise errors[0]
    return outcomes


def run_images(payload, images, principal=None, on_page=None) -> dict:
    """principal None is the PC's own token (the local owner); a device
    token passes its member, who may only send into a title they can edit.
    `on_page(result)` hears each page as soon as it is done; the answer
    still lists every page, in the order they were sent."""
    drama_id = payload.get("drama_id")
    store = bool(payload.get("store", True))
    typeset_style = _typeset_style(payload.get("typeset_style"))
    require_drama_access(principal, drama_id)
    source_url = str(payload.get("source_url") or "")
    decoded = _decode_images(images, allow_fetch=not store)
    job = {"drama_id": drama_id, "source_url": source_url, "store": store,
           "source_language": str(payload.get("source_language") or ""),
           "typeset": payload.get("typeset") is True, "typeset_style": typeset_style,
           "typeset_max_width": _typeset_max_width(payload.get("typeset_max_width")),
           "glossary_version": _glossary_version(drama_id),
           "member": principal is not None, "single": False, "run_id": _run_id(payload)}
    unfetched = _prefetch(decoded, payload, job, member=_user_id(principal)) if any(i["content"] is None for i in decoded) else []

    # Let the existing page-image filter decide what is a real page,
    # but only when there is a set worth clustering: filtering a
    # deliberate single "translate this one" send would just refuse
    # the very image the person picked. A picture answered from the cache was accepted when it
    # was first read and has no bytes to measure, so it passes through.
    skipped = []
    if len(decoded) > 1 and payload.get("filter_pages", True):
        measurable = [i for i in decoded if i["content"] is not None]
        kept, rejected = page_capture_checks.select_page_images(measurable, source_url)
        skipped = [{"key": image["key"], "url": image["url"], "reason": reason}
                   for image, reason in rejected]
        kept_ids = {id(i) for i in kept}
        decoded = [i for i in decoded if i["content"] is None or id(i) in kept_ids]
        if not decoded and not unfetched:
            raise page_server.EndpointError(422, "none of those images look like comic pages")
    job["single"] = len(decoded) == 1
    # One bad page must not discard the rest of a chapter: each failure
    # is named so the person sees a mismatch instead of a short count.
    results, failed, stopped = [], [], ""
    for outcome, value in (_translate_all(decoded, job, on_page) if decoded else []):
        if outcome == "page":
            results.append(value)
        elif outcome in ("failed", "fatal"):
            failed.append(value)
            if outcome == "fatal":
                stopped = value["error"]
        elif not stopped:
            stopped = "cancelled by the extension"
    if store:
        _record_chapter(drama_id, results, source_url, job["run_id"])
    return {"pages": results, "skipped": skipped, "failed": failed,
            "received": len(images),
            **({"unfetched": unfetched} if unfetched else {}),
            "stored": sum(1 for r in results if r.get("stored")),
            "already_stored": sum(1 for r in results if r.get("already_stored")),
            **({"stopped": stopped} if stopped else {})}


STREAM_CONTENT_TYPE = "application/x-ndjson"


def streamed_lines(payload, images, write, principal=None) -> None:
    """run_images for a request that asked for `stream`: `write` gets {"page": result} as each
    page finishes, then {"done": the answer without its pages}, which the extension already has."""
    answer = run_images(payload, images, principal=principal, on_page=lambda page: write({"page": page}))
    write({"done": {key: value for key, value in answer.items() if key != "pages"}})
