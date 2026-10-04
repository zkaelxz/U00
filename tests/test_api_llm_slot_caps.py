"""Security LOW L-b: the synchronous LLM routes outside the Reader take a slot
from the shared cap in api/llm_slots.py, so a busy cap answers 429 without
calling the engine."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api import llm_slots
from api.api_config import ApiSettings
from api.server import create_app
from services import discover_lookup_service, line_ai_service, translate_service

ROUTES = [
    ("/api/translate", {"text": "你好", "engine": "ollama", "source_language": "zh",
                        "target_language": "en"}),
    ("/api/line-ai/dramas/1/lines/1/improve", {"engine": "ollama"}),
    ("/api/line-ai/dramas/1/lines/1/explain", {"engine": "ollama"}),
    ("/api/discover/translate-query", {"q": "hello", "engine": "ollama"}),
]


@pytest.fixture
def client(isolated_db, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("engine must not be called while the cap is full")

    monkeypatch.setattr(translate_service, "translate", boom)
    monkeypatch.setattr(line_ai_service, "improve_line", boom)
    monkeypatch.setattr(line_ai_service, "explain_line", boom)
    monkeypatch.setattr(discover_lookup_service, "translate_query", boom)
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    c.calls = calls
    return c


@pytest.mark.parametrize("path,body", ROUTES)
def test_global_cap_gives_429(client, path, body):
    for _ in range(llm_slots.LLM_MAX_IN_FLIGHT):
        assert llm_slots.SLOTS.acquire(blocking=False)
    try:
        r = client.post(path, json=body)
    finally:
        for _ in range(llm_slots.LLM_MAX_IN_FLIGHT):
            llm_slots.SLOTS.release()
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert client.calls == []


@pytest.mark.parametrize("path,body", ROUTES)
def test_per_caller_cap_gives_429(client, path, body):
    with llm_slots.ACTIVE_LOCK:
        llm_slots.ACTIVE_CALLERS.add("local")
    try:
        r = client.post(path, json=body)
    finally:
        with llm_slots.ACTIVE_LOCK:
            llm_slots.ACTIVE_CALLERS.discard("local")
    assert r.status_code == 429
    assert client.calls == []


@pytest.mark.parametrize("path,body", ROUTES)
def test_slot_is_released_after_the_call(client, path, body):
    client.post(path, json=body)  # the stub raises -> 500, slot must still be freed
    assert len(client.calls) == 1
    assert not llm_slots.ACTIVE_CALLERS
