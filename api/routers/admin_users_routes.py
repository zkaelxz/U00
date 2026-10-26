"""
api/routers/admin_users_routes.py -- user administration and the read-only
audit log view. Thin: the rules live in `services/auth_service.py`.

    GET  /api/admin/users                            list users
    POST /api/admin/users/{user_id}/deactivate       block + end their sessions
    POST /api/admin/users/{user_id}/activate         unblock
    POST /api/admin/users/{user_id}/revoke-sessions  sign them out everywhere
    POST /api/admin/users/{user_id}/revoke-admin     make an admin a normal member
    DELETE /api/admin/users/{user_id}/ollama-key     remove their own Ollama key (never read it)
    POST /api/admin/users/{user_id}/permissions/{permission}    give a member one right
    DELETE /api/admin/users/{user_id}/permissions/{permission}  take it away
    GET  /api/admin/audit                            newest first, paged

The two reads are `admin.users.read`, the writes `admin.users` (both held
only by admins, and by the local owner with auth off), so with auth on the
writes also need the session's CSRF token. On the household listener an
admin session holds `admin.users.read` only, so every write is refused
there (403) and admin changes happen at the PC. Guards (409): not your own
account, not the last active admin, not your own sessions, revoke-admin
only on an admin. A write whose target is an admin account (revoke-admin
always is) is PC-only (403 from a remote session): on the single-port
sign-in setup remote admins manage non-admin accounts only.
The rights toggles change only an active member account (409 on an admin,
which holds every right through the flag, and so on the caller's own), and
only the member permissions the list returns as `member_permissions`; they
never make or unmake an admin. Unknown user: 404. The service audits every
write. Nothing here creates users or edits or deletes audit rows.
"""

from typing import Optional

from fastapi import APIRouter, Path, Query, Request, Response

from api.admin_users_schemas import AdminSessionsRevoked, AdminUser, AdminUserList, AuditPage
from api.schemas import MemberKeyRemoved
from api.auth import is_local_request, principal_user_id, require_permission
from api.no_store import mark_no_store
from services import auth_service, member_key_service

router = APIRouter(prefix="/api/admin", tags=["admin"])

_READ = [require_permission("admin.users.read")]
_ADMIN = [require_permission("admin.users")]
_MAX_ID = 2 ** 62   # past SQLite's integer range the lookup would raise, not 404
_UserId = Path(..., ge=1, le=_MAX_ID)
_Permission = Path(..., min_length=1, max_length=40)


@router.get("/users", dependencies=_READ, response_model=AdminUserList,
            summary="List users (no session details)")
def list_users(request: Request, response: Response):
    mark_no_store(response)
    return {"users": auth_service.admin_list_users(principal_user_id(request)),
            "member_permissions": list(auth_service.MEMBER_PERMISSIONS)}


@router.post("/users/{user_id}/deactivate", dependencies=_ADMIN, response_model=AdminUser,
             summary="Deactivate a user and end their sessions")
def deactivate_user(request: Request, response: Response, user_id: int = _UserId):
    mark_no_store(response)
    return auth_service.admin_set_active(user_id, False, actor_id=principal_user_id(request),
                                       at_pc=is_local_request(request))


@router.post("/users/{user_id}/activate", dependencies=_ADMIN, response_model=AdminUser,
             summary="Activate a user")
def activate_user(request: Request, response: Response, user_id: int = _UserId):
    mark_no_store(response)
    return auth_service.admin_set_active(user_id, True, actor_id=principal_user_id(request),
                                       at_pc=is_local_request(request))


@router.post("/users/{user_id}/revoke-sessions", dependencies=_ADMIN,
             response_model=AdminSessionsRevoked, summary="End every session of a user")
def revoke_sessions(request: Request, response: Response, user_id: int = _UserId):
    mark_no_store(response)
    return auth_service.admin_revoke_sessions(user_id, actor_id=principal_user_id(request),
                                            at_pc=is_local_request(request))


@router.post("/users/{user_id}/revoke-admin", dependencies=_ADMIN, response_model=AdminUser,
             summary="Remove a user's admin rights (PC only)")
def revoke_admin(request: Request, response: Response, user_id: int = _UserId):
    mark_no_store(response)
    return auth_service.revoke_admin(user_id, actor_id=principal_user_id(request),
                                     at_pc=is_local_request(request))


@router.delete("/users/{user_id}/ollama-key", dependencies=_ADMIN, response_model=MemberKeyRemoved,
               summary="Remove a user's own Ollama key")
def remove_ollama_key(request: Request, response: Response, user_id: int = _UserId):
    mark_no_store(response)
    return member_key_service.admin_remove(user_id, actor_id=principal_user_id(request),
                                           at_pc=is_local_request(request))


@router.post("/users/{user_id}/permissions/{permission}", dependencies=_ADMIN,
             response_model=AdminUser, summary="Give a member one permission")
def grant_user_permission(request: Request, response: Response, user_id: int = _UserId,
                          permission: str = _Permission):
    mark_no_store(response)
    return auth_service.admin_set_permission(user_id, permission, True,
                                             actor_id=principal_user_id(request))


@router.delete("/users/{user_id}/permissions/{permission}", dependencies=_ADMIN,
               response_model=AdminUser, summary="Take one permission from a member")
def revoke_user_permission(request: Request, response: Response, user_id: int = _UserId,
                           permission: str = _Permission):
    mark_no_store(response)
    return auth_service.admin_set_permission(user_id, permission, False,
                                             actor_id=principal_user_id(request))


@router.get("/audit", dependencies=_READ, response_model=AuditPage,
            summary="Audit log, newest first (read-only)")
def list_audit(response: Response,
               limit: int = Query(50, ge=1, le=auth_service.AUDIT_PAGE_MAX),
               before_id: Optional[int] = Query(None, ge=1, le=_MAX_ID),
               action: Optional[str] = Query(None, min_length=1, max_length=80),
               user_id: Optional[int] = Query(None, ge=1, le=_MAX_ID)):
    mark_no_store(response)
    return auth_service.audit_page(limit, before_id=before_id, action=action, user_id=user_id)
