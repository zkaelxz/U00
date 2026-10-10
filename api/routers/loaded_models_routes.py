"""
api/routers/loaded_models_routes.py -- Settings "Loaded now" panel.

The read is an ordinary Settings read. Freeing the app's models is PC only
(`local_only()`) with confirm=true, like the other Settings writes: it
changes the PC's GPU memory and would stall a remote user's next job.
"""

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from api.auth import local_only, require_permission
from api.routers.settings_routes import read_body, require_confirm
from api.schemas import FreeAppModelsRequest, LoadedModels
from services import loaded_models_service

router = APIRouter(prefix="/api/settings/loaded-models", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")], response_model=LoadedModels,
            summary="What Ollama, this app and the GPU have loaded right now (read-only)")
async def get_loaded_models():
    # Probes can take a couple of seconds, so off the event loop.
    return await run_in_threadpool(loaded_models_service.get_loaded_models)


@router.post("/free-app-models", dependencies=[local_only()], response_model=LoadedModels,
             summary="PC only: drop the app's cached models (refused while a GPU job runs)")
async def free_app_models(request: Request):
    body = await read_body(request, FreeAppModelsRequest)
    require_confirm(body.confirm)
    return await run_in_threadpool(loaded_models_service.free_app_models)
