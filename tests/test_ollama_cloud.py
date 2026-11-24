"""Ollama hosted ("cloud") models reached through the local Ollama app.
Mocked HTTP only: no network, no real sign-in."""
import json

import pytest
import requests

from engine_backends import shared, engine_registry, pricing
from engine_backends import local as engine_local
from lib import http
from services import model_registry_service, translate_service
from lib.errors import InvalidInputError  # noqa: F401

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


class StreamedErrorResp(Resp):
    """A stream=True error reply: the body is only readable through
    iter_content while open, as with requests."""

    def __init__(self, status, body: bytes):
        super().__init__(status=status)
        self._raw, self.closed = body, False

    @property
    def text(self):
        raise AssertionError("a streamed body must be read through iter_content")

    @text.setter
    def text(self, value):
        pass

    def iter_content(self, size):
        if self.closed:
            raise RuntimeError("read after close")
        yield self._raw

    def close(self):
        self.closed = True


def _reply(content):
    return Resp({"message": {"content": content}})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("engine_backends.shared.time.sleep", lambda s: None)


def test_cloud_detection():
    assert engine_local.is_ollama_cloud_model("gemma4:31b-cloud")
    assert engine_local.is_ollama_cloud_model("qwen3-coder:cloud")
    assert not engine_local.is_ollama_cloud_model("gemma4:12b")
    assert not engine_local.is_ollama_cloud_model(None)


def test_default_is_the_hosted_gemma_31b_and_the_local_picker_is_empty():
    assert engine_local.OLLAMA_DEFAULT_MODEL == CLOUD
    assert engine_local.is_ollama_cloud_model(engine_local.OllamaEngine().model)
    assert not set(engine_local.OLLAMA_CLOUD_MODELS) & set(engine_local.OLLAMA_MODELS)


def test_cloud_results_matched_by_id_not_position(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: _reply('{"2": "Second.", "1": "First."}'))
    out = engine_local.OllamaEngine(model=CLOUD).translate_batch(
        ["一", "二"], {"line_ids": [1, 2]})
    assert out == ["First.", "Second."]


def test_cloud_call_has_timeout_and_no_key_material(monkeypatch):
    seen = {}

    def post(url, json=None, timeout=None, stream=None, headers=None):
        seen.update(url=url, timeout=timeout, headers=headers)
        return _reply('{"1": "Hi."}')
    monkeypatch.setattr("requests.post", post)
    engine_local.OllamaEngine(model=CLOUD).translate_batch(["你好"], {})
    assert seen["timeout"] and seen["url"].startswith("http://localhost:11434")
    assert seen["headers"] is None


def test_quota_error_retries_then_gives_plain_message_with_redaction(monkeypatch):
    calls = []

    def post(*a, **k):
        calls.append(1)
        return StreamedErrorResp(
            429, b"weekly limit reached. Authorization: Bearer sk-secret1234567890abcd")
    monkeypatch.setattr("requests.post", post)
    engine = engine_local.OllamaEngine(model=CLOUD)
    # The translate loop wraps each batch in call_with_backoff; 429 must read as a rate limit.
    with pytest.raises(engine_local.OllamaCloudLimitError) as info:
        shared.call_with_backoff(lambda: engine.translate_batch(["你好"], {}), max_retries=3)
    assert len(calls) == 3
    msg = str(info.value)
    assert "rate-limited or over its usage limit" in msg
    assert "sk-secret1234567890abcd" not in msg
    assert "weekly limit reached" in msg


def test_quota_detail_is_cut_to_200_characters(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: StreamedErrorResp(429, b"x" * 1500))
    with pytest.raises(engine_local.OllamaCloudLimitError) as info:
        engine_local._ollama_chat("http://localhost:11434", {"model": CLOUD})
    assert str(info.value).count("x") == 200


def test_local_429_is_not_rewritten(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: Resp(status=429, text="x"))
    with pytest.raises(requests.HTTPError):
        engine_local._ollama_chat("http://localhost:11434", {"model": "gemma4:12b"})


def test_signed_out_cloud_gives_signin_message(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: Resp(status=401))
    with pytest.raises(engine_local.OllamaUnavailableError) as info:
        engine_local._ollama_chat("http://localhost:11434", {"model": CLOUD})
    assert info.value.reason == "ollama_cloud_signin"
    assert "ollama signin" in str(info.value)


def test_missing_local_model_message_unchanged(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: Resp(status=404))
    with pytest.raises(engine_local.OllamaUnavailableError) as info:
        engine_local._ollama_chat("http://localhost:11434", {"model": "gemma4:12b"})
    assert info.value.reason == "ollama_model_missing"


