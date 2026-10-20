"""Headers for responses that carry secrets or account data, so neither the
browser nor a proxy keeps a copy. The two sets are kept as each router has
always sent them; changing a router's set is a security decision."""
from fastapi import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

NO_STORE_CC = {"Cache-Control": "no-store"}
# Pragma covers HTTP/1.0 caches.
NO_STORE_FULL = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def no_store_json(body: BaseModel, headers: dict[str, str]) -> JSONResponse:
    return JSONResponse(body.model_dump(), headers=dict(headers))


def mark_no_store(response: Response, headers: dict[str, str]) -> None:
    response.headers.update(headers)
