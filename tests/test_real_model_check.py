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


def test_bundled_samples_are_a_real_png_and_wav():
    with open(svc._IMAGE, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
    with open(svc._CLIP, "rb") as f:
        head = f.read(12)
    assert head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    for path in (svc._CLIP, svc._IMAGE):
        assert os.path.getsize(path) < 300 * 1024


# --- ASR -------------------------------------------------------------------

class _Backend:
    def __init__(self, segments=None, fallback=False, error=None, clip_text=None):
        self.segments, self.fallback, self.error = segments or [{"text": "x"}], fallback, error
        self.clip_text, self.seen = clip_text, []

    def transcribe(self, audio, language, on_gpu_fallback=None, **kw):
        assert language == "zh"
        self.seen.append(audio)
        if self.error:
            raise self.error
        if self.fallback:
            on_gpu_fallback(RuntimeError("cuda"))
        if self.clip_text is not None and not os.path.samefile(audio, svc._CLIP):
            return [{"text": self.clip_text}]
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


def test_asr_pass_says_the_model_ran_not_that_words_were_recognised(asr):
    assert "not that words were recognised" in _run("asr")["reason"]


def test_asr_speech_clip_is_compared_with_the_expected_text(asr, tmp_path):
    clip = tmp_path / "speech.wav"
    clip.write_bytes(b"x")
    asr["backend"] = _Backend(clip_text="你好, 世界!")
    r = svc._run_check("asr", "Transcription", svc._check_asr,
                       speech_clip=str(clip), expected_text="你好世界")
    assert r["status"] == svc.PASS and "matched the expected text" in r["reason"]
    assert [os.path.basename(p) for p in asr["backend"].seen] == ["clip.wav", "speech.wav"]


def test_asr_speech_clip_that_does_not_match_fails_without_echoing_the_transcript(asr, tmp_path):
    clip = tmp_path / "speech.wav"
    clip.write_bytes(b"x")
    asr["backend"] = _Backend(clip_text="completely different")
    r = svc._run_check("asr", "Transcription", svc._check_asr,
                       speech_clip=str(clip), expected_text="你好")
    assert r["status"] == svc.FAIL and "completely" not in r["reason"]


def test_asr_speech_clip_without_expected_text_is_run_but_not_compared(asr, tmp_path):
    clip = tmp_path / "speech.wav"
    clip.write_bytes(b"x")
    asr["backend"] = _Backend(clip_text="hello")
    r = svc._run_check("asr", "Transcription", svc._check_asr, speech_clip=str(clip))
    assert r["status"] == svc.PASS and "no expected text" in r["reason"]


def test_asr_missing_speech_clip_fails_without_naming_its_path(asr, tmp_path):
    r = svc._run_check("asr", "Transcription", svc._check_asr,
                       speech_clip=str(tmp_path / "private-name.wav"))
    assert r["status"] == svc.FAIL and "private-name" not in r["reason"]


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


def _paddle_home(monkeypatch, tmp_path, folders):
    monkeypatch.delenv("PADDLE_PDX_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    models = tmp_path / ".paddlex" / "official_models"
    for name in folders:
        (models / name).mkdir(parents=True)


def test_paddle_owners_real_folders_are_present(monkeypatch, tmp_path):
    _paddle_home(monkeypatch, tmp_path, [
        "PP-LCNet_x1_0_textline_ori", "PP-OCRv5_server_det", "PP-OCRv5_server_rec"])
    assert svc._paddle_models_state() == "present"


def test_paddle_other_models_only_is_missing(monkeypatch, tmp_path):
    _paddle_home(monkeypatch, tmp_path, ["PP-LCNet_x1_0_textline_ori", "PP-OCRv6_medium_det",
                                         "PP-DocLayout-L"])
    assert svc._paddle_models_state() == "missing"


@pytest.mark.parametrize("folders", [
    # A Korean-only user: the call loads the Chinese rec model, so this
    # would be downloaded silently.
    ["PP-LCNet_x1_0_textline_ori", "PP-OCRv5_server_det", "korean_PP-OCRv5_mobile_rec"],
    # Leftovers from an older PP-OCR version.
    ["PP-LCNet_x1_0_textline_ori", "PP-OCRv4_server_det", "PP-OCRv4_server_rec"],
    ["PP-LCNet_x1_0_textline_ori", "PP-OCRv5_server_det", "latin_PP-OCRv5_mobile_rec",
     "japan_PP-OCRv5_mobile_rec"]])
def test_paddle_language_prefixed_or_older_models_are_missing(monkeypatch, tmp_path, folders):
    _paddle_home(monkeypatch, tmp_path, folders)
    assert svc._paddle_models_state() == "missing"


def test_paddle_no_folder_or_empty_folder_is_unknown(monkeypatch, tmp_path):
    _paddle_home(monkeypatch, tmp_path, [])
    assert svc._paddle_models_state() == "unknown"
    (tmp_path / ".paddlex" / "official_models").mkdir(parents=True)
    assert svc._paddle_models_state() == "unknown"


def test_paddle_cache_override_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty-home"))
    import ocr
    for n in ocr._PADDLE_CH_MODELS.values():
        (tmp_path / "elsewhere" / "official_models" / n).mkdir(parents=True)
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path / "elsewhere"))
    assert svc._paddle_models_state() == "present"


def test_paddle_missing_models_are_skipped_not_claimed_for_unknown(monkeypatch):
    monkeypatch.setattr(svc, "_installed", lambda m: True)
    monkeypatch.setattr(svc, "_paddle_models_state", lambda: "missing")
    with pytest.raises(svc._Skip):
        svc._ocr_requirement("paddle")


def test_paddle_models_not_found_is_could_not_check_never_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "_installed", lambda m: True)
    monkeypatch.setattr(svc, "_paddle_models_state", lambda: "unknown")
    monkeypatch.setattr(settings_service, "resolve_ocr_backend", lambda lang, **kw: "paddle")
    r = _run("ocr")
    assert r["status"] == svc.COULD_NOT_CHECK
    assert "not downloaded" not in r["reason"] and "missing" not in r["reason"].lower()
    assert str(tmp_path) not in r["reason"]


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
        ("asr", "Transcription", lambda **_: "ok"),
        ("ocr", "OCR", boom),
        ("translate", "Translation (Ollama)", lambda: (_ for _ in ()).throw(svc._Skip("no ollama")))))
    assert svc.start(confirm=True) == {"job_id": svc.JOB_ID, "started": True}
    _wait()
    state = svc.get_state()
    assert [(c["id"], c["status"]) for c in state["checks"]] == [
        ("asr", "pass"), ("ocr", "fail"), ("translate", "skipped")]
    assert state["finished"] and SECRET not in str(state)


def test_could_not_check_does_not_fail_the_job(env, monkeypatch):
    def unsure(**_):
        raise svc._CouldNotCheck("cannot tell")
    monkeypatch.setattr(svc, "_CHECKS", (("ocr", "OCR", unsure),))
    svc.start(confirm=True)
    assert _wait()["result"] == {"status": svc.PASS}
    assert svc.get_state()["checks"][0]["status"] == "could_not_check"


def test_run_checks_releases_gpu_models_even_when_stopped(monkeypatch):
    released = []
    monkeypatch.setattr("core.release_gpu_models", lambda: released.append(1))

    def stop(i, label):
        raise background_jobs.JobCancelled()
    with pytest.raises(background_jobs.JobCancelled):
        svc.run_checks(before_check=stop)
    assert released == [1]


