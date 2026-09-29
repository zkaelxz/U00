"""
Tests for Migration Slice 24: write-only engine key endpoints. Temp .env
via a patched default path; no network. The guard is a safeguard, not
authentication (see docs/migration-review.md).
"""
import logging
import os
import stat

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings, load_settings
from api.server import create_app
from services import settings_service

SECRET = "sk-ant-TESTSECRET1234567890abcdef"
LOCAL = "http://127.0.0.1:8600"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "_default_env_path", lambda: str(path))
    for names in settings_service.ENV_NAMES.values():
        for n in names:
            monkeypatch.delenv(n, raising=False)
    return path


def _client(enabled=True, peer=("127.0.0.1", 50000), base=LOCAL):
    app = create_app(ApiSettings(allow_key_writes=enabled))
    return TestClient(app, base_url=base, client=peer, raise_server_exceptions=False)


def _set(c, engine="claude", value=SECRET, confirm=True, headers=None):
    return c.post(f"/api/settings/keys/{engine}",
                  json={"value": value, "confirm": confirm}, headers=headers)


def test_config_flag_default_off_and_parse():
    assert load_settings({}).allow_key_writes is False
    assert load_settings({"BAIHE_API_ALLOW_KEY_WRITES": "true"}).allow_key_writes is False
    assert load_settings({"BAIHE_API_ALLOW_KEY_WRITES": "1"}).allow_key_writes is True


def test_disabled_by_default_403(env_file):
    r = _set(_client(enabled=False))
    assert r.status_code == 403
    assert not env_file.exists()
    assert _client(enabled=False).post("/api/settings/keys/claude/clear",
                                       json={"confirm": True}).status_code == 403


def test_enabled_local_ok_and_resolves(env_file):
    c = _client()
    r = _set(c)
    assert r.status_code == 200
    assert r.json() == {"engine": "claude", "configured": True}
    assert SECRET not in r.text
    assert settings_service.resolve_key("claude") == SECRET
    overview = c.get("/api/settings")
    assert overview.json()["engine_keys"]["claude"] is True
    assert SECRET not in overview.text


def test_preserves_other_lines_replaces_in_place_and_permissions(env_file):
    env_file.write_text("# comment\nOTHER=1\nBAIHE_CLAUDE_KEY=old\nBAIHE_GROQ_KEY=g")
    os.chmod(env_file, 0o640)
    assert _set(_client()).status_code == 200
    assert env_file.read_text() == (
        f"# comment\nOTHER=1\nBAIHE_CLAUDE_KEY={SECRET}\nBAIHE_GROQ_KEY=g")
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o640
    assert [p.name for p in env_file.parent.iterdir()] == [".env"]


def test_new_file_and_append(env_file):
    _set(_client())
    assert env_file.read_text() == f"BAIHE_CLAUDE_KEY={SECRET}\n"
    _set(_client(), engine="groq", value="gsk_abc")
    assert env_file.read_text().splitlines() == [
        f"BAIHE_CLAUDE_KEY={SECRET}", "BAIHE_GROQ_KEY=gsk_abc"]


@pytest.mark.parametrize("header", [
    "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded", "X-Real-IP",
    "Tailscale-User-Login", "Cf-Connecting-Ip", "Cf-Ray", "Via"])
def test_proxy_headers_refused(env_file, header):
    assert _set(_client(), headers={header: "1.2.3.4"}).status_code == 403
    assert not env_file.exists()


def test_non_loopback_host_refused(env_file):
    assert _set(_client(base="http://baihe.example.ts.net")).status_code == 403
    assert _set(_client(base="http://192.168.1.5:8600")).status_code == 403
    assert not env_file.exists()


def test_non_loopback_peer_refused(env_file):
    assert _set(_client(peer=("192.168.1.9", 5000))).status_code == 403
    assert _set(_client(peer=("testclient", 5000))).status_code == 403
    assert not env_file.exists()


@pytest.mark.parametrize("host", ["localhost:8600", "[::1]:8600"])
def test_other_loopback_hosts_ok(env_file, host):
    assert _set(_client(base=f"http://{host}")).status_code == 200


def test_cross_origin_refused_local_origin_ok(env_file):
    c = _client()
    assert _set(c, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _set(c, headers={"Origin": "null"}).status_code == 403
    assert not env_file.exists()
    assert _set(c, headers={"Origin": "http://localhost:5173"}).status_code == 200


def test_missing_confirm_refused(env_file):
    c = _client()
    assert c.post("/api/settings/keys/claude", json={"value": SECRET}).status_code == 422
    assert _set(c, confirm=False).status_code == 422
    assert not env_file.exists()


def test_bad_engine_and_urls_rejected(env_file):
    c = _client()
    for engine in ("nope", "ollama_url", "monthly_cap_usd"):
        r = _set(c, engine=engine)
        assert r.status_code == 422
        assert SECRET not in r.text
    assert not env_file.exists()


@pytest.mark.parametrize("value", [
    "", "   ", "a" * 513, "abc\nINJECT=1", "abc\rdef", "ab cd", 'a"b', "a\x00b"])
def test_bad_values_rejected(env_file, value):
    r = _set(_client(), value=value)
    assert r.status_code == 422
    assert not env_file.exists()
    if len(value) > 20:
        assert value not in r.text


def test_wrong_types_rejected(env_file):
    c = _client()
    assert c.post("/api/settings/keys/claude",
                  json={"value": 123, "confirm": True}).status_code == 422
    assert c.post("/api/settings/keys/claude",
                  json={"value": SECRET, "confirm": True, "extra": 1}).status_code == 422


def test_secret_never_in_logs_or_errors(env_file, caplog):
    caplog.set_level(logging.DEBUG)
    c = _client()
    bodies = [_set(c).text,
              _set(c, confirm=False).text,
              _set(c, engine="nope").text,
              _set(c, value=SECRET + "\nX=1").text,
              _set(c, headers={"Via": "x"}).text,
              _set(_client(enabled=False)).text,
              c.get("/api/settings").text]
    assert SECRET not in str(bodies)
    assert SECRET not in caplog.text


def test_service_error_does_not_echo_value():
    with pytest.raises(Exception) as ei:
        settings_service._validate_key_value(SECRET + "\nX")
    assert SECRET not in str(ei.value)


def test_clear(env_file):
    c = _client()
    env_file.write_text(f"OTHER=1\nBAIHE_CLAUDE_KEY={SECRET}\nANTHROPIC_API_KEY=zzz\n")
    assert settings_service.resolve_key("claude") == SECRET
    r = c.post("/api/settings/keys/claude/clear", json={"confirm": True})
    assert r.status_code == 200
    assert r.json() == {"engine": "claude", "configured": False}
    assert env_file.read_text() == "OTHER=1\n"
    assert settings_service.resolve_key("claude") is None


def test_clear_guards(env_file):
    env_file.write_text(f"BAIHE_CLAUDE_KEY={SECRET}\n")
    c = _client()
    assert c.post("/api/settings/keys/claude/clear", json={"confirm": False}).status_code == 422
    assert c.post("/api/settings/keys/claude/clear", json={"confirm": True},
                  headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 403
    assert c.post("/api/settings/keys/nope/clear", json={"confirm": True}).status_code == 422
    assert SECRET in env_file.read_text()


def test_clear_without_file_ok(env_file):
    r = _client().post("/api/settings/keys/claude/clear", json={"confirm": True})
    assert r.status_code == 200 and r.json()["configured"] is False
    assert not env_file.exists()
