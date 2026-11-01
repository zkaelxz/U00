"""
api/server.py -- the FastAPI application: Baihe's HTTP API.

It serves the React app and the JSON routes over the `services/` layer, and
reads the same `library/` folder as the `python -m api` admin commands (the
two never call each other over HTTP). See `docs/archive/migration-react-fastapi.md` for the history.

Run it with `python -m api` (reads `BAIHE_API_*`, see
`api/api_config.py`). With `BAIHE_API_AUTH=off` (the default) every
non-loopback request is refused (`api.auth.LoopbackOnlyGate`), whatever
address uvicorn was told to bind. Interactive API docs are served at
`/api/docs` and the OpenAPI schema at `/api/openapi.json` with auth off
only.

`create_app(listener="household")` builds the second, household app that
`python -m api` serves on `BAIHE_API_HOUSEHOLD_PORT` in the same process
(see `api/api_config.py`): sign-in on, no background services, no docs,
never "the PC", only the `BAIHE_PUBLIC_URL` host, security headers on
every reply (`api.auth.HouseholdGate`).
"""

# Must run before any other app import (see portable.py's docstring).
import portable
portable.activate_portable_mode()

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.api_config import ApiSettings, check_bind_safety, household_settings, load_settings
from api.auth import (ActingPrincipalMiddleware, EarlyAuthGate, HouseholdGate,
                      LocalOnlyCrossSiteGate, LoopbackOnlyGate, local_only_matchers,
                      public_api_paths)
