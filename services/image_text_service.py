"""
services/image_text_service.py -- Quick translate's "Read from a screenshot":
sends one picture to a vision engine and returns the text written in it, to
fill the text box. UI-free.

The picture goes through the page-upload rules (services/page_import_limits:
PNG, JPEG or WebP, 30 MB, 100 megapixels), is cut down to MAX_EDGE pixels on
its long side so the cost is predictable, and is never stored. A paid engine
is priced first and refused when this month's cap is spent or the picture
would pass it; every request is logged like any other paid call.
"""
import io
import math
import types
from typing import Optional

import db
import translate_engines
from engine_backends import paid_calls
from services import page_import_limits, settings_service, translate_run_service
from services.service_errors import (
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    UnsupportedOperationError,
)
from services.translate_service import resolve_api_key

# Past this a provider shrinks the picture itself, so the extra pixels only
# cost upload time. A zoomed crop from the browser is the usual way to reach it.
MAX_EDGE = 2000
_PIXELS_PER_TOKEN = 750
# Providers bill a picture at about pixels/750 tokens, up to a ceiling of
# roughly this many; the prompt and a short reply come on top.
_MAX_IMAGE_TOKENS = 1600
_PROMPT_TOKENS = 120
_REPLY_TOKENS = 200

_MIME = {"PNG": "image/png", "JPEG": "image/jpeg"}
NO_TEXT_MESSAGE = ("No text was found in the picture. If you picked a local model, "
                   "check that it can read pictures.")
CAP_MESSAGE = ("Reading this picture could pass your monthly spending cap, so it was not sent. "
               "Raise the cap in Settings or pick a free engine.")


def _checked_engine(engine_name: str, model: Optional[str], free_tier: Optional[bool]) -> bool:
    """Validation shared by read_image() and estimate(); returns use_free_tier."""
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    if engine_name not in translate_engines.VISION_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name.capitalize()} can't read pictures. Pick Claude, Gemini, OpenAI or Ollama.")
    translate_run_service._require_offered_model(engine_name, model)
    return engine_name == "gemini" and settings_service.resolve_gemini_free_tier(free_tier)


def _tokens(width: int, height: int) -> tuple:
    image = min(math.ceil(width * height / _PIXELS_PER_TOKEN), _MAX_IMAGE_TOKENS)
    return image + _PROMPT_TOKENS, _REPLY_TOKENS


def _prepared(content: bytes) -> tuple:
    """(bytes, mime, width, height) of the picture as it will be sent."""
    from PIL import Image
    try:
        fmt, width, height = page_import_limits.check_image(content)
    except page_import_limits.ImageRejected as exc:
        raise InvalidInputError(f"This picture can't be used: {exc}.") from None
    scale = min(1.0, MAX_EDGE / max(width, height))
    if fmt in _MIME and scale == 1.0:
        return content, _MIME[fmt], width, height
    try:
        with Image.open(io.BytesIO(content)) as im:
            if scale < 1.0:
                im = im.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                               Image.LANCZOS)
            if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                im = im.convert("RGB")
            out = io.BytesIO()
            im.save(out, "PNG")
            width, height = im.size
    except Exception:
        raise InvalidInputError("This picture can't be used: it is damaged or incomplete.") from None
    return out.getvalue(), "image/png", width, height


def _estimate(engine_name: str, model: Optional[str], use_free_tier: bool,
              width: int, height: int) -> dict:
    priced = types.SimpleNamespace(
        model=model or translate_engines.effective_default_model(engine_name) or "",
        free_tier=use_free_tier)
    tokens_in, tokens_out = _tokens(width, height)
    estimated = translate_engines.estimate_cost_for_engine(priced, tokens_in, tokens_out)
    cap_applies = translate_run_service.engine_cap_applies(engine_name, use_free_tier)
    monthly = translate_run_service.month_cap_usd() if cap_applies else 0
    cap_left = max(0.0, monthly - db.get_month_spend()) if monthly else None
    return {"engine": engine_name, "model": priced.model or None, "estimated_usd": estimated,
            "cap_applies": cap_applies, "monthly_cap_usd": float(monthly) if monthly else None,
            "cap_left_usd": cap_left, "fits_cap": cap_left is None or estimated <= cap_left}


def estimate(width: int, height: int, engine_name: str, model: Optional[str] = None,
             free_tier: Optional[bool] = None) -> dict:
    """What reading a picture of this size would cost, for the page to show
    before the owner presses the button. Read-only: no key is needed and
    nothing is sent or logged. Sizes past MAX_EDGE are priced as cut down."""
    use_free_tier = _checked_engine(engine_name, model, free_tier)
    scale = min(1.0, MAX_EDGE / max(width, height))
    return _estimate(engine_name, model, use_free_tier, round(width * scale), round(height * scale))


def read_image(content: bytes, engine_name: str, model: Optional[str] = None,
               language: str = "", free_tier: Optional[bool] = None,
               env_path: Optional[str] = None) -> dict:
    """{"text": ..., "message": ... (only when nothing was found)} for the text
    in a picture. Raises InvalidInputError for a picture the page rules refuse,
    UnsupportedOperationError for an engine that takes no pictures or a spent
    cap, MissingKeyError / DependencyUnavailableError when the engine isn't set up."""
    use_free_tier = _checked_engine(engine_name, model, free_tier)
    data, mime, width, height = _prepared(content)

    api_key = resolve_api_key(engine_name, env_path)
    if api_key is None:
        raise MissingKeyError(engine_name)
    translate_run_service.refuse_when_cap_spent(engine_name, use_free_tier)
    priced = _estimate(engine_name, model, use_free_tier, width, height)
    if priced["cap_applies"] and not priced["fits_cap"]:
        raise UnsupportedOperationError(CAP_MESSAGE)

    engine = translate_engines.get_engine(
        engine_name, api_key, model, free_tier=use_free_tier,
        base_url=((settings_service.resolve_key("ollama_url") or None)
                  if engine_name == "ollama" else None))
    usage = {}
    try:
        text, usage = translate_engines.read_image_text(engine, data, mime, language)
    except translate_engines.OllamaUnavailableError as exc:
        raise DependencyUnavailableError(
            translate_engines.redact_secrets(exc.message), details={"reason": exc.reason}) from None
    finally:
        # A failed request is logged too (zero tokens): a paid provider may
        # have billed it.
        paid_calls.record_call(
            engine, engine_name, "read_image_text", usage,
            metered=translate_run_service.engine_cap_applies(engine_name, use_free_tier))
    return {"text": text} if text else {"text": "", "message": NO_TEXT_MESSAGE}
