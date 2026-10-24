"""
api/static_frontend.py -- serves the built React app (`frontend/dist`)
from the same FastAPI process and origin as `/api`.

This is what lets the React app run as one process on one port with no
Vite/npm at runtime (`docs/archive/remote-access-design.md` section 2). If
`frontend/dist/index.html` doesn't exist, no file route is registered: the
server is API-only, logs a build hint, and `/` answers with a plain "not
built" page (an exception handler, so the route list is the same).

The app uses a hash router, so no deep-link path fallback is needed;
unknown extension-less paths still get `index.html` so a stray path
can't strand the user on a JSON error. `/api` and everything under it is
never answered here (an unknown `/api/...` stays a JSON 404), and a
missing file that looks like an asset (has an extension) is a 404 rather
than `index.html`. Only files that resolve inside the dist folder are
ever served.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.exceptions import HTTPException
from starlette.routing import Match

from api.auth import public_route

log = logging.getLogger(__name__)

# Explicit types for what a Vite build emits. FileResponse otherwise guesses
# with `mimetypes`, which on Windows reads the registry and can answer
# `text/plain` for `.js`; browsers refuse a module script served that way
# and the app loads blank.
_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
}


def _media_type(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")

DEFAULT_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


_UNBUILT_HTML = ("<!doctype html><title>Baihe</title><h1>The web app is not built</h1>"
                 "<p>Run <code>npm run build</code> in the <code>frontend</code> folder, "
                 "then restart Baihe.</p>")


def install_unbuilt_page(app: FastAPI):
    # An exception handler, not a route: the route list and the permission
    # table must stay the same whether or not the frontend is built.
    previous = app.exception_handlers.get(HTTPException)

    async def handler(request: Request, exc: HTTPException):
        if exc.status_code == 404 and request.method == "GET" and request.url.path == "/":
            return HTMLResponse(_UNBUILT_HTML, status_code=200)
        return await previous(request, exc)

    if previous is not None:
        app.add_exception_handler(HTTPException, handler)


def install_frontend(app: FastAPI, dist_dir=None) -> bool:
    """Registers the catch-all GET route that serves `dist_dir`
    (default `frontend/dist`). Must run after every API router so their
    routes match first. Returns True if the frontend is being served."""
    dist = Path(dist_dir) if dist_dir is not None else DEFAULT_DIST
    index = dist / "index.html"
    if not index.is_file():
        log.warning("The web app is not built; run npm run build (cd frontend && npm ci "
                    "first). Serving the API only.")
        install_unbuilt_page(app)
        return False
    root = dist.resolve()

    # public_route(): the app shell and its assets carry no data and must
    # load before login; /api paths below it are never answered with data.
    @app.get("/{path:path}", include_in_schema=False, dependencies=[public_route()])
    def serve_frontend(path: str, request: Request):
        if path == "api" or path.startswith("api/"):
            # A real API route that just doesn't take GET must stay a 405 (with
            # Allow), not turn into a 404 because this catch-all matched first.
            allowed = set()
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                probe = {**request.scope, "method": method}
                for r in request.app.router.routes:
                    if getattr(r, "endpoint", None) is serve_frontend:
                        continue
                    if r.matches(probe)[0] == Match.FULL:
                        allowed.add(method)
                        break
            if allowed:
                raise HTTPException(status_code=405, headers={"Allow": ", ".join(sorted(allowed))})
            raise HTTPException(status_code=404)
        # Refused before the join: on Windows a backslash, drive or leading
        # slash turns `root / path` into a UNC or drive path, and resolve()
        # would open it (an SMB connection that hands over the service
        # account's NTLM hash) before the inside check below runs.
        if "\\" in path or ":" in path or "\0" in path or path.startswith("/"):
            raise HTTPException(status_code=404)
        candidate = (root / path).resolve() if path else index
        inside = candidate == root or root in candidate.parents
        if inside and candidate.is_file():
            headers = {"X-Content-Type-Options": "nosniff"}
            if candidate.name == "index.html":
                headers["Cache-Control"] = "no-cache"
            elif path.startswith("assets/"):
                # Vite puts a content hash in every name under assets/, so a
                # changed file gets a new name and the old one never changes.
                headers["Cache-Control"] = "max-age=31536000, immutable"
            return FileResponse(candidate, headers=headers, media_type=_media_type(candidate))
        if "." in path.rsplit("/", 1)[-1]:
            raise HTTPException(status_code=404)  # a missing asset, not a page
        return FileResponse(index, media_type=_MEDIA_TYPES[".html"],
                            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    return True
