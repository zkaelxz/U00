"""
api/routers/member_key_routes.py -- a signed-in member's own Ollama API key.
Thin: the rules live in `services/member_key_service.py`.

    GET    /api/auth/ollama-key     jobs.start   mine: set or not, last 4 characters
    POST   /api/auth/ollama-key     jobs.start   save or replace mine
    DELETE /api/auth/ollama-key     signed in    remove mine (even after losing jobs.start)

The user id comes from the session, never the request, so a member only
ever touches their own key. No response carries the key: the most it shows
is the last four characters. An admin removes a member's key from the Users
list (`DELETE /api/admin/users/{user_id}/ollama-key`, admin_users_routes).
With sign-in off they answer 404 (the owner at the PC has no account and
uses the PC's own `ollama signin`). Every response is `Cache-Control: no-store`.
`GET /api/auth/me` also says whether the caller has a usable key
(`own_ollama_key`), so pickers can offer cloud models.
"""

from fastapi import APIRouter, Request

from api.auth import authenticated, is_auth_enabled, require_permission
from api.no_store import NO_STORE_FULL, no_store_json
from api.schemas import MemberKeySaveRequest, MemberKeyStatus, error_responses
from services import member_key_service as svc
from services.service_errors import NotFoundError

router = APIRouter(prefix="/api/auth", tags=["auth"])

_PERMISSION = [require_permission("jobs.start")]


def _own_user_id(request: Request):
    if not is_auth_enabled(request.app):
        raise NotFoundError("Not found.")
    return request.state.principal["user_id"]


@router.get("/ollama-key", dependencies=_PERMISSION, response_model=MemberKeyStatus,
            summary="Whether I saved my own Ollama key (never the key)")
def get_own(request: Request):
    return no_store_json(MemberKeyStatus(**svc.status(_own_user_id(request))), NO_STORE_FULL)


@router.post("/ollama-key", dependencies=_PERMISSION, response_model=MemberKeyStatus,
             summary="Save or replace my own Ollama key", responses=error_responses(422))
def save_own(body: MemberKeySaveRequest, request: Request):
    return no_store_json(MemberKeyStatus(**svc.save(_own_user_id(request), body.key)), NO_STORE_FULL)


@router.delete("/ollama-key", dependencies=[authenticated()], response_model=MemberKeyStatus,
               summary="Remove my own Ollama key")
def remove_own(request: Request):
    user_id = _own_user_id(request)
    svc.remove(user_id, actor_id=user_id)
    return no_store_json(MemberKeyStatus(**svc.status(user_id)), NO_STORE_FULL)
