"""Live capture routes (API batch 1, spec L-1). The capture job itself is
faked: no ffmpeg, yt-dlp, Whisper, DNS or engine call."""
import socket
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import live_translate
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, live_service, translate_service

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
URL = "https://www.youtube.com/watch?v=abc"
SECRET = "sk-ant-" + "a" * 40


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture
def fake_live(monkeypatch, isolated_db):
    background_jobs.clear_all_jobs()
    live_service._sessions.clear()
    monkeypatch.setattr(background_jobs, "get_gpu_limit_enabled", lambda: False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: SECRET)
    seen = {}

    def fake_run(job_id, url, out_dir, segment_seconds, source_language, whisper_size, engine,
                 **kw):
        seen.update(url=url, out_dir=out_dir, kw=kw)
        background_jobs.set_result(job_id, [
            {"start": 0, "end": 2, "text": "你好", "translated": "Hello"},
            {"start": 2, "end": 4, "text": "再见",
             "translated": f"[translation failed: /home/me/x key {SECRET}]"}])
        background_jobs.update_progress(job_id, 0.5, f"Capturing into {out_dir}")
        # Runs until stopped, like the real capture. A short cutoff here let
        # the session end on its own while a test was still relying on it
        # being active (the 409 check in test_auth_on_permissions comes after
        # a cold Claude-engine build, over 1 s even on an idle machine), so
        # the one-session guard let a second start through. The teardown
        # below always cancels; the job-gone check covers a cleared record.
        _wait(lambda: background_jobs.is_cancel_requested(job_id)
              or background_jobs.get_status(job_id) is None, timeout=60.0)
    monkeypatch.setattr(live_translate, "run_live_job", fake_run)
    yield seen
    for sid in list(live_service._sessions):
        live_translate.bump_generation(sid)
        background_jobs.request_cancel(sid)
    _wait(lambda: not any(background_jobs.is_running(s) for s in live_service._sessions))
    background_jobs.clear_all_jobs()
    live_service._sessions.clear()


@pytest.fixture
def client(fake_live):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})  # as the React client sends


def _no_leak(r, extra=()):
    for bad in (SECRET, "/home/me", db.LIBRARY_DIR, "baihe_live_", *extra):
        assert bad not in r.text, bad


def test_start_poll_stop_flow(client, fake_live):
    r = client.post("/api/live/sessions", json={"url": URL, "engine": "fake",
                                                "max_minutes": 5, "use_gpu": True})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    # the job thread may not have reached the fake runner yet
    assert _wait(lambda: "kw" in fake_live)
    assert fake_live["kw"]["use_gpu"] is True and fake_live["kw"]["max_seconds"] == 300
    assert _wait(lambda: client.get(f"/api/live/sessions/{sid}").json()["next_index"] == 2)
    g = client.get(f"/api/live/sessions/{sid}")
    b = g.json()
    assert b["status"] == "running" and [c["translated"] for c in b["cues"]][0] == "Hello"
    assert "<path>" in b["message"]
    _no_leak(g, (fake_live["out_dir"],))
    after = client.get(f"/api/live/sessions/{sid}", params={"after": 1}).json()
    assert len(after["cues"]) == 1 and after["next_index"] == 2
    lst = client.get("/api/live/sessions")
    assert lst.status_code == 200 and lst.json()[0]["session_id"] == sid
    assert lst.json()[0]["cue_count"] == 2
    s = client.post(f"/api/live/sessions/{sid}/stop")
    assert s.status_code == 200 and s.json() == {"session_id": sid, "stopping": True}
    assert _wait(lambda: client.get(f"/api/live/sessions/{sid}").json()["status"] == "cancelled")
    assert client.post(f"/api/live/sessions/{sid}/stop").status_code == 200  # idempotent


def test_404_and_422(client):
    unknown = "live_" + "0" * 32
    assert client.get(f"/api/live/sessions/{unknown}").status_code == 404
    assert client.post(f"/api/live/sessions/{unknown}/stop").status_code == 404
    assert client.get("/api/live/sessions/not-a-session").status_code == 422
    assert client.post("/api/live/sessions/x/stop").status_code == 422
    for body in ({"url": "file:///etc/passwd", "engine": "fake"},
                 {"url": URL, "engine": "fake", "source_language": "fr"},
                 {"url": URL, "engine": "fake", "whisper_size": "huge"},
                 {"url": URL, "engine": "nope"},
                 {"url": URL, "cookies": "x"},
                 {"url": URL, "use_gpu": "yes"},
                 {}):
        r = client.post("/api/live/sessions", json=body)
        assert r.status_code == 422, body
    assert client.get(f"/api/live/sessions/{unknown}", params={"after": -1}).status_code == 422


