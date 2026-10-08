"""Ollama hosted ("cloud") models reached through the local Ollama app.
Mocked HTTP only: no network, no real sign-in."""
import json

import pytest
import requests

import translate_engines as te
from services import model_registry_service, translate_service
from services.service_errors import InvalidInputError  # noqa: F401

CLOUD = "gemma4:31b-cloud"


class Resp:
    def __init__(self, body=None, status=200, text=""):
        self._body, self.status_code, self.text = body, status, text
        self.headers = {}
        self.ok = status < 400

    def iter_content(self, size):
        yield json.dumps(self._body).encode()

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} for http://localhost:11434/api/chat")
            err.response = self
            raise err


def _reply(content):
    return Resp({"message": {"content": content}})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("engine_backends.shared.time.sleep", lambda s: None)


def test_cloud_detection():
    assert te.is_ollama_cloud_model("gemma4:31b-cloud")
    assert te.is_ollama_cloud_model("gemma4:cloud")
    assert not te.is_ollama_cloud_model("gemma4:12b")
    assert not te.is_ollama_cloud_model(None)


def test_local_default_is_unchanged_and_not_cloud():
    assert te.OLLAMA_DEFAULT_MODEL == "gemma4:12b"
    assert not te.is_ollama_cloud_model(te.OllamaEngine().model)
    assert not set(te.OLLAMA_CLOUD_MODELS) & set(te.OLLAMA_MODELS)


def test_cloud_results_matched_by_id_not_position(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: _reply('{"2": "Second.", "1": "First."}'))
    out = te.OllamaEngine(model=CLOUD).translate_batch(
        ["一", "二"], {"line_ids": [1, 2]})
    assert out == ["First.", "Second."]


def test_cloud_call_has_timeout_and_no_key_material(monkeypatch):
    seen = {}

    def post(url, json=None, timeout=None, stream=None, headers=None):
        seen.update(url=url, timeout=timeout, headers=headers)
        return _reply('{"1": "Hi."}')
    monkeypatch.setattr("requests.post", post)
    te.OllamaEngine(model=CLOUD).translate_batch(["你好"], {})
    assert seen["timeout"] and seen["url"].startswith("http://localhost:11434")
    assert seen["headers"] is None


def test_quota_error_retries_then_gives_plain_message_with_redaction(monkeypatch):
    calls = []

    def post(*a, **k):
        calls.append(1)
        return Resp(status=429, text="weekly limit reached. Authorization: Bearer sk-secret1234567890abcd")
    monkeypatch.setattr("requests.post", post)
    engine = te.OllamaEngine(model=CLOUD)
    # The translate loop wraps each batch in call_with_backoff; 429 must read as a rate limit.
    with pytest.raises(te.OllamaCloudLimitError) as info:
        te.call_with_backoff(lambda: engine.translate_batch(["你好"], {}), max_retries=3)
    assert len(calls) == 3
    msg = str(info.value)
    assert "rate-limited or over the free usage cap" in msg
    assert "sk-secret1234567890abcd" not in msg
    assert "weekly limit reached" in msg


def test_local_429_is_not_rewritten(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: Resp(status=429, text="x"))
    with pytest.raises(requests.HTTPError):
        te._ollama_chat("http://localhost:11434", {"model": "gemma4:12b"})


def test_signed_out_cloud_gives_signin_message(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: Resp(status=401))
    with pytest.raises(te.OllamaUnavailableError) as info:
        te._ollama_chat("http://localhost:11434", {"model": CLOUD})
    assert info.value.reason == "ollama_cloud_signin"
    assert "ollama signin" in str(info.value)


def test_missing_local_model_message_unchanged(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: Resp(status=404))
    with pytest.raises(te.OllamaUnavailableError) as info:
        te._ollama_chat("http://localhost:11434", {"model": "gemma4:12b"})
    assert info.value.reason == "ollama_model_missing"


def test_installed_check_does_not_require_cloud_tag_in_local_list(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: Resp({"models": []}))
    te.check_ollama_model_installed("http://localhost:11434", CLOUD)
    with pytest.raises(te.OllamaUnavailableError):
        te.check_ollama_model_installed("http://localhost:11434", "gemma4:12b")


def test_cloud_models_are_labelled_in_engine_list(isolated_db):
    ollama = next(e for e in translate_service.list_engines() if e["name"] == "ollama")
    assert CLOUD in ollama["models"] and CLOUD in ollama["cloud_models"]
    assert "off this PC" in ollama["model_labels"][CLOUD]
    assert "gemma4:12b" not in ollama["cloud_models"]
    assert "gemma4:12b" not in ollama["model_labels"]


def test_cloud_models_never_become_default_or_fallback_choice(isolated_db):
    assert te.effective_default_model("ollama") == "gemma4:12b"
    assert not any(te.is_ollama_cloud_model(t.get("engine_model"))
                   for t in te.WORKFLOW_TIERS.values())


def test_gpu_seam_skips_cloud_only():
    assert te.ollama_touches_local_gpu("ollama", "gemma4:12b")
    assert te.ollama_touches_local_gpu("ollama", None)
    assert not te.ollama_touches_local_gpu("ollama", CLOUD)
    assert not te.ollama_touches_local_gpu("claude", "claude-sonnet-5-5")


def test_cost_is_zero_not_a_made_up_price():
    engine = te.OllamaEngine(model=CLOUD)
    assert te.estimate_cost_for_engine(engine, 100_000, 50_000) == 0.0


def test_model_health_does_not_call_cloud_tag_retired_or_unlisted(isolated_db):
    a = model_registry_service._assess("ollama", CLOUD, model_registry_service.load_registry(), {})
    assert a["status"] == "current"
    other = model_registry_service._assess("ollama", "gpt-oss:120b-cloud", {}, {})
    assert other["status"] not in ("retired", "not_listed")
