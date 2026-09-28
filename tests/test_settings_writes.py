"""Tests for non-secret Settings writes and the persisted use_gpu toggle
(Migration Slice 23). Mocked throughout; no network."""

import inspect

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
from api.api_config import ApiSettings
from api.server import create_app
from services import settings_service, transcribe_service
from services.service_errors import InvalidInputError

TOGGLES = ("gpu_limit_enabled", "notify_on_completion", "use_gpu", "gemini_free_tier")


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


class TestService:
    def test_defaults(self, isolated_db):
        assert settings_service.get_use_gpu() is False
        assert settings_service.get_gemini_free_tier() is False
        ov = settings_service.get_settings_overview()
        assert ov["use_gpu"] is False and ov["gemini_free_tier"] is False

    @pytest.mark.parametrize("key", TOGGLES)
    def test_round_trip(self, isolated_db, key):
        assert settings_service.set_settings({key: True})[key] is True
        assert settings_service.get_settings_overview()[key] is True
        assert settings_service.set_settings({key: False})[key] is False

    def test_backed_by_background_jobs_and_readers(self, isolated_db):
        settings_service.set_settings({"gpu_limit_enabled": False, "notify_on_completion": True,
                                       "use_gpu": True, "gemini_free_tier": True})
        assert background_jobs.get_gpu_limit_enabled() is False
        assert background_jobs.get_notify_on_completion() is True
        assert settings_service.get_use_gpu() is True
        assert settings_service.get_gemini_free_tier() is True

    def test_unknown_key_rejected_without_echo(self, isolated_db):
        with pytest.raises(InvalidInputError) as ei:
            settings_service.set_settings({"groq_key": "sk-secret-value"})
        assert "sk-secret-value" not in str(ei.value)

    @pytest.mark.parametrize("bad", ["true", 1, None, "sk-secret-value"])
    def test_non_bool_rejected_atomically(self, isolated_db, bad):
        with pytest.raises(InvalidInputError) as ei:
            settings_service.set_settings({"use_gpu": True, "notify_on_completion": bad})
        assert "sk-secret-value" not in str(ei.value)
        assert settings_service.get_use_gpu() is False  # nothing written


class TestApi:
    def test_get_defaults_and_no_secrets(self, client, monkeypatch):
        monkeypatch.setenv("BAIHE_GROQ_KEY", "sk-very-secret")
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["use_gpu"] is False and body["gemini_free_tier"] is False
        assert body["engine_keys"]["groq"] is True
        assert "sk-very-secret" not in resp.text

    @pytest.mark.parametrize("key", TOGGLES)
    def test_post_round_trip(self, client, key):
        resp = client.post("/api/settings", json={key: True})
        assert resp.status_code == 200 and resp.json()[key] is True
        assert client.get("/api/settings").json()[key] is True

    def test_partial_update_leaves_others(self, client):
        client.post("/api/settings", json={"use_gpu": True})
        body = client.post("/api/settings", json={"gemini_free_tier": True}).json()
        assert body["use_gpu"] is True and body["gemini_free_tier"] is True

    def test_unknown_key_422_no_echo(self, client):
        resp = client.post("/api/settings", json={"groq_key": "sk-secret-value"})
        assert resp.status_code == 422
        assert "sk-secret-value" not in resp.text

    @pytest.mark.parametrize("bad", ["yes", 1, None])
    def test_non_bool_422(self, client, bad):
        assert client.post("/api/settings", json={"use_gpu": bad}).status_code == 422
        assert client.get("/api/settings").json()["use_gpu"] is False


def test_use_gpu_reaches_transcribe_job_args(isolated_db, monkeypatch):
    from tests.test_transcribe_service import _drama_with_audio
    did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
    captured = {}

    def fake_start_job(job_id, target, *a, **k):
        captured.update(dict(zip(inspect.signature(target).parameters, a)))
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)

    transcribe_service.start_transcribe_run(did)
    assert captured["use_gpu"] is False
    settings_service.set_settings({"use_gpu": True})
    transcribe_service.start_transcribe_run(did)
    assert captured["use_gpu"] is True