def test_installed_check_does_not_require_cloud_tag_in_local_list(monkeypatch):
    monkeypatch.setattr(http, "pinned_get", lambda *a, **k: Resp({"models": []}))
    engine_local.check_ollama_model_installed("http://localhost:11434", CLOUD)
    with pytest.raises(engine_local.OllamaUnavailableError):
        engine_local.check_ollama_model_installed("http://localhost:11434", "gemma4:12b")


def test_cloud_models_are_flagged_in_engine_list_but_keep_their_plain_name(isolated_db):
    ollama = next(e for e in translate_service.list_engines() if e["name"] == "ollama")
    assert CLOUD in ollama["models"] and CLOUD in ollama["cloud_models"]
    assert CLOUD not in ollama["model_labels"]
    assert "gemma4:12b" not in ollama["cloud_models"]
    assert "gemma4:12b" not in ollama["model_labels"]


def test_no_workflow_tier_names_a_cloud_model(isolated_db):
    assert engine_registry.effective_default_model("ollama") == CLOUD
    assert not any(engine_local.is_ollama_cloud_model(t.get("engine_model"))
                   for t in engine_registry.WORKFLOW_TIERS.values())


def test_gpu_seam_skips_cloud_only():
    assert engine_local.ollama_touches_local_gpu("ollama", "gemma4:12b")
    assert engine_local.ollama_touches_local_gpu("ollama", None)
    assert not engine_local.ollama_touches_local_gpu("ollama", CLOUD)
    assert not engine_local.ollama_touches_local_gpu("claude", "claude-sonnet-5-5")


def test_local_model_costs_nothing():
    engine = engine_local.OllamaEngine(model="gemma4:12b")
    assert pricing.estimate_cost_for_engine(engine, 100_000, 50_000) == 0.0


def test_model_health_does_not_call_cloud_tag_retired_or_unlisted(isolated_db):
    a = model_registry_service._assess("ollama", CLOUD, model_registry_service.load_registry(), {})
    assert a["status"] == "current"
    other = model_registry_service._assess("ollama", "gpt-oss:120b-cloud", {}, {})
    assert other["status"] not in ("retired", "not_listed")


# --- a hosted tag other than the built-in default is picked per run -------------

def test_ollama_default_picker_lists_local_tags_only():
    assert CLOUD not in translate_service.ENGINE_MODEL_DICTS["ollama"]


@pytest.mark.parametrize("tag", ["gpt-oss:120b-cloud", "qwen3-coder:cloud"])
def test_cloud_tag_cannot_replace_the_ollama_default_while_hosted_defaults_are_off(isolated_db, tag):
    import db
    db.set_app_setting("pref.ollama_cloud_default", False)
    with pytest.raises(InvalidInputError, match="can't be the default"):
        model_registry_service.set_model_override("default", "ollama", engine_registry.effective_default_model("ollama"), tag)
    assert engine_registry.effective_default_model("ollama") == CLOUD


def test_run_naming_ollama_without_a_model_resolves_to_the_hosted_default(isolated_db):
    engine = engine_registry.get_engine("ollama", None)
    assert engine.model == CLOUD


def test_cloud_flag_covers_any_cloud_shaped_tag_including_saved_presets(isolated_db):
    import db
    db.save_preset(name="big", translation_engine="ollama", engine_model="gpt-oss:120b-cloud")
    ollama = next(e for e in translate_service.list_engines() if e["name"] == "ollama")
    assert "gpt-oss:120b-cloud" in ollama["models"]
    assert "gpt-oss:120b-cloud" in ollama["cloud_models"]
    assert "gpt-oss:120b-cloud" not in ollama["model_labels"]
    for m in ollama["models"]:
        assert (m in ollama["cloud_models"]) == engine_local.is_ollama_cloud_model(m)


# --- cloud tags need engines.paid -------------------------------------------

def _household(email, extra=()):
    from api import auth as api_auth
    from services import auth_service
    u = auth_service.add_user(email)
    for perm in extra:
        auth_service.grant_permission(u["id"], perm)
    sess = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={sess['session_token']}",
            api_auth.CSRF_HEADER: sess["csrf_token"]}


@pytest.fixture
def remote(isolated_db):
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                      client=("203.0.113.9", 5000), raise_server_exceptions=False)


@pytest.fixture
def run_url(isolated_db):
    import db
    # The engine gate runs after the path guard, so it needs a real drama.
    return f"/api/translate-run/dramas/{db.create_drama(title_en='T', source_language='zh')}/run"


# A hosted tag outside MEMBER_CLOUD_MODELS: the paid gate applies to it.
PAID_CLOUD = "gpt-oss:120b-cloud"


