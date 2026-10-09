"""Per-page checks and failure wording for `page_server`, split out to keep
that module under the size limit: the blank-page probe, the byte-identical
page lookup for re-captures, and the fixed failure messages."""

import os


def regions_for_response(bubbles):
    """Boxes exactly as the pipeline produced them: `x/y/w/h` in absolute
    pixels of the image that was sent, top-left origin. The extension
    maps them to screen coordinates itself by the element's own scale,
    so nothing here is normalised or rounded to a different basis."""
    regions = []
    for b in bubbles:
        regions.append({
            "x": int(b.get("x", 0)), "y": int(b.get("y", 0)),
            "w": int(b.get("w", 0)), "h": int(b.get("h", 0)),
            "source_text": b.get("source_text") or "",
            "translated_text": b.get("translated_text") or "",
            "kind": b.get("kind") or "bubble",
            "font_category": b.get("font_category") or "regular",
            "reading_order": int(b.get("reading_order", 0)),
        })
    return regions


def snapshot_texts(bubbles) -> dict:
    """The texts a later write compares against, taken before
    translate_page_bubbles mutates the dicts in place."""
    return {b["id"]: {"translated_text": b.get("translated_text") or "",
                      "source_text": b.get("source_text") or ""} for b in bubbles}


def save_filled_translations(page_id: int, bubbles, originals: dict) -> list:
    """Writes translations the bridge filled in. The LLM call ran without any
    page lock, so another writer (a Scanlate job, a manual edit) may have
    touched a bubble since; the compare-and-set leaves their data alone and
    bumps the page rev for the writes made. Returns notes."""
    import db
    changed = 0
    for b in bubbles:
        text = (b.get("translated_text") or "").strip()
        if not text:
            continue
        if not db.update_bubble_fields(b["id"], {"translated_text": text},
                                       expected=originals[b["id"]], page_id=page_id):
            changed += 1
    if changed:
        return [["warning", f"{changed} bubble(s) changed meanwhile and were left as they are"]]
    return []


def save_read_bubbles(page_id: int, bubbles, newly_stored: bool, reused_rev: int) -> list:
    """Stores a fresh read. A reused page was empty when checked, but the read
    ran unlocked, so it is only replaced if nobody wrote since. Returns notes."""
    import db
    if newly_stored:
        db.save_bubbles(page_id, bubbles)
    elif db.replace_bubbles_if_unchanged(page_id, [], bubbles, expected_rev=reused_rev) is None:
        return [["warning", "the page changed meanwhile, so this read was not saved"]]
    return []


def reused_page_response(data: bytes, saved, notes, drama_id: int, page_id: int) -> dict:
    width, height = image_size(data)
    return {"width": width, "height": height, "regions": regions_for_response(saved),
            "notes": notes, "drama_id": drama_id, "page_id": page_id,
            "stored": True, "already_stored": True}


MAX_ECHOED_URL = 200


def short_url(url) -> str:
    """A URL only ever echoed back for a person to recognise the image
    by, so an inline `data:`/`blob:` one is truncated instead of copied
    whole into the response."""
    text = str(url or "")
    if len(text) <= MAX_ECHOED_URL:
        return text
    return text[:MAX_ECHOED_URL] + "…"


def image_size(data: bytes):
    try:
        import io

        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            return int(img.width), int(img.height)
    except Exception:
        return 0, 0


def page_with_same_bytes(drama_id: int, data: bytes):
    """A page already in the drama whose file is byte-identical, so a
    re-capture of a chapter reuses it instead of doubling the pages. Pages
    the importer re-encoded (WebP) never match and are added again."""
    import db
    for page in db.list_pages(drama_id):
        path = os.path.join(db.drama_dir(drama_id), page["filename"])
        try:
            if os.path.getsize(path) != len(data):
                continue
            with open(path, "rb") as fh:
                if fh.read() == data:
                    return page
        except OSError:
            continue
    return None


# Pixels the blank check will decode. A flat canvas compresses to almost
# nothing, so the byte cap alone does not bound the memory a decode takes.
BLANK_CHECK_MAX_PIXELS = 100_000_000


def looks_blank(data: bytes) -> bool:
    """True for a single-colour image. A reader that has not painted a
    canvas yet hands back exactly this, and storing it would leave a silent
    empty page in the chapter. Undecodable data is not called blank: the
    pipeline reports that itself."""
    try:
        import io

        from PIL import Image
        with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as img:
            if img.width * img.height > BLANK_CHECK_MAX_PIXELS:
                return False
            # draft() lets JPEG decode at a fraction of the size; convert
            # only after shrinking so the full canvas is never held as RGB.
            img.draft("RGB", (128, 128))
            img.thumbnail((64, 64))
            probe = img.convert("RGB")
            return all(lo == hi for lo, hi in probe.getextrema())
    except Exception:
        return False


def is_request_fatal(e: Exception) -> bool:
    """Failures every page of the request would repeat."""
    from memory_headroom import HeadroomError
    return isinstance(e, HeadroomError)


def page_failure_message(e: Exception) -> str:
    """Fixed text per failure kind. Exception text is never used: it can
    quote the image path."""
    from PIL import UnidentifiedImageError
    from memory_headroom import HeadroomError
    if isinstance(e, HeadroomError):
        return ("not enough free memory to load the reading model; "
                "stopped here, nothing further was processed")
    if isinstance(e, (UnidentifiedImageError, ValueError)):
        return "the page could not be read as an image"
    if isinstance(e, OSError):
        return "the page could not be saved or read on this PC"
    return "the page could not be processed"


def log_page_failure(e: Exception) -> None:
    import translate_engines
    try:
        from applog import get_logger
        get_logger().warning("page_server page failed: %s: %s", type(e).__name__,
                             translate_engines.redact_secrets(str(e))[:300])
    except Exception:
        pass
