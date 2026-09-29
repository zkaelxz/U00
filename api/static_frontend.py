"""
api/static_frontend.py -- serves the built React app (`frontend/dist`)
from the same FastAPI process and origin as `/api`.

This is what lets the React app run as one process on one port with no
Vite/npm at runtime (`docs/remote-access-design.md` section 2). If
`frontend/dist/index.html` doesn't exist, nothing is registered and the
server is API-only, exactly as before.

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

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException

log = logging.getLogger(__name__)

DEFAULT_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def install_frontend(app: FastAPI, dist_dir=None) -> bool:
    """Registers the catch-all GET route that serves `dist_dir`
    (default `frontend/dist`). Must run after every API router so their
    routes match first. Returns True if the frontend is being served."""
    dist = Path(dist_dir) if dist_dir is not None else DEFAULT_DIST
    index = dist / "index.html"
    if not index.is_file():
        log.info("React frontend not built (%s missing): serving the API only. "
                 "Build it with: cd frontend && npm ci && npm run build", index)
        return False
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def serve_frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (root / path).resolve() if path else index
        inside = candidate == root or root in candidate.parents
        if inside and candidate.is_file():
            headers = {"Cache-Control": "no-cache"} if candidate.name == "index.html" else None
            return FileResponse(candidate, headers=headers)
        if "." in path.rsplit("/", 1)[-1]:
            raise HTTPException(status_code=404)  # a missing asset, not a page
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return True
