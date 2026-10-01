"""
api/routers/admin_users_routes.py -- user administration and the read-only
audit log view. Thin: the rules live in `services/auth_service.py`.

    GET  /api/admin/users                            list users
    POST /api/admin/users/{user_id}/deactivate       block + end their sessions
    POST /api/admin/users/{user_id}/activate         unblock
    POST /api/admin/users/{user_id}/revoke-sessions  sign them out everywhere
    GET  /api/admin/audit                            newest first, paged

The two reads are `admin.users.read`, the writes `admin.users` (both held
only by admins, and by the local owner with auth off), so with auth on the
writes also need the session's CSRF token. On the household listener an
admin session holds `admin.users.read` only, so every write is refused
there (403) and admin changes happen at the PC. Guards (409): not your own
account, not the last active admin, not your own sessions. A write whose
target is an admin account is PC-only (403 from a remote session): on the
single-port sign-in setup remote admins manage non-admin accounts only.
Unknown user: 404. The service audits every write.
Nothing here creates users, changes permissions, or edits or deletes
audit rows.
"""

from typing import Optional

from fastapi import APIRouter, Path, Query, Request, Response

from api.admin_users_schemas import AdminSessionsRevoked, AdminUser, AdminUserList, AuditPage
from api.auth import is_local_request, require_permission
from services import auth_service

router = APIRouter(prefix="/api/admin", tags=["admin"])

_READ = [require_permission("admin.users.read")]
_ADMIN = [require_permission("admin.users")]
_MAX_ID = 2 ** 62   # past SQLite's integer range the lookup would raise, not 404
_UserId = Path(..., ge=1, le=_MAX_ID)


def _actor(request: Request):
    """The caller's user id; None for the local owner (auth off)."""
    return (getattr(request.state, "principal", None) or {}).get("user_id")


def _no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


@router.get("/users", dependencies=_READ, response_model=AdminUserList,
            summary="List users (no session details)")
def list_users(request: Request, response: Response):
    _no_store(response)
    return {"users": auth_service.admin_list_users(_actor(request))}


@router.post("/users/{user_id}/deactivate", dependencies=_ADMIN, response_model=AdminUser,
             summary="Deactivate a user and end their sessions")
def deactivate_user(request: Request, response: Response, user_id: int = _UserId):
    _no_store(response)
    return auth_service.admin_set_active(user_id, False, actor_id=_actor(request),
                                       at_pc=is_local_request(request))


@router.post("/users/{user_id}/activate", dependencies=_ADMIN, response_model=AdminUser,
             summary="Activate a user")
def activate_user(request: Request, response: Response, user_id: int = _UserId):
    _no_store(response)
    return auth_service.admin_set_active(user_id, True, actor_id=_actor(request),
                                       at_pc=is_local_request(request))


@router.post("/users/{user_id}/revoke-sessions", dependencies=_ADMIN,
             response_model=AdminSessionsRevoked, summary="End every session of a user")
def revoke_sessions(request: Request, response: Response, user_id: int = _UserId):
    _no_store(response)
    return auth_service.admin_revoke_sessions(user_id, actor_id=_actor(request),
                                            at_pc=is_local_request(request))


@router.get("/audit", dependencies=_READ, response_model=AuditPage,
            summary="Audit log, newest first (read-only)")
def list_audit(response: Response,
               limit: int = Query(50, ge=1, le=auth_service.AUDIT_PAGE_MAX),
               before_id: Optional[int] = Query(None, ge=1, le=_MAX_ID),
               action: Optional[str] = Query(None, min_length=1, max_length=80),
               user_id: Optional[int] = Query(None, ge=1, le=_MAX_ID)):
    _no_store(response)
    return auth_service.audit_page(limit, before_id=before_id, action=action, user_id=user_id)
