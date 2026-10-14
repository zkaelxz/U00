"""
api/routers/vdb_names_routes.py -- the optional personal VTuber name list.
Thin: see services/vdb_names_service.py.

- `GET /api/vdb-names` (`library.read`): whether the list is downloaded, counts, age.
- `POST /api/vdb-names/fetch`, `DELETE /api/vdb-names` (`local_only`): download
  or remove the file. They reach the internet and write to the data folder of
  this PC, which no household member should do from elsewhere.
- `GET|POST /api/vdb-names/dramas/{drama_id}` (`library.read` / `lines.edit`):
  the title's opt-in.
"""

from fastapi import APIRouter, Path, Request

from api.auth import local_only, require_permission
from api.schemas import TitleVdbNames, TitleVdbNamesChoice, VdbNamesStatus, error_responses
from services import vdb_names_service

router = APIRouter(prefix="/api/vdb-names", tags=["glossary"])


@router.get("", dependencies=[require_permission("library.read")], response_model=VdbNamesStatus,
            summary="Whether the VTuber name list is downloaded (counts only)")
def get_status():
    return vdb_names_service.status()


@router.post("/fetch", dependencies=[local_only()], response_model=VdbNamesStatus,
             summary="Download the VTuber name list into the data folder",
             responses=error_responses(409, 503))
def post_fetch():
    return vdb_names_service.fetch()


@router.delete("", dependencies=[local_only()], response_model=VdbNamesStatus,
               summary="Delete the downloaded VTuber name list",
               responses=error_responses(503))
def delete_names():
    return vdb_names_service.delete()


@router.get("/dramas/{drama_id}", dependencies=[require_permission("library.read")],
            response_model=TitleVdbNames, summary="Whether this title uses the VTuber names",
            responses=error_responses(404))
def get_title(request: Request, drama_id: int = Path(ge=1)):
    return vdb_names_service.get_title(drama_id, principal=request.state.principal)


@router.post("/dramas/{drama_id}", dependencies=[require_permission("lines.edit")],
             response_model=TitleVdbNames, summary="Turn the VTuber names on or off for this title",
             responses=error_responses(403, 404))
def set_title(request: Request, payload: TitleVdbNamesChoice, drama_id: int = Path(ge=1)):
    return vdb_names_service.set_title(drama_id, payload.enabled, principal=request.state.principal)
