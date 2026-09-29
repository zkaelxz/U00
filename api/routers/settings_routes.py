"""
api/routers/settings_routes.py -- Settings endpoints (Migration Slices 10, 23).

GET: whether each engine key/endpoint is configured, plus the app_settings
toggles. Never returns a key's value (D2). POST (Slice 23): non-secret
boolean toggles only -- writing a secret to disk over HTTP is a
separate, higher-risk slice of its own (see docs/migration-review.md).

Slice 24: write-only engine key endpoints (`POST /keys/{engine}` and
`/keys/{engine}/clear`). Off by default (BAIHE_API_ALLOW_KEY_WRITES=1) and
guarded by `_require_local_admin`. The guard is a safeguard against
proxied/remote/cross-site requests, NOT authentication; real isolation is
the separate admin listener (D5), not built yet.
"""

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from api.auth import local_only, require_permission
from api.schemas import (EngineKeyClearRequest, EngineKeyResult, EngineKeySetRequest,
                         SettingsOverview, SettingsUpdateRequest)
from services import settings_service
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")], response_model=SettingsOverview,
            summary="Read-only settings overview (engine key presence, job toggles)")
def get_overview():
    return settings_service.get_settings_overview()


@router.post("", dependencies=[local_only()], response_model=SettingsOverview,
             summary="Update non-secret boolean settings (never keys/URLs/paths)")
def update_settings(body: SettingsUpdateRequest):
    return settings_service.set_settings(body.model_dump(exclude_unset=True))


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded",
                  "x-real-ip", "tailscale-user-login", "cf-connecting-ip", "cf-ray", "via")


def _host_name(netloc: str) -> str:
    """Host part of a Host header / URL netloc, port removed, lower-cased."""
    netloc = (netloc or "").strip().lower()
    if netloc.startswith("["):
        end = netloc.find("]")
        return netloc[:end + 1] if end != -1 else netloc
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


def _is_loopback_peer(host) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _require_local_admin(request: Request):
    """Every refusal is the same generic 403. Checks, in order: feature
    flag, loopback TCP peer, loopback Host header, no proxy/identity
    headers, Origin (if present) is loopback."""
    def deny():
        raise HTTPException(status_code=403, detail="Not allowed from this connection.")

    settings = getattr(request.app.state, "settings", None)
    if not getattr(settings, "allow_key_writes", False):
        deny()
    peer = request.client.host if request.client else None
    if not _is_loopback_peer(peer):
        deny()
    host_header = request.headers.get("host", "")
    if "@" in host_header or _host_name(host_header) not in _LOOPBACK_HOSTS:
        deny()
    if any(h in request.headers for h in _PROXY_HEADERS):
        deny()
    origin = request.headers.get("origin")
    if origin is not None:
        try:
            parts = urlsplit(origin)
            hostname = parts.hostname   # drops any userinfo
        except ValueError:
            deny()
        if ("@" in origin or parts.scheme not in ("http", "https")
                or not hostname
                or (f"[{hostname}]" if ":" in hostname else hostname) not in _LOOPBACK_HOSTS):
            deny()


def _require_confirm(confirm: bool):
    if confirm is not True:
        raise InvalidInputError("Confirmation required (confirm=true).")


async def _read_body(request: Request, model):
    """Parsed only AFTER the guard so a disallowed caller always gets the
    generic 403, never a body-validation 422. Errors never echo input."""
    try:
        data = await request.json()
        return model.model_validate(data)
    except Exception:
        raise InvalidInputError("The request is invalid.")


@router.post("/keys/{engine}", dependencies=[local_only()], response_model=EngineKeyResult,
             summary="Set an engine API key (write-only; disabled by default, local PC only)")
async def set_engine_key(engine: str, request: Request):
    _require_local_admin(request)
    body = await _read_body(request, EngineKeySetRequest)
    _require_confirm(body.confirm)
    return settings_service.set_engine_key(engine, body.value)


@router.post("/keys/{engine}/clear", dependencies=[local_only()], response_model=EngineKeyResult,
             summary="Remove an engine API key from .env (disabled by default, local PC only)")
async def clear_engine_key(engine: str, request: Request):
    _require_local_admin(request)
    body = await _read_body(request, EngineKeyClearRequest)
    _require_confirm(body.confirm)
    return settings_service.clear_engine_key(engine)
