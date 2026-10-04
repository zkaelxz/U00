"""
Tests for the optional Jellyfin connector (roadmap Step 39,
services/jellyfin_service.py and api/routers/jellyfin_routes.py). Fully
mocked: no Jellyfin server, no network, no real key.
"""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
requests = pytest.importorskip("requests")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from services import jellyfin_service as jf
from services import settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

KEY = "0123456789abcdef0123456789abcdef"
URL = "http://192.168.1.20:8096"


class Resp:
    def __init__(self, status=200, data=None, raw=None):
        import json
        self.status_code = status
        self._body = raw if raw is not None else (json.dumps(data).encode() if data is not None
                                                  else b"")

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        pass


class FakeSession:
    """Stands in for requests.Session; routes by (method, path)."""
    routes = {}
    calls = []

    def __init__(self):
        self.trust_env = True

    def request(self, method, url, params=None, headers=None, timeout=None,
                allow_redirects=True, stream=False):
        assert timeout and allow_redirects is False and self.trust_env is False and stream
        path = url[len(URL):]
        FakeSession.calls.append((method, path, dict(params or {}), headers))
        handler = FakeSession.routes.get((method, path))
        if handler is None:
            return Resp(404)
        return handler(params or {})

    def close(self):
        pass


def _stream(lang):
    return {"Type": "Subtitle", "Language": lang}


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Media"
    (root / "Show").mkdir(parents=True)
    (root / "Show" / "S01E01.mkv").write_bytes(b"v")
    (root / "Movie").mkdir()
    (root / "Movie" / "Movie.mp4").write_bytes(b"v")
    return root


@pytest.fixture
def setup(isolated_db, monkeypatch, lib, tmp_path):
    env = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "default_env_path", lambda: str(env))
    monkeypatch.setattr(jf.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("192.168.1.20", port))])
    FakeSession.routes, FakeSession.calls = {}, []
    monkeypatch.setattr(requests, "Session", FakeSession)
    jf.set_key(KEY)
    jf.set_config(enabled=True, server_url=URL, library_dir=str(lib))
    items = [
        {"Id": "ep1", "Name": "Pilot", "Type": "Episode", "SeriesName": "Show",
         "ParentIndexNumber": 1, "IndexNumber": 1, "Path": str(lib / "Show" / "S01E01.mkv"),
         "MediaStreams": [{"Type": "Video"}, _stream("chi")]},
        {"Id": "mv1", "Name": "Movie", "Type": "Movie", "Path": str(lib / "Movie" / "Movie.mp4"),
         "MediaStreams": [_stream("eng")]},
        {"Id": "mv2", "Name": "Elsewhere", "Type": "Movie", "Path": "/srv/other/x.mkv",
         "MediaStreams": []},
    ]

    def list_items(params):
        if "Ids" in params:
            return Resp(200, {"Items": [i for i in items if i["Id"] == params["Ids"]]})
        start, limit = int(params["StartIndex"]), int(params["Limit"])
        return Resp(200, {"Items": items[start:start + limit], "TotalRecordCount": len(items)})
    FakeSession.routes = {
        ("GET", "/Items"): list_items,
        ("GET", "/System/Info"): lambda p: Resp(200, {"ServerName": "Den", "Version": "10.9.0"}),
        ("POST", "/Library/Refresh"): lambda p: Resp(204),
        ("POST", "/Items/ep1/Refresh"): lambda p: Resp(204 if p.get("metadataRefreshMode")
                                                      == "Default" else 400),
    }
    drama = db.create_drama(title_en="My Drama", source_language="zh")
    db.save_lines(drama, _lines())
    return drama, lib


def _lines():
    from core import Line
    return [Line(idx=0, start=0.0, end=1.5, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=3.0, zh="再见", en="Bye")]


def test_off_by_default(isolated_db):
    cfg = jf.get_config()
    assert cfg["enabled"] is False and cfg["server_url"] is None
    with pytest.raises(ConflictError):
        jf.scan()


def test_config_never_returns_key(setup):
    cfg = jf.get_config()
    assert cfg["key_configured"] is True and KEY not in str(cfg)


def test_bad_scheme_and_credentials_refused(setup):
    for bad in ("file:///etc/passwd", "ftp://x", "http://user:pw@host:8096", "javascript:x"):
        with pytest.raises(InvalidInputError):
            jf.set_config(server_url=bad)
    with pytest.raises(InvalidInputError):
        jf.set_key("not a key!")


