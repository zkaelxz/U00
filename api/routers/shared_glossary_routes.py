"""
api/routers/shared_glossary_routes.py -- shared glossary groups, over
services/shared_glossary_service.py.

    GET  /api/shared-glossaries                              library.read  every group (name, counts)
    POST /api/shared-glossaries                              lines.edit    create a group
    POST /api/shared-glossaries/{group_id}/delete            lines.edit    its creator or any admin
    GET  /api/shared-glossaries/{group_id}/terms             library.read
    POST /api/shared-glossaries/{group_id}/terms             lines.edit    its creator or an admin
    POST /api/shared-glossaries/{group_id}/terms/{term_id}/delete  lines.edit  its creator or an admin

A group the caller may not change answers 404 from the service. A series joins
or leaves a group through /api/series/{series_id}/shared-glossary.
"""

from typing import List

from fastapi import APIRouter, Path, Request

from api.auth import require_permission
from api.schemas import (SharedGlossaryTerm, SharedGlossaryTermWrite, SharedGlossaryCreateRequest,
                         SharedGlossaryDeleteResult, SharedGlossaryGroup, SharedGlossaryGroupList,
                         SharedGlossaryTermDeleteResult, error_responses)
from services import shared_glossary_service as svc

router = APIRouter(prefix="/api/shared-glossaries", tags=["shared-glossary"])

_ERRS = error_responses(403, 404, 409, 422)
_MAX_ID = 2**31 - 1


@router.get("", dependencies=[require_permission("library.read")],
            response_model=SharedGlossaryGroupList, summary="Every shared glossary group")
def list_groups(request: Request):
    return svc.list_groups(request.state.principal)


@router.post("", dependencies=[require_permission("lines.edit")],
             response_model=SharedGlossaryGroup, responses=_ERRS,
             summary="Create a shared glossary group (409 if the name is taken, ignoring case)")
def create_group(body: SharedGlossaryCreateRequest, request: Request):
    return svc.create_group(request.state.principal, body.name)


@router.post("/{group_id}/delete", dependencies=[require_permission("lines.edit")],
             response_model=SharedGlossaryDeleteResult, responses=_ERRS,
             summary="Delete a group and its terms; series that joined it keep their own terms")
def delete_group(request: Request, group_id: int = Path(ge=1, le=_MAX_ID)):
    return svc.delete_group(request.state.principal, group_id)


@router.get("/{group_id}/terms", dependencies=[require_permission("library.read")],
            response_model=List[SharedGlossaryTerm], responses=_ERRS, summary="A group's terms")
def list_terms(request: Request, group_id: int = Path(ge=1, le=_MAX_ID)):
    return svc.list_terms(request.state.principal, group_id)


@router.post("/{group_id}/terms", dependencies=[require_permission("lines.edit")],
             response_model=SharedGlossaryTerm, responses=_ERRS,
             summary="Create or update a group term (update when id is given)")
def save_term(body: SharedGlossaryTermWrite, request: Request, group_id: int = Path(ge=1, le=_MAX_ID)):
    return svc.save_term(request.state.principal, group_id, body.model_dump(exclude_unset=True))


@router.post("/{group_id}/terms/{term_id}/delete", dependencies=[require_permission("lines.edit")],
             response_model=SharedGlossaryTermDeleteResult, responses=_ERRS,
             summary="Delete one group term")
def delete_term(request: Request, group_id: int = Path(ge=1, le=_MAX_ID),
                term_id: int = Path(ge=1, le=_MAX_ID)):
    return svc.delete_term(request.state.principal, group_id, term_id)
