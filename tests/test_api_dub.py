"""
Tests for the Dub-stage API endpoints (Migration Slice 25): contract shape,
404s, the shared error body, and that no filesystem path leaks. Uses
TestClient over an `isolated_db` library; no network, no models.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import settings_service


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: None)
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _seed(db, **fields):
    fields.setdefault("title_en", "D")
    did = db.create_drama(**fields)
    db.save_lines(did, [
        Line(idx=0, start=0, end=1, zh="你好", en="hi", speaker="S1"),
        Line(idx=1, start=1, end=2, zh="再见", en="bye", speaker="S2"),
    ])
    return did


def _assert_error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])


class TestConfig:
    def test_contract_shape(self, client, isolated_db):
        did = _seed(isolated_db)
        resp = client.get(f"/api/dub/dramas/{did}/config")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "drama_id", "content_mode", "is_narration", "narration_language",
            "narration_language_options", "source_language", "tts_engines", "default_engine",
            "blocker", "defaults", "speakers", "gpu_required", "speakable_line_count", "track_available",
            "can_keep_background"}
        assert body["drama_id"] == did
        assert body["speakable_line_count"] == 2
        assert set(body["defaults"]) == {"max_speedup", "max_slowdown", "speedup_range",
                                         "slowdown_range"}
        assert {s["speaker_label"] for s in body["speakers"]} == {"S1", "S2"}
        for s in body["speakers"]:
            assert set(s) == {"speaker_label", "character_name", "engine", "has_clone_ref",
                              "clone_warning"}
        for e in body["tts_engines"]:
            assert set(e) == {"key", "label", "unavailable_reason"}
        assert body["default_engine"] == "omnivoice"
        assert [e["key"] for e in body["tts_engines"]][0] == "omnivoice"

    def test_narration_defaults_null(self, client, isolated_db):
        did = _seed(isolated_db, content_mode="novel_narration")
        body = client.get(f"/api/dub/dramas/{did}/config").json()
        assert body["is_narration"] is True
        assert body["defaults"] is None

    def test_unknown_drama_404(self, client):
        resp = client.get("/api/dub/dramas/999/config")
        assert resp.status_code == 404
        _assert_error(resp)

    def test_invalid_id_rejected(self, client):
        assert client.get("/api/dub/dramas/0/config").status_code == 422

    def test_no_path_leak(self, client, isolated_db):
        did = _seed(isolated_db)
        resp = client.get(f"/api/dub/dramas/{did}/config")
        assert str(isolated_db.LIBRARY_DIR) not in resp.text


class TestPacing:
    def test_empty_unavailable(self, client, isolated_db):
        did = _seed(isolated_db)
        resp = client.get(f"/api/dub/dramas/{did}/pacing")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"available", "counts", "lines"}
        assert body["available"] is False
        assert body["lines"] == []
        assert str(isolated_db.LIBRARY_DIR) not in resp.text

    def test_unknown_drama_404(self, client):
        resp = client.get("/api/dub/dramas/999/pacing")
        assert resp.status_code == 404
        _assert_error(resp)


def test_h3_pacing_accepts_float_ms(client, isolated_db):
    import json
    import os
    import dub
    did = _seed(isolated_db)
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="hi", dub_filename="a.wav")])
    d = os.path.join(isolated_db.drama_dir(did), "dub_clips")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, dub.PACING_FILENAME), "w", encoding="utf-8") as f:
        json.dump({"0": {"status": "fit", "factor": 1.0, "clip_ms": 950.5, "window_ms": 1000.25,
                         "dub_filename": "a.wav"}}, f)
    resp = client.get(f"/api/dub/dramas/{did}/pacing")
    assert resp.status_code == 200, resp.text
    assert resp.json()["lines"][0]["clip_ms"] == 950.5


def test_track_download(client, isolated_db, tmp_path):
    import db
    did = _seed(db)
    assert client.get(f"/api/dub/dramas/{did}/track").status_code == 404
    _assert_error(client.get(f"/api/dub/dramas/{did}/track"))
    assert client.get("/api/dub/dramas/9999/track").status_code == 404
    import os
    with open(os.path.join(db.drama_dir(did), "dub_track.wav"), "wb") as f:
        f.write(b"RIFFdata")
    r = client.get(f"/api/dub/dramas/{did}/track")
    assert r.status_code == 200 and r.content == b"RIFFdata"
    assert r.headers["content-disposition"] == f'attachment; filename="drama_{did}_dub_track.wav"'


def test_track_symlink_is_404(client, isolated_db, tmp_path):
    import os
    import db
    did = _seed(db)
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"x")
    os.symlink(outside, os.path.join(db.drama_dir(did), "dub_track.wav"))
    assert client.get(f"/api/dub/dramas/{did}/track").status_code == 404


class TestEngineAvailability:
    def test_a_missing_engine_package_is_named_on_that_engine(self, client, isolated_db, monkeypatch):
        import importlib.util
        did = _seed(isolated_db)
        real = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, *a, **k: None if name == "omnivoice" else real(name, *a, **k))
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        engines = {e["key"]: e for e in client.get(f"/api/dub/dramas/{did}/config").json()["tts_engines"]}
        assert engines["omnivoice"]["unavailable_reason"] == "OmniVoice is not installed. Install it in Diagnostics."

    def test_no_engine_installed_is_a_plain_blocker_not_an_error(self, client, isolated_db, monkeypatch):
        import importlib.util
        did = _seed(isolated_db)
        real = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, *a, **k: None if name == "omnivoice" else real(name, *a, **k))
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        resp = client.get(f"/api/dub/dramas/{did}/config")
        assert resp.status_code == 200
        assert resp.json()["blocker"] == "No voice engine is installed. Install one in Diagnostics."
        run = client.post(f"/api/dub/dramas/{did}/run", json={})
        assert run.status_code == 503
        assert run.json()["error"]["message"] == "OmniVoice is not installed. Install it in Diagnostics."
        assert "Traceback" not in run.text and "ImportError" not in run.text

    def test_a_character_stored_with_a_removed_engine_loads_with_the_plain_message(
            self, client, isolated_db):
        did = _seed(isolated_db)
        isolated_db.upsert_character(did, "S1", clone_engine="f5tts")
        body = client.get(f"/api/dub/dramas/{did}/config").json()
        speaker = next(s for s in body["speakers"] if s["speaker_label"] == "S1")
        assert speaker["clone_warning"] == "The F5-TTS engine was removed. Pick another voice engine in Dub."
        assert "F5-TTS engine was removed" in body["blocker"]
