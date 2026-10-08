"""Settings "Loaded now" panel: each source on its own, no paths or URLs out,
the remote Ollama never contacted, and the free action refused while a GPU job
runs."""

import json
import sys
import types

import pytest

import background_jobs
import core
from services import loaded_models_service as svc
from services.service_errors import ConflictError


class _Resp:
    def __init__(self, payload, ok=True):
        self._payload, self.ok = payload, ok

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """Nothing loaded, no GPU, no Ollama, no llama.cpp unless a test says so."""
    monkeypatch.setattr(core, "_whisper_model_cache", {})
    monkeypatch.setattr(core, "_whisper_device_info", {})
    monkeypatch.setattr(svc.settings_service, "resolve_key", lambda key, *a, **k: None)
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "torch", None)

    def refuse(url):
        raise ConnectionError("nothing listening")
    monkeypatch.setattr(svc, "_loopback_get", refuse)
    monkeypatch.setattr(background_jobs, "list_all_jobs", lambda: {})


def test_everything_empty_is_honest():
    out = svc.get_loaded_models()
    assert out["ollama"] == {"state": "not_running", "models": []}
    assert out["app"] == {"state": "ok", "models": []}
    assert out["gpu"] == {"state": "unknown"}
    assert out["llama_cpp_running"] is False
    assert out["gpu_job_running"] is False


def test_ollama_models_listed(monkeypatch):
    seen = []

    def get(url):
        seen.append(url)
        return _Resp({"models": [{"name": "gemma4:12b", "size": 100, "size_vram": 60}]})
    monkeypatch.setattr(svc, "_loopback_get", get)
    out = svc._ollama_rows()
    assert out == {"state": "running",
                   "models": [{"name": "gemma4:12b", "size_bytes": 100, "vram_bytes": 60}]}
    assert seen == ["http://localhost:11434/api/ps"]


def test_remote_ollama_is_never_contacted(monkeypatch):
    monkeypatch.setattr(svc.settings_service, "resolve_key",
                        lambda key, *a, **k: "http://192.168.1.50:11434")
    monkeypatch.setattr(svc, "_loopback_get",
                        lambda url: pytest.fail("contacted a non-loopback host"))
    assert svc._ollama_rows() == {"state": "not_local", "models": []}


@pytest.mark.parametrize("url,expected", [
    ("http://localhost:11434", True), ("http://127.0.0.1:1", True), ("http://[::1]:1", True),
    ("http://10.0.0.2:11434", False), ("http://localhost.evil.com", False), ("not a url", False)])
def test_is_loopback_url(url, expected):
    assert svc.is_loopback_url(url) is expected


def test_ollama_error_status_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setattr(svc, "_loopback_get", lambda url: _Resp({}, ok=False))
    assert svc._ollama_rows()["state"] == "unavailable"


def test_app_models_from_caches_hide_local_paths(monkeypatch):
    monkeypatch.setattr(core, "_whisper_model_cache",
                        {"large-v3-turbo_gpu": object(), "/home/me/models/my-whisper_cpu": object()})
    monkeypatch.setattr(core, "_whisper_device_info", {"large-v3-turbo_gpu": {"device": "cuda"}})
    out = svc._app_rows()
    assert {(m["name"], m["device"]) for m in out["models"]} == {
        ("large-v3-turbo", "GPU"), ("my-whisper", "CPU")}
    assert "/home" not in json.dumps(out)


def test_app_models_reads_other_caches_only_when_loaded(monkeypatch):
    aligner = types.SimpleNamespace(_aligner_model_cache={"gpu": object()})
    asr = types.SimpleNamespace(_asr_model_cache={"1.7B_cpu": object(), "moss_cuda": object()})
    monkeypatch.setitem(sys.modules, "forced_align", aligner)
    monkeypatch.setitem(sys.modules, "asr_backend", asr)
    names = {(m["name"], m["device"]) for m in svc._app_rows()["models"]}
    assert names == {("Qwen3 forced aligner", "GPU"), ("Qwen3-ASR 1.7B", "CPU"),
                     ("Moss transcribe + diarize", "GPU")}


def test_gpu_unknown_on_cpu_only_machine():
    assert svc._gpu_row() == {"state": "unknown"}


