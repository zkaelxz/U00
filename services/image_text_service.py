"""
services/image_text_service.py -- Quick translate's "Read from a screenshot":
sends one picture to a vision engine and returns the text written in it, to
fill the text box. UI-free.

The picture goes through the page-upload rules (page_import_limits:
PNG, JPEG or WebP, 30 MB) with a tighter pixel cap, is decoded and
written out again unless its file holds nothing but pixels (so EXIF, GPS, XMP,
thumbnails and PNG text chunks never reach a provider) and is cut down to MAX_EDGE pixels on its long side so the cost is
predictable. It is not kept: only the route's upload buffer holds it, which
Starlette may spool to a temp file over 1 MB, removed when the request ends.
A paid engine is priced first, at the most a request can cost, and refused when
this month's cap is spent or the picture would pass it; every request is logged
like any other paid call.
"""
import io
import math
import threading
import types
from typing import Optional

import db
from engine_backends import engine_registry, pricing
from engine_backends import vision as engine_vision
from engine_backends import local as engine_local
from engine_backends import shared as engine_shared
from engine_backends import paid_calls
from engine_backends.vision import MAX_REPLY_TOKENS, OPENAI_MAX_COMPLETION_TOKENS
import page_import_limits
from services import settings_service, translate_run_service
from lib.errors import (
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    UnsupportedOperationError,
)
from services.translate_service import resolve_api_key

# Past this a provider shrinks the picture itself, so the extra pixels only
# cost upload time. A zoomed crop from the browser is the usual way to reach it.
MAX_EDGE = 2000
# A flat 10000x10000 PNG is a few KB on the wire and about 400 MB decoded, so
# the 100 MP header check of the page rules is too loose for a path that
# decodes the whole picture. Same cap as the cover upload.
MAX_READ_PIXELS = 25_000_000
# Claude takes at most 5 MB per image; stay under it.
MAX_SENT_BYTES = 4_500_000
_JPEG_QUALITIES = (85, 70, 55)

_PIXELS_PER_TOKEN = 750
# The most a picture of MAX_EDGE can be billed at: Claude shrinks to about 1600
# tokens, OpenAI's high detail tops out near 1100, and Gemini bills 258 per
# 768 px tile (9 tiles for 2000x2000).
_MAX_IMAGE_TOKENS = {"claude": 1600, "openai": 1100, "gemini": 2300}
_PROMPT_TOKENS = 120

NO_TEXT_MESSAGE = ("No text was found in the picture. If you picked a local model, "
                   "check that it can read pictures.")
CAP_MESSAGE = ("Reading this picture could pass your monthly spending cap, so it was not sent. "
               "Raise the cap in Settings or pick a free engine.")


def _checked_engine(engine_name: str, model: Optional[str], free_tier: Optional[bool]) -> bool:
    """Validation shared by read_image() and estimate(); returns use_free_tier."""
    if engine_name not in engine_registry.ENGINES:
        raise InvalidInputError(engine_registry.unknown_engine_message(engine_name))
    if engine_name not in engine_vision.VISION_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name.capitalize()} can't read pictures. Pick Claude, Gemini, OpenAI or Ollama.")
    translate_run_service._require_offered_model(engine_name, model)
    return engine_name == "gemini" and settings_service.resolve_gemini_free_tier(free_tier)


def _tokens(engine_name: str, width: int, height: int) -> tuple:
    """(input, output) tokens a request can use at most, so the cap check
    refuses on the worst case rather than the usual one."""
    image = min(math.ceil(width * height / _PIXELS_PER_TOKEN), _MAX_IMAGE_TOKENS.get(engine_name, 1600))
    output = OPENAI_MAX_COMPLETION_TOKENS if engine_name == "openai" else MAX_REPLY_TOKENS
    return image + _PROMPT_TOKENS, output


def _encoded(im) -> tuple:
    """(bytes, mime) of a decoded picture written out fresh: PNG, or JPEG when
    the PNG is too big for a provider."""
    out = io.BytesIO()
    im.save(out, "PNG")
    if out.tell() <= MAX_SENT_BYTES:
        return out.getvalue(), "image/png"
    flat = im.convert("RGB") if im.mode != "RGB" else im
    for quality in _JPEG_QUALITIES:
        out = io.BytesIO()
        flat.save(out, "JPEG", quality=quality)
        if out.tell() <= MAX_SENT_BYTES:
            return out.getvalue(), "image/jpeg"
    raise InvalidInputError("This picture is too detailed to send. Crop it smaller.")


