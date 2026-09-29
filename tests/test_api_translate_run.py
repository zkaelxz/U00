"""Tests for the Translate-stage config/estimate endpoints (Migration Slice 39)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import settings_service

BASE = "/api/translate-run/dramas"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _seed(**fields):
    did = db.create_drama(title_zh="D", **fields)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en=""),
                        Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    return did


def test_config_audio_and_novel(client):
    a = client.get(f"{BASE}/{_seed()}/config")
    assert a.status_code == 200
    body = a.json()
    assert body["defaults"] == {"context_window": 6, "context_window_ahead": 3, "batch_size": 20}
    assert body["line_count"] == 2 and body["untranslated_count"] == 1
    assert body["engines"] and body["workflow_tiers"] and body["style_presets"]
    assert isinstance(body["cap_applies_by_engine"]["claude"], bool)
    n = client.get(f"{BASE}/{_seed(content_mode='novel_narration')}/config").json()
    assert n["defaults"] == {"context_window": 10, "context_window_ahead": 6, "batch_size": 30}
    assert n["default_style_preset"] == "novel"


def test_unknown_drama_404(client):
    for path in ("config", "estimate"):
        r = client.get(f"{BASE}/9999/{path}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


def test_estimate_free_engine(client):
    r = client.get(f"{BASE}/{_seed()}/estimate", params={"engine": "test_offline"})
    assert r.status_code == 200
    body = r.json()
    assert body["free"] is True and body["engine"] == "test_offline"
    assert body["target_line_count"] == 1


def test_estimate_validation(client):
    did = _seed()
    r = client.get(f"{BASE}/{did}/estimate", params={"engine": "nope"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    r = client.get(f"{BASE}/{did}/estimate", params={"engine": "deepl", "reflect": "true"})
    assert r.status_code == 400
    assert "error" in r.json()
    r = client.get(f"{BASE}/{did}/estimate", params={"job_cost_cap_usd": -1})
    assert r.status_code == 422


def test_no_secrets_in_responses(client, monkeypatch):
    fake = "sk-FAKE-SECRET-1234567890"
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **kw: fake)
    did = _seed()
    for path in ("config", "estimate"):
        assert fake not in client.get(f"{BASE}/{did}/{path}").text


# --- Slice 51: bulk batch list + cancel -------------------------------------

def test_bulk_list_and_cancel(client, monkeypatch):
    import bulk_translate
    did = _seed()
    other = _seed()
    jid = db.create_bulk_job(did, "claude", "m", "submitted", [(1, "k", "h", "")],
                             provider_batch_id="msgbatch_SECRET",
                             translate_args={"prompt": "SECRETPROMPT"})
    done = db.create_bulk_job(did, "gemini", None, "applied", [])
    foreign = db.create_bulk_job(other, "claude", "m", "submitted", [])
    assert client.get("/api/translate-run/dramas/9999/bulk").status_code == 404
    r = client.get(f"{BASE}/{did}/bulk")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert [j["bulk_job_id"] for j in jobs] == [done, jid]
    assert jobs[1]["pending"] and jobs[1]["cancellable"] and jobs[1]["line_count"] == 1
    assert not jobs[0]["pending"] and not jobs[0]["cancellable"]
    assert "SECRET" not in r.text

    calls = []
    monkeypatch.setattr(bulk_translate, "cancel_bulk_job",
                        lambda i, p=None: calls.append((i, p)) or db.update_bulk_job(i, status="cancelled") or "ok")
    assert client.post(f"{BASE}/{did}/bulk/{foreign}/cancel").status_code == 404
    assert client.post(f"{BASE}/{did}/bulk/{done}/cancel").status_code == 409
    c = client.post(f"{BASE}/{did}/bulk/{jid}/cancel")
    assert c.status_code == 200 and c.json()["bulk_job"]["status"] == "cancelled"
    assert calls and calls[0][0] == jid



def _provider_env(monkeypatch, provider):
    import bulk_translate
    import translate_engines
    from services import translate_service
    seen = {}
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: seen.setdefault("key_for", name) and "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
    monkeypatch.setattr(bulk_translate, "make_provider", lambda name, engine: provider)
    return seen


def test_bulk_cancel_asks_provider_when_key_exists(client, monkeypatch):
    did = _seed()
    jid = db.create_bulk_job(did, "claude", "m", "submitted", [(1, "k", "h", "")],
                             provider_batch_id="msgbatch_1")

    class Provider:
        cancelled = []

        def cancel(self, batch_id):
            self.cancelled.append(batch_id)
    p = Provider()
    seen = _provider_env(monkeypatch, p)
    r = client.post(f"{BASE}/{did}/bulk/{jid}/cancel")
    assert r.status_code == 200
    assert seen["key_for"] == "claude" and p.cancelled == ["msgbatch_1"]
    assert r.json()["message"] == "Cancelled at the provider and here."
    assert r.json()["bulk_job"]["status"] == "cancelled"


def test_bulk_cancel_provider_failure_is_redacted_and_local_cancel_stands(client, monkeypatch):
    did = _seed()
    secret = "sk-ant-SECRET1234567890abcdef"
    jid = db.create_bulk_job(did, "claude", "m", "scheduled", [(1, "k", "h", "")],
                             provider_batch_id="msgbatch_2")

    class Boom:
        def cancel(self, batch_id):
            raise RuntimeError(f"401 bad key {secret}")
    _provider_env(monkeypatch, Boom())
    r = client.post(f"{BASE}/{did}/bulk/{jid}/cancel")
    assert r.status_code == 200
    assert secret not in r.text
    assert "provider's own cancel call failed" in r.json()["message"]
    assert db.get_bulk_job(jid)["status"] == "cancelled"


def test_bulk_cancel_running_job_is_409(client):
    did = _seed()
    jid = db.create_bulk_job(did, "claude", "m", "running", [(1, "k", "h", "")])
    r = client.post(f"{BASE}/{did}/bulk/{jid}/cancel")
    assert r.status_code == 409
    assert db.get_bulk_job(jid)["status"] == "running"


def test_bulk_line_count_uses_count(isolated_db):
    did = _seed()
    jid = db.create_bulk_job(did, "claude", "m", "submitted",
                             [(1, "k", "h", ""), (2, "k", "h", "")])
    assert db.count_bulk_job_lines(jid) == 2
    assert db.count_bulk_job_lines(jid + 999) == 0
