"""Tests for the Translate-stage config/estimate endpoints (Migration Slice 39)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


import db
from core import Line
from services import settings_service

BASE = "/api/translate-run/dramas"


def _seed(**fields):
    did = db.create_drama(title_zh="D", **fields)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en=""),
                        Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    return did


def test_config_audio_and_novel(local_client):
    a = local_client.get(f"{BASE}/{_seed()}/config")
    assert a.status_code == 200
    body = a.json()
    assert body["defaults"] == {"context_window": 10, "context_window_ahead": 6, "batch_size": 60}
    assert body["line_count"] == 2 and body["untranslated_count"] == 1
    assert body["engines"] and body["workflow_tiers"] and body["style_presets"]
    assert isinstance(body["cap_applies_by_engine"]["claude"], bool)
    n = local_client.get(f"{BASE}/{_seed(content_mode='novel_narration')}/config").json()
    assert n["defaults"] == {"context_window": 30, "context_window_ahead": 15, "batch_size": 60}
    assert n["default_style_preset"] == "novel"


def test_unknown_drama_404(local_client):
    for path in ("config", "estimate"):
        r = local_client.get(f"{BASE}/9999/{path}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


def test_estimate_free_engine(local_client):
    r = local_client.get(f"{BASE}/{_seed()}/estimate", params={"engine": "fake"})
    assert r.status_code == 200
    body = r.json()
    assert body["free"] is True and body["engine"] == "fake"
    assert body["target_line_count"] == 1


def test_estimate_validation(local_client):
    did = _seed()
    r = local_client.get(f"{BASE}/{did}/estimate", params={"engine": "nope"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    r = local_client.get(f"{BASE}/{did}/estimate", params={"engine": "fake_mt", "reflect": "true"})
    assert r.status_code == 400
    assert "error" in r.json()
    r = local_client.get(f"{BASE}/{did}/estimate", params={"job_cost_cap_usd": -1})
    assert r.status_code == 422
    for bad in (0, 1000):
        assert local_client.get(f"{BASE}/{did}/estimate", params={"batch_size": bad}).status_code == 422
    assert local_client.get(f"{BASE}/{did}/estimate", params={"engine": "fake", "batch_size": 5}).status_code == 200


def test_no_secrets_in_responses(local_client, monkeypatch):
    fake = "sk-FAKE-SECRET-1234567890"
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **kw: fake)
    did = _seed()
    for path in ("config", "estimate"):
        assert fake not in local_client.get(f"{BASE}/{did}/{path}").text


# --- Slice 51: bulk batch list + cancel -------------------------------------

def test_bulk_list_and_cancel(local_client, monkeypatch):
    import bulk_poll
    did = _seed()
    other = _seed()
    jid = db.create_bulk_job(did, "claude", "m", "submitted", [(1, "k", "h", "")],
                             provider_batch_id="msgbatch_SECRET",
                             translate_args={"prompt": "SECRETPROMPT"})
    done = db.create_bulk_job(did, "gemini", None, "applied", [])
    foreign = db.create_bulk_job(other, "claude", "m", "submitted", [])
    assert local_client.get("/api/translate-run/dramas/9999/bulk").status_code == 404
    r = local_client.get(f"{BASE}/{did}/bulk")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert [j["bulk_job_id"] for j in jobs] == [done, jid]
    assert jobs[1]["pending"] and jobs[1]["cancellable"] and jobs[1]["line_count"] == 1
    assert not jobs[0]["pending"] and not jobs[0]["cancellable"]
    assert "SECRET" not in r.text

    calls = []
    monkeypatch.setattr(bulk_poll, "cancel_bulk_job",
                        lambda i, p=None: calls.append((i, p)) or db.update_bulk_job(i, status="cancelled") or "ok")
    assert local_client.post(f"{BASE}/{did}/bulk/{foreign}/cancel").status_code == 404
    assert local_client.post(f"{BASE}/{did}/bulk/{done}/cancel").status_code == 409
    c = local_client.post(f"{BASE}/{did}/bulk/{jid}/cancel")
    assert c.status_code == 200 and c.json()["bulk_job"]["status"] == "cancelled"
    assert calls and calls[0][0] == jid



def _provider_env(monkeypatch, provider):
    import bulk_providers
    import translate_engines
    from services import translate_service
    seen = {}
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: seen.setdefault("key_for", name) and "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
    monkeypatch.setattr(bulk_providers, "make_provider", lambda name, engine: provider)
    return seen


def test_bulk_cancel_asks_provider_when_key_exists(local_client, monkeypatch):
    did = _seed()
    jid = db.create_bulk_job(did, "claude", "m", "submitted", [(1, "k", "h", "")],
                             provider_batch_id="msgbatch_1")

    class Provider:
        cancelled = []

        def cancel(self, batch_id):
            self.cancelled.append(batch_id)
    p = Provider()
    seen = _provider_env(monkeypatch, p)
    r = local_client.post(f"{BASE}/{did}/bulk/{jid}/cancel")
    assert r.status_code == 200
    assert seen["key_for"] == "claude" and p.cancelled == ["msgbatch_1"]
    assert r.json()["message"] == "Cancelled at the provider and here."
    assert r.json()["bulk_job"]["status"] == "cancelled"


def test_bulk_cancel_provider_failure_is_redacted_and_local_cancel_stands(local_client, monkeypatch):
    did = _seed()
    secret = "sk-ant-SECRET1234567890abcdef"
    jid = db.create_bulk_job(did, "claude", "m", "scheduled", [(1, "k", "h", "")],
                             provider_batch_id="msgbatch_2")

    class Boom:
        def cancel(self, batch_id):
            raise RuntimeError(f"401 bad key {secret}")
    _provider_env(monkeypatch, Boom())
    r = local_client.post(f"{BASE}/{did}/bulk/{jid}/cancel")
    assert r.status_code == 200
    assert secret not in r.text
    assert "provider's own cancel call failed" in r.json()["message"]
    assert db.get_bulk_job(jid)["status"] == "cancelled"


def test_bulk_cancel_running_job_is_409(local_client):
    did = _seed()
    jid = db.create_bulk_job(did, "claude", "m", "running", [(1, "k", "h", "")])
    r = local_client.post(f"{BASE}/{did}/bulk/{jid}/cancel")
    assert r.status_code == 409
    assert db.get_bulk_job(jid)["status"] == "running"


def test_bulk_line_count_uses_count(isolated_db):
    did = _seed()
    jid = db.create_bulk_job(did, "claude", "m", "submitted",
                             [(1, "k", "h", ""), (2, "k", "h", "")])
    assert db.count_bulk_job_lines(jid) == 2
    assert db.count_bulk_job_lines(jid + 999) == 0


@pytest.mark.parametrize("engine,model", [
    ("fake", "someone/evil-repo"), ("fake", "../models/x"), ("claude", "not-a-claude-model"),
    ("fake", "anything"), ("deepseek", 123)])
def test_run_refuses_model_not_offered(local_client, monkeypatch, engine, model):
    import translate_engines
    from services import translate_service
    built = []
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: built.append(a))
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    r = local_client.post(f"{BASE}/{_seed()}/run", json={"engine": engine, "model": model})
    assert r.status_code == 422
    if not isinstance(model, int):  # a non-string is refused by the request schema
        assert r.json()["error"]["message"] == "That model isn't offered for this engine."
        assert str(model) not in r.text
    assert built == []


def test_offered_or_default_models_pass_the_check():
    import translate_engines
    from services import translate_run_service as svc
    from services.service_errors import InvalidInputError
    svc._require_offered_model("fake", None)
    svc._require_offered_model("ollama", next(iter(translate_engines.OLLAMA_MODELS)))
    svc._require_offered_model("claude", next(iter(translate_engines.CLAUDE_MODELS)))
    svc._require_offered_model("deepseek", translate_engines.effective_default_model("deepseek"))
    with pytest.raises(InvalidInputError):
        svc._require_offered_model("deepseek", "other-model")


@pytest.mark.parametrize("model,ok", [
    ("gemma4:12b", True), ("qwen3:14b", True), ("my-own/llama3.1:8b-instruct-q4_K_M", True),
    ("../x", False), ("/abs/path", False), ("has space", False), ("a/../b", False),
    ("x" * 101, False), ("", False)])
def test_ollama_takes_any_safe_model_name(model, ok):
    from services import translate_run_service as svc
    from services.service_errors import InvalidInputError
    if ok:
        svc._require_offered_model("ollama", model)
    else:
        with pytest.raises(InvalidInputError):
            svc._require_offered_model("ollama", model)


def test_estimate_refuses_model_not_offered(local_client):
    r = local_client.get(f"{BASE}/{_seed()}/estimate",
                   params={"engine": "fake", "model": "someone/evil-repo"})
    assert r.status_code == 422
    assert "evil-repo" not in r.text


def test_estimate_counts_only_the_chosen_lines(local_client):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句", en="") for i in range(4)])
    ids = [ln["id"] for ln in db.load_lines(did)]
    whole = local_client.get(f"{BASE}/{did}/estimate", params={"engine": "fake"}).json()
    some = local_client.get(f"{BASE}/{did}/estimate", params={"engine": "fake", "line_ids": ids[:2]}).json()
    assert whole["target_line_count"] == 4 and some["target_line_count"] == 2
