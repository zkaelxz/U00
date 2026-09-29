"""Tests for services/line_ai_service.py + /api/line-ai (Migration Slice 50).
Fully mocked: fake engine and stubbed LLM helpers, no network."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import line_tools
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import line_ai_service as svc, translate_service
from services.service_errors import (DependencyUnavailableError, NotFoundError,
                                      ServiceError, UnsupportedOperationError)

SECRET = "sk-ant-SECRET1234567890abcdef"


class FakeEngine:
    model = "fake-model"
    supports_reference = True


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    monkeypatch.setattr(translate_service, "_resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())


@pytest.fixture
def client():
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _forbid_writes(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("line AI must not write")
    monkeypatch.setattr(db, "save_lines", boom)
    monkeypatch.setattr(db, "record_edit_sample", boom)


def _seed():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="hello"),
                        Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    return did, [r["id"] for r in db.load_lines(did)]


def test_improve_matches_by_id_and_writes_nothing(client, monkeypatch):
    did, ids = _seed()
    _forbid_writes(monkeypatch)
    seen = {}

    def fake(zh, en, engine, issue="", source_language="zh", style_guidelines=""):
        seen.update(zh=zh, en=en, issue=issue, style=style_guidelines)
        return "farewell"
    monkeypatch.setattr(line_tools, "improve_line", fake)
    r = client.post(f"/api/line-ai/dramas/{did}/lines/{ids[1]}/improve", json={"issue": "stiff"})
    assert r.status_code == 200
    assert r.json() == {"line_id": ids[1], "current_en": "bye", "suggestion": "farewell",
                        "changed": True, "engine": "claude", "model": "fake-model"}
    assert (seen["zh"], seen["en"], seen["issue"]) == ("再见", "bye", "stiff")
    assert seen["style"]
    assert [r["en"] for r in db.load_lines(did)] == ["hello", "bye"]


def test_explain(client, monkeypatch):
    did, ids = _seed()
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(line_tools, "explain_translation", lambda zh, en, eng, **k: f"why {zh}")
    r = client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/explain", json={})
    assert r.status_code == 200 and r.json()["explanation"] == "why 你好"


def test_missing_key_is_503(client, monkeypatch):
    did, ids = _seed()
    monkeypatch.setattr(translate_service, "_resolve_api_key", lambda name, env_path=None: None)
    for verb in ("improve", "explain"):
        r = client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/{verb}", json={})
        assert r.status_code == 503


def test_unknown_drama_line_and_bad_input(client):
    did, ids = _seed()
    assert client.post(f"/api/line-ai/dramas/999/lines/{ids[0]}/improve", json={}).status_code == 404
    assert client.post(f"/api/line-ai/dramas/{did}/lines/99999/explain", json={}).status_code == 404
    assert client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/improve",
                       json={"issue": "x" * 501}).status_code == 422
    assert client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/improve",
                       json={"api_key": "x"}).status_code == 422
    assert client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/improve",
                       json={"engine": "nope"}).status_code == 422


def test_engine_failure_is_redacted(monkeypatch):
    did, ids = _seed()

    def boom(*a, **k):
        raise RuntimeError(f"401 bad key {SECRET}")
    monkeypatch.setattr(line_tools, "explain_translation", boom)
    with pytest.raises(ServiceError) as ei:
        svc.explain_line(did, ids[0])
    assert SECRET not in ei.value.message


def test_empty_explanation_and_non_llm_engine(monkeypatch):
    did, ids = _seed()
    monkeypatch.setattr(line_tools, "explain_translation", lambda *a, **k: "")
    with pytest.raises(ServiceError):
        svc.explain_line(did, ids[0])
    class Plain:
        supports_reference = False
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: Plain())
    with pytest.raises(UnsupportedOperationError):
        svc.improve_line(did, ids[0])


def test_untranslated_line_refused():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    lid = db.load_lines(did)[0]["id"]
    with pytest.raises(UnsupportedOperationError):
        svc.explain_line(did, lid)
