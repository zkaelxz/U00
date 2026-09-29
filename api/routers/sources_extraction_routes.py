"""
api/routers/sources_extraction_routes.py -- the pasted-URL extraction
extras (Streamlit Sources parity SO09). Thin: see
services/sources_extraction_service.py.

Same /api/sources prefix as the other Sources routers. The paths here have
a fixed first segment ("url/..."), so none can be read as a source
`{name}`.
"""

from fastapi import APIRouter

from api.auth import require_permission
from api.sources_extraction_schemas import SourcesAiEngines
from services import sources_extraction_service as svc

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("/url/ai-engines", dependencies=[require_permission("sources.import")],
            response_model=SourcesAiEngines,
            summary="Engines the pasted-URL AI fallback can use, and the saved default")
def get_ai_engines():
    return svc.engines_view()
