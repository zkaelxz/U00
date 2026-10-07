"""Diagnostics parity (react-misc-parity): ffmpeg libass in the setup
checks (Q01), deleting a cached model (Q14, PC only). Every scan/delete is faked; no network, no files
outside the throwaway library."""
import subprocess

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import diagnostics
from api.api_config import ApiSettings
from api.server import create_app
from services import library_admin_service

REV = "a" * 40


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def cache(monkeypatch):
    deleted = []
    monkeypatch.setattr(diagnostics, "scan_hf_cache", lambda *a, **k: [
        {"repo_id": "org/model", "repo_type": "model", "revision": REV, "size_bytes": 10}])
    monkeypatch.setattr(diagnostics, "delete_hf_cache_revision",
                        lambda rev, *a, **k: deleted.append(rev) or True)
    monkeypatch.setattr(diagnostics, "scan_model_folder", lambda kind, *a, **k: [
        {"name": {"torch": "htdemucs.th", "audio_separator": "model.ckpt"}[kind], "size_bytes": 7}])
    monkeypatch.setattr(diagnostics, "delete_model_folder_entry",
                        lambda kind, name, *a, **k: deleted.append(f"{kind}:{name}") or True)
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    return deleted


class TestLibass:
    def _run(self, monkeypatch, stdout):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr(diagnostics.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout=stdout, stderr=""))
        return diagnostics.check_ffmpeg()

    def test_detects_libass_from_configure_flags(self, monkeypatch):
        with_ = "ffmpeg version 6.1\n  configuration: --enable-gpl --enable-libass --enable-libx264\n"
        assert self._run(monkeypatch, with_)["libass"] is True
        assert self._run(monkeypatch, "ffmpeg version 6.1\n  configuration: --enable-gpl\n")["libass"] is False

    def test_missing_ffmpeg_reports_unknown(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        assert diagnostics.check_ffmpeg()["libass"] is None

    def test_setup_checks_route_carries_libass(self, client, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_ffmpeg",
                            lambda: {"found": True, "version": "ffmpeg 6", "path": "/x", "libass": False})
        body = client.get("/api/diagnostics/setup-checks").json()
        assert body["ffmpeg"] == {"found": True, "version": "ffmpeg 6", "libass": False}


class TestModelCacheDelete:
    def test_deletes_only_listed_names_with_confirm(self, client, cache):
        url = f"/api/diagnostics/model-cache/hf/{REV}/delete"
        assert client.post(url, json={}).status_code == 422
        assert cache == []
        r = client.post(url, json={"confirm": True})
        assert r.status_code == 200 and r.json() == {"deleted": True, "name": REV}
        assert cache == [REV]

    def test_model_folder_files_delete_only_listed_names(self, client, cache):
        base = "/api/diagnostics/model-cache/files"
        assert client.post(f"{base}/torch/htdemucs.th/delete", json={}).status_code == 422
        r = client.post(f"{base}/torch/htdemucs.th/delete", json={"confirm": True})
        assert r.status_code == 200 and r.json() == {"deleted": True, "name": "htdemucs.th"}
        r = client.post(f"{base}/audio_separator/model.ckpt/delete", json={"confirm": True})
        assert r.status_code == 200
        # A name listed in the other folder, an unknown folder, a path.
        assert client.post(f"{base}/torch/model.ckpt/delete",
                           json={"confirm": True}).status_code == 404
        assert client.post(f"{base}/hf/htdemucs.th/delete",
                           json={"confirm": True}).status_code == 422
        assert client.post(f"{base}/torch/..%2Fhtdemucs.th/delete",
                           json={"confirm": True}).status_code in (404, 405, 422)
        assert cache == ["torch:htdemucs.th", "audio_separator:model.ckpt"]

    def test_unknown_or_malformed_names_refused(self, client, cache):
        assert client.post(f"/api/diagnostics/model-cache/hf/{'b' * 40}/delete",
                           json={"confirm": True}).status_code == 404
        assert client.post("/api/diagnostics/model-cache/hf/abc/delete",
                           json={"confirm": True}).status_code == 422
        assert client.post("/api/diagnostics/model-cache/piper/en_US-amy-medium/delete",
                           json={"confirm": True}).status_code in (404, 405)
        assert cache == []

    def test_refused_while_a_job_runs(self, client, cache, monkeypatch):
        monkeypatch.setattr(library_admin_service, "any_job_running", lambda: True)
        r = client.post(f"/api/diagnostics/model-cache/hf/{REV}/delete", json={"confirm": True})
        assert r.status_code == 409 and cache == []

    def test_holds_the_library_while_deleting(self, client, cache, monkeypatch):
        import background_jobs
        seen = []
        monkeypatch.setattr(diagnostics, "delete_hf_cache_revision",
                            lambda v, *a, **k: seen.append(background_jobs.exclusive_active()) or True)
        r = client.post(f"/api/diagnostics/model-cache/hf/{REV}/delete", json={"confirm": True})
        assert r.status_code == 200 and seen == [True]
        assert not background_jobs.exclusive_active()          # released afterwards
        assert background_jobs.acquire_exclusive("test hold")
        try:
            r = client.post(f"/api/diagnostics/model-cache/hf/{REV}/delete", json={"confirm": True})
            assert r.status_code == 409 and cache == []
        finally:
            background_jobs.release_exclusive()

    def test_failed_delete_is_reported(self, client, cache, monkeypatch):
        monkeypatch.setattr(diagnostics, "delete_hf_cache_revision", lambda *a, **k: False)
        r = client.post(f"/api/diagnostics/model-cache/hf/{REV}/delete", json={"confirm": True})
        assert r.status_code >= 400

    def test_pc_only(self, isolated_db, cache):
        remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                            raise_server_exceptions=False)
        assert remote.post(f"/api/diagnostics/model-cache/hf/{REV}/delete",
                           json={"confirm": True}).status_code in (401, 403)
        assert remote.post("/api/diagnostics/model-cache/files/torch/htdemucs.th/delete",
                           json={"confirm": True}).status_code in (401, 403)
        assert cache == []
