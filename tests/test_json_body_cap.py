"""The body cap: a request over the limit is 413 whether the Content-Length
says so or the bytes that arrive do, whatever its Content-Type says (FastAPI
reads the whole body before it checks that); multipart uploads and bodies
under the limit pass through."""
import asyncio
import json
import re

import pytest

pytest.importorskip("fastapi")

from api.json_body_cap import MAX_JSON_BODY_BYTES, TOO_LARGE_MESSAGE, JsonBodyCapGate


async def _echo(scope, receive, send):
    from starlette.requests import Request
    body = await Request(scope, receive).body()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(len(body)).encode()})


def _call(headers, chunks, limit=100, multipart_routes=None):
    sent, queue = [], list(chunks)

    async def receive():
        body = queue.pop(0) if queue else b""
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/x", "headers": headers}
    asyncio.run(JsonBodyCapGate(_echo, limit,
                                multipart_routes_fn=lambda: multipart_routes or [])(
        scope, receive, send))
    return sent[0]["status"], sent[1].get("body", b"")


def _json(*extra):
    return [(b"content-type", b"application/json"), *extra]


def test_default_cap_is_generous_enough_for_a_pasted_novel():
    assert MAX_JSON_BODY_BYTES >= 8_000_000 * 3


def test_body_under_the_cap_passes():
    assert _call(_json((b"content-length", b"50")), [b"x" * 50]) == (200, b"50")


def test_declared_length_over_the_cap_is_413_without_reading():
    status, body = _call(_json((b"content-length", b"101")), [])
    assert status == 413 and TOO_LARGE_MESSAGE in body.decode()
    assert "too_large" in json.dumps(json.loads(body))


def test_chunked_body_over_the_cap_is_413():
    assert _call(_json(), [b"x" * 60, b"x" * 60])[0] == 413


def test_body_longer_than_its_content_length_is_cut_off():
    assert _call(_json((b"content-length", b"10")), [b"x" * 60, b"x" * 60])[0] == 413


@pytest.mark.parametrize("headers", [
    [(b"content-type", b"text/plain")],
    [(b"content-type", b"application/x-www-form-urlencoded")],
    [],
])
def test_a_body_not_labelled_json_is_capped_too(headers):
    assert _call(headers, [b"x" * 60, b"x" * 60])[0] == 413
    assert _call(headers + [(b"content-length", b"101")], [])[0] == 413


def _route(path):
    return [(re.compile(f"^{path}$"), frozenset({"POST"}))]


def test_multipart_to_a_route_that_takes_it_is_left_to_that_route():
    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    assert _call(headers, [b"x" * 300], multipart_routes=_route("/api/x")) == (200, b"300")


def test_a_multipart_label_does_not_lift_the_cap_on_any_other_route():
    """A route that reads its body by hand (request.json() in a dependency)
    has no body model to spot, so only routes that take multipart skip."""
    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    assert _call(headers, [b"x" * 60, b"x" * 60])[0] == 413
    assert _call(headers, [b"x" * 60, b"x" * 60],
                 multipart_routes=_route("/api/upload"))[0] == 413


def test_the_app_exempts_only_its_multipart_routes():
    pytest.importorskip("httpx")
    from api.api_config import ApiSettings
    from api.json_body_cap import multipart_matchers
    from api.remote_uploads import upload_matchers
    from api.server import create_app
    app = create_app(ApiSettings())
    exempt = {rx.pattern for rx, _ in multipart_matchers(app)}
    # Hand-documented multipart (read_capped_form) and File() routes alike.
    assert {"^/api/library/admin/restore$", "^/api/backups/import$",
            "^/api/translate/read\\-image$"} <= exempt
    # Every upload route not exempt takes pasted text as JSON.
    assert all(p.endswith(("attach\\-text$", "/text$"))
               for p in {rx.pattern for rx, _ in upload_matchers(app)} - exempt)
    # Routes that read a JSON body by hand stay capped.
    assert not any("/settings" in p or "retry\\-blocked" in p or "/jellyfin" in p
                   for p in exempt)
