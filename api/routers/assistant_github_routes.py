"""
api/routers/assistant_github_routes.py -- deliver a maintenance-assistant
proposed fix as a GitHub pull request. Thin: see
services/assistant_github_service.py.

Every route is `local_only()`. Off by default: until it is enabled with a
token and a repo, no route makes a GitHub call. The token is a key: it is
written like the engine keys (also behind the key-write gate
`_require_local_admin`, so off unless BAIHE_API_ALLOW_KEY_WRITES=1, with
`confirm=true`) and never returned. Preview makes no network call and
returns the exact diff with its sha256; deliver needs `confirm=true` and
that sha256, creates one new `baihe-assistant/...` branch and one draft PR
into the configured base, and never writes an existing branch.
"""

from fastapi import APIRouter, Request

from api.assistant_schemas import (AssistantConfirm, AssistantGithubConnection,
                                   AssistantGithubDelivered, AssistantGithubDeliverRequest,
                                   AssistantGithubPreview, AssistantGithubPreviewRequest,
                                   AssistantGithubSettingsUpdate, AssistantGithubStatus,
                                   AssistantGithubTokenResult, AssistantGithubTokenSet)
from api.auth import local_only
from api.routers.settings_routes import _read_body, _require_confirm, _require_local_admin
from api.schemas import ErrorResponse
from services import assistant_github_service as svc

router = APIRouter(prefix="/api/assistant/github", tags=["assistant"])

_ERRS = {409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.get("", dependencies=[local_only()], response_model=AssistantGithubStatus,
            summary="PC only: GitHub delivery settings (token: configured or not, never the value)")
def get_status():
    return svc.get_status()


@router.post("/settings", dependencies=[local_only()], response_model=AssistantGithubStatus,
             summary="PC only: turn GitHub delivery on/off, set the repo and base branch",
             responses=_ERRS)
def post_settings(body: AssistantGithubSettingsUpdate):
    return svc.set_settings(body.model_dump(exclude_unset=True))


@router.post("/token", dependencies=[local_only()], response_model=AssistantGithubTokenResult,
             summary="PC only: store the GitHub token in .env (write-only; key-write gate)",
             responses={422: {"model": ErrorResponse}})
async def post_token(request: Request):
    _require_local_admin(request)
    body = await _read_body(request, AssistantGithubTokenSet)
    _require_confirm(body.confirm)
    return svc.set_token(body.value)


@router.post("/token/clear", dependencies=[local_only()],
             response_model=AssistantGithubTokenResult,
             summary="PC only: remove the GitHub token from .env (key-write gate)",
             responses={422: {"model": ErrorResponse}})
async def post_token_clear(request: Request):
    _require_local_admin(request)
    body = await _read_body(request, AssistantConfirm)
    _require_confirm(body.confirm)
    return svc.clear_token()


@router.post("/test", dependencies=[local_only()], response_model=AssistantGithubConnection,
             summary="PC only: check the token can see the repo (409 while off / no token)",
             responses=_ERRS)
def post_test():
    return svc.test_connection()


@router.post("/preview", dependencies=[local_only()], response_model=AssistantGithubPreview,
             summary="PC only: the exact diff a PR would carry, with its sha256 (no network)",
             responses=_ERRS)
def post_preview(body: AssistantGithubPreviewRequest):
    return svc.preview(body.patch, body.title)


@router.post("/deliver", dependencies=[local_only()], response_model=AssistantGithubDelivered,
             summary="PC only: open ONE draft PR from a new branch for the previewed diff "
                     "(confirm=true + its sha256; never pushes to an existing branch)",
             responses=_ERRS)
def post_deliver(body: AssistantGithubDeliverRequest):
    return svc.deliver(body.patch, body.title, body.body, sha256=body.sha256,
                       confirm=body.confirm)
