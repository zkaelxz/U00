"""
services/extension_bridge_service.py -- what the extension's requests do once they are
accepted, shared by the PC-only bridge (page_server.py, the shared loopback
token, the local owner) and the device-token routes
(api/routers/extension_bridge_routes.py, one household member).

`principal` is None for the PC's own token. A member's principal limits the
title list to what they can see and sends only into a title they can edit.
"""

import json

import page_capture_checks
import page_server
from services import ownership_service


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
    except (TypeError, ValueError):
        allowed = False
    if not allowed:
        raise page_server.EndpointError(404, "no such drama")


def health_payload(principal=None, app_port=None) -> dict:
    import db
    dramas = db.list_dramas()
    if principal is not None:
        visible = set(ownership_service.filter_visible_drama_ids(
            principal, [d["id"] for d in dramas]))
        dramas = [d for d in dramas if d["id"] in visible]
    return {
        "ok": True,
        "app": "Baihe Subtitler",
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


def run_images(payload, images, principal=None) -> dict:
    """principal None is the PC's own token (the local owner); a device
    token passes its member, who may only send into a title they can edit."""
    import base64
    drama_id = payload.get("drama_id")
    store = bool(payload.get("store", True))
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
    for image in decoded:
        try:
            if page_capture_checks.looks_blank(image["content"]):
                raise page_server.EndpointError(422, "the page was blank (the reader had not drawn it yet)")
            result = page_server.translate_image(
                image["content"], image["content_type"], drama_id=drama_id,
                source_url=source_url, source_language=source_language, store=store,
                member=principal is not None)
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
        result["key"] = image["key"]
        result["url"] = image["url"]
        results.append(result)
    return {"pages": results, "skipped": skipped, "failed": failed,
            "received": len(images),
            "stored": sum(1 for r in results if r.get("stored")),
            "already_stored": sum(1 for r in results if r.get("already_stored")),
            **({"stopped": stopped} if stopped else {})}
