"""
api/routers/translation_version_routes.py -- make a saved translation
version the drama's current English. Thin adapter over
`services.translation_version_service`.

`lines.edit`: it rewrites only the `en` column of existing lines, by
permanent id. Needs `{"confirm": true}` (422 otherwise) because it
overwrites the current English; 409 while a job runs on the drama or when
the version was saved over a different set of lines.
"""

from fastapi import APIRouter, Path

from api.auth import require_permission
from api.schemas import (TranslationVersionActivateRequest,
                         TranslationVersionActivateResult, error_responses)
from lib.ids import MAX_ID
from services import translation_version_service

router = APIRouter(prefix="/api/review", tags=["review"])


@router.post("/dramas/{drama_id}/versions/{version_id}/activate",
             dependencies=[require_permission("lines.edit")],
             response_model=TranslationVersionActivateResult, responses=error_responses(404, 409, 422),
             summary="Use a saved translation version as the current English (confirm=true)")
def post_activate_version(body: TranslationVersionActivateRequest, drama_id: int = Path(ge=1),
                          version_id: int = Path(ge=1, le=MAX_ID)):
    return translation_version_service.activate_version(drama_id, version_id,
                                                        confirm=body.confirm)
