"""
api/routers/sources_local_routes.py -- the PC-only Sources actions (spec
S-6 sign-in, inventory SO17 tier tests, SO18 proxy). Thin: see
services/sources_signin_service.py and services/sources_registry_service.py.

Every route is `local_only()`: opening a sign-in window, deleting a saved
browser profile, driving the browser for a tier test and setting the proxy
all happen on (and to) the PC itself, so another device can never trigger
them ("sign-in, proxy, pacing floors and cookies stay local-only",
docs/remote-access-decision.md). Nothing returned carries a cookie, a
profile path or the proxy URL.

Same /api/sources prefix as the other Sources routers. The paths are
/{name}/signin/..., /{name}/tier-test and /settings/proxy, which no other
Sources route shape matches.
"""

from fastapi import APIRouter, Path

from api.auth import local_only
from api.schemas import (ErrorResponse, SourceSigninForgetRequest, SourceSigninForgetResult,
                         SourceSigninOpenRequest, SourcesJobStarted, SourcesProxyRequest,
                         SourcesSettings, SourceTierTestRequest)
from services import sources_registry_service as registry_svc
from services import sources_signin_service as svc

router = APIRouter(prefix="/api/sources", tags=["sources"])

_NAME = Path(min_length=1, max_length=60)
_ERRS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.post("/settings/proxy", dependencies=[local_only()], response_model=SourcesSettings,
             summary="Set or clear the HTTP(S) proxy for source requests (never echoed back)",
             responses=_ERRS)
def post_proxy(body: SourcesProxyRequest):
    return registry_svc.set_proxy_url(body.url)


@router.post("/{name}/signin/open", dependencies=[local_only()], response_model=SourcesJobStarted,
             summary="Job: open a sign-in window on this PC and wait until it is closed",
             responses=_ERRS)
def post_signin_open(body: SourceSigninOpenRequest, name: str = _NAME):
    return svc.start_signin(name, body.url)


@router.post("/{name}/signin/forget", dependencies=[local_only()],
             response_model=SourceSigninForgetResult,
             summary="Delete this source's saved browser profile (needs confirm=true)",
             responses=_ERRS)
def post_signin_forget(body: SourceSigninForgetRequest, name: str = _NAME):
    return svc.forget_signin(name, body.confirm)


@router.post("/{name}/tier-test", dependencies=[local_only()], response_model=SourcesJobStarted,
             summary="Job: test one access tier against a page on this source's site",
             responses=_ERRS)
def post_tier_test(body: SourceTierTestRequest, name: str = _NAME):
    return svc.start_tier_test(name, body.tier, body.url)
