"""The If-None-Match 304 for files served with FileResponse, which sets an ETag but never answers 304 itself."""
from fastapi import Request
from fastapi.responses import FileResponse, Response


def _etag_matches(header: str, etag: str) -> bool:
    for tag in header.split(","):
        tag = tag.strip()
        if tag == "*" or tag.removeprefix("W/") == etag:
            return True
    return False


def or_not_modified(request: Request, resp: FileResponse, cache_control: str) -> Response:
    """`resp`, or a bodiless 304 when the browser already holds this version of the file."""
    inm = request.headers.get("if-none-match")
    if inm and _etag_matches(inm, resp.headers["etag"]):
        return Response(status_code=304, headers={
            "ETag": resp.headers["etag"], "Last-Modified": resp.headers["last-modified"],
            "Cache-Control": cache_control, "X-Content-Type-Options": "nosniff"})
    return resp
