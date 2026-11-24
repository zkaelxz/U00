"""
api/routers/translate_routes.py -- Translate-standalone endpoints.

The two read-only routes: the engine list
(name/label/free/models/key_configured, never a key value) and
translate history. The translate action itself resolves
a server-side key per engine rather than accepting one from the
caller. Clearing history is also here -- a confirm-gated
delete (see services/translate_service.py's own docstring for why it
requires an explicit confirm=true rather than a bare DELETE).
"""

import asyncio

import applog
from engine_backends import shared as engine_shared
from engine_backends import vision as engine_vision
from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from api.auth import engine_for_caller, local_only, require_engines_allowed, require_permission
from api.llm_slots import llm_slot
from api.schemas import (ClearHistoryResult, ReadImageEstimate, ReadImageEstimateRequest,
                         ReadImageResponse, TranslateEngineListResponse,
                         TranslateEstimate, TranslateHistoryResponse, TranslateRequest,
                         TranslateResponse, error_responses)
from lib.capped_request import read_capped_form
import page_import_limits
from services import image_text_service, settings_service, translate_service
from lib.errors import DependencyUnavailableError, InvalidInputError, ServiceError

router = APIRouter(prefix="/api/translate", tags=["translate"])


@router.get("/engines", dependencies=[require_permission("library.read")], response_model=TranslateEngineListResponse,
            summary="Available translate engines and whether each has a key configured")
def get_engines():
    return {"items": translate_service.list_engines(probe_ollama=True),
            "default_engine": settings_service.get("default_engine")}


@router.get("/history", dependencies=[require_permission("library.read")], response_model=TranslateHistoryResponse,
            summary="Standalone-translate history, most recent first")
def get_history(request: Request, limit: int = Query(50, ge=1, le=200)):
    return {"items": translate_service.list_history(limit=limit,
                                                    principal=request.state.principal)}


def _require_engine_for_caller(request: Request, named, default, model) -> tuple:
    """(engine, model) this caller runs: an omitted engine is the owner's default, or
    Ollama for an account that may not use it (`engine_for_caller`), so a member's
    Text page needs no engine picker; a named engine is held to `engines.paid`."""
    engine, model = engine_for_caller(request, named, named or default, model)
    require_engines_allowed(request, engine, model=model)
    return engine, model


def _vision_default() -> str:
    default = settings_service.get("default_engine")
    return default if default in engine_vision.VISION_ENGINES else "ollama"


@router.post("", dependencies=[require_permission("jobs.start")], response_model=TranslateResponse,
             response_model_exclude_none=True,
            summary="Translate text with a chosen engine (server-side key resolution, D2)",
            responses=error_responses(400, 403, 422, 429, 503))
def post_translate(payload: TranslateRequest, request: Request):
    engine, model = _require_engine_for_caller(
        request, payload.engine, settings_service.get("default_engine"), payload.model)
    # Synchronous LLM call: capped by the shared slot pool (api/llm_slots.py).
    with llm_slot(request):
        return translate_service.translate(
            payload.text, engine, payload.source_language, payload.target_language,
            model=model, free_tier=payload.free_tier,
            principal=request.state.principal)


@router.post("/estimate", dependencies=[require_permission("jobs.start")], response_model=TranslateEstimate,
             summary="What translating this text would cost, and what is left of the monthly cap",
             responses=error_responses(400, 403, 422))
def post_translate_estimate(payload: TranslateRequest, request: Request):
    engine, model = _require_engine_for_caller(
        request, payload.engine, settings_service.get("default_engine"), payload.model)
    return translate_service.estimate(
        payload.text, engine, payload.source_language, payload.target_language,
        model=model, free_tier=payload.free_tier)


_IMAGE_TOO_LARGE = (f"The picture is larger than {page_import_limits.MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
# read_capped_form bounds the body, and the slot is taken before it is read. Any member
# may send one, so a body trickled in slowly must not hold a shared slot for long.
_IMAGE_READ_SECONDS = 120
_IMAGE_TOO_SLOW = "The picture took too long to arrive. Try again on a better connection."
# Room for the multipart boundaries and the four short text fields.
_IMAGE_BODY_MAX = page_import_limits.MAX_IMAGE_BYTES + 64 * 1024
_READ_IMAGE_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["image"],
    "properties": {"image": {"type": "string", "format": "binary"},
                   "engine": {"type": "string"}, "model": {"type": "string"},
                   "language": {"type": "string"}, "free_tier": {"type": "string"}}}}}}}


@router.post("/read-image", dependencies=[require_permission("jobs.start")],
             response_model=ReadImageResponse, response_model_exclude_none=True,
             openapi_extra=_READ_IMAGE_BODY,
             summary="Read the text written in a picture with a vision engine",
             responses=error_responses(400, 403, 413, 422, 429, 503))
async def post_read_image(request: Request):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise InvalidInputError("Send the picture as multipart/form-data.")
    # Synchronous provider call: capped by the shared slot pool (api/llm_slots.py).
    # Taken before the body is read so a member's parallel 30 MB uploads are
    # refused at once instead of each filling temp disk and memory first.
    with llm_slot(request):
        try:
            form = await asyncio.wait_for(read_capped_form(
                request, _IMAGE_BODY_MAX, "the picture", _IMAGE_TOO_LARGE,
                "The picture form could not be read.", max_files=1, max_fields=4), _IMAGE_READ_SECONDS)
        except TimeoutError:
            raise InvalidInputError(_IMAGE_TOO_SLOW) from None
        try:
            upload = form.get("image")
            named = form.get("engine")
            if not isinstance(upload, UploadFile) or not (named is None or isinstance(named, str)):
                raise InvalidInputError("Send an image.")
            fields = {k: v for k, v in form.items() if isinstance(v, str)}
            engine, model = _require_engine_for_caller(request, named or None, _vision_default(),
                                                       fields.get("model") or None)
            content = await upload.read(page_import_limits.MAX_IMAGE_BYTES + 1)
            if len(content) > page_import_limits.MAX_IMAGE_BYTES:
                raise InvalidInputError(_IMAGE_TOO_LARGE)
        finally:
            await form.close()
        free_tier = {"true": True, "false": False}.get(fields.get("free_tier", ""))
        try:
            return await run_in_threadpool(
                image_text_service.read_image, content, engine, model,
                fields.get("language", ""), free_tier)
        except ServiceError:
            raise
        except Exception as e:  # the provider's own reason, without a key
            reason = engine_shared.redact_secrets(str(e))[:300] or type(e).__name__
            applog.get_logger().warning("Read from a picture failed: %s", reason)
            raise DependencyUnavailableError(f"The engine call failed: {reason}") from None


@router.post("/read-image/estimate", dependencies=[require_permission("jobs.start")],
             response_model=ReadImageEstimate,
             summary="What reading a picture of this size would cost, and what is left of the monthly cap",
             responses=error_responses(400, 403, 422))
def post_read_image_estimate(payload: ReadImageEstimateRequest, request: Request):
    engine, model = _require_engine_for_caller(request, payload.engine, _vision_default(), payload.model)
    return image_text_service.estimate(
        payload.width, payload.height, engine, model=model,
        free_tier=payload.free_tier)


# Deletes are PC-only (docs/remote-access-decision.md).
@router.delete("/history", dependencies=[local_only()], response_model=ClearHistoryResult,
              summary="Clear standalone-translate history (requires confirm=true)",
              responses=error_responses(422))
def delete_history(confirm: bool = Query(False)):
    return translate_service.clear_history(confirm=confirm)
