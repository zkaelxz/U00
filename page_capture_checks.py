"""Per-page checks and failure wording for `page_server`, split out to keep
that module under the size limit: the blank-page probe, the byte-identical
page lookup for re-captures, and the fixed failure messages."""

import os
from typing import IO

import en_cleanup
from scanlate import scanlate_regions
def regions_for_response(bubbles):
    """Boxes exactly as the pipeline produced them: `x/y/w/h` in absolute
    pixels of the image that was sent, top-left origin. The extension
    maps them to screen coordinates itself by the element's own scale,
    so nothing here is normalised or rounded to a different basis."""
    regions = []
    for b in bubbles:
        translated = (b.get("translated_text") or "").strip()
        regions.append({
            "x": int(b.get("x", 0)), "y": int(b.get("y", 0)),
            "w": int(b.get("w", 0)), "h": int(b.get("h", 0)),
            "source_text": b.get("source_text") or "",
            "translated_text": b.get("translated_text") or "",
            "kind": b.get("kind") or "bubble",
            "font_category": b.get("font_category") or "regular",
            "reading_order": int(b.get("reading_order", 0)),
            # Lettering Scanlate leaves alone (a sound effect, a hand-skipped region): the
            # preview must not paint over what the typeset page would keep.
            "left_alone": bool(b.get("skip")) or scanlate_regions.region_excluded_from_auto(b),
            # No English came back, or the model left source-script letters in it.
            "untranslated": (not translated and bool((b.get("source_text") or "").strip()))
                            or en_cleanup.has_cjk_letters(translated),
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


def save_translations_of_read(page_id: int, stored, translated) -> list:
    """Fills the rows stored from a read (same order) with the translations
    the bridge produced on the in-memory list afterwards."""
    return save_filled_translations(
        page_id,
        [{"id": row["id"], "translated_text": b.get("translated_text")}
         for row, b in zip(stored, translated)],
        snapshot_texts(stored))


def save_read_bubbles(page_id: int, bubbles, newly_stored: bool, reused_rev: int) -> list:
    """Stores a fresh read. The page was empty when checked, but the read ran
    unlocked (and a new page is editable the moment it is stored), so it is
    only replaced if nobody wrote since. Returns notes."""
    import db
    expected_rev = 0 if newly_stored else reused_rev
    if db.replace_bubbles_if_unchanged(page_id, [], bubbles, expected_rev=expected_rev) is None:
        return [["warning", "the page changed meanwhile, so this read was not saved"]]
    return []


def reused_page_response(data: bytes, saved, notes, drama_id: int, page_id: int,
                         untranslated_lines: int) -> dict:
    width, height = image_size(data)
    return {"width": width, "height": height, "regions": regions_for_response(saved),
            "notes": notes, "drama_id": drama_id, "page_id": page_id,
            "stored": True, "already_stored": True, "untranslated_lines": untranslated_lines}


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


def flat_colour(data: bytes):
    """The one colour of a single-colour image, else None. A reader that has
    not painted a canvas yet hands back exactly this, and storing it would
    leave a silent empty page in the chapter. Undecodable data is not called
    flat: the pipeline reports that itself."""
    try:
        import io

        from PIL import Image
        with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as img:
            if img.width * img.height > BLANK_CHECK_MAX_PIXELS:
                return None
            # draft() lets JPEG decode at a fraction of the size; convert
            # only after shrinking so the full canvas is never held as RGB.
            img.draft("RGB", (128, 128))
            img.thumbnail((64, 64))
            bands = [band for band in img.convert("RGB").getextrema() if isinstance(band, tuple)]
            if len(bands) == 3 and all(band[0] == band[1] for band in bands):
                return tuple(band[0] for band in bands)
            return None
    except Exception:
        return None


# Printed black and white come out at (or within JPEG noise of) 0 and 255; a dark- or
# light-mode reader's empty canvas is usually a shade off, which must stay "not drawn yet".
_PLAIN_TONE_MARGIN = 4


def _plain_tone(colour) -> str:
    if max(colour) <= _PLAIN_TONE_MARGIN:
        return "black"
    if min(colour) >= 255 - _PLAIN_TONE_MARGIN:
        return "white"
    return ""


class FlatPages:
    """Decides, page by page, which flat pages of one batch are not drawn yet.

    A chapter can print one plain black or white page (an intermission), so with `keep_plain`
    such a page is kept when no other flat page of the batch has its tone and a drawn page is
    beside it; undrawn canvases come several at a time, alone, or in a reader's own colour.
    Each page is decoded when its own turn asks, and the rest of the batch only for a plain
    flat page, so the first page is not held up by the others."""

    def __init__(self, images, keep_plain: bool):
        self._images = images
        self._keep_plain = keep_plain
        self._colours = {}

    def _colour(self, image):
        # Worker threads may decode the same page twice; the answer is the same either way.
        if image["key"] not in self._colours:
            self._colours[image["key"]] = flat_colour(image["content"]) if image.get("content") else None
        return self._colours[image["key"]]

    def undrawn(self, image) -> bool:
        colour = self._colour(image)
        if colour is None:
            return False
        tone = _plain_tone(colour)
        if not (self._keep_plain and tone):
            return True
        others = [(other, self._colour(other)) for other in self._images if other is not image]
        drawn = any(other.get("content") and c is None for other, c in others)
        alike = any(c is not None and _plain_tone(c) == tone for _, c in others)
        return not drawn or alike


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
    from engine_backends import shared
    try:
        from applog import get_logger
        get_logger().warning("page_server page failed: %s: %s", type(e).__name__,
                             shared.redact_secrets(str(e))[:300])
    except Exception:
        pass


def select_page_images(images, page_url: str):
    """Which of the sent images are real pages, decided by
    `sources/generic_import.py`'s existing filter -- size floor, aspect
    and width clustering and duplicate rejection -- rather
    than by a second implementation in JavaScript that would drift from
    it.

    `images`: list of dicts with `url`, `content` and `content_type`.
    Returns `(kept, rejected)`, each a list of `(image, reason)` pairs
    where `reason` is `""` for kept ones.
    """
    from sources import generic_import
    candidates = []
    for order, image in enumerate(images):
        c = generic_import.ImageCandidate(image.get("url") or page_url, order)
        c.content = image.get("content") or b""
        generic_import.measure(c)
        candidates.append(c)
    # The extension labels a canvas page with the page's address and a
    # downloaded one with its CDN's: those are not where each image came
    # from, so the third-party rule would drop real pages of a mixed batch.
    kept, rejected = generic_import.filter_page_images(candidates, page_url,
                                                       judge_domains=False)
    kept_set = {c.order for c in kept}
    return ([images[c.order] for c in kept],
            [(images[c.order], c.reject_reason) for c in rejected
             if c.order not in kept_set])


def upright(data: bytes, ext: str):
    """(data, ext) with the image's EXIF orientation applied. A downloaded
    JPEG can be stored sideways with a rotate tag the browser obeys, and
    boxes found on the raw pixels would then sit wrong over the page the
    person sees. Strips are not sliced: the bridge answers per image sent."""
    import page_import_limits
    try:
        return page_import_limits.prepare_page(data, slice_strips=False)[0]
    except page_import_limits.ImageRejected:
        return data, ext


def sniff_image(data: bytes) -> tuple[str, None] | tuple[None, tuple[int, str]]:
    """(extension, None) from the bytes themselves, or (None, (status, reason)).
    The client's claimed type is only a hint; the pixel cap is read from the
    header so a huge image is refused before anything decodes it."""
    import page_import_limits
    try:
        return page_import_limits.image_extension(data), None
    except page_import_limits.ImageRejected as exc:
        reason = str(exc)
        return None, (413 if reason.startswith("over the") else 415, reason)


# A typeset page is drawn over the reader's own image, so it goes as a JPEG: a long strip is a
# few hundred KB that way where its PNG is megabytes. Over the cap the extension keeps its boxes.
PREVIEW_MAX_BYTES = 1_500_000


# A redrawn page is looked at once on a phone or over the page it replaces, never kept, and it
# travels back over the household's connection: 75 is about a third smaller than 85 with no
# difference a reader sees on lettering at screen size.
PREVIEW_JPEG_QUALITY = 75


def jpeg_data_url(path: "str | IO[bytes]", max_width: int | None = None) -> str:
    """The image at `path` (a file, or an open file-like object) as a JPEG data: URL, or "" when it is over PREVIEW_MAX_BYTES.
    `max_width` scales a wider image down to it (a phone sends its screen width), never up."""
    import base64
    import io

    from PIL import Image
    with Image.open(path) as image:
        picture = image.convert("RGB")
        if max_width and picture.width > max_width:
            height = max(1, round(picture.height * max_width / picture.width))
            picture = picture.resize((max_width, height), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=PREVIEW_JPEG_QUALITY)
    if buffer.tell() > PREVIEW_MAX_BYTES:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