_AS_IS_MIME = {"JPEG": "image/jpeg", "PNG": "image/png"}
_AS_IS_MODES = {"JPEG": ("RGB", "L"), "PNG": ("RGB", "RGBA", "L", "LA", "P")}
# Pillow's im.info misses much of what a file can carry (extra APP segments, JPEG thumbnails,
# PNG chunks after IDAT, bytes after the end marker), so the file's own structure is walked and
# anything outside these pixel-only pieces means the picture is rewritten, which strips it.
_JPEG_TABLES = {0xC4, 0xCC, 0xDB, 0xDD}
_JPEG_FRAMES = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_PNG_CHUNKS = {b"IHDR", b"PLTE", b"tRNS", b"pHYs", b"IDAT", b"IEND"}


def _jpeg_is_bare(data: bytes) -> bool:
    if data[:2] != b"\xff\xd8":
        return False
    i, n = 2, len(data)
    while i + 2 <= n:
        if data[i] != 0xFF:
            return False
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0xD9:
            return i + 2 == n
        if i + 4 > n:
            return False
        length = int.from_bytes(data[i + 2:i + 4], "big")
        body = data[i + 4:i + 2 + length]
        if length < 2 or len(body) != length - 2:
            return False
        if marker == 0xE0:
            # JFIF with no thumbnail: the two thumbnail sizes after the density are zero.
            if not (body[:5] == b"JFIF\0" and len(body) == 14 and body[12:14] == b"\0\0"):
                return False
        elif marker == 0xEE:
            if body[:5] != b"Adobe":
                return False
        elif marker not in _JPEG_TABLES and marker not in _JPEG_FRAMES and marker != 0xDA:
            return False
        i += 2 + length
        if marker == 0xDA:
            # Entropy-coded data runs to the next marker that isn't a stuffed byte or a restart.
            while i + 1 < n and not (data[i] == 0xFF and data[i + 1] not in (0x00, *range(0xD0, 0xD8))):
                i += 1
    return False


def _png_is_bare(data: bytes) -> bool:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    i, n = 8, len(data)
    while i + 12 <= n:
        length = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8]
        if kind not in _PNG_CHUNKS:
            return False
        i += 12 + length
        if kind == b"IEND":
            return i == n
    return False


_BARE = {"JPEG": _jpeg_is_bare, "PNG": _png_is_bare}


def _sendable_as_is(im, content: bytes) -> bool:
    """True when re-encoding would only rewrite the same pixels into a bigger file: a site's
    JPEG page became a PNG several times its size, which every Quick read page then uploaded."""
    if not (im.format in _AS_IS_MIME and im.mode in _AS_IS_MODES[im.format]
            and len(content) <= MAX_SENT_BYTES and max(im.size) <= MAX_EDGE
            and _BARE[im.format](content)):
        return False
    # A cut-short file must still be refused before any paid call, as the rewrite path does.
    im.load()
    return True


def _prepared(content: bytes) -> tuple:
    """(bytes, mime, width, height) of the picture as it will be sent."""
    from PIL import Image, ImageOps
    try:
        _fmt, width, height = page_import_limits.check_image(content)
    except page_import_limits.ImageRejected as exc:
        raise InvalidInputError(f"This picture can't be used: {exc}.") from None
    if width * height > MAX_READ_PIXELS:
        raise InvalidInputError(
            f"This picture can't be used: over the {MAX_READ_PIXELS // 1_000_000} megapixel "
            f"limit ({width}x{height}). Crop it smaller.")
    try:
        with Image.open(io.BytesIO(content)) as im:
            if _sendable_as_is(im, content):
                return content, _AS_IS_MIME[im.format], im.width, im.height
            im = ImageOps.exif_transpose(im)
            scale = min(1.0, MAX_EDGE / max(im.size))
            if scale < 1.0:
                im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                               Image.Resampling.LANCZOS)
            if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                im = im.convert("RGB")
            data, mime = _encoded(im)
            width, height = im.size
    except InvalidInputError:
        raise
    except Exception:
        raise InvalidInputError("This picture can't be used: it is damaged or incomplete.") from None
    return data, mime, width, height


# A page translation sends a stored page the same way a screenshot is sent.
prepare_picture = _prepared


def _estimate(engine_name: str, model: Optional[str], use_free_tier: bool,
              width: int, height: int) -> dict:
    priced = types.SimpleNamespace(
        model=model or engine_registry.effective_default_model(engine_name) or "",
        free_tier=use_free_tier)
    tokens_in, tokens_out = _tokens(engine_name, width, height)
    estimated = pricing.estimate_cost_for_engine(priced, tokens_in, tokens_out)
    cap_applies = translate_run_service.engine_cap_applies(engine_name, use_free_tier, priced.model)
    monthly = translate_run_service.month_cap_usd() if cap_applies else 0
    cap_left = max(0.0, monthly - db.get_month_spend()) if monthly else None
    return {"engine": engine_name, "model": priced.model or None, "estimated_usd": estimated,
            "cap_applies": cap_applies, "monthly_cap_usd": float(monthly) if monthly else None,
            "cap_left_usd": cap_left, "fits_cap": cap_left is None or estimated <= cap_left}


