"""
api/routers/real_model_check_routes.py -- the opt-in real-model check in
Diagnostics (thin; see services/real_model_check_service.py). Starting it
is `local_only()` + confirm=true (409 while anything else runs, since it
uses the GPU); the latest result is `admin.diagnostics`. Progress is also
visible through GET /api/jobs/{job_id}.
"""

from fastapi import APIRouter

from api.auth import local_only, require_permission
from api.schemas import (ErrorResponse, RealModelCheckStart, RealModelCheckStarted,
                         RealModelCheckState)
from services import real_model_check_service as svc

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/real-model-check", dependencies=[require_permission("admin.diagnostics")],
            response_model=RealModelCheckState,
            summary="The latest real-model check: per-check pass, fail or skipped")
def get_real_model_check():
    return svc.get_state()


@router.post("/real-model-check", dependencies=[local_only()],
             response_model=RealModelCheckStarted,
             summary="PC only: run one transcription, OCR read and Ollama translation "
                     "(confirm=true; background job, uses the GPU)",
             responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_real_model_check(body: RealModelCheckStart):
    return svc.start(confirm=body.confirm)
