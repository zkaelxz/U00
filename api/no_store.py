"""Headers for responses that carry secrets or account data, so neither the
browser nor a proxy keeps a copy."""
from fastapi import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Pragma covers HTTP/1.0 caches.
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def no_store_json(body: BaseModel) -> JSONResponse:
    return JSONResponse(body.model_dump(), headers=dict(NO_STORE))


def mark_no_store(response: Response) -> None:
    response.headers.update(NO_STORE)