def estimate(width: int, height: int, engine_name: str, model: Optional[str] = None,
             free_tier: Optional[bool] = None) -> dict:
    """What reading a picture of this size would cost at most, for the page to
    show before the owner presses the button. Read-only: no key is needed and
    nothing is sent or logged. Sizes past MAX_EDGE are priced as cut down."""
    use_free_tier = _checked_engine(engine_name, model, free_tier)
    scale = min(1.0, MAX_EDGE / max(width, height))
    return _estimate(engine_name, model, use_free_tier, round(width * scale), round(height * scale))


def read_image(content: bytes, engine_name: str, model: Optional[str] = None,
               language: str = "", free_tier: Optional[bool] = None,
               env_path: Optional[str] = None) -> dict:
    """{"text": ..., "message": ... (only when nothing was found)} for the text
    in a picture. Raises InvalidInputError for a picture the rules refuse,
    UnsupportedOperationError for an engine that takes no pictures or a spent
    cap, MissingKeyError / DependencyUnavailableError when the engine isn't set up."""
    use_free_tier = _checked_engine(engine_name, model, free_tier)
    data, mime, width, height = _prepared(content)

    api_key = resolve_api_key(engine_name, env_path)
    if api_key is None:
        raise MissingKeyError(engine_name)
    # The model the call will run, so an omitted Ollama model (hosted by default) meets the member limit.
    translate_run_service.refuse_when_cap_spent(
        engine_name, use_free_tier, model or engine_registry.effective_default_model(engine_name))
    priced = _estimate(engine_name, model, use_free_tier, width, height)
    if priced["cap_applies"] and not priced["fits_cap"]:
        raise UnsupportedOperationError(CAP_MESSAGE)

    engine = engine_registry.get_engine(
        engine_name, api_key, model, free_tier=use_free_tier,
        base_url=(settings_service.ollama_url()
                  if engine_name == "ollama" else None))

    text, _usage = _send(engine, engine_name, use_free_tier, data, mime, width, height, language)
    return {"text": text} if text else {"text": "", "message": NO_TEXT_MESSAGE}


def _send(engine, engine_name: str, use_free_tier: bool, data: bytes, mime: str,
          width: int, height: int, language: str) -> tuple:
    """(text, usage) for one prepared picture, logging exactly one usage row."""
    metered = translate_run_service.engine_cap_applies(
        engine_name, use_free_tier, getattr(engine, "model", None))
    lock = threading.Lock()
    logged = []

    def log(usage: dict) -> None:
        """One usage row per request, from whichever side learns the outcome
        first: the request itself (also when it fails, with zero counts, since
        a paid provider may have billed it) or, when its deadline passes while
        it is still running, the estimate below."""
        with lock:
            if logged:
                return
            logged.append(True)
        paid_calls.record_call(engine, engine_name, "read_image_text", usage, metered=metered)

    try:
        return engine_vision.read_image_text(engine, data, mime, language, on_done=log)
    except engine_local.OllamaUnavailableError as exc:
        raise DependencyUnavailableError(
            engine_shared.redact_secrets(exc.message), details={"reason": exc.reason}) from None
    except BaseException:
        tokens_in, tokens_out = _tokens(engine_name, width, height)
        log({"input_tokens": tokens_in, "output_tokens": tokens_out})
        raise


def read_stored_image(engine, engine_name: str, model: Optional[str], content: bytes,
                      language: str, use_free_tier: bool, budget: Optional[float] = None) -> dict:
    """One read of a picture the app already holds (a Lab case), with an engine the
    caller built. {"text", "cost_usd"}, or {"over_budget": True} without sending when
    the worst-case price is more than `budget`. Raises like read_image; a failed
    request still costs its worst-case price (exc.cost_usd), since a paid provider
    may have billed it."""
    data, mime, width, height = _prepared(content)
    worst = _estimate(engine_name, model, use_free_tier, width, height)["estimated_usd"]
    if budget is not None and worst > budget:
        return {"over_budget": True}
    try:
        text, usage = _send(engine, engine_name, use_free_tier, data, mime, width, height, language)
    except BaseException as exc:
        setattr(exc, "cost_usd", worst)
        raise
    cost = pricing.estimate_cost_for_engine(
        engine, usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0)
    return {"text": text, "cost_usd": cost}