def test_gpu_from_nvidia_smi(monkeypatch):
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="NVIDIA RTX 4090, 24564, 8000, 16564\n"))
    row = svc._gpu_row()
    assert row["name"] == "NVIDIA RTX 4090"
    assert row["total_bytes"] == 24564 * 1024 * 1024
    assert row["free_bytes"] + row["used_bytes"] == row["total_bytes"]


def test_gpu_falls_back_to_loaded_torch_when_smi_fails(monkeypatch):
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    def boom(*a, **k):
        raise OSError("driver mismatch")
    monkeypatch.setattr(svc.subprocess, "run", boom)
    cuda = types.SimpleNamespace(is_available=lambda: True, mem_get_info=lambda: (30, 100),
                                 get_device_name=lambda i: "Fake GPU")
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=cuda))
    assert svc._gpu_row() == {"state": "ok", "name": "Fake GPU", "total_bytes": 100,
                              "free_bytes": 30, "used_bytes": 70}


def test_one_failing_source_does_not_hide_the_others(monkeypatch):
    def boom():
        raise RuntimeError("secret sk-123 at /home/me")
    monkeypatch.setattr(svc, "_ollama_rows", boom)
    monkeypatch.setattr(svc, "_gpu_row", boom)
    out = svc.get_loaded_models()
    assert out["ollama"] == {"state": "unavailable", "models": []}
    assert out["gpu"] == {"state": "unknown"}
    assert out["app"]["state"] == "ok"
    assert "sk-123" not in json.dumps(out)


def test_llama_cpp_probe(monkeypatch):
    monkeypatch.setattr(svc, "_loopback_get", lambda url: _Resp({"data": []}))
    assert svc._llama_cpp_running() is True
    monkeypatch.setattr(svc, "_loopback_get", lambda url: _Resp({"other": 1}))
    assert svc._llama_cpp_running() is False


def test_free_refused_while_gpu_job_runs(monkeypatch):
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {"j1": {"status": "running", "gpu_touching": True}})
    monkeypatch.setattr(core, "release_gpu_models", lambda: pytest.fail("freed under a job"))
    with pytest.raises(ConflictError):
        svc.free_app_models()


def test_free_drops_models_when_idle(monkeypatch):
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {"j1": {"status": "done", "gpu_touching": True},
                                 "j2": {"status": "running", "gpu_touching": False}})
    calls = []
    monkeypatch.setattr(core, "release_gpu_models", lambda: calls.append(1))
    assert svc.free_app_models()["gpu_job_running"] is False
    assert calls == [1]


def _client(auth="off"):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    app = create_app(ApiSettings(auth_mode=auth, serve_frontend=False), frontend_dist=None)
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def test_route_response_has_no_paths_or_urls(monkeypatch, isolated_db):
    monkeypatch.setattr(core, "_whisper_model_cache", {"/home/me/secret dir/w_gpu": object()})
    monkeypatch.setattr(svc, "_loopback_get", lambda url: _Resp(
        {"models": [{"name": "m:1", "size": 5, "size_vram": 5, "digest": "x",
                     "details": {"path": "/models/m"}}], "data": []}))
    res = _client().get("/api/settings/loaded-models")
    assert res.status_code == 200
    text = res.text
    assert "/home" not in text and "/models" not in text
    assert "http" not in text and "localhost" not in text and "11434" not in text
    assert res.json()["ollama"]["models"][0]["name"] == "m:1"


def test_free_route_needs_confirm_and_is_busy_aware(monkeypatch, isolated_db):
    client = _client()
    assert client.post("/api/settings/loaded-models/free-app-models", json={}).status_code == 422
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {"j": {"status": "queued", "gpu_touching": True}})
    res = client.post("/api/settings/loaded-models/free-app-models", json={"confirm": True})
    assert res.status_code == 409
    assert "GPU job" in res.text


def test_free_route_is_refused_for_a_remote_caller(isolated_db):
    from fastapi.testclient import TestClient
    client = _client()
    remote = TestClient(client.app, base_url="https://example.com", raise_server_exceptions=False)
    assert remote.post("/api/settings/loaded-models/free-app-models",
                       json={"confirm": True}).status_code in (401, 403)
