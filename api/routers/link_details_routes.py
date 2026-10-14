"""
api/routers/link_details_routes.py -- "Fill from link"
(services/link_details_service.py): read what a link says about a title,
fill only the blank fields the owner ticks, and the read-only scan of old
titles that have a link. All PC-only (local_only): each read fetches from
the internet on this PC's connection, and the stored link may carry a token.
"""

from fastapi import APIRouter, Path, Request

from api.auth import local_only
from api.schemas import (LinkDetailsApplyRequest, LinkDetailsApplyResult,
                         LinkScanResult, LinkScanStarted, MediaLinkProbe, MediaLinkProbeRequest,
                         TitleLinkProbe, error_responses)
from services import link_details_service

router = APIRouter(tags=["link details"])

_ERRORS = error_responses(404, 409, 422, 503)


@router.post("/api/media-link/probe", dependencies=[local_only()], response_model=MediaLinkProbe,
             summary="What a link says about a title: title, channel, stream date (PC only)",
             description="Nothing is downloaded or saved. A video or stream link is read with "
                         "yt-dlp (metadata only), a supported Sources link with that source's own "
                         "data; any other page returns kind \"page\" without being fetched. 422 a "
                         "bad or private address, 503 the read failed or yt-dlp is missing.",
             responses=_ERRORS)
def post_media_link_probe(body: MediaLinkProbeRequest):
    return link_details_service.read_link(body.url)


@router.post("/api/dramas/{drama_id}/link-details/probe", dependencies=[local_only()],
             response_model=TitleLinkProbe,
             summary="Refresh details from link: the title's fields beside what its link says",
             responses=_ERRORS)
def post_title_link_probe(request: Request, drama_id: int = Path(ge=1, le=2**31 - 1)):
    return link_details_service.probe_title(drama_id, principal=request.state.principal)


@router.post("/api/dramas/{drama_id}/link-details/apply", dependencies=[local_only()],
             response_model=LinkDetailsApplyResult,
             summary="Fill the ticked fields, each only while it is still blank",
             responses=_ERRORS)
def post_link_details_apply(body: LinkDetailsApplyRequest, request: Request,
                            drama_id: int = Path(ge=1, le=2**31 - 1)):
    return link_details_service.apply(drama_id, body.model_dump(exclude_none=True),
                                      principal=request.state.principal)


@router.post("/api/library/link-scan", dependencies=[local_only()], response_model=LinkScanStarted,
             summary="Job: check old titles from their links (read-only; PC only)",
             description="Reads the link of every title with a blank original title, release "
                         "date, author or series, 3 at a time and 10-20 s apart. Writes nothing; "
                         "read the preview with GET /api/library/link-scan, cancel with POST "
                         "/api/jobs/link_scan/cancel. 409 while one runs.",
             responses=_ERRORS)
def post_link_scan(request: Request):
    return link_details_service.start_scan(principal=request.state.principal)


@router.get("/api/library/link-scan", dependencies=[local_only()], response_model=LinkScanResult,
            summary="The last link check's per-title preview (PC only)")
def get_link_scan():
    return link_details_service.scan_result()