def test_cloud_tag_run_is_refused_without_engines_paid(remote, run_url):
    h = _household("kid@example.com")
    r = remote.post(run_url, json={"engine": "ollama", "model": PAID_CLOUD}, headers=h)
    assert r.status_code == 403
    assert "paid-engine permission" in r.json()["error"]["message"]
    r = remote.post(run_url, json={"engine": "ollama", "model": "gemma4:12b",
                                   "fallback_chain": [{"engine": "ollama", "model": PAID_CLOUD}]}, headers=h)
    assert r.status_code == 403


def test_cloud_tag_run_is_allowed_with_engines_paid_and_local_tags_stay_free(remote, run_url):
    paid = _household("paid@example.com", ["engines.paid"])
    assert remote.post(run_url, json={"engine": "ollama", "model": PAID_CLOUD}, headers=paid).status_code != 403
    free = _household("free@example.com")
    assert remote.post(run_url, json={"engine": "ollama", "model": "gemma4:12b"}, headers=free).status_code != 403


def test_cloud_tag_live_start_needs_engines_paid(remote):
    h = _household("kid@example.com", ["media.import_url"])
    body = {"url": "https://example.com/v", "engine": "ollama", "model": PAID_CLOUD}
    assert remote.post("/api/live/sessions", json=body, headers=h).status_code == 403


def test_cloud_tag_gate_unit():
    from types import SimpleNamespace
    from unittest.mock import patch
    from api import auth as api_auth
    from lib.errors import ForbiddenError
    with patch.object(api_auth, "holds", return_value=False):
        with pytest.raises(ForbiddenError):
            api_auth.require_cloud_model_allowed(SimpleNamespace(), ("ollama", PAID_CLOUD))
        api_auth.require_cloud_model_allowed(SimpleNamespace(), ("ollama", "gemma4:12b"), ("claude", None))
    with patch.object(api_auth, "holds", return_value=True):
        api_auth.require_cloud_model_allowed(SimpleNamespace(), ("ollama", PAID_CLOUD))


def test_member_cloud_model_needs_no_engines_paid(remote, run_url):
    free = _household("member@example.com")
    assert remote.post(run_url, json={"engine": "ollama", "model": CLOUD}, headers=free).status_code != 403
    live = _household("viewer@example.com", ["media.import_url"])
    body = {"url": "https://example.com/v", "engine": "ollama", "model": CLOUD}
    r = remote.post("/api/live/sessions", json=body, headers=live)
    # Past the paid gate; the fake PC has no Whisper model, which is refused later.
    assert r.json()["error"]["code"] != "forbidden"


def test_member_cloud_model_gate_unit():
    from types import SimpleNamespace
    from unittest.mock import patch
    from api import auth as api_auth
    from lib.errors import ForbiddenError
    with patch.object(api_auth, "holds", return_value=False):
        api_auth.require_cloud_model_allowed(SimpleNamespace(), ("ollama", CLOUD))
        api_auth.require_engines_allowed(SimpleNamespace(), "ollama", model=CLOUD)
        with pytest.raises(ForbiddenError):
            api_auth.require_cloud_model_allowed(SimpleNamespace(), ("ollama", CLOUD), ("ollama", PAID_CLOUD))
    assert not engine_local.cloud_model_needs_paid(CLOUD)
    assert engine_local.cloud_model_needs_paid(PAID_CLOUD)
    assert not engine_local.cloud_model_needs_paid("gemma4:12b")


def test_cloud_models_are_priced_so_the_spend_cap_counts_them():
    for tag in engine_local.OLLAMA_CLOUD_MODELS:
        assert pricing.estimate_cost(tag, 1_000_000, 1_000_000) == pytest.approx(0.54)
    assert pricing.estimate_cost("gemma4:12b", 1_000_000, 1_000_000) == 0.0


def test_a_member_naming_no_engine_runs_on_the_member_model_not_a_refused_default(remote, run_url, monkeypatch):
    import db
    from lib.errors import ConflictError
    from services import translate_thinking_service
    seen = {}

    def fake_start(fn, drama_id, thinking, remember, *a, **kw):
        seen.update(kw)
        raise ConflictError("stop here")

    monkeypatch.setattr(translate_thinking_service, "start_with_thinking", fake_start)
    drama_id = int(run_url.split("/")[-2])
    db.update_drama(drama_id, translation_engine="claude")
    db.set_app_setting("pref.ollama_cloud_default", True)
    member = _household("auto@example.com")
    assert remote.post(run_url, json={}, headers=member).status_code == 409
    assert (seen["engine_name"], seen["model"]) == ("ollama", CLOUD)
    # A named paid engine is never switched: it stays refused.
    assert remote.post(run_url, json={"engine": "claude"}, headers=member).status_code == 403
    paid = _household("paid-auto@example.com", ["engines.paid"])
    assert remote.post(run_url, json={}, headers=paid).status_code == 409
    assert seen["engine_name"] == "claude"


def test_engine_for_caller_unit(isolated_db):
    import db
    from types import SimpleNamespace
    from unittest.mock import patch
    from api import auth as api_auth
    req = SimpleNamespace()
    with patch.object(api_auth, "holds", return_value=False):
        # With the owner's hosted-default switch off: Ollama's own default model (override, else built-in).
        db.set_app_setting("pref.ollama_cloud_default", False)
        assert api_auth.engine_for_caller(req, None, "claude", None) == ("ollama", None)
        db.set_app_setting("pref.ollama_cloud_default", True)
        assert api_auth.engine_for_caller(req, None, "claude", None) == ("ollama", CLOUD)
        assert api_auth.engine_for_caller(req, None, "ollama", "gemma4:12b") == ("ollama", "gemma4:12b")
        assert api_auth.engine_for_caller(req, None, "ollama", PAID_CLOUD) == ("ollama", CLOUD)
        assert api_auth.engine_for_caller(req, "claude", "claude", None) == ("claude", None)
    with patch.object(api_auth, "holds", return_value=True):
        assert api_auth.engine_for_caller(req, None, "claude", "m") == ("claude", "m")


def test_line_tool_with_no_engine_runs_on_ollama_for_a_member(remote, monkeypatch):
    import db
    from services import line_ai_service
    seen = {}

    def fake_improve(drama_id, line_id, engine, model, *a, **kw):
        seen.update(engine=engine, model=model)
        return {"suggestion": "x", "reason": "y"}

    monkeypatch.setattr(line_ai_service, "improve_line", fake_improve)
    drama_id = db.create_drama(title_en="T", source_language="zh", translation_engine="claude")
    db.set_app_setting("pref.ollama_cloud_default", True)
    h = _household("tool@example.com", ["lines.read"])
    remote.post(f"/api/line-ai/dramas/{drama_id}/lines/1/improve", json={}, headers=h)
    assert seen == {"engine": "ollama", "model": CLOUD}


def test_a_member_over_their_own_limit_is_still_refused(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch
    from api import auth as api_auth
    from lib.errors import ForbiddenError
    from services import auth_service
    monkeypatch.setattr(auth_service, "paid_engines_held_back", lambda uid: True)
    req = SimpleNamespace(state=SimpleNamespace(principal={"user_id": 7, "permissions": []}))
    with patch.object(api_auth, "holds", return_value=False):
        with pytest.raises(ForbiddenError):
            api_auth.engine_for_caller(req, None, "claude", None)


def test_text_page_with_no_engine_runs_on_ollama_for_a_member(remote, monkeypatch):
    import db
    from services import translate_service
    seen = {}

    def fake_translate(text, engine_name, source, target, model=None, **kw):
        seen.update(engine=engine_name, model=model)
        return {"translated_text": "hi"}

    monkeypatch.setattr(translate_service, "translate", fake_translate)
    db.set_app_setting("default_engine", "claude")
    db.set_app_setting("pref.ollama_cloud_default", True)
    body = {"text": "你好", "source_language": "zh", "target_language": "en"}
    member = _household("text@example.com")
    assert remote.post("/api/translate", json=body, headers=member).status_code == 200
    assert seen == {"engine": "ollama", "model": CLOUD}
    # A named paid engine or hosted tag stays refused; the owner's default runs for a paid holder.
    assert remote.post("/api/translate", json={**body, "engine": "claude"}, headers=member).status_code == 403
    assert remote.post("/api/translate", json={**body, "engine": "ollama", "model": PAID_CLOUD},
                       headers=member).status_code == 403
    paid = _household("text-paid@example.com", ["engines.paid"])
    assert remote.post("/api/translate", json=body, headers=paid).status_code == 200
    assert seen["engine"] == "claude"


def test_text_price_check_with_no_engine_prices_ollama_for_a_member(remote):
    import db
    db.set_app_setting("default_engine", "claude")
    body = {"text": "你好", "source_language": "zh", "target_language": "en"}
    member = _household("price@example.com")
    resp = remote.post("/api/translate/estimate", json=body, headers=member)
    assert resp.status_code == 200
    assert remote.post("/api/translate/estimate", json={**body, "engine": "claude"}, headers=member).status_code == 403
    assert remote.post("/api/translate/estimate", json={**body, "engine": "ollama", "model": PAID_CLOUD},
                       headers=member).status_code == 403
