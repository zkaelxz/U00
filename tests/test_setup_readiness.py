"""GET /api/setup/readiness: what is installed, as booleans only, so the UI can
say why a control is off. Nothing here runs a real subprocess or touches the network."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import diagnostics
import diagnostics_torch
import qwen3_native
import whisper_models
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, settings_service, setup_readiness_service as svc

REMOTE = "https://baihe.example.com"


@pytest.fixture(autouse=True)
def fresh_cache():
    svc.clear_cache()
    yield
    svc.clear_cache()


@pytest.fixture
def bare_pc(monkeypatch):
    """A fresh install: nothing optional, no GPU, no keys, no Ollama."""
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: False)
    monkeypatch.setattr(diagnostics, "check_ffmpeg", lambda: {"found": False, "libass": None})
    monkeypatch.setattr(diagnostics, "check_js_runtime", lambda: {"found": False, "name": None})
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    monkeypatch.setattr(diagnostics_torch, "torch_family_versions",
                        lambda: {n: {"version": None, "build": None} for n in diagnostics_torch.TORCH_FAMILY})
    monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: None)
    monkeypatch.setattr(svc, "_ollama_state", lambda: (False, False))
    monkeypatch.setattr(svc, "_tesseract_program_found", lambda: False)
    monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda size: False)
    monkeypatch.setattr(qwen3_native, "transformers_problem", lambda feature="x": "needs transformers")


def test_every_value_is_a_boolean(bare_pc):
    body = svc.get_readiness()
    assert body and all(type(v) is bool for v in body.values()), body


def test_fresh_install_reports_nothing_ready(bare_pc):
    body = svc.get_readiness()
    assert not any(body.values()), [k for k, v in body.items() if v]


def test_qwen3_asr_needs_torch_soundfile_and_new_enough_transformers(monkeypatch, bare_pc):
    present = {"torch", "soundfile"}
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: name in present)
    assert svc.get_readiness()["qwen3_asr"] is False  # transformers too old
    svc.clear_cache()
    monkeypatch.setattr(qwen3_native, "transformers_problem", lambda feature="x": None)
    assert svc.get_readiness()["qwen3_asr"] is True
    svc.clear_cache()
    present.discard("torch")
    assert svc.get_readiness()["qwen3_asr"] is False


def test_package_flags_follow_the_import_names(monkeypatch, bare_pc):
    present = {"faster_whisper", "audio_separator", "demucs", "pyannote.audio", "onnxruntime", "openai",
               "yt_dlp", "pypdf", "genanki", "jieba", "ebooklib", "cv2"}
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: name in present)
    body = svc.get_readiness()
    for flag in ("faster_whisper", "audio_separator", "demucs", "pyannote", "onnxruntime", "openai",
                 "yt_dlp", "pypdf", "genanki", "jieba", "ebooklib", "cv2"):
        assert body[flag] is True, flag
    assert body["sudachi"] is False and body["kiwi"] is False and body["anthropic"] is False


def test_whisper_cache_flags_cover_each_live_size(monkeypatch, bare_pc):
    monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda size: size in ("small", "large-v3-turbo"))
    body = svc.get_readiness()
    assert {k: v for k, v in body.items() if k.startswith("whisper_cached_")} == {
        "whisper_cached_tiny": False, "whisper_cached_base": False, "whisper_cached_small": True,
        "whisper_cached_medium": False, "whisper_cached_large_v3_turbo": True,
    }
    assert {svc.whisper_cached_flag(s) for s in whisper_models.LIVE_WHISPER_SIZES} <= set(body)


def test_paddle_needs_both_packages(monkeypatch, bare_pc):
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: name == "paddleocr")
    assert svc.get_readiness()["paddle_ocr"] is False
    svc.clear_cache()
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: name in ("paddleocr", "paddle"))
    assert svc.get_readiness()["paddle_ocr"] is True


def test_ffmpeg_libass_is_not_assumed_when_ffmpeg_is_missing(monkeypatch, bare_pc):
    monkeypatch.setattr(diagnostics, "check_ffmpeg", lambda: {"found": True, "libass": None})
    body = svc.get_readiness()
    assert body["ffmpeg"] is True and body["ffmpeg_libass"] is False


def test_gpu_found_is_independent_of_torch(monkeypatch, bare_pc):
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info",
                        lambda: {"gpu_name": "RTX 4090", "driver_version": "560.1"})
    body = svc.get_readiness()
    assert body["gpu_found"] is True and body["torch_cuda"] is False


@pytest.mark.parametrize("build, platform, expected", [
    ("cuda", "linux", True),
    ("cpu", "linux", False),
    # PyPI's Linux wheels carry no build tag and are CUDA builds; its Windows wheels are CPU-only.
    (None, "linux", True),
    (None, "win32", False),
])
def test_torch_cuda_is_read_from_the_installed_build_not_an_import(monkeypatch, bare_pc, build, platform, expected):
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: name == "torch")
    versions = {n: {"version": None, "build": None} for n in diagnostics_torch.TORCH_FAMILY}
    versions["torch"] = {"version": "2.8.0", "build": build}
    monkeypatch.setattr(diagnostics_torch, "torch_family_versions", lambda: versions)
    monkeypatch.setattr(svc.sys, "platform", platform)
    assert svc.get_readiness()["torch_cuda"] is expected


def test_keys_are_presence_only(monkeypatch, bare_pc):
    secret = "hf_SHOULD_NOT_LEAK"
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda key, env_path=None: secret if key in ("hf_token", "groq") else None)
    body = svc.get_readiness()
    assert body["hf_token_configured"] is True and body["key_groq"] is True
    assert body["key_claude"] is False
    assert secret not in repr(body)


def test_ollama_model_is_only_asked_after_the_server_answers(monkeypatch, bare_pc):
    monkeypatch.setattr(svc, "_ollama_state", lambda: (True, False))
    body = svc.get_readiness()
    assert body["ollama_reachable"] is True and body["ollama_model_pulled"] is False


def test_answers_are_cached_for_a_few_seconds(monkeypatch, bare_pc):
    calls = []
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: calls.append(1))
    svc.get_readiness()
    svc.get_readiness()
    assert len(calls) == 1
    svc.clear_cache()
    svc.get_readiness()
    assert len(calls) == 2


def test_cache_expires(monkeypatch, bare_pc):
    calls = []
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: calls.append(1))
    clock = [1000.0]
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock[0])
    svc.get_readiness()
    clock[0] += svc.CACHE_SECONDS + 1
    svc.get_readiness()
    assert len(calls) == 2


def test_a_slow_compute_is_not_stale_the_moment_it_is_stored(monkeypatch, bare_pc):
    """Probes can take many seconds; callers queued behind the first must reuse its answer, not re-run them."""
    clock = [1000.0]
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock[0])
    calls = []

    def slow():
        calls.append(1)
        clock[0] += svc.CACHE_SECONDS + 10  # the probes outlast the cache lifetime
        return None
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", slow)
    svc.get_readiness()
    svc.get_readiness()  # a caller that was waiting on the lock
    assert len(calls) == 1


def test_a_failing_check_reads_as_not_ready_not_as_an_error(monkeypatch, bare_pc):
    def boom(*a, **k):
        raise RuntimeError("C:\\Users\\someone\\secret")
    monkeypatch.setattr(diagnostics, "check_ffmpeg", boom)
    body = svc.get_readiness()
    assert body["ffmpeg"] is False and body["ffmpeg_libass"] is False


# --- the route -------------------------------------------------------------

def _app():
    return create_app(ApiSettings(auth_mode="on"))


def _headers(session):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}"}


def _member(*permissions, without=()):
    """A household member: library.read comes with the default role."""
    user = auth_service.add_user("kid@example.com")
    for p in permissions:
        auth_service.grant_permission(user["id"], p)
    for p in without:
        auth_service.revoke_permission(user["id"], p)
    return auth_service.create_session(user["id"], "pytest", "203.0.113.9")


def test_route_returns_only_booleans_to_a_member(isolated_db, bare_pc):
    client = TestClient(_app(), base_url=REMOTE, raise_server_exceptions=False)
    r = client.get("/api/setup/readiness", headers=_headers(_member()))
    assert r.status_code == 200
    body = r.json()
    assert body and all(type(v) is bool for v in body.values())
    text = r.text.lower()
    assert "\\" not in text and "/home" not in text and "http" not in text


def test_route_needs_library_read_and_nothing_stronger(isolated_db, bare_pc):
    client = TestClient(_app(), base_url=REMOTE, raise_server_exceptions=False)
    assert client.get("/api/setup/readiness").status_code == 401
    assert client.get("/api/setup/readiness", headers=_headers(_member(without=("library.read",)))).status_code == 403


def test_route_fields_match_the_service(isolated_db, bare_pc):
    client = TestClient(_app(), base_url=REMOTE, raise_server_exceptions=False)
    r = client.get("/api/setup/readiness", headers=_headers(_member()))
    assert set(r.json()) == set(svc.get_readiness())
