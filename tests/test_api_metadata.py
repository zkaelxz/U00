"""
Tests for media analysis and metadata auto-fill (Migration Slice 37).
Fully mocked: no ffprobe, network, DNS or LLM.
"""
from lib import http
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import media_inspect
from api.api_config import ApiSettings
from api.server import create_app
from services import metadata_service


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})  # as the React client sends


@pytest.fixture
def drama(isolated_db):
    return db.create_drama(title_en="D", source_language="zh")


def _code(resp):
    return resp.json()["error"]["code"]


def _dns(monkeypatch, ip):
    monkeypatch.setattr(metadata_service.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (ip, port))])


PROBE = {"format": {"duration": "12.5"},
         "streams": [{"codec_type": "video", "disposition": {"attached_pic": 1}},
                     {"codec_type": "audio", "sample_rate": "44100"}]}


def test_analyze_media(client, drama, monkeypatch):
    folder = db.drama_dir(drama)
    with open(os.path.join(folder, "audio.wav"), "wb") as f:
        f.write(b"x")
    db.update_drama(drama, audio_filename="audio.wav")
    monkeypatch.setattr(media_inspect, "run_ffprobe", lambda path, **kw: PROBE)
    r = client.post(f"/api/metadata/dramas/{drama}/analyze-media")
    assert r.status_code == 200
    assert r.json() == {"drama_id": drama, "duration_seconds": 12.5, "has_video": False,
                        "has_audio": True, "audio_track_count": 1, "sample_rate": 44100,
                        "width": None, "height": None, "fps": None, "subtitle_tracks": [],
                        "suggested_pipeline": ["Transcribe (Whisper)", "Diarize speakers",
                                               "Translate", "Export subtitles (ASS/VTT/SRT)"],
                        "content_type_guess": "audio_drama",
                        "content_type_reason": "audio-only file"}


VIDEO_PROBE = {"format": {"duration": "60"},
               "streams": [{"index": 0, "codec_type": "video", "width": 1920, "height": 1080,
                            "r_frame_rate": "30000/1001"},
                           {"index": 1, "codec_type": "audio", "sample_rate": "48000"},
                           {"index": 2, "codec_type": "subtitle", "codec_name": "ass",
                            "tags": {"language": "chi"}},
                           {"index": 3, "codec_type": "subtitle", "codec_name": "subrip",
                            "tags": {"language": "und"}}]}


def test_analyze_media_resolution_subtitles_pipeline(client, drama, monkeypatch):
    """Parity P05: resolution, subtitle tracks and the suggested pipeline."""
    folder = db.drama_dir(drama)
    with open(os.path.join(folder, "v.mp4"), "wb") as f:
        f.write(b"x")
    db.update_drama(drama, source_video_filename="v.mp4")
    calls = []
    monkeypatch.setattr(media_inspect, "run_ffprobe",
                        lambda path, **kw: calls.append(path) or VIDEO_PROBE)
    r = client.post(f"/api/metadata/dramas/{drama}/analyze-media")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(calls) == 1                                  # ffprobe runs once
    assert (body["width"], body["height"]) == (1920, 1080)
    assert round(body["fps"], 2) == 29.97 and body["has_video"] is True
    assert body["subtitle_tracks"] == [{"index": 2, "codec": "ass", "language": "chi"},
                                       {"index": 3, "codec": "subrip", "language": None}]
    assert body["suggested_pipeline"][0].startswith("Import existing subtitle track")
    assert body["content_type_guess"] == "video_drama"
    assert body["content_type_reason"] == "has a video track"
    assert folder not in r.text and "v.mp4" not in r.text   # never a path


def test_analyze_errors(client, drama, monkeypatch):
    assert client.post("/api/metadata/dramas/9999/analyze-media").status_code == 404
    assert client.post(f"/api/metadata/dramas/{drama}/analyze-media").status_code == 422
    folder = db.drama_dir(drama)
    with open(os.path.join(folder, "audio.wav"), "wb") as f:
        f.write(b"x")
    db.update_drama(drama, audio_filename="audio.wav")

    def boom(path, **kw):
        raise media_inspect.ProbeError("/secret/path failed")
    monkeypatch.setattr(media_inspect, "run_ffprobe", boom)
    r = client.post(f"/api/metadata/dramas/{drama}/analyze-media")
    assert r.status_code == 503 and "/secret" not in r.text


def test_analyze_ignores_traversal_filename(client, drama, monkeypatch):
    db.update_drama(drama, audio_filename="../x.wav")
    monkeypatch.setattr(media_inspect, "run_ffprobe", lambda p, **kw: PROBE)
    assert client.post(f"/api/metadata/dramas/{drama}/analyze-media").status_code == 422


def _fake_extract(monkeypatch, result, seen=None):
    def fake(text, engine, max_chars=6000):
        if seen is not None:
            seen.append(text)
        return result
    monkeypatch.setattr(metadata_service.metadata_lookup, "extract_metadata_llm", fake)
    monkeypatch.setattr(metadata_service.translate_engines, "get_engine",
                        lambda name, key, **kw: object())