from api.device_token_paths import device_token_paths
from api.error_handlers import install_error_handlers
from api.remote_uploads import RemoteUploadGate, upload_matchers
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
    device_token_routes,
    member_key_routes,
    diagnostics_browser_routes,
    diagnostics_gaps_routes,
    disk_usage_routes,
    diagnostics_installs_routes,
    pending_install_routes,
    diagnostics_routes,
    setup_routes,
    real_model_check_routes,
    diarization_routes,
    source_suspect_routes,
    timing_check_routes,
    discover_lookup_routes,
    discover_routes,
    drama_routes,
    events_routes,
    listen_routes,
    engine_routing_routes,
    stronger_engine_routes,
    export_routes,
    extension_bridge_routes,
    extension_routes,
    glossary_routes,
    language_pack_routes,
    job_stage_routes,
    jellyfin_routes,
    jobs_routes,
    queue_routes,
    quick_video_routes,
    library_admin_routes,
    library_routes,
    link_details_routes,
    line_ai_routes,
    lines_routes,
    live_follow_routes,
    live_routes,
    media_routes,
    metadata_research_routes,
    metadata_routes,
    model_reeval_routes,
    model_registry_routes,
    narration_routes,
    notification_center_routes,
    notification_routes,
    novel_files_routes,
    novel_routes,
    reader_routes,
    restructure_routes,
    saved_comics_routes,
    scanlate_routes,
    review_extras_routes,
    review_jobs_routes,
    review_lines_routes,
    review_records_routes,
    series_people_routes,
    series_routes,
    loaded_models_routes,
    settings_routes,
    sharing_routes,
    source_domains_routes,
    source_routes,
    source_update_routes,
    sources_catalog_routes,
    sources_extraction_routes,
    sources_import_routes,
    sources_local_routes,
    sources_search_routes,
    sources_tools_routes,
    subtitle_import_routes,
    system_routes,
    transcribe_routes,
    translate_routes,
    translate_run_routes,
    translation_version_routes,
    spend_history_routes,
    update_routes,
    usage_recost_routes,
    vdb_names_routes,
    voice_bank_audio_routes,
    voice_clone_routes,
    web_search_routes,
    workflow_routes,
)
from api.schemas import API_VERSION
from api.static_frontend import install_frontend
from lib import features
from services import auth_service
# Imported for its side effect: it registers the monthly-cap check that
# engine_backends.paid_calls applies to paid calls made outside a job.
from services import paid_job_service  # noqa: F401


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Starts the background pieces (chapter-check
    scheduler, extension endpoint when enabled, the GPU-queue re-check),
    only when `settings.background_services` is on -- never in tests.
    Idempotent. The GPU-queue re-check is stopped at shutdown, any
    running lightnovel-crawler import is cancelled and its program killed,
    and job records left running by a dead process are closed, as
    are stale sign-in sessions (expired, idle or of a deactivated user);
    the exit flush of this server's own job rows is registered here, not
    at import, so tests and GPU workers never run it.
    The household listener's app does none of this, at start or stop: it
    shares the process with the admin listener, whose lifespan owns it."""
    if getattr(app.state.settings, "is_household", False):
        yield
        return
    from engine_backends import echo
    from services import assistant_settings_service, lncrawl_service
    echo.set_enabled(assistant_settings_service.developer_mode_enabled())
    if not getattr(app.state.settings, "background_services", False):
        try:
            yield
        finally:
            lncrawl_service.shutdown()
        return
    from jobs import job_store
    from services import jobs_service
    job_store.register_exit_flush()
    try:
        jobs_service.sweep_stale_job_records()
    except Exception:
        logging.getLogger(__name__).warning("Stale job-record sweep failed", exc_info=True)
    from jobs import gpu_slots
    try:
        gpu_slots.reap()
    except Exception:
        logging.getLogger(__name__).warning("Orphaned GPU slot release failed", exc_info=True)
    from services import auth_service
    try:
        auth_service.sweep_stale_sessions()
    except Exception:
        logging.getLogger(__name__).warning("Stale session sweep failed", exc_info=True)
    try:
        auth_service.prune_audit_log()
    except Exception:
        logging.getLogger(__name__).warning("Activity log pruning failed", exc_info=True)
    from api.background import (start_background_services, start_gpu_queue_poller,
                                start_reeval_scheduler, start_remote_health_monitor,
                                stop_gpu_queue_poller, stop_reeval_scheduler,
                                stop_remote_health_monitor)
    start_background_services(app.state.settings.features_off)
    start_gpu_queue_poller()
    start_reeval_scheduler()
    start_remote_health_monitor(app.state.settings)
    from services import live_follow_poller, queue_service
    queue_service.start_runner()
    try:
        live_follow_poller.start_if_needed()
    except Exception:
        logging.getLogger(__name__).warning("Followed-channel checks did not start", exc_info=True)
    try:
        yield
    finally:
        live_follow_poller.stop()
        queue_service.stop_runner()
        stop_gpu_queue_poller()
        lncrawl_service.shutdown()

        stop_reeval_scheduler()
        stop_remote_health_monitor()


_ROUTER_MODULES = (
    system_routes, update_routes, source_update_routes, library_routes, reader_routes, diagnostics_routes, setup_routes,
    jobs_routes, queue_routes, quick_video_routes, events_routes, job_stage_routes, loaded_models_routes,
    settings_routes, translate_routes, export_routes, diarization_routes, source_suspect_routes,
    timing_check_routes, source_routes, transcribe_routes, subtitle_import_routes, listen_routes,
    drama_routes, translate_run_routes, characters_routes, glossary_routes, language_pack_routes,
    vdb_names_routes, review_lines_routes, review_records_routes, lines_routes, artifact_routes,
    media_routes, link_details_routes, narration_routes, metadata_routes, metadata_research_routes,
    jellyfin_routes, novel_routes, review_jobs_routes, review_extras_routes, line_ai_routes,
    restructure_routes, discover_routes, web_search_routes, sources_catalog_routes,
    source_domains_routes, workflow_routes, live_routes, live_follow_routes, discover_lookup_routes,
    sources_search_routes, sources_import_routes, saved_comics_routes, sources_extraction_routes,
    sources_local_routes, diagnostics_gaps_routes, extension_routes, extension_bridge_routes,
    library_admin_routes,
    backup_routes, disk_usage_routes, delete_routes, translation_version_routes,
    blocked_retry_routes, notification_routes, notification_center_routes, asr_options_routes,
    usage_recost_routes, spend_history_routes, comic_routes, scanlate_routes, engine_routing_routes,
    stronger_engine_routes, series_people_routes, series_routes, auth_routes, admin_users_routes,
    device_token_routes, member_key_routes, voice_clone_routes, bug_report_routes,
    novel_files_routes, benchmark_routes, model_registry_routes, model_reeval_routes,
    diagnostics_installs_routes, pending_install_routes, diagnostics_browser_routes,
    real_model_check_routes, voice_bank_audio_routes, sources_tools_routes, assistant_routes,
    sharing_routes,
)


# Routers a feature switch takes out when it is off (lib/features.py). Anything
# the other areas call into stays mounted so an off switch never breaks them:
# sources_tools_routes holds Discover's pasted-listing job and the novel URL
# import, and the comic source-chapter routes live with the comic routes.
_FEATURE_ROUTERS = {
    "jellyfin": (jellyfin_routes,),
    "sources": (sources_catalog_routes, source_domains_routes, sources_search_routes,
                sources_import_routes, sources_extraction_routes, sources_local_routes),
    "extension": (extension_routes, extension_bridge_routes),
}


def _active_router_modules(features_off) -> tuple:
    off = {m for name in features_off for m in _FEATURE_ROUTERS[name]}
    return tuple(m for m in _ROUTER_MODULES if m not in off)


def _install_middleware(app: FastAPI, settings: ApiSettings) -> None:
    """Each add_middleware wraps the ones added before it, so the last one
    added runs first on a request."""
    if settings.auth_enabled:
        # Innermost: gives each request a holder for "who started this job"
        # (api.auth.ActingPrincipalMiddleware).
        app.add_middleware(ActingPrincipalMiddleware)
        # Inside EarlyAuthGate: needs its session and CSRF checks done first.
        app.add_middleware(RemoteUploadGate, upload_fn=lambda: upload_matchers(app))
        # Added before CORS so CORS stays the outermost layer (dev preflight).
        app.add_middleware(EarlyAuthGate, public_paths_fn=lambda: (public_api_paths(app)
                                                    | device_token_paths(app)),
                           local_only_fn=lambda: local_only_matchers(app),
                           never_local=settings.is_household)
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
    if settings.is_household:
        # Outermost: checks the Host before anything else runs and adds the
        # security headers to every reply, the other gates' refusals included.
        app.add_middleware(HouseholdGate, public_url=settings.public_url)


def create_app(settings: ApiSettings | None = None, frontend_dist=None,
               listener: str = "admin") -> FastAPI:
    """Builds the app. Touches no database or optional package, so it's
    safe to call at import time and in tests; the library is opened
    lazily by the first request that needs it (`db._ensure_ready`).
    `listener="household"` builds the household app from the admin
    listener's settings (`api_config.household_settings`, which refuses an
    unsafe household port or host)."""
    settings = settings or load_settings()
    if listener == "household":
        settings = household_settings(settings)
    elif listener != "admin" or settings.is_household:
        raise ValueError("listener must be 'admin' or 'household'.")
    app = FastAPI(
        title="Baihe Studio API",
        version=API_VERSION,
        description="HTTP API for Baihe Studio. "
                    "Authentication is off by default (local use); "
                    "set BAIHE_API_AUTH=on to enforce sessions and permissions.",
        # With auth on, the interactive docs/schema would publish every route
        # to anyone who can reach the port, so they are not served.
        docs_url=None if settings.auth_enabled else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.auth_enabled else "/api/openapi.json",
        lifespan=_lifespan,
    )
    app.state.settings = settings
    if settings.features_unknown and not settings.is_household:
        logging.getLogger(__name__).warning("%s names no feature switch: %s", features.ENV_VAR,
                                            ", ".join(settings.features_unknown))
    # Refuses a non-loopback BAIHE_API_HOST while auth is off (same check as
    # `python -m api`); a bind given straight to uvicorn (--host) isn't
    # visible here, which is why LoopbackOnlyGate refuses remote requests too.
    check_bind_safety(settings)
    # Process-wide: both listeners are built from the same settings.
    auth_service.configure_timeouts(settings.session_idle_days * 86400,
                                    settings.session_max_days * 86400)
    _install_middleware(app, settings)
    install_error_handlers(app)
    # Order matters: the first route that matches a path wins.
    for module in _active_router_modules(settings.features_off):
        app.include_router(module.router)
    if settings.serve_frontend:
        install_frontend(app, frontend_dist)  # last: /api routes match first
    return app


app = create_app()
