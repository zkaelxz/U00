"""
api/routers/settings_routes.py -- Settings endpoints (Migration Slices 10, 23).

GET: whether each engine key/endpoint is configured, plus the app_settings
toggles. Never returns a key's value (D2). POST takes non-secret toggles
and preferences; secrets use the key/endpoint routes below.

Settings parity: POST also takes the persisted preferences (defaults for
new dramas, spending cap, Ollama num_ctx, offline Whisper folder, OCR
defaults, yt-dlp cookies); `/endpoints/{name}` sets or clears the Ollama
and GPT-SoVITS URLs in .env behind the same guard as keys.

Write-only engine key endpoints (`POST /keys/{engine}` and
`/keys/{engine}/clear`). Off by default (BAIHE_API_ALLOW_KEY_WRITES=1) and
guarded by `_require_local_admin`. The guard is a safeguard against
proxied/remote/cross-site requests, NOT authentication; real isolation is
the separate admin listener (D5): on the household listener it always refuses.
"""

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from api.auth import (LOOPBACK_HOSTS, PROXY_HEADERS, host_name, is_local_request,
                      is_loopback_peer, local_only, require_permission)
from api.schemas import (EndpointUrlResult, EndpointUrlSetRequest, EngineKeyClearRequest,
                         EngineKeyResult, EngineKeySetRequest, SettingsOverview,
                         SettingsUpdateRequest)
from services import settings_service
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PATH_PREFERENCES = ("whisper_model_path", "tesseract_cmd", "cookies_file", "lncrawl_cmd")


@router.get("", dependencies=[require_permission("admin.settings")], response_model=SettingsOverview,
            summary="Read-only settings overview (engine key presence, job toggles)")
def get_overview(request: Request):
    return _with_path_flags(settings_service.get_settings_overview(),
                            is_local_request(request))


# PC-only: the preferences include paths the server itself uses (the
# Tesseract program it runs, the Whisper folder, the cookies file); see
# docs/remote-access-decision.md.
@router.post("", dependencies=[local_only()], response_model=SettingsOverview,
             summary="Update non-secret toggles and preferences (never keys or endpoint URLs)")
def update_settings(body: SettingsUpdateRequest):
    return _with_path_flags(settings_service.set_settings(body.model_dump(exclude_unset=True)),
                            local=True)


def _with_path_flags(overview: dict, local: bool) -> dict:
    """Adds <path>_configured for each path preference and, for a caller
    that isn't the PC itself, blanks the path: absolute paths on the PC stay
    on the PC, other devices only learn whether one is set."""
    prefs = overview["preferences"]
    for name in _PATH_PREFERENCES:
        prefs[f"{name}_configured"] = bool(prefs.get(name))
        if not local:
            prefs[name] = ""
    return overview


def require_local_admin(request: Request):
    """Every refusal is the same generic 403. Checks, in order: feature
    flag, loopback TCP peer, loopback Host header, no proxy/identity
    headers, Origin (if present) is loopback."""
    def deny():
        raise HTTPException(status_code=403, detail="Not allowed from this connection.")

    settings = getattr(request.app.state, "settings", None)
    if not getattr(settings, "allow_key_writes", False):
        deny()
    if not is_local_request(request):   # also False on the household listener
        deny()
    peer = request.client.host if request.client else None
    if not is_loopback_peer(peer):
        deny()
    host_header = request.headers.get("host", "")
    if "@" in host_header or host_name(host_header) not in LOOPBACK_HOSTS:
        deny()
    if any(h in request.headers for h in PROXY_HEADERS):
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
                or (f"[{hostname}]" if ":" in hostname else hostname) not in LOOPBACK_HOSTS):
            deny()


def require_confirm(confirm: bool):
    if confirm is not True:
        raise InvalidInputError("Confirmation required (confirm=true).")


async def read_body(request: Request, model):
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
    require_local_admin(request)
    body = await read_body(request, EngineKeySetRequest)
    require_confirm(body.confirm)
    return settings_service.set_engine_key(engine, body.value)


@router.post("/keys/{engine}/clear", dependencies=[local_only()], response_model=EngineKeyResult,
             summary="Remove an engine API key from .env (disabled by default, local PC only)")
async def clear_engine_key(engine: str, request: Request):
    require_local_admin(request)
    body = await read_body(request, EngineKeyClearRequest)
    require_confirm(body.confirm)
    return settings_service.clear_engine_key(engine)


@router.post("/endpoints/{name}", dependencies=[local_only()], response_model=EndpointUrlResult,
             summary="Set the Ollama or GPT-SoVITS URL in .env (local PC only)")
async def set_endpoint_url(name: str, request: Request):
    require_local_admin(request)
    body = await read_body(request, EndpointUrlSetRequest)
    require_confirm(body.confirm)
    return settings_service.set_endpoint_url(name, body.url)


@router.post("/endpoints/{name}/clear", dependencies=[local_only()],
             response_model=EndpointUrlResult,
             summary="Remove an endpoint URL from .env (local PC only)")
async def clear_endpoint_url(name: str, request: Request):
    require_local_admin(request)
    body = await read_body(request, EngineKeyClearRequest)
    require_confirm(body.confirm)
    return settings_service.clear_endpoint_url(name)
