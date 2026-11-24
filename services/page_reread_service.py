"""Owns Quick read's second look at hard comic text: OCR lines that read with low confidence are cut out of the page at full size and read again by the picture model before the page is translated."""

import contextvars
import io
from concurrent.futures import ThreadPoolExecutor

from engine_backends import vision

# RapidOCR scores of a clean line sit above 0.9; stylised lettering and heavy outlines fall
# below 0.8, where the text is often one wrong character from another word.
LOW_CONFIDENCE = 0.8
# Lettering drawn on the art is stylised by nature, so it gets a stricter bar.
CAPTION_CONFIDENCE = 0.9
# A page of mostly hard text would turn one translation call into many; the worst ones go first.
MAX_REGIONS_PER_PAGE = 4
MAX_WORKERS = 3
# The page picture sent for translation is shrunk, which is what loses the thin strokes.
CROP_LONG_SIDE = 768
CROP_MARGIN = 0.1
REPLY_TOKENS = 300
_UNREADABLE = "UNREADABLE"
_LANGUAGE_NAMES = {"ja": "Japanese", "ko": "Korean", "zh": "Chinese"}


# A box this much longer than it is tall holds one line of roughly square glyphs, so the line
# should have about that many characters; a third of that is a read that lost most of it.
_LINE_RATIO = 4
_SHORT_SHARE = 3


def _lost_most_of_a_line(b: dict) -> bool:
    """A long single-line box whose text is a character or two ("第112话" read as "的"): the OCR
    found the box but not the lettering, and reports no doubt about it."""
    long_side, short_side = max(b["w"], b["h"]), max(1, min(b["w"], b["h"]))
    chars = len("".join((b.get("source_text") or "").split()))
    return long_side / short_side >= _LINE_RATIO and chars * _SHORT_SHARE < long_side / short_side


def _score(b: dict) -> float:
    return b["ocr_confidence"] if b.get("ocr_confidence") is not None else 0.0


def hard_regions(bubbles) -> list:
    """The bubbles worth a second look, least confident first and at most MAX_REGIONS_PER_PAGE:
    text that was read and not skipped, and either scored under its bar or far shorter than its
    box. A bubble with no score (another OCR backend) is picked only by the second test."""
    picked = []
    for b in bubbles:
        if b.get("skip") or b.get("kind") == "sfx" or not (b.get("source_text") or "").strip():
            continue
        score = b.get("ocr_confidence")
        bar = CAPTION_CONFIDENCE if b.get("kind") == "caption" else LOW_CONFIDENCE
        if (score is not None and score < bar) or _lost_most_of_a_line(b):
            picked.append(b)
    return sorted(picked, key=_score)[:MAX_REGIONS_PER_PAGE]


def _crop(image: bytes, box: dict) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(image)) as page:
        page = page.convert("RGB")
        mx, my = box["w"] * CROP_MARGIN, box["h"] * CROP_MARGIN
        crop = page.crop((max(0, int(box["x"] - mx)), max(0, int(box["y"] - my)),
                          min(page.width, int(box["x"] + box["w"] + mx)),
                          min(page.height, int(box["y"] + box["h"] + my))))
    if max(crop.size) > CROP_LONG_SIDE:
        crop.thumbnail((CROP_LONG_SIDE, CROP_LONG_SIDE))
    out = io.BytesIO()
    crop.save(out, "JPEG", quality=88)
    return out.getvalue()


def _has_script(text: str, language: str) -> bool:
    """Whether `text` holds a letter of the source language, so an English apology or a refusal
    is never taken for a reading."""
    ranges = {"ja": ((0x3040, 0x30FF), (0x4E00, 0x9FFF)), "zh": ((0x4E00, 0x9FFF),),
              "ko": ((0xAC00, 0xD7AF), (0x1100, 0x11FF))}.get(language, ())
    return any(lo <= ord(c) <= hi for c in text for lo, hi in ranges)


def clean_reading(answer, original: str, language: str) -> str:
    """The model's reading as text to use, or "" when it is not one: empty, UNREADABLE, in no
    letter of the language, or so much longer than the OCR text that it is commentary."""
    text = str(answer or "").strip().strip("`\"'「」『』").strip()
    if not text or _UNREADABLE in text.upper() or not _has_script(text, language):
        return ""
    if len(text) > max(30, 3 * len(original)):
        return ""
    return text


def _read_one(bubble: dict, image: bytes, language: str, readers: list) -> str:
    prompt = (f"This picture is one piece of lettering cut from a {_LANGUAGE_NAMES.get(language, '')} "
              "comic page. Reply with only the text written in it, exactly as lettered, in reading "
              "order, with no translation, quotes or commentary. An OCR program read it as: "
              f"{bubble['source_text']}. It may be wrong. If you cannot read it, reply UNREADABLE.")
    crop = _crop(image, bubble)
    for reader in readers:
        try:
            text, usage = vision.ask_about_image(reader["engine"], prompt, crop, "image/jpeg",
                                                 max_tokens=REPLY_TOKENS)
        except Exception:
            continue
        if reader.get("usage_cb"):
            reader["usage_cb"](usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        return clean_reading(text, bubble["source_text"], language)
    return ""


def reread(bubbles, image: bytes, language: str, readers) -> int:
    """Replaces the source text of hard-to-read bubbles with the picture model's reading and
    returns how many changed. `readers` are the page translation's picture readers, tried in
    order; none, or any failure, leaves the OCR text, so this can only add accuracy."""
    hard = hard_regions(bubbles) if readers and image else []
    if not hard:
        return 0
    # Each worker needs its own copy of the context: the household member whose spend this is
    # lives in it.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(contextvars.copy_context().run, _read_one, b, image, language,
                               list(readers)) for b in hard]
        readings = []
        for future in futures:
            try:
                readings.append(future.result())
            except Exception:
                readings.append("")
    changed = 0
    for bubble, text in zip(hard, readings):
        if text and text != bubble["source_text"].strip():
            bubble["source_text"] = text
            changed += 1
    return changed