def test_autofill_text_returns_without_writing(client, drama, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda k: "k")
    _fake_extract(monkeypatch, {"title_en": " New ", "author": "A", "bogus": "x", "studio": 5})
    r = client.post(f"/api/metadata/dramas/{drama}/autofill", json={"page_text": "page"})
    assert r.status_code == 200
    assert r.json()["suggestion"] == {"title_en": "New", "author": "A"}
    assert db.get_drama(drama)["title_en"] == "D"


def test_autofill_url_flow_adds_source_url(client, drama, monkeypatch):
    monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda k: "k")
    monkeypatch.setattr(metadata_service, "_fetch_page_text", lambda u: "text")
    _dns(monkeypatch, "93.184.216.34")
    _fake_extract(monkeypatch, {"summary": "S"})
    r = client.post(f"/api/metadata/dramas/{drama}/autofill", json={"url": "https://e.com/p"})
    assert r.json()["suggestion"] == {"summary": "S", "source_url": "https://e.com/p"}


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://e.com/", "https://u:p@e.com/",
                                 "http://", "javascript:alert(1)"])
def test_autofill_bad_scheme_422(client, drama, monkeypatch, url):
    monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda k: "k")
    r = client.post(f"/api/metadata/dramas/{drama}/autofill", json={"url": url})
    assert r.status_code == 422


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254",
                                "::1", "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::1"])
def test_autofill_private_hosts_422(client, drama, monkeypatch, ip):
    monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda k: "k")
    _dns(monkeypatch, ip)
    r = client.post(f"/api/metadata/dramas/{drama}/autofill", json={"url": "http://x.example/"})
    assert r.status_code == 422


def test_redirect_to_private_blocked(monkeypatch):
    calls = []
    _dns(monkeypatch, "93.184.216.34")

    class Resp:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/admin"}
        def close(self): pass

    def fake_get(url, ip, headers, *a, **kw):
        calls.append((url, ip))
        return Resp()
    monkeypatch.setattr(http, "pinned_get", fake_get)
    # second hop resolves to loopback
    monkeypatch.setattr(metadata_service.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (
                            "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", port))])
    from services.service_errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        metadata_service._fetch_page_text("http://ok.example/")
    assert calls == [("http://ok.example/", "93.184.216.34")]


def test_connection_pinned_to_validated_ip(monkeypatch):
    """A second DNS answer (private) must not be used for the connection."""
    import requests
    answers = iter(["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr(metadata_service.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (next(answers), port))])
    sent = []

    def fake_send(self, request, **kw):
        sent.append((request.url, request.headers["Host"], kw))
        r = requests.Response()
        r.status_code = 200
        return r
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    ip = metadata_service.check_public_url("https://ok.example:8443/p?q=1")
    assert ip == "93.184.216.34"
    http.pinned_get("https://ok.example:8443/p?q=1", ip, {})
    url, host, kw = sent[0]
    assert url == "https://93.184.216.34:8443/p?q=1"
    assert host == "ok.example:8443"
    assert kw["timeout"] and next(answers) == "127.0.0.1"  # never re-resolved


def test_pinned_adapter_keeps_hostname_for_tls(monkeypatch):
    import requests
    seen = {}
    real = requests.adapters.HTTPAdapter.init_poolmanager

    def spy(self, *a, **kw):
        seen.update(kw)
        return real(self, *a, **kw)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "init_poolmanager", spy)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                        lambda self, r, **kw: requests.Response())
    http.pinned_get("https://ok.example/", "2606:4700::1", {})
    assert seen["server_hostname"] == "ok.example" == seen["assert_hostname"]


def test_autofill_errors(client, drama, monkeypatch):
    assert client.post("/api/metadata/dramas/9999/autofill",
                       json={"page_text": "x"}).status_code == 404
    url = f"/api/metadata/dramas/{drama}/autofill"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"page_text": "a", "url": "http://e.com"}).status_code == 422
    assert client.post(url, json={"page_text": "a", "api_key": "k"}).status_code == 422
    assert client.post(url, json={"page_text": "a", "engine": "nope"}).status_code == 422
    monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda k: None)
    r = client.post(url, json={"page_text": "a"})
    assert r.status_code == 503 and _code(r) == "dependency_unavailable"


def test_autofill_llm_failure_redacted(client, drama, monkeypatch):
    monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda k: "k")
    monkeypatch.setattr(metadata_service.translate_engines, "get_engine",
                        lambda n, k: (_ for _ in ()).throw(RuntimeError("key sk-ant-SECRET")))
    r = client.post(f"/api/metadata/dramas/{drama}/autofill", json={"page_text": "a"})
    assert r.status_code == 503 and "SECRET" not in r.text


def test_apply_writes_only_whitelisted(client, drama):
    url = f"/api/metadata/dramas/{drama}/autofill/apply"
    r = client.post(url, json={"title_en": "Fresh", "source_url": "https://e.com/p"})
    assert r.status_code == 200
    row = db.get_drama(drama)
    assert row["title_en"] == "Fresh" and row["source_url"] == "https://e.com/p"
    assert client.post(url, json={"status": "done"}).status_code == 422
    assert client.post(url, json={"audio_filename": "x"}).status_code == 422
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"source_url": "javascript:x"}).status_code == 422
    assert client.post("/api/metadata/dramas/9999/autofill/apply",
                       json={"title_en": "x"}).status_code == 404
