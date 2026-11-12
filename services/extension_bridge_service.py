"""
services/extension_bridge_service.py -- what the extension's requests do once they are
accepted, shared by the PC-only bridge (page_server.py, the shared loopback
token, the local owner) and the device-token routes
(api/routers/extension_bridge_routes.py, one household member).

`principal` is None for the PC's own token. A member's principal limits the
title list to what they can see and sends only into a title they can edit.
"""

import json
import os
import re
import threading
import time

import page_capture_checks
import page_server
from services import ownership_service
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
    text = scanlate_pages_service.clean_note(" ".join(summary.split()))[:MAX_SUMMARY_CHARS]
    get_logger().info("Extension capture (title %s%s): %s", int(drama_id) if isinstance(drama_id, int) else "?",
                      ", member" if principal is not None else "", text)
    return {"logged": True}


def _run_cancelled(run_id: str) -> bool:
    with _cancelled_lock:
        return bool(run_id) and run_id in _cancelled_runs


def run_images(payload, images, principal=None) -> dict:
    """principal None is the PC's own token (the local owner); a device
    token passes its member, who may only send into a title they can edit."""
    import base64
    drama_id = payload.get("drama_id")
    store = bool(payload.get("store", True))
    typeset = payload.get("typeset") is True
    require_drama_access(principal, drama_id)
    source_url = str(payload.get("source_url") or "")
    source_language = str(payload.get("source_language") or "")
    decoded = []
    for image in images:
        if not isinstance(image, dict):
            raise page_server.EndpointError(400, "each image must be a JSON object")
        # Checked before decoding so an oversized payload never allocates
        # the full decoded copy.
        data = image.get("data") or ""
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

    # Let the existing page-image filter decide what is a real page,
    # but only when there is a set worth clustering: filtering a
    # deliberate single "translate this one" send would just refuse
    # the very image the person picked.
    skipped = []
    if len(decoded) > 1 and payload.get("filter_pages", True):
        decoded, rejected = page_capture_checks.select_page_images(decoded, source_url)
        skipped = [{"key": image["key"], "url": image["url"], "reason": reason}
                   for image, reason in rejected]
        if not decoded:
            raise page_server.EndpointError(422, "none of those images look like comic pages")

    # One bad page must not discard the rest of a chapter: each failure
    # is named so the person sees a mismatch instead of a short count.
    results = []
    failed = []
    stopped = ""
    run_id = _run_id(payload)
    for image in decoded:
        if _run_cancelled(run_id):
            stopped = "cancelled by the extension"
            break
        try:
            if page_capture_checks.looks_blank(image["content"]):
                raise page_server.EndpointError(422, "the page was blank (the reader had not drawn it yet)")
            result = page_server.translate_image(
                image["content"], image["content_type"], drama_id=drama_id,
                source_url=source_url, source_language=source_language, store=store,
                member=principal is not None, typeset=typeset and not store)
        except page_server.EndpointError as e:
            if len(decoded) == 1 and e.status in (404, 413, 415):
                raise
            failed.append({"key": image["key"], "url": image["url"], "error": e.message})
            continue
        except Exception as e:
            # The raw text can carry the OS user name or library path
            # and the extension shows it on the page being read, so
            # only a fixed message leaves; the detail stays in the log.
            page_capture_checks.log_page_failure(e)
            failed.append({"key": image["key"], "url": image["url"],
                           "error": page_capture_checks.page_failure_message(e)})
            if page_capture_checks.is_request_fatal(e):
                # Every remaining page would fail the same way and
                # each would leave a stored, empty page behind.
                stopped = page_capture_checks.page_failure_message(e)
                break
            continue
        if typeset and store:
            typeset_started = time.monotonic()
            _typeset_stored_page(result, drama_id, member=principal is not None)
            result.setdefault("seconds", {})["typeset"] = round(time.monotonic() - typeset_started, 2)
        _record_run_notes(result)
        result["key"] = image["key"]
        result["url"] = image["url"]
        results.append(result)
    if store:
        _record_chapter(drama_id, results, source_url, run_id)
    return {"pages": results, "skipped": skipped, "failed": failed,
            "received": len(images),
            "stored": sum(1 for r in results if r.get("stored")),
            "already_stored": sum(1 for r in results if r.get("already_stored")),
            **({"stopped": stopped} if stopped else {})}
