"""
api/server.py -- the FastAPI application: Baihe's HTTP API.

Stage one of the React + FastAPI migration (see
`docs/archive/migration-react-fastapi.md`). This runs *alongside* the Streamlit
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

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.api_config import ApiSettings, check_bind_safety, load_settings
from api.auth import (ActingPrincipalMiddleware, EarlyAuthGate, LocalOnlyCrossSiteGate,
                      LoopbackOnlyGate, local_only_matchers, public_api_paths)
from api.error_handlers import install_error_handlers
from api.routers import (
    admin_users_routes,
    artifact_routes,
    assistant_routes,
    asr_options_routes,
    auth_routes,
    backup_routes,
    benchmark_routes,
    blocked_retry_routes,
    bug_report_routes,
    characters_routes,
    comic_routes,
    delete_routes,
    diagnostics_gaps_routes,
    diagnostics_installs_routes,
    diagnostics_routes,
    diarization_routes,
    discover_lookup_routes,
    discover_routes,
    drama_routes,
    events_routes,
    dub_routes,
    engine_routing_routes,
    stronger_engine_routes,
    export_routes,
    extension_routes,
    glossary_routes,
    job_stage_routes,
    jellyfin_routes,
    jobs_routes,
    library_admin_routes,
    library_routes,
    line_ai_routes,
    lines_routes,
    live_routes,
    media_routes,
    metadata_research_routes,
    metadata_routes,
    model_reeval_routes,
    model_registry_routes,
    narration_routes,
    notification_center_routes,
    notification_routes,
    notion_routes,
    novel_files_routes,
    novel_routes,
    reader_routes,
    restructure_routes,
    scanlate_routes,
    review_extras_routes,
    review_jobs_routes,
    review_lines_routes,
    review_records_routes,
    series_people_routes,
    settings_routes,
    source_routes,
    sources_catalog_routes,
    sources_extraction_routes,
    sources_import_routes,
    sources_local_routes,
    sources_search_routes,
    sources_tools_routes,
    system_routes,
    transcribe_routes,
    translate_routes,
    translate_run_routes,
    translation_version_routes,
    voice_bank_audio_routes,
    voice_clone_routes,
    web_search_routes,
    workflow_routes,
)
from api.schemas import API_VERSION
from api.static_frontend import install_frontend


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Starts the background pieces Streamlit used to start (chapter-check
    scheduler, extension endpoint when enabled, the GPU-queue re-check),
    only when `settings.background_services` is on -- never in tests.
    Idempotent. The GPU-queue re-check is stopped at shutdown, any
    running lightnovel-crawler import is cancelled and its program killed,
    and job records left running by a dead process are closed (B-04)."""
    from services import lncrawl_service
    if not getattr(app.state.settings, "background_services", False):
        try:
            yield
        finally:
            lncrawl_service.shutdown()
        return
    from services import jobs_service
    try:
        jobs_service.sweep_stale_job_records()
    except Exception:
        logging.getLogger(__name__).warning("Stale job-record sweep failed", exc_info=True)
    from api.background import (start_background_services, start_gpu_queue_poller,
                                start_reeval_scheduler, stop_gpu_queue_poller,
                                stop_reeval_scheduler)
    start_background_services()
    start_gpu_queue_poller()
    start_reeval_scheduler()
    try:
        yield
    finally:
        stop_gpu_queue_poller()
        lncrawl_service.shutdown()

        stop_reeval_scheduler()


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
        # Innermost: gives each request a holder for "who started this job"
        # (auth B2, api.auth.ActingPrincipalMiddleware).
        app.add_middleware(ActingPrincipalMiddleware)
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
    app.include_router(events_routes.router)
    app.include_router(job_stage_routes.router)
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
    app.include_router(metadata_research_routes.router)
    app.include_router(jellyfin_routes.router)
    app.include_router(notion_routes.router)
    app.include_router(novel_routes.router)
    app.include_router(review_jobs_routes.router)
    app.include_router(review_extras_routes.router)
    app.include_router(line_ai_routes.router)
    app.include_router(restructure_routes.router)
    app.include_router(discover_routes.router)
    app.include_router(web_search_routes.router)
    app.include_router(sources_catalog_routes.router)
    app.include_router(workflow_routes.router)
    app.include_router(live_routes.router)
    app.include_router(discover_lookup_routes.router)
    app.include_router(sources_search_routes.router)
    app.include_router(sources_import_routes.router)
    app.include_router(sources_extraction_routes.router)
    app.include_router(sources_local_routes.router)
    app.include_router(diagnostics_gaps_routes.router)
    app.include_router(extension_routes.router)
    app.include_router(library_admin_routes.router)
    app.include_router(backup_routes.router)
    app.include_router(delete_routes.router)
    app.include_router(translation_version_routes.router)
    app.include_router(blocked_retry_routes.router)
    app.include_router(notification_routes.router)
    app.include_router(notification_center_routes.router)
    app.include_router(asr_options_routes.router)
    app.include_router(comic_routes.router)
    app.include_router(scanlate_routes.router)
    app.include_router(engine_routing_routes.router)
    app.include_router(stronger_engine_routes.router)
    app.include_router(series_people_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(admin_users_routes.router)
    app.include_router(voice_clone_routes.router)
    app.include_router(bug_report_routes.router)
    app.include_router(novel_files_routes.router)
    app.include_router(benchmark_routes.router)
    app.include_router(model_registry_routes.router)
    app.include_router(model_reeval_routes.router)

    app.include_router(diagnostics_installs_routes.router)
    app.include_router(voice_bank_audio_routes.router)
    app.include_router(sources_tools_routes.router)
    app.include_router(assistant_routes.router)
    if settings.serve_frontend:
        install_frontend(app, frontend_dist)  # last: /api routes match first
    return app


app = create_app()
