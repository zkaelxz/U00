"""
api/routers/source_domains_routes.py -- domain lists of sources that move
between domains, and the hosts discovery proposed. Thin: see
services/source_domains_service.py.

Every route is `local_only()`: the list decides where source requests go
(an SSRF/exfiltration pivot if another device could set it, like the
proxy), and the proposals name hosts, the one deliberate exception to "no
fetched URLs" -- the owner needs the name to confirm it. Nothing returned
carries a scheme, path or query.

Own prefix, /api/source-domains: under /api/sources a GET would be read as
the catalog's GET /api/sources/{name}.
"""

from typing import List

from fastapi import APIRouter, Path

from api.auth import local_only
from api.schemas import (ErrorResponse, SourceDomainList, SourceDomainProposal,
                         SourceDomainProposalAction, SourceDomainProposalDismissed,
                         SourceDomainsUpdate)
from services import source_domains_service as svc

router = APIRouter(prefix="/api/source-domains", tags=["sources"])

_NAME = Path(min_length=1, max_length=60)
_ERRS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


@router.get("", dependencies=[local_only()], response_model=List[SourceDomainList],
            summary="PC only: every source with a domain list (host names only)")
def list_domains():
    return svc.list_domains()


@router.get("/proposals", dependencies=[local_only()], response_model=List[SourceDomainProposal],
            summary="PC only: hosts discovery proposed, waiting for the owner (host names only)")
def list_proposals():
    return svc.list_proposals()


@router.post("/proposals/confirm", dependencies=[local_only()], response_model=SourceDomainList,
             summary="PC only: put a proposed host first on its source's list", responses=_ERRS)
def post_confirm(body: SourceDomainProposalAction):
    return svc.confirm_proposal(body.source, body.host)


@router.post("/proposals/dismiss", dependencies=[local_only()],
             response_model=SourceDomainProposalDismissed,
             summary="PC only: dismiss a proposed host (it is not proposed again)", responses=_ERRS)
def post_dismiss(body: SourceDomainProposalAction):
    return svc.dismiss_proposal(body.source, body.host)


@router.post("/{name}", dependencies=[local_only()], response_model=SourceDomainList,
             summary="PC only: replace a source's domain list (host names, in order)",
             responses=_ERRS)
def post_domains(body: SourceDomainsUpdate, name: str = _NAME):
    return svc.set_domains(name, body.domains)


@router.post("/{name}/reset", dependencies=[local_only()], response_model=SourceDomainList,
             summary="PC only: back to the adapter's own domain list", responses=_ERRS)
def post_reset(name: str = _NAME):
    return svc.reset_domains(name)
