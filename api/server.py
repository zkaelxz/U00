"""
api/server.py -- the FastAPI application: Baihe's HTTP API.

Stage one of the React + FastAPI migration (see
`docs/migration-react-fastapi.md`). This runs *alongside* the Streamlit
app, not instead of it: both import the same modules and read the same
`library/` folder, and neither calls the other over HTTP. Streamlit
still owns every feature; this API exposes only what has been moved
into `services/` so far.

Run it with `python -m api` (reads `BAIHE_API_*`, see
`api/api_config.py`). With `BAIHE_API_AUTH=off` (the default) every
non-loopback request is refused (`api.auth.LoopbackOnlyGate`), whatever
address uvicorn was told to bind. Interactive API docs are served at
`/api/docs` and the OpenAPI schema at `/api/openapi.json` with auth off
only.
"""

# Must run before any other app import -- same rule, and same reason, as
# app.py and cli.py (see portable.py's docstring).
import portable
portable.activate_portable_mode()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.api_config import ApiSettings, check_bind_safety, load_settings
from api.auth import (EarlyAuthGate, LocalOnlyCrossSiteGate, LoopbackOnlyGate, local_only_matchers,
                      public_api_paths)
from api.error_handlers import install_error_handlers
from api.routers import (
    artifact_routes,
    characters_routes,
    delete_routes,
    diagnostics_gaps_routes,
    diagnostics_routes,
    diarization_routes,
    discover_lookup_routes,
    discover_routes,
    drama_routes,
    dub_routes,
    export_routes,
    extension_routes,
    glossary_routes,
    jobs_routes,
    library_admin_routes,
    library_routes,
    line_ai_routes,
    lines_routes,
    live_routes,
    media_routes,
    metadata_routes,
    narration_routes,
    novel_routes,
    reader_routes,
    restructure_routes,
    review_jobs_routes,
    review_lines_routes,
    review_records_routes,
    settings_routes,
    source_routes,
    sources_catalog_routes,
    sources_search_routes,
    system_routes,
    transcribe_routes,
    translate_routes,
    translate_run_routes,
    workflow_routes,
)
from api.schemas import API_VERSION
from api.static_frontend import install_frontend


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Starts the background pieces Streamlit used to start (chapter-check
    scheduler, extension endpoint when enabled, the GPU-queue re-check),
    only when `settings.background_services` is on -- never in tests.
    Idempotent. The GPU-queue re-check is stopped at shutdown."""
    if not getattr(app.state.settings, "background_services", False):
        yield
        return
    from api.background import (start_background_services, start_gpu_queue_poller,
                                stop_gpu_queue_poller)
    start_background_services()
    start_gpu_queue_poller()
    try:
        yield
    finally:
        stop_gpu_queue_poller()


def create_app(settings: ApiSettings = None, frontend_dist=None) -> FastAPI:
    """Builds the app. Touches no database or optional package, so it's
    safe to call at import time and in tests; the library is opened
    lazily by the first request that needs it (`db._ensure_ready`)."""
    settings = settings or load_settings()
    app = FastAPI(
        title="Baihe Studio API",
        version=API_VERSION,
        description="HTTP API for Baihe Studio. Runs alongside the Streamlit app and "
                    "shares its library. Authentication is off by default (local use); "
                    "set BAIHE_API_AUTH=on to enforce sessions and permissions.",
        # With auth on, the interactive docs/schema would publish every route
        # to anyone who can reach the port, so they are not served.
        docs_url=None if settings.auth_enabled else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.auth_enabled else "/api/openapi.json",
        lifespan=_lifespan,
    )
    app.state.settings = settings
    # Refuses a non-loopback BAIHE_API_HOST while auth is off (same check as
    # `python -m api`); a bind given straight to uvicorn (--host) isn't
    # visible here, which is why LoopbackOnlyGate refuses remote requests too.
    check_bind_safety(settings)
    if settings.auth_enabled:
        # Added before CORS so CORS stays the outermost layer (dev preflight).
        app.add_middleware(EarlyAuthGate, public_paths_fn=lambda: public_api_paths(app),
                           local_only_fn=lambda: local_only_matchers(app))
    else:
        app.add_middleware(LoopbackOnlyGate)
    # Refuse a simple (no-preflight) POST before its body is read (see
    # api.auth._cross_site_safe): local_only routes in both modes, and every
    # /api POST/PUT/PATCH with auth off (no CSRF token there).
    app.add_middleware(LocalOnlyCrossSiteGate, local_only_fn=lambda: local_only_matchers(app),
                       all_api=not settings.auth_enabled)
    if settings.is_development and settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )
    install_error_handlers(app)
    app.include_router(system_routes.router)
    app.include_router(library_routes.router)
    app.include_router(reader_routes.router)
    app.include_router(diagnostics_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(translate_routes.router)
    app.include_router(export_routes.router)
    app.include_router(diarization_routes.router)
    app.include_router(source_routes.router)
    app.include_router(transcribe_routes.router)
    app.include_router(dub_routes.router)
    app.include_router(drama_routes.router)
    app.include_router(translate_run_routes.router)
    app.include_router(characters_routes.router)
    app.include_router(glossary_routes.router)
    app.include_router(review_lines_routes.router)
    app.include_router(review_records_routes.router)
    app.include_router(lines_routes.router)
    app.include_router(artifact_routes.router)
    app.include_router(media_routes.router)
    app.include_router(narration_routes.router)
    app.include_router(metadata_routes.router)
    app.include_router(novel_routes.router)
    app.include_router(review_jobs_routes.router)
    app.include_router(line_ai_routes.router)
    app.include_router(restructure_routes.router)
    app.include_router(discover_routes.router)
    app.include_router(sources_catalog_routes.router)
    app.include_router(workflow_routes.router)
    app.include_router(live_routes.router)
    app.include_router(discover_lookup_routes.router)
    app.include_router(sources_search_routes.router)
    app.include_router(diagnostics_gaps_routes.router)
    app.include_router(extension_routes.router)
    app.include_router(library_admin_routes.router)
    app.include_router(delete_routes.router)
    if settings.serve_frontend:
        install_frontend(app, frontend_dist)  # last: /api routes match first
    return app


app = create_app()
