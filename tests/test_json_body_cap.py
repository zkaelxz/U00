"""The JSON body cap: a JSON request over the limit is 413 whether the
Content-Length says so or the bytes that arrive do; other content types and
bodies under the limit pass through."""
import asyncio
import json

import pytest

pytest.importorskip("fastapi")

from api.json_body_cap import MAX_JSON_BODY_BYTES, TOO_LARGE_MESSAGE, JsonBodyCapGate


async def _echo(scope, receive, send):
    from starlette.requests import Request
    body = await Request(scope, receive).body()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(len(body)).encode()})


def _call(headers, chunks, limit=100):
    sent, queue = [], list(chunks)

    async def receive():
        body = queue.pop(0) if queue else b""
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/x", "headers": headers}
    asyncio.run(JsonBodyCapGate(_echo, limit)(scope, receive, send))
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


def test_other_content_types_are_left_to_their_routes():
    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    assert _call(headers, [b"x" * 300]) == (200, b"300")
