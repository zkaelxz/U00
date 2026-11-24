"""Response compression on the app's own listener (LAN phones skip Caddy)."""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import APIRouter, Response
from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app

# Shaped like a long episode's line list: repetitive keys, CJK text.
_LINES = [{"id": i, "start": i * 2.5, "end": i * 2.5 + 2.0, "speaker": "SPEAKER_01",
           "orig": "今天的直播就到这里，谢谢大家的支持。" + str(i),
           "en": f"That's all for today's stream, thank you for your support. {i}"}
          for i in range(3000)]
_LINES_JSON = json.dumps(_LINES, ensure_ascii=False).encode("utf-8")


@pytest.fixture
def client(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>" + "x" * 5000 + "</html>")
    (dist / "assets" / "app.js").write_text("console.log('fake');" * 500)
    app = create_app(ApiSettings(), frontend_dist=dist)
    extra = APIRouter()

    @extra.get("/api/_t/lines")
    def lines():
        return Response(_LINES_JSON, media_type="application/json")

    @extra.get("/api/_t/small")
    def small():
        return Response(b'{"ok": true}', media_type="application/json")

    @extra.get("/api/_t/download")
    def download():
        return Response(b"A" * 5000, media_type="application/octet-stream")

    @extra.get("/api/_t/zip")
    def zipped():
        return Response(b"A" * 5000, media_type="application/zip")

    @extra.get("/api/_t/stream")
    def stream():
        return Response(b"data: " + b"A" * 5000, media_type="text/event-stream")

    # Ahead of the frontend's catch-all route, which would otherwise answer.
    app.router.routes[0:0] = extra.routes
    return TestClient(app, raise_server_exceptions=False)


def test_big_line_list_shrinks_a_lot(client):
    plain = client.get("/api/_t/lines", headers={"Accept-Encoding": "identity"})
    packed = client.get("/api/_t/lines", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in plain.headers
    assert packed.headers["content-encoding"] == "gzip"
    assert int(packed.headers["content-length"]) < int(plain.headers["content-length"]) / 5
    assert packed.content == _LINES_JSON  # client undoes the gzip


def test_frontend_files_are_compressed(client):
    for path in ("/", "/assets/app.js"):
        r = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert r.headers["content-encoding"] == "gzip", path


def test_range_request_on_an_asset_is_left_alone(client):
    r = client.get("/assets/app.js", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
    assert r.status_code == 206
    assert "content-encoding" not in r.headers
    assert r.content == b"console.lo"


@pytest.mark.parametrize("path", ["/api/_t/small", "/api/_t/download", "/api/_t/zip",
                                  "/api/_t/stream"])
def test_small_and_already_packed_replies_are_not_compressed(client, path):
    r = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers


def test_no_gzip_without_the_client_asking(client):
    r = client.get("/api/_t/lines", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers
