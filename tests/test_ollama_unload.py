"""ollama_unload: free a local Ollama's GPU memory before a GPU transcription
loads, and warn when a model is still loaded. No real Ollama, network or GPU."""
import io
import json
import sys
import time
import types

import pytest
import requests

import ollama_unload
from services import jobs_service, settings_service
from services.service_errors import InvalidInputError

LOCAL = "http://127.0.0.1:11434"


class FakeOllama:
    """Stands in for requests.get/post against Ollama's /api/ps and
    /api/generate. `sticky` models ignore the unload request."""

    def __init__(self, loaded=(), sticky=(), ps_error=None, ps_body=None):
        self.loaded = list(loaded)
        self.sticky = set(sticky)
        self.ps_error = ps_error
        self.ps_body = ps_body
        self.gets, self.posts, self.timeouts = [], [], []

    def get(self, url, **kw):
        self.gets.append(url)
        self.timeouts.append(kw.get("timeout"))
        if self.ps_error:
            raise self.ps_error
        body = self.ps_body if self.ps_body is not None else json.dumps(
            {"models": [{"name": n, "model": n} for n in self.loaded]}).encode()
        return types.SimpleNamespace(ok=True, headers={}, close=lambda: None,
                                     iter_content=lambda size: iter([body]))

    def post(self, url, **kw):
        self.posts.append((url, kw.get("json")))
        self.timeouts.append(kw.get("timeout"))
        name = kw["json"]["model"]
        if name not in self.sticky and name in self.loaded:
            self.loaded.remove(name)
        return types.SimpleNamespace(close=lambda: None)


@pytest.fixture(autouse=True)
def fresh_thread_state(monkeypatch):
    # Each test starts as a new job thread would: no earlier check or notice.
    ollama_unload._state.__dict__.clear()
    monkeypatch.setattr(ollama_unload, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(ollama_unload, "UNLOAD_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda name, *a, **k: LOCAL if name == "ollama_url" else None)
    yield
    ollama_unload._state.__dict__.clear()


def _install(monkeypatch, fake):
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(requests, "post", fake.post)
    return fake


def test_loaded_models_are_unloaded_then_polled_until_empty(isolated_db, monkeypatch):
    fake = _install(monkeypatch, FakeOllama(loaded=["gemma4:26b", "qwen3:8b"]))
    ollama_unload.prepare_gpu_for_transcription(True)
    assert fake.posts == [(f"{LOCAL}/api/generate", {"model": "gemma4:26b", "keep_alive": 0}),
                          (f"{LOCAL}/api/generate", {"model": "qwen3:8b", "keep_alive": 0})]
    assert len(fake.gets) >= 2  # the first listing, then at least one poll
    assert fake.loaded == []
    assert ollama_unload.take_notice_result() == {}


def test_nothing_loaded_sends_no_unload_and_no_notice(isolated_db, monkeypatch):
    fake = _install(monkeypatch, FakeOllama())
    ollama_unload.prepare_gpu_for_transcription(True)
    assert fake.posts == [] and len(fake.gets) == 1
    assert ollama_unload.take_notice_result() == {}


@pytest.mark.parametrize("fake", [
    FakeOllama(ps_error=requests.ConnectionError("refused at http://127.0.0.1:11434")),
    FakeOllama(ps_error=requests.ReadTimeout("slow")),
    FakeOllama(ps_body=b"not json at all"),
    FakeOllama(ps_body=b'{"models": "odd"}'),
    FakeOllama(ps_body=b'["a"]'),
])
def test_ollama_down_slow_or_odd_never_fails_the_job(isolated_db, monkeypatch, fake):
    _install(monkeypatch, fake)
    started = time.monotonic()
    ollama_unload.prepare_gpu_for_transcription(True)
    assert time.monotonic() - started < 2
    assert fake.posts == []
    assert ollama_unload.take_notice_result() == {}


@pytest.mark.parametrize("url", ["http://192.168.1.5:11434", "http://example.com:11434",
                                 "http://localhost.evil.com:11434", "ftp://localhost:11434", "garbage"])
def test_a_non_loopback_ollama_gets_no_request_and_no_notice(isolated_db, monkeypatch, url):
    fake = _install(monkeypatch, FakeOllama(loaded=["gemma4:26b"]))
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: url)
    ollama_unload.prepare_gpu_for_transcription(True)
    assert fake.gets == [] and fake.posts == []
    assert ollama_unload.take_notice_result() == {}


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://[::1]:11434", "http://127.0.0.1:9999/"])
def test_loopback_forms_are_accepted(isolated_db, monkeypatch, url):
    fake = _install(monkeypatch, FakeOllama())
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: url)
    ollama_unload.prepare_gpu_for_transcription(True)
    assert len(fake.gets) == 1


def test_a_cpu_run_makes_no_request(isolated_db, monkeypatch):
    fake = _install(monkeypatch, FakeOllama(loaded=["gemma4:26b"]))
    ollama_unload.prepare_gpu_for_transcription(False)
    assert fake.gets == [] and fake.posts == []


def test_setting_off_with_a_model_loaded_warns_and_does_not_unload(isolated_db, monkeypatch):
    fake = _install(monkeypatch, FakeOllama(loaded=["gemma4:26b"]))
    settings_service.set_settings({"unload_ollama_before_transcribe": False})
    ollama_unload.prepare_gpu_for_transcription(True)
    assert fake.posts == []
    notice = ollama_unload.take_notice_result()["ollama_notice"]
    assert "gemma4:26b" in notice and "ollama stop gemma4:26b" in notice
    assert "Settings > Free Ollama's GPU memory before transcribing" in notice
    assert "127.0.0.1" not in notice and "http" not in notice


def test_a_model_that_stays_loaded_after_the_bounded_wait_warns(isolated_db, monkeypatch):
    fake = _install(monkeypatch, FakeOllama(loaded=["gemma4:26b"], sticky=["gemma4:26b"]))
    started = time.monotonic()
    ollama_unload.prepare_gpu_for_transcription(True)
    assert time.monotonic() - started < 2  # the wait is bounded
    assert len(fake.posts) == 1
    notice = ollama_unload.take_notice_result()["ollama_notice"]
    assert "ollama stop gemma4:26b" in notice
    assert "turn on Settings" not in notice  # it is already on


def test_the_notice_is_reported_once(isolated_db, monkeypatch):
    _install(monkeypatch, FakeOllama(loaded=["a"], sticky=["a"]))
    ollama_unload.prepare_gpu_for_transcription(True)
    assert ollama_unload.take_notice_result()
    assert ollama_unload.take_notice_result() == {}


def test_a_tag_that_is_not_a_plain_tag_never_reaches_the_notice(isolated_db, monkeypatch):
    _install(monkeypatch, FakeOllama(loaded=["http://evil.example/x?key=sk-abcdefghijklmnop", "ok:1b"],
                                     sticky=["http://evil.example/x?key=sk-abcdefghijklmnop", "ok:1b"]))
    ollama_unload.prepare_gpu_for_transcription(True)
    notice = ollama_unload.take_notice_result()["ollama_notice"]
    assert "ok:1b" in notice and "evil" not in notice and "sk-" not in notice


def test_every_request_has_a_timeout_inside_the_bound(isolated_db, monkeypatch):
    fake = _install(monkeypatch, FakeOllama(loaded=["a"]))
    ollama_unload.prepare_gpu_for_transcription(True)
    assert fake.timeouts and all(t is not None and 0 < t <= ollama_unload.REQUEST_TIMEOUT_SECONDS
                                 for t in fake.timeouts)


def test_one_check_per_thread_window_however_many_models_load(isolated_db, monkeypatch):
    fake = _install(monkeypatch, FakeOllama())
    for _ in range(3):  # Whisper, Qwen3-ASR and the aligner each call the hook
        ollama_unload.prepare_gpu_for_transcription(True)
    assert len(fake.gets) == 1


def test_a_failure_inside_the_hook_is_swallowed(isolated_db, monkeypatch):
    monkeypatch.setattr(ollama_unload, "_free_ollama_gpu_memory",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom http://x/?key=sk-abcdefghijkl")))
    ollama_unload.prepare_gpu_for_transcription(True)
    assert ollama_unload.take_notice_result() == {}


# --- every GPU loader calls the hook -------------------------------------

@pytest.fixture
def hook_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(ollama_unload, "prepare_gpu_for_transcription", lambda use_gpu: calls.append(use_gpu))
    return calls


def test_the_whisper_loader_calls_the_hook_once_per_load(monkeypatch, hook_calls):
    import core
    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
    monkeypatch.setattr(core, "_whisper_model_cache", {})
    core.load_whisper_model("tiny", use_gpu=True)
    assert hook_calls == [True]


def test_transcribe_for_timing_asks_once_for_a_job(monkeypatch, hook_calls):
    import core
    fake_fw = types.ModuleType("faster_whisper")

    class Model:
        def transcribe(self, *a, **k):
            return iter([]), types.SimpleNamespace(duration=1)
    fake_fw.WhisperModel = lambda *a, **k: Model()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
    monkeypatch.setattr(core, "_whisper_model_cache", {})
    core.transcribe_for_timing("a.wav", "tiny", use_gpu=True)
    assert hook_calls == [True]


def test_the_qwen3_asr_and_aligner_loaders_call_the_hook(monkeypatch, hook_calls):
    import asr_backend
    import forced_align
    import qwen3_native
    fake_torch = types.SimpleNamespace(
        bfloat16=object(), float16=object(),
        cuda=types.SimpleNamespace(is_bf16_supported=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(qwen3_native, "require_transformers", lambda feature="x": None)
    monkeypatch.setattr(qwen3_native.NativeQwen3ASR, "from_pretrained",
                        classmethod(lambda cls, *a, **k: object()))
    monkeypatch.setattr(qwen3_native.NativeQwen3Aligner, "from_pretrained",
                        classmethod(lambda cls, *a, **k: object()))
    monkeypatch.setattr(asr_backend, "_asr_model_cache", {})
    monkeypatch.setattr(forced_align, "_aligner_model_cache", {})
    asr_backend.load_qwen3_asr(use_gpu=True)
    forced_align.load_qwen3_aligner(use_gpu=True)
    assert hook_calls == [True, True]


# --- the setting ----------------------------------------------------------

def test_the_setting_defaults_on_and_round_trips(isolated_db):
    assert settings_service.get_settings_overview()["unload_ollama_before_transcribe"] is True
    out = settings_service.set_settings({"unload_ollama_before_transcribe": False})
    assert out["unload_ollama_before_transcribe"] is False and ollama_unload.is_enabled() is False
    out = settings_service.set_settings({"unload_ollama_before_transcribe": True})
    assert out["unload_ollama_before_transcribe"] is True


def test_the_setting_rejects_a_non_boolean(isolated_db):
    with pytest.raises(InvalidInputError):
        settings_service.set_settings({"unload_ollama_before_transcribe": "yes"})


def test_the_api_schema_carries_the_setting():
    from api.schemas import SettingsUpdateRequest
    assert SettingsUpdateRequest(unload_ollama_before_transcribe=False).unload_ollama_before_transcribe is False