def test_a_second_start_while_running_is_refused(env, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(svc, "_CHECKS", (("asr", "Transcription", lambda **_: gate.wait(5) and "ok"),))
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
    monkeypatch.setattr(svc, "_CHECKS", (("asr", "Transcription", lambda **_: "ok"),))
    client = TestClient(create_app())
    assert client.post("/api/diagnostics/real-model-check", json={}).status_code == 422
    r = client.post("/api/diagnostics/real-model-check", json={"confirm": True})
    assert r.status_code == 200, r.text
    _wait()
    body = client.get("/api/diagnostics/real-model-check").json()
    assert body["checks"][0]["status"] == "pass" and body["finished"] is True


# --- Qwen: exact repos, offline, tone-only -------------------------------------

class _QwenBackend:
    model_size = "1.7B"

    def __init__(self, long_windows=True, segments=None, load=False):
        self.long_windows, self.segments, self.load = long_windows, segments or [], load
        self.calls, self.offline = 0, []

    def transcribe(self, audio, language, on_device=None, on_gpu_fallback=None, **kw):
        import huggingface_hub.constants as hc
        self.calls += 1
        self.offline.append((os.environ.get("HF_HUB_OFFLINE"), hc.HF_HUB_OFFLINE))
        if self.load:
            on_device("Qwen3-ASR", "GPU")
        return self.segments


@pytest.fixture
def qwen(monkeypatch):
    import sys
    import types
    import asr_backend
    # A stand-in so the offline switch is exercised without the real package.
    constants = types.ModuleType("huggingface_hub.constants")
    constants.HF_HUB_OFFLINE = False
    hub = types.ModuleType("huggingface_hub")
    hub.constants = constants
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", constants)
    state = {"backend": _QwenBackend(), "repos": ["Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B"]}
    monkeypatch.setattr(asr_options_service, "stored_asr_backend", lambda d: "qwen3_asr_long")
    monkeypatch.setattr(settings_service, "get_use_gpu", lambda: True)
    monkeypatch.setattr(svc, "_installed", lambda m: True)
    monkeypatch.setattr(asr_backend, "get_backend", lambda name: state["backend"])
    monkeypatch.setattr(svc.diagnostics, "scan_hf_cache",
                        lambda: [{"repo_id": r} for r in state["repos"]])
    return state


def test_qwen_with_only_another_qwen_model_cached_is_skipped_and_never_loads(qwen):
    qwen["repos"] = ["Qwen/Qwen3-ASR-0.6B", "Qwen/Qwen3-ASR-1.7B-Extra"]
    r = _run("asr")
    assert r["status"] == svc.SKIPPED and "not downloaded" in r["reason"]
    assert qwen["backend"].calls == 0


def test_qwen_long_needs_the_aligner_too(qwen):
    qwen["repos"] = ["Qwen/Qwen3-ASR-1.7B"]
    assert _run("asr")["status"] == svc.SKIPPED
    assert qwen["backend"].calls == 0
    qwen["backend"] = _QwenBackend(long_windows=False, load=True)
    assert _run("asr")["status"] == svc.PASS


def test_qwen_repo_ids_come_from_the_backend_constants(qwen, monkeypatch):
    import forced_align
    monkeypatch.setattr(forced_align, "ALIGNER_REPO_ID", "Org/Other-Aligner")
    assert _run("asr")["status"] == svc.SKIPPED


def test_the_check_runs_models_offline_and_restores_the_switch(qwen, monkeypatch):
    import huggingface_hub.constants as hc
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    before = hc.HF_HUB_OFFLINE
    qwen["backend"] = _QwenBackend(load=True)
    assert _run("asr")["status"] == svc.PASS
    assert qwen["backend"].offline == [("1", True)]
    assert "HF_HUB_OFFLINE" not in os.environ and hc.HF_HUB_OFFLINE == before


def test_tone_only_is_skipped_not_passed_when_no_model_loaded(qwen):
    r = _run("asr")
    assert r["status"] == svc.SKIPPED
    assert r["reason"] == ("Tone only: speech detection found no speech, "
                           "so the recognition model was not loaded.")


def test_a_loaded_model_passes_even_with_no_segments(qwen):
    qwen["backend"] = _QwenBackend(load=True)
    r = _run("asr")
    assert r["status"] == svc.PASS and "0 segment(s)" in r["reason"]


# --- No raw exception text in results ----------------------------------------------

def _no_url(text):
    for bad in ("http", "192.168", "11434", "/api/chat", "localhost", "private-host"):
        assert bad not in text


def test_ollama_http_error_never_leaks_the_url(ollama):
    import requests
    ollama["chat_error"] = requests.HTTPError(
        "500 Server Error: Internal Server Error for url: http://private-host:11434/api/chat")
    r = _run("translate")
    assert r["status"] == svc.FAIL and "ran out of memory" in r["reason"]
    _no_url(r["reason"])


def test_invalid_ollama_url_never_leaks_the_url(ollama):
    import requests
    ollama["installed_error"] = requests.exceptions.InvalidURL(
        "Invalid URL 'http://private-host:11434:99/api/tags': bad port")
    r = _run("translate")
    assert r["status"] == svc.FAIL and "not valid" in r["reason"]
    _no_url(r["reason"])


def test_unknown_exception_reports_only_its_class_name(ollama):
    ollama["chat_error"] = ValueError("failed at http://192.168.1.9:11434/api/chat C:\\Users\\me\\x")
    r = _run("translate")
    assert r["reason"] == "Unexpected error (ValueError)."


def test_api_result_carries_no_url(env, monkeypatch, ollama):
    import requests
    ollama["chat_error"] = requests.HTTPError(
        "500 for url: http://private-host:11434/api/chat")
    monkeypatch.setattr(svc, "_CHECKS", tuple(c for c in svc._CHECKS if c[0] == "translate"))
    svc.start(confirm=True)
    _wait()
    state = svc.get_state()
    assert state["checks"][0]["status"] == svc.FAIL
    _no_url(str(state))


# --- offline OCR, VL repos, refusals, import errors, clip limits -----------

def test_ocr_load_runs_with_hugging_face_offline(ocr_env, monkeypatch):
    import ocr
    seen = {}

    def read(paths, **kw):
        seen.update(hf=os.environ.get("HF_HUB_OFFLINE"), tf=os.environ.get("TRANSFORMERS_OFFLINE"))
        return "你好"
    monkeypatch.setattr(ocr, "extract_text_from_images", read)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert _run("ocr")["status"] == svc.PASS
    assert seen == {"hf": "1", "tf": "1"}
    assert "HF_HUB_OFFLINE" not in os.environ


@pytest.mark.parametrize("cached", [
    ["jzhang533/PaddleOCR-VL-For-Manga"],
    ["PaddlePaddle/PaddleOCR-VL"],
    []])
def test_vl_manga_needs_both_repos_cached(monkeypatch, cached):
    import ocr
    monkeypatch.setattr(svc, "_installed", lambda m: True)
    monkeypatch.setattr(ocr, "paddle_vl_manga_problem", lambda: None)
    monkeypatch.setattr(svc.diagnostics, "scan_hf_cache", lambda: [{"repo_id": r} for r in cached])
    with pytest.raises(svc._Skip):
        svc._ocr_requirement("paddle_vl_manga")


def test_vl_manga_with_both_repos_cached_passes_the_requirement(monkeypatch):
    import ocr
    monkeypatch.setattr(svc, "_installed", lambda m: True)
    monkeypatch.setattr(ocr, "paddle_vl_manga_problem", lambda: None)
    monkeypatch.setattr(svc.diagnostics, "scan_hf_cache", lambda: [
        {"repo_id": ocr._PADDLE_VL_MANGA_REPO}, {"repo_id": ocr._PADDLE_VL_PROCESSOR_REPO}])
    svc._ocr_requirement("paddle_vl_manga")


def _refusal(kind):
    import memory_headroom
    from services import vram_service
    return {"vram": vram_service.InsufficientVramError,
            "headroom": memory_headroom.HeadroomError}[kind](
        "Not enough free memory: C:\\Users\\me\\x would eat into your Keep free setting.")


@pytest.mark.parametrize("kind", ["vram", "headroom"])
def test_memory_refusal_keeps_its_message_and_stops_the_run(monkeypatch, kind):
    ran = []
    monkeypatch.setattr("core.release_gpu_models", lambda: None)

    def refuse(**kw):
        raise _refusal(kind)
    monkeypatch.setattr(svc, "_CHECKS", (
        ("asr", "Transcription", lambda **kw: "ok"),
        ("ocr", "OCR", refuse),
        ("translate", "Translation (Ollama)", lambda **kw: ran.append(1) or "ok")))
    results = svc.run_checks()
    assert [r["id"] for r in results] == ["asr", "ocr"]
    assert results[1]["status"] == svc.FAIL
    assert "Keep free" in results[1]["reason"] or "free memory" in results[1]["reason"]
    assert "Unexpected error" not in results[1]["reason"] and "C:\\Users" not in results[1]["reason"]
    assert not ran


def test_broken_installed_package_is_a_failure_not_a_skip(asr):
    asr["backend"] = _Backend(error=ImportError("DLL load failed: C:\\x\\ctranslate2.dll"))
    r = _run("asr")
    assert r["status"] == svc.FAIL and "ctranslate2" not in r["reason"] and "C:" not in r["reason"]


def test_speech_clip_over_the_size_limit_is_refused(asr, tmp_path, monkeypatch):
    clip = tmp_path / "big-private.wav"
    clip.write_bytes(b"x" * 2048)
    monkeypatch.setattr(svc, "_MAX_CLIP_BYTES", 1024)
    r = svc._run_check("asr", "Transcription", svc._check_asr, speech_clip=str(clip))
    assert r["status"] == svc.FAIL and "larger than" in r["reason"] and "big-private" not in r["reason"]
    assert not asr["backend"].seen


def test_speech_clip_over_the_duration_limit_is_refused(asr, tmp_path):
    import wave
    clip = tmp_path / "long.wav"
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000 * (svc._MAX_CLIP_SECONDS + 1))
    r = svc._run_check("asr", "Transcription", svc._check_asr, speech_clip=str(clip))
    assert r["status"] == svc.FAIL and "longer than" in r["reason"]
    assert not asr["backend"].seen