def test_link_local_and_own_ports_refused(setup, monkeypatch):
    monkeypatch.setattr(jf.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("169.254.169.254", port))])
    with pytest.raises(InvalidInputError):
        jf.test_connection()
    monkeypatch.setattr(jf.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", port))])
    jf.set_config(server_url="http://127.0.0.1:8756")
    with pytest.raises(InvalidInputError):
        jf.test_connection()
    jf._check_target("http://localhost:8096")  # a Jellyfin on this PC is fine


def test_household_port_refused_only_when_set(setup, monkeypatch):
    monkeypatch.setattr(jf.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", port))])
    monkeypatch.delenv(settings_service.HOUSEHOLD_PORT_ENV, raising=False)
    jf._check_target("http://127.0.0.1:8610")  # unset: an ordinary local port
    monkeypatch.setenv(settings_service.HOUSEHOLD_PORT_ENV, "8610")
    with pytest.raises(InvalidInputError):
        jf._check_target("http://127.0.0.1:8610")
    jf._check_target("http://localhost:8096")


def test_changed_api_port_is_refused_and_default_still_is(setup, monkeypatch):
    monkeypatch.setattr(jf.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", port))])
    monkeypatch.setenv(settings_service.API_PORT_ENV, "9123")
    for port in (9123, 8600, 8756):
        with pytest.raises(InvalidInputError):
            jf._check_target(f"http://127.0.0.1:{port}")
    jf._check_target("http://localhost:8096")


def test_test_connection_sends_key_as_header(setup):
    assert jf.test_connection() == {"ok": True, "server_name": "Den", "version": "10.9.0"}
    method, path, params, headers = FakeSession.calls[-1]
    assert KEY not in path and KEY not in str(params)
    assert headers["Authorization"] == f'MediaBrowser Token="{KEY}"'


def test_bad_key_and_unreachable_are_fixed_text(setup):
    FakeSession.routes[("GET", "/System/Info")] = lambda p: Resp(401)
    with pytest.raises(DependencyUnavailableError) as e:
        jf.test_connection()
    assert KEY not in str(e.value) and URL not in str(e.value)

    def boom(p):
        raise requests.ConnectionError(f"failed {URL}?api_key={KEY}")
    FakeSession.routes[("GET", "/System/Info")] = boom
    with pytest.raises(DependencyUnavailableError) as e:
        jf.test_connection()
    assert KEY not in str(e.value) and "192.168" not in str(e.value)


def test_scan_identifies_items_missing_target_language(setup):
    r = jf.scan("en")
    assert (r["total"], r["with_subtitles"], r["missing"]) == (3, 1, 2)
    by = {i["id"]: i for i in r["items"]}
    assert set(by) == {"ep1", "mv2"}
    assert by["ep1"]["writable"] is True and by["mv2"]["writable"] is False
    assert by["ep1"]["series"] == "Show" and by["ep1"]["episode"] == 1
    assert "Path" not in str(r) and "/srv/other" not in str(r)
    zh = jf.scan("zh")
    assert zh["missing"] == 2 and {i["id"] for i in zh["items"]} == {"mv1", "mv2"}
    # read-only: only GETs went out
    assert {c[0] for c in FakeSession.calls} == {"GET"}


def test_scan_pages(setup, monkeypatch):
    monkeypatch.setattr(jf, "PAGE_SIZE", 2)
    assert jf.scan("en")["total"] == 3
    starts = [c[2]["StartIndex"] for c in FakeSession.calls if c[1] == "/Items"]
    assert starts == [0, 2]


def test_send_next_to_item_uses_jellyfin_naming(setup):
    drama, lib = setup
    r = jf.send_to_jellyfin(drama, item_id="ep1")
    dest = lib / "Show" / "S01E01.eng.srt"
    assert dest.is_file() and "Hello" in dest.read_text(encoding="utf-8")
    assert r["files"] == [os.path.join("Show", "S01E01.eng.srt")]
    assert r["refresh"] == "done"
    assert FakeSession.calls[-1][:2] == ("POST", "/Items/ep1/Refresh")


def test_send_never_overwrites_without_consent(setup):
    drama, lib = setup
    dest = lib / "Show" / "S01E01.eng.srt"
    dest.write_text("mine", encoding="utf-8")
    with pytest.raises(ConflictError):
        jf.send_to_jellyfin(drama, item_id="ep1", refresh=False)
    assert dest.read_text(encoding="utf-8") == "mine"
    jf.send_to_jellyfin(drama, item_id="ep1", refresh=False, overwrite=True)
    assert "Hello" in dest.read_text(encoding="utf-8")


def test_send_refuses_item_outside_library(setup):
    drama, _ = setup
    with pytest.raises(InvalidInputError):
        jf.send_to_jellyfin(drama, item_id="mv2")
    with pytest.raises(NotFoundError):
        jf.send_to_jellyfin(drama, item_id="nope")
    with pytest.raises(InvalidInputError):
        jf.send_to_jellyfin(drama, item_id="../../etc")


def test_send_new_title_folder_layout(setup):
    drama, lib = setup
    folder = db.drama_dir(drama)
    with open(os.path.join(folder, "src.mkv"), "wb") as f:
        f.write(b"video")
    db.update_drama(drama, source_video_filename="src.mkv")
    r = jf.send_to_jellyfin(drama, fmt="ass", media="source")
    assert (lib / "My Drama" / "My Drama.mkv").read_bytes() == b"video"
    assert (lib / "My Drama" / "My Drama.eng.ass").is_file()
    assert r["refresh"] == "done" and FakeSession.calls[-1][:2] == ("POST", "/Library/Refresh")


def test_send_title_is_sanitized(setup):
    drama, lib = setup
    db.update_drama(drama, title_en='../..\\evil:name?')
    jf.send_to_jellyfin(drama, refresh=False)
    made = [p for p in lib.rglob("*.srt")]
    assert len(made) == 1 and lib in made[0].parents
    assert ".." not in made[0].parent.name


def test_send_refused_while_a_job_runs_for_the_drama(setup, monkeypatch):
    drama, lib = setup
    from services import drama_service
    monkeypatch.setattr(drama_service, "job_running_for_drama", lambda did: did == drama)
    with pytest.raises(ConflictError) as e:
        jf.send_to_jellyfin(drama, item_id="ep1")
    assert e.value.details["reason"] == "job_running"
    assert not (lib / "Show" / "S01E01.eng.srt").exists()


def test_refresh_failure_keeps_the_file(setup):
    drama, lib = setup
    FakeSession.routes[("POST", "/Items/ep1/Refresh")] = lambda p: Resp(500)
    r = jf.send_to_jellyfin(drama, item_id="ep1")
    assert r["refresh"] == "failed" and (lib / "Show" / "S01E01.eng.srt").is_file()


def test_send_requires_enabled_and_folder(setup):
    drama, _ = setup
    jf.set_config(enabled=False)
    with pytest.raises(ConflictError):
        jf.send_to_jellyfin(drama)
    jf.set_config(enabled=True, library_dir="")
    with pytest.raises(InvalidInputError):
        jf.send_to_jellyfin(drama)
    with pytest.raises(InvalidInputError):
        jf.set_config(library_dir="relative/path")


def test_item_is_matched_by_id_not_position(setup):
    drama, lib = setup
    everything = FakeSession.routes[("GET", "/Items")]
    # a server that ignores Ids and returns the whole library
    FakeSession.routes[("GET", "/Items")] = lambda p: everything({"StartIndex": 0, "Limit": 99})
    jf.send_to_jellyfin(drama, item_id="mv1", refresh=False, overwrite=True)
    assert (lib / "Movie" / "Movie.eng.srt").is_file()
    assert not (lib / "Show" / "S01E01.eng.srt").exists()


def test_planted_temp_link_is_never_followed(setup, tmp_path):
    drama, lib = setup
    outside = tmp_path / "outside.txt"
    outside.write_text("precious", encoding="utf-8")
    for name in ("S01E01.eng.srt.baihe-tmp", ".baihe-planted.tmp"):
        os.symlink(outside, lib / "Show" / name)
    jf.send_to_jellyfin(drama, item_id="ep1", refresh=False)
    assert outside.read_text(encoding="utf-8") == "precious"
    dest = lib / "Show" / "S01E01.eng.srt"
    assert dest.is_file() and not dest.is_symlink()


def test_symlinked_title_folder_is_refused(setup, tmp_path):
    drama, lib = setup
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, lib / "My Drama")
    with pytest.raises(InvalidInputError):
        jf.send_to_jellyfin(drama, refresh=False)
    assert list(elsewhere.iterdir()) == []


def test_folder_send_checks_every_file_before_writing(setup):
    drama, lib = setup
    folder = db.drama_dir(drama)
    with open(os.path.join(folder, "src.mkv"), "wb") as f:
        f.write(b"video")
    db.update_drama(drama, source_video_filename="src.mkv")
    (lib / "My Drama").mkdir()
    (lib / "My Drama" / "My Drama.eng.srt").write_text("mine", encoding="utf-8")
    with pytest.raises(ConflictError):
        jf.send_to_jellyfin(drama, media="source", refresh=False)
    assert sorted(p.name for p in (lib / "My Drama").iterdir()) == ["My Drama.eng.srt"]


def test_video_is_copied_not_linked(setup):
    drama, lib = setup
    src = os.path.join(db.drama_dir(drama), "src.mkv")
    with open(src, "wb") as f:
        f.write(b"video")
    db.update_drama(drama, source_video_filename="src.mkv")
    jf.send_to_jellyfin(drama, media="source", refresh=False)
    assert os.stat(src).st_nlink == 1


def test_dubbed_media_needs_a_dubbed_video(setup):
    drama, lib = setup
    with pytest.raises(InvalidInputError):
        jf.send_to_jellyfin(drama, media="dubbed", refresh=False)
    from services import artifact_service
    path = artifact_service.output_path(drama, "dubbed_video", "dub.mp4")
    with open(path, "wb") as f:
        f.write(b"dub")
    jf.send_to_jellyfin(drama, media="dubbed", refresh=False)
    assert (lib / "My Drama" / "My Drama.mp4").read_bytes() == b"dub"


def test_oversized_reply_is_refused(setup, monkeypatch):
    monkeypatch.setattr(jf, "MAX_RESPONSE_BYTES", 100)
    FakeSession.routes[("GET", "/System/Info")] = lambda p: Resp(200, raw=b"{" + b" " * 500 + b"}")
    with pytest.raises(DependencyUnavailableError):
        jf.test_connection()


def test_slow_reply_hits_the_deadline_and_is_closed(monkeypatch):
    from services import capped_body
    ticks = iter([0.0, 1.0, jf.READ_DEADLINE + 1])
    monkeypatch.setattr(capped_body.time, "monotonic", lambda: next(ticks))

    class Slow(Resp):
        closed = False

        def close(self):
            self.closed = True
    resp = Slow(200, raw=b"x" * 200_000)
    with pytest.raises(DependencyUnavailableError):
        jf._read_capped(resp)
    assert resp.closed


def test_reply_declaring_more_than_the_cap_is_refused_unread(monkeypatch):
    monkeypatch.setattr(jf, "MAX_RESPONSE_BYTES", 100)

    class Big(Resp):
        headers = {"Content-Length": "5000"}
        read = False

        def iter_content(self, size):
            Big.read = True
            return super().iter_content(size)
    with pytest.raises(DependencyUnavailableError):
        jf._read_capped(Big(200, raw=b"{}"))
    assert Big.read is False


def test_static_timeout():
    from tests.test_static_analysis import PROJECT_ROOT, _find_requests_calls_missing_timeout
    path = os.path.join(PROJECT_ROOT, "services", "jellyfin_service.py")
    assert _find_requests_calls_missing_timeout(path, session_verbs=True) == []


# --- routes -----------------------------------------------------------------

@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def test_routes_round_trip(client, setup):
    drama, lib = setup
    cfg = client.get("/api/jellyfin/config")
    assert cfg.status_code == 200 and KEY not in cfg.text and cfg.json()["enabled"] is True
    assert client.post("/api/jellyfin/test").json()["server_name"] == "Den"
    scan = client.post("/api/jellyfin/scan", json={"language": "en"})
    assert scan.status_code == 200 and scan.json()["missing"] == 2
    sent = client.post(f"/api/jellyfin/dramas/{drama}/send", json={"item_id": "ep1"})
    assert sent.status_code == 200, sent.text
    assert (lib / "Show" / "S01E01.eng.srt").is_file()
    again = client.post(f"/api/jellyfin/dramas/{drama}/send", json={"item_id": "ep1"})
    assert again.status_code == 409


def test_key_and_address_routes_use_the_key_write_gate(setup, isolated_db):
    drama, lib = setup
    closed = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                        headers={"X-Baihe-Local": "1"})
    assert closed.post("/api/jellyfin/key", json={"value": KEY, "confirm": True}).status_code == 403
    assert closed.post("/api/jellyfin/key/clear", json={"confirm": True}).status_code == 403
    r = closed.post("/api/jellyfin/config", json={"server_url": "http://evil.example", "confirm": True})
    assert r.status_code == 403 and jf.get_config()["server_url"] == URL
    assert closed.post("/api/jellyfin/config", json={"enabled": False}).status_code == 200
    opened = TestClient(create_app(ApiSettings(allow_key_writes=True)), raise_server_exceptions=False,
                        headers={"X-Baihe-Local": "1"})
    assert opened.post("/api/jellyfin/key", json={"value": KEY}).status_code == 422  # no confirm
    r = opened.post("/api/jellyfin/key", json={"value": KEY, "confirm": True})
    assert r.status_code == 200 and KEY not in r.text and r.json()["key_configured"] is True
    r = opened.post("/api/jellyfin/config", json={"server_url": "http://10.0.0.5:8096", "confirm": True})
    assert r.status_code == 200 and r.json()["server_url"] == "http://10.0.0.5:8096"
    assert opened.post("/api/jellyfin/key/clear", json={"confirm": True}).json()["key_configured"] is False


def test_routes_validation(client, setup):
    drama, _ = setup
    assert client.post("/api/jellyfin/config", json={"api_key": KEY}).status_code == 422
    assert client.post("/api/jellyfin/config", json={"library_dir": "relative"}).status_code == 422
    assert client.post("/api/jellyfin/scan", json={"language": "xx"}).status_code == 422
    assert client.post("/api/jellyfin/dramas/99999/send", json={}).status_code == 404
    assert client.post(f"/api/jellyfin/dramas/{drama}/send",
                       json={"format": "exe"}).status_code == 422
