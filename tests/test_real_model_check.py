"""The opt-in real-model check (services/real_model_check_service.py). Every
model, backend and Ollama call is faked: no GPU, no models, no network."""
import os
import time

import pytest

import background_jobs
import translate_engines
from services import asr_options_service, settings_service
from services import diagnostics_gaps_service as gaps
from services import real_model_check_service as svc

SECRET = "sk-ant-api03-SECRETSECRETSECRET123456"


def _wait(timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(svc.JOB_ID)
        if st and st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture
def env(isolated_db, monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    monkeypatch.setattr("core.release_gpu_models", lambda: None)
    background_jobs.clear_job(svc.JOB_ID)
    svc._STATE.update(checks=[], finished=False)
    yield
    background_jobs.clear_job(svc.JOB_ID)


def test_bundled_samples_ship_in_a_small_included_directory():
    import installer.build_installer as bi
    for path in (svc._CLIP, svc._IMAGE):
        assert os.path.isfile(path)
        assert os.path.getsize(path) < 300 * 1024
        assert not bi.is_excluded(os.path.relpath(path, gaps.default_project_root()))


# --- ASR -------------------------------------------------------------------

class _Backend:
    def __init__(self, segments=None, fallback=False, error=None):
        self.segments, self.fallback, self.error = segments or [{"text": "x"}], fallback, error

    def transcribe(self, audio, language, on_gpu_fallback=None, **kw):
        assert os.path.samefile(audio, svc._CLIP) and language == "zh"
        if self.error:
            raise self.error
        if self.fallback:
            on_gpu_fallback(RuntimeError("cuda"))
        return self.segments


@pytest.fixture
def asr(monkeypatch):
    import asr_backend
    import core
    state = {"backend": _Backend(), "cached": True}
    monkeypatch.setattr(asr_options_service, "stored_asr_backend", lambda d: "whisper")
    monkeypatch.setattr(settings_service, "get_use_gpu", lambda: True)
    monkeypatch.setattr(svc, "_installed", lambda m: True)
    monkeypatch.setattr(core, "is_whisper_model_cached", lambda size: state["cached"])
    monkeypatch.setattr(asr_backend, "get_backend", lambda name: state["backend"])
    return state


def _run(check_id):
    _id, label, fn = next(c for c in svc._CHECKS if c[0] == check_id)
    return svc._run_check(_id, label, fn)


def test_asr_passes(asr):
    r = _run("asr")
    assert r["status"] == svc.PASS and "GPU" in r["reason"]


def test_asr_skips_when_model_not_downloaded(asr):
    asr["cached"] = False
    assert _run("asr")["status"] == svc.SKIPPED


def test_asr_skips_when_package_missing(asr, monkeypatch):
    monkeypatch.setattr(svc, "_installed", lambda m: False)
    assert _run("asr")["status"] == svc.SKIPPED


def test_asr_skips_on_import_error_at_run_time(asr):
    asr["backend"] = _Backend(error=ModuleNotFoundError("no", name="ctranslate2"))
    r = _run("asr")
    assert r["status"] == svc.SKIPPED and "ctranslate2" in r["reason"]


def test_asr_fails_when_gpu_silently_falls_back(asr):
    asr["backend"] = _Backend(fallback=True)
    assert _run("asr")["status"] == svc.FAIL


def test_asr_failure_reason_is_redacted(asr):
    asr["backend"] = _Backend(error=RuntimeError(f"boom {SECRET} at /home/someone/private/x.py"))
    r = _run("asr")
    assert r["status"] == svc.FAIL
    assert SECRET not in r["reason"] and "/home/someone" not in r["reason"]


# --- OCR -------------------------------------------------------------------

@pytest.fixture
def ocr_env(monkeypatch):
    import ocr
    state = {"text": "你好", "requirement": None}
    monkeypatch.setattr(settings_service, "resolve_ocr_backend", lambda lang, **kw: "paddle")

    def requirement(backend):
        if state["requirement"]:
            raise svc._Skip(state["requirement"])
    monkeypatch.setattr(svc, "_ocr_requirement", requirement)
    monkeypatch.setattr(ocr, "extract_text_from_images",
                        lambda paths, **kw: state["text"])
    return state


def test_ocr_passes(ocr_env):
    r = _run("ocr")
    assert r["status"] == svc.PASS and "paddle" in r["reason"]


def test_ocr_skips_when_backend_unavailable(ocr_env):
    ocr_env["requirement"] = "paddleocr is not installed."
    r = _run("ocr")
    assert r["status"] == svc.SKIPPED and "paddleocr" in r["reason"]


def test_ocr_fails_when_no_text_is_found(ocr_env):
    ocr_env["text"] = "  "
    assert _run("ocr")["status"] == svc.FAIL


@pytest.mark.parametrize("backend,missing", [
    ("tesseract", "pytesseract"), ("manga_ocr", "manga_ocr"), ("paddle", "paddleocr")])
def test_ocr_requirement_skips_a_missing_package(monkeypatch, backend, missing):
    monkeypatch.setattr(svc, "_installed", lambda m: False)
    with pytest.raises(svc._Skip) as exc:
        svc._ocr_requirement(backend)
    assert missing.replace("_", "") in str(exc.value).replace("-", "").replace("_", "")


def test_ocr_requirement_skips_an_undownloaded_model(monkeypatch):
    monkeypatch.setattr(svc, "_installed", lambda m: True)
    monkeypatch.setattr(svc, "_hf_repo_cached", lambda f: False)
    with pytest.raises(svc._Skip):
        svc._ocr_requirement("manga_ocr")


# --- Translate -------------------------------------------------------------

@pytest.fixture
def ollama(monkeypatch):
    state = {"installed_error": None, "reply": {"message": {"content": "Hello"}},
             "chat_error": None, "payload": None}

    def installed(base_url, model):
        if state["installed_error"]:
            raise state["installed_error"]

    def chat(base_url, payload):
        state["payload"] = payload
        if state["chat_error"]:
            raise state["chat_error"]
        return state["reply"]
    monkeypatch.setattr(translate_engines, "check_ollama_model_installed", installed)
    monkeypatch.setattr(translate_engines, "_ollama_chat", chat)
    return state


def test_translate_passes_and_never_pulls(ollama):
    r = _run("translate")
    assert r["status"] == svc.PASS
    assert ollama["payload"]["stream"] is False


def test_translate_skips_when_ollama_unreachable(ollama):
    ollama["installed_error"] = translate_engines.OllamaUnavailableError(
        "ollama_unreachable", "Ollama isn't running. Start it, or pick another translator in Settings.")
    r = _run("translate")
    assert r["status"] == svc.SKIPPED and "isn't running" in r["reason"]
    assert ollama["payload"] is None


def test_translate_skips_when_model_not_pulled(ollama):
    ollama["installed_error"] = translate_engines.OllamaUnavailableError(
        "ollama_model_missing", "Ollama doesn't have the model m.")
    assert _run("translate")["status"] == svc.SKIPPED


def test_translate_fails_on_timeout_and_empty_answer(ollama):
    ollama["chat_error"] = translate_engines.OllamaUnavailableError("ollama_timeout", "too slow")
    assert _run("translate")["status"] == svc.FAIL
    ollama["chat_error"] = None
    ollama["reply"] = {"message": {"content": "<think>hm</think>"}}
    assert _run("translate")["status"] == svc.FAIL


def test_translate_uses_the_configured_ollama_url_without_leaking_it(ollama, monkeypatch):
    seen = []
    monkeypatch.setattr(settings_service, "resolve_key", lambda k: "http://192.168.1.9:11434/")
    monkeypatch.setattr(translate_engines, "_ollama_chat",
                        lambda url, payload: seen.append(url) or {"message": {"content": "Hi"}})
    r = _run("translate")
    assert seen == ["http://192.168.1.9:11434"] and "192.168" not in r["reason"]


# --- The job ---------------------------------------------------------------

def test_start_requires_confirm(env):
    with pytest.raises(gaps.AdminActionUnconfirmed):
        svc.start(confirm=False)


def test_job_runs_all_checks_independently(env, monkeypatch):
    def boom():
        raise RuntimeError(f"bad {SECRET}")
    monkeypatch.setattr(svc, "_CHECKS", (
        ("asr", "Transcription", lambda: "ok"),
        ("ocr", "OCR", boom),
        ("translate", "Translation (Ollama)", lambda: (_ for _ in ()).throw(svc._Skip("no ollama")))))
    assert svc.start(confirm=True) == {"job_id": svc.JOB_ID, "started": True}
    _wait()
    state = svc.get_state()
    assert [(c["id"], c["status"]) for c in state["checks"]] == [
        ("asr", "pass"), ("ocr", "fail"), ("translate", "skipped")]
    assert state["finished"] and SECRET not in str(state)


def test_a_second_start_while_running_is_refused(env, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(svc, "_CHECKS", (("asr", "Transcription", lambda: gate.wait(5) and "ok"),))
    svc.start(confirm=True)
    try:
        with pytest.raises(gaps.AdminActionJobsRunning):
            svc.start(confirm=True)
    finally:
        gate.set()
        _wait()


# --- Route -----------------------------------------------------------------

def test_routes_are_declared_and_start(env, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.server import create_app
    monkeypatch.setattr(svc, "_CHECKS", (("asr", "Transcription", lambda: "ok"),))
    client = TestClient(create_app())
    assert client.post("/api/diagnostics/real-model-check", json={}).status_code == 422
    r = client.post("/api/diagnostics/real-model-check", json={"confirm": True})
    assert r.status_code == 200, r.text
    _wait()
    body = client.get("/api/diagnostics/real-model-check").json()
    assert body["checks"][0]["status"] == "pass" and body["finished"] is True