def test_private_host_422_and_no_key_503(client, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 443))])
    assert client.post("/api/live/sessions",
                       json={"url": "http://router.local/", "engine": "fake"}
                       ).status_code == 422
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: None)
    r = client.post("/api/live/sessions", json={"url": URL, "engine": "claude"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "dependency_unavailable"
    _no_leak(r)


def _on_client():
    return TestClient(create_app(ApiSettings(auth_mode="on")),
                      base_url="https://baihe.example.com", raise_server_exceptions=False)


def _headers(*perms, email="kid@example.com", revoke=()):
    u = auth_service.add_user(email)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    for p in revoke:
        auth_service.revoke_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_auth_on_permissions(fake_live):
    c = _on_client()
    free = {"url": URL, "engine": "fake"}
    paid = {"url": URL, "engine": "claude"}
    assert c.post("/api/live/sessions", json=free).status_code == 401
    household = _headers()
    assert c.post("/api/live/sessions", json=free, headers=household).status_code == 403
    importer = _headers("media.import_url", email="imp@example.com")
    assert c.post("/api/live/sessions", json=paid, headers=importer).status_code == 403
    assert c.post("/api/live/sessions", json={"url": URL}, headers=importer).status_code == 403
    r = c.post("/api/live/sessions", json=free, headers=importer)
    assert r.status_code == 200
    sid = r.json()["session_id"]
    spender = _headers("media.import_url", "engines.paid", email="pay@example.com")
    # past the permission checks; refused only because one session is running
    assert c.post("/api/live/sessions", json=paid, headers=spender).status_code == 409
    # reads: library.read; stop: jobs.cancel (both household defaults).
    # Auth B2: a session is its starter's; another user gets a 404.
    assert c.get(f"/api/live/sessions/{sid}", headers=importer).status_code == 200
    assert c.get(f"/api/live/sessions/{sid}", headers=household).status_code == 404
    assert c.get("/api/live/sessions", headers=household).json() == []
    reader_only = _headers(email="ro@example.com", revoke=("jobs.cancel",))
    assert c.post(f"/api/live/sessions/{sid}/stop", headers=reader_only).status_code == 403
    no_read = _headers(email="nr@example.com", revoke=("library.read",))
    assert c.get(f"/api/live/sessions/{sid}", headers=no_read).status_code == 403
    assert c.post(f"/api/live/sessions/{sid}/stop", headers=household).status_code == 404
    assert c.post(f"/api/live/sessions/{sid}/stop", headers=importer).status_code == 200


def test_one_session_at_a_time(client, fake_live):
    body = {"url": URL, "engine": "fake"}
    first = client.post("/api/live/sessions", json=body)
    assert first.status_code == 200
    for _ in range(3):
        r = client.post("/api/live/sessions", json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert len(live_service._sessions) == 1
    sid = first.json()["session_id"]
    client.post(f"/api/live/sessions/{sid}/stop")
    assert _wait(lambda: client.get(f"/api/live/sessions/{sid}").json()["status"] == "cancelled")
    assert _wait(lambda: client.post("/api/live/sessions", json=body).status_code == 200)


def test_job_gets_stream_check_and_ffmpeg_whitelist(client, fake_live):
    r = client.post("/api/live/sessions", json={"url": URL, "engine": "fake"})
    assert r.status_code == 200
    assert _wait(lambda: "kw" in fake_live)
    assert fake_live["kw"]["stream_url_check"] is live_service.check_stream_url
    assert fake_live["kw"]["protocol_whitelist"] == "http,https,tcp,tls,crypto,httpproxy"
    assert fake_live["kw"]["proxy"].startswith("http://baihe:")
    assert "@127.0.0.1:" in fake_live["kw"]["proxy"]


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "rtmp://example.com/live", "concat:a|b",
                                 "http://user:pw@example.com/x", "https:///nohost", None, "",
                                 "data:text/plain,x"])
def test_stream_url_check_refuses_non_http(fake_live, bad):
    with pytest.raises(live_service.InvalidInputError) as e:
        live_service.check_stream_url(bad)
    assert "etc/passwd" not in str(e.value) and "pw@" not in str(e.value)


def test_stream_url_check_refuses_private_host_and_allows_long_public(fake_live, monkeypatch):
    long_url = "https://manifest.googlevideo.com/api/manifest/hls_playlist/" + "x" * 4000
    live_service.check_stream_url(long_url)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))])
    with pytest.raises(live_service.InvalidInputError) as e:
        live_service.check_stream_url("http://metadata.example/latest?sig=SECRETSIG")
    assert "SECRETSIG" not in str(e.value)


def test_refused_stream_url_ends_the_job_without_leaking(client, fake_live, monkeypatch):
    def fake_run(job_id, url, out_dir, *a, stream_url_check=None, **kw):
        stream_url_check("file:///etc/passwd?token=" + SECRET)
    monkeypatch.setattr(live_translate, "run_live_job", fake_run)
    sid = client.post("/api/live/sessions", json={"url": URL, "engine": "fake"}
                      ).json()["session_id"]
    assert _wait(lambda: client.get(f"/api/live/sessions/{sid}").json()["status"] == "error")
    g = client.get(f"/api/live/sessions/{sid}")
    assert "not a public" in g.json()["message"]
    _no_leak(g, ("etc/passwd",))
