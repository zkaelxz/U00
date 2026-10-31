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

import applog
import translate_engines
from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from api.auth import local_only, require_permission
from api.llm_slots import llm_slot
from api.schemas import (ClearHistoryResult, ReadImageEstimate, ReadImageEstimateRequest,
                         ReadImageResponse, TranslateEngineListResponse,
                         TranslateEstimate, TranslateHistoryResponse, TranslateRequest,
                         TranslateResponse, error_responses)
from lib.capped_request import read_capped_form
from services import image_text_service, page_import_limits, settings_service, translate_service
from services.service_errors import DependencyUnavailableError, InvalidInputError, ServiceError

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


@router.post("", dependencies=[require_permission("engines.paid")], response_model=TranslateResponse,
             response_model_exclude_none=True,
            summary="Translate text with a chosen engine (server-side key resolution, D2)",
            responses=error_responses(400, 422, 429, 503))
def post_translate(payload: TranslateRequest, request: Request):
    # Synchronous LLM call: capped by the shared slot pool (api/llm_slots.py).
    with llm_slot(request):
        return translate_service.translate(
            payload.text, payload.engine, payload.source_language, payload.target_language,
            model=payload.model, free_tier=payload.free_tier,
            principal=request.state.principal)


@router.post("/estimate", dependencies=[require_permission("engines.paid")], response_model=TranslateEstimate,
             summary="What translating this text would cost, and what is left of the monthly cap",
             responses=error_responses(400, 422))
def post_translate_estimate(payload: TranslateRequest):
    return translate_service.estimate(
        payload.text, payload.engine, payload.source_language, payload.target_language,
        model=payload.model, free_tier=payload.free_tier)


_IMAGE_TOO_LARGE = (f"The picture is larger than {page_import_limits.MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
# Uploads are PC-only (docs/remote-access-decision.md), so local_only() refuses a
# remote caller before any of the body is read.
# Room for the multipart boundaries and the four short text fields.
_IMAGE_BODY_MAX = page_import_limits.MAX_IMAGE_BYTES + 64 * 1024
_READ_IMAGE_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["image", "engine"],
    "properties": {"image": {"type": "string", "format": "binary"},
                   "engine": {"type": "string"}, "model": {"type": "string"},
                   "language": {"type": "string"}, "free_tier": {"type": "string"}}}}}}}


@router.post("/read-image", dependencies=[local_only()],
             response_model=ReadImageResponse, response_model_exclude_none=True,
             openapi_extra=_READ_IMAGE_BODY,
             summary="PC only: read the text written in a picture with a vision engine",
             responses=error_responses(400, 413, 422, 429, 503))
async def post_read_image(request: Request):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise InvalidInputError("Send the picture as multipart/form-data.")
    form = await read_capped_form(
        request, _IMAGE_BODY_MAX, "the picture", _IMAGE_TOO_LARGE,
        "The picture form could not be read.", max_files=1, max_fields=4)
    try:
        upload = form.get("image")
        engine = form.get("engine")
        if not isinstance(upload, UploadFile) or not isinstance(engine, str) or not engine:
            raise InvalidInputError("Send an image and an engine.")
        content = await upload.read(page_import_limits.MAX_IMAGE_BYTES + 1)
        if len(content) > page_import_limits.MAX_IMAGE_BYTES:
            raise InvalidInputError(_IMAGE_TOO_LARGE)
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
    finally:
        await form.close()
    free_tier = {"true": True, "false": False}.get(fields.get("free_tier", ""))
    # Synchronous provider call: capped by the shared slot pool (api/llm_slots.py).
    with llm_slot(request):
        try:
            return await run_in_threadpool(
                image_text_service.read_image, content, engine, fields.get("model") or None,
                fields.get("language", ""), free_tier)
        except ServiceError:
            raise
        except Exception as e:  # the provider's own reason, without a key
            reason = translate_engines.redact_secrets(str(e))[:300] or type(e).__name__
            applog.get_logger().warning("Read from a picture failed: %s", reason)
            raise DependencyUnavailableError(f"The engine call failed: {reason}") from None


@router.post("/read-image/estimate", dependencies=[local_only()],
             response_model=ReadImageEstimate,
             summary="What reading a picture of this size would cost, and what is left of the monthly cap",
             responses=error_responses(400, 422))
def post_read_image_estimate(payload: ReadImageEstimateRequest):
    return image_text_service.estimate(
        payload.width, payload.height, payload.engine, model=payload.model,
        free_tier=payload.free_tier)


# Deletes are PC-only (docs/remote-access-decision.md).
@router.delete("/history", dependencies=[local_only()], response_model=ClearHistoryResult,
              summary="Clear standalone-translate history (requires confirm=true)",
              responses=error_responses(422))
def delete_history(confirm: bool = Query(False)):
    return translate_service.clear_history(confirm=confirm)
