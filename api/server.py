"""
api/server.py -- the FastAPI application: Baihe's HTTP API.

Stage one of the React + FastAPI migration (see
`docs/migration-react-fastapi.md`). This runs *alongside* the Streamlit
app, not instead of it: both import the same modules and read the same
`library/` folder, and neither calls the other over HTTP. Streamlit
still owns every feature; this API exposes only what has been moved
into `services/` so far.

Run it with `python -m api` (reads `BAIHE_API_*`, see
`api/api_config.py`), or `uvicorn api.server:app` directly. Interactive
API docs are served at `/api/docs`; the OpenAPI schema at
`/api/openapi.json`.
"""

# Must run before any other app import -- same rule, and same reason, as
# app.py and cli.py (see portable.py's docstring).
import portable
portable.activate_portable_mode()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.api_config import ApiSettings, load_settings
from api.error_handlers import install_error_handlers
from api.routers import (
    characters_routes,
    diagnostics_routes,
    diarization_routes,
    drama_routes,
    dub_routes,
    export_routes,
    glossary_routes,
    jobs_routes,
    library_routes,
    lines_routes,
    reader_routes,
    review_lines_routes,
    review_records_routes,
    settings_routes,
    source_routes,
    system_routes,
    transcribe_routes,
    translate_routes,
    translate_run_routes,
)
from api.schemas import API_VERSION


def create_app(settings: ApiSettings = None) -> FastAPI:
    """Builds the app. Touches no database or optional package, so it's
    safe to call at import time and in tests; the library is opened
    lazily by the first request that needs it (`db._ensure_ready`)."""
    settings = settings or load_settings()
    app = FastAPI(
        title="Baihe Studio API",
        version=API_VERSION,
        description="HTTP API for Baihe Studio. Runs alongside the Streamlit app and "
                    "shares its library. Local/trusted-network use only; no authentication.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
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
    app.include_router(lines_routes.router)
    app.include_router(review_records_routes.router)
    return app


app = create_app()
