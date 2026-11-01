"""The OpenAI engine: listing, key status, request shape, id-keyed parsing,
retry, error redaction and refusing to run without a key. No network."""
import json

import pytest
import requests

import db
from engine_backends import engine_registry, openai_compat, pricing, llm_tasks
from engine_backends import shared as engine_shared
from core import Line
from services import settings_service, translate_run_service, translate_service
from lib.errors import DependencyUnavailableError, InvalidInputError
from tests.http_fakes import StreamedBody, patch_post
from lib.http import FetchError

KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"


class FakeResp(StreamedBody):
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.ok = status < 400

    def json(self):
        return self._body


def _ok(text, usage=None):
    return FakeResp(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                          "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5,
                                             "prompt_tokens_details": {"cached_tokens": 4}}})


@pytest.fixture
def posts(monkeypatch):
    """Records every requests.post; replies come from `posts.replies`."""
    class Calls(list):
        replies = []

    calls = Calls()
    replies = calls.replies = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return replies.pop(0)

    patch_post(monkeypatch, fake_post)
    monkeypatch.setattr("engine_backends.shared._cancellable_sleep", lambda s: None)
    return calls


def test_engine_is_listed_with_capabilities_and_note():
    assert engine_registry.ENGINES["openai"] is openai_compat.OpenAIEngine
    assert engine_registry.CAP_INSTRUCTIONS in engine_registry.engine_capabilities("openai")
    assert engine_registry.ENGINE_NOTES["openai"]
    assert engine_registry.builtin_default_model("openai") in pricing.OPENAI_MODELS
    assert all(m in pricing.PRICING_PER_MILLION_TOKENS for m in pricing.OPENAI_MODELS)


def test_key_status_true_and_false(tmp_path, monkeypatch):
    for name in ("BAIHE_OPENAI_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    assert settings_service.key_status(str(env))["openai"] is False
    env.write_text(f"BAIHE_OPENAI_KEY={KEY}\n", encoding="utf-8")
    assert settings_service.key_status(str(env))["openai"] is True
    overview = settings_service.get_settings_overview(str(env))
    assert overview["engine_keys"]["openai"] is True
    assert KEY not in repr(overview)
    # The key counts as usable only with the SDK importable (not installed in every test environment).
    monkeypatch.setattr(translate_service.diagnostics, "check_dependency", lambda name: True)
    engines = {e["name"]: e for e in translate_service.list_engines(str(env))}
    assert engines["openai"]["key_configured"] is True
    assert engines["openai"]["models"] == list(pricing.OPENAI_MODELS)
    assert KEY not in repr(engines)


def test_key_can_be_saved_through_the_settings_writer(tmp_path, monkeypatch):
    monkeypatch.delenv("BAIHE_OPENAI_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = str(tmp_path / ".env")
    result = settings_service.set_engine_key("openai", KEY, env)
    assert KEY not in repr(result)
    assert "BAIHE_OPENAI_KEY" in open(env, encoding="utf-8").read()
    assert settings_service.key_status(env)["openai"] is True


def test_request_has_key_only_in_authorization_header(posts):
    posts.replies.append(_ok('{"1": "Hello"}'))
    engine = engine_registry.get_engine("openai", KEY, "gpt-5-mini")
    assert engine.translate_batch(["你好"], {"source_language": "zh", "target_language": "en"}) == ["Hello"]
    call = posts[0]
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["url"] == pricing.OPENAI_CHAT_URL and KEY not in call["url"]
    assert KEY not in json.dumps(call["json"])
    assert call["json"]["model"] == "gpt-5-mini"
    assert [m["role"] for m in call["json"]["messages"]] == ["system", "user"]
    assert call["timeout"] and call["timeout"] > 0
    assert engine.last_usage == {"input_tokens": 10, "output_tokens": 5,
                                 "cache_read_tokens": 4, "cache_write_tokens": 0}


def test_results_are_matched_by_id_not_position(posts):
    # Reordered, one id missing, one unknown id: only known ids land, on their own lines.
    posts.replies.append(_ok('{"3": "three", "1": "one", "99": "stray"}'))
    posts.replies.append(_ok('{"2": "two"}'))  # the retry of the missing line
    engine = openai_compat.OpenAIEngine(KEY)
    out = engine.translate_batch(["a", "b", "c"], {"source_language": "zh", "target_language": "en"})
    assert out == ["one", "two", "three"]
    assert len(posts) == 2


def test_http_error_is_redacted_and_keeps_the_response(posts):
    body = {"error": {"message": f"Incorrect API key provided: {KEY}"}}
    posts.replies.append(FakeResp(401, body))
    engine = openai_compat.OpenAIEngine(KEY)
    with pytest.raises(requests.HTTPError) as exc:
        engine.chat([{"role": "user", "content": "hi"}])
    assert KEY not in str(exc.value)
    assert "401" in str(exc.value)
    assert "Incorrect API key provided" in str(exc.value)  # the provider's explanation still comes through the capped read
    assert exc.value.response.status_code == 401


def test_rate_limit_is_retried_with_backoff_then_succeeds(posts):
    posts.replies.extend([FakeResp(429, {"error": {"message": "slow down"}}), _ok("fine")])
    engine = openai_compat.OpenAIEngine(KEY)
    assert engine_shared.call_with_backoff(lambda: engine.chat([{"role": "user", "content": "x"}])) == "fine"
    assert len(posts) == 2


def test_refusal_raises_content_moderation_blocked(posts):
    posts.replies.append(FakeResp(200, {"choices": [{"message": {"content": None, "refusal": "no"},
                                                      "finish_reason": "stop"}]}))
    with pytest.raises(engine_shared.ContentModerationBlocked):
        openai_compat.OpenAIEngine(KEY).chat([{"role": "user", "content": "x"}])


def test_call_llm_json_path_uses_the_header_and_reports_usage(posts):
    posts.replies.append(_ok("[]"))
    seen = []
    assert llm_tasks.call_llm_json(openai_compat.OpenAIEngine(KEY), "p", usage_cb=lambda i, o: seen.append((i, o))) == "[]"
    assert posts[0]["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert seen == [(10, 5)]


def test_cost_estimate_uses_the_price_table_and_unlisted_models_use_the_ceiling_or_highest_rate():
    listed = pricing.estimate_cost("gpt-5-mini", 1_000_000, 1_000_000)
    assert listed == pytest.approx(0.25 + 2.0)
    # An unpriced GPT-5+ id gets the explicit ceiling (newer releases can cost
    # more than gpt-5); any other unpriced gpt- id gets the family's highest rate.
    assert pricing.estimate_cost("gpt-9-future", 1_000_000, 0) == pytest.approx(
        pricing.OPENAI_EXTRA_MODEL_CEILING["input"])
    assert pricing.estimate_cost("gpt-4", 1_000_000, 0) == pytest.approx(
        max(r["input"] for m, r in pricing.PRICING_PER_MILLION_TOKENS.items() if m.startswith("gpt-")))


def test_translate_refuses_when_no_key(isolated_db, tmp_path, monkeypatch):
    monkeypatch.delenv("BAIHE_OPENAI_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(DependencyUnavailableError):
        translate_service.translate("hi", "openai", "zh", "en", env_path=str(tmp_path / "none.env"))


def test_translate_run_refuses_when_no_key_and_estimate_counts_against_the_cap(isolated_db, monkeypatch):
    monkeypatch.delenv("BAIHE_OPENAI_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(settings_service, "default_env_path", lambda: "/nonexistent/.env")
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    est = translate_run_service.estimate_translate_cost(did, engine_name="openai")
    assert est["cap_applies"] is True and est["free"] is False and est["estimated_usd"] > 0
    with pytest.raises(DependencyUnavailableError):
        translate_run_service.start_translate_run(did, engine_name="openai")


@pytest.mark.parametrize("model", ["o1", "gpt-4", "gpt-4-turbo", "chatgpt-4o-latest", "gpt-9-future"])
def test_a_model_the_app_does_not_offer_is_refused_so_it_cannot_dodge_the_cost_caps(model):
    with pytest.raises(InvalidInputError):
        engine_registry.get_engine("openai", KEY, model)
    with pytest.raises(InvalidInputError):
        openai_compat.OpenAIEngine(KEY, model)


def test_every_offered_model_is_priced_and_accepted():
    for model in pricing.OPENAI_MODELS:
        assert model in pricing.PRICING_PER_MILLION_TOKENS
        assert engine_registry.get_engine("openai", KEY, model).model == model


def _listed_by_openai(models):
    db.set_app_setting(pricing.PROVIDER_CHECK_CACHE_KEY, json.dumps(
        {"checked_at": "2026-10-01T00:00:00", "engines": {"openai": {"ok": True, "models": models}}}))


def test_a_gpt5_or_later_model_openai_listed_is_accepted_and_costed_at_the_ceiling_above_every_built_in_rate(isolated_db):
    _listed_by_openai(["gpt-6-luna", "gpt-4", "o3", "gpt-5-codex", "gpt-5-pro"])
    assert pricing.openai_listed_extra_models() == ["gpt-6-luna"]
    assert engine_registry.get_engine("openai", KEY, "gpt-6-luna").model == "gpt-6-luna"
    ceiling = pricing.OPENAI_EXTRA_MODEL_CEILING
    for m in pricing.OPENAI_MODELS:
        assert ceiling["input"] > pricing.PRICING_PER_MILLION_TOKENS[m]["input"]
        assert ceiling["output"] > pricing.PRICING_PER_MILLION_TOKENS[m]["output"]
    assert pricing.estimate_cost("gpt-6-luna", 0, 1_000_000) == pytest.approx(ceiling["output"])
    assert pricing.estimate_cost("gpt-6-luna", 1_000_000, 0) == pytest.approx(ceiling["input"])


def test_a_malformed_provider_cache_adds_nothing_instead_of_crashing(isolated_db):
    for models in (5, True, [{"a": 1}], [["x"]], {"gpt-6-luna": 1}):
        db.set_app_setting(pricing.PROVIDER_CHECK_CACHE_KEY, json.dumps(
            {"engines": {"openai": {"ok": True, "models": models}}}))
        assert pricing.openai_listed_extra_models() == []


@pytest.mark.parametrize("model", ["gpt-4", "o3", "gpt-5-codex", "gpt-5-pro", "gpt-9-unlisted"])
def test_listing_does_not_make_older_or_non_chat_or_unlisted_models_usable(isolated_db, model):
    _listed_by_openai(["gpt-6-luna", "gpt-4", "o3", "gpt-5-codex", "gpt-5-pro"])
    with pytest.raises(InvalidInputError):
        engine_registry.get_engine("openai", KEY, model)


def test_a_failed_check_adds_nothing(isolated_db):
    db.set_app_setting(pricing.PROVIDER_CHECK_CACHE_KEY, json.dumps(
        {"engines": {"openai": {"ok": False, "models": ["gpt-6-luna"]}}}))
    assert pricing.openai_listed_extra_models() == []


def _post_json_with(monkeypatch, outcome):
    from engine_backends import shared
    seen = {}

    def fake(url, **kw):
        seen.update(kw)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(shared.http, "post", fake)
    return seen


def test_transport_failure_through_post_json_is_transient_for_the_fallback_chain(monkeypatch):
    from engine_backends import fallback, shared
    from lib import http
    _post_json_with(monkeypatch, http.FetchError())
    with pytest.raises(FetchError) as exc:
        shared.post_json("https://x.test", {}, timeout=5, headers={"Authorization": KEY})
    assert fallback.is_fallback_error(exc.value)
    assert fallback.is_transient_fallback_error(exc.value)
    assert KEY not in str(exc.value)


def test_redirect_through_post_json_is_not_transient_and_neither_is_401(monkeypatch):
    from types import SimpleNamespace
    from engine_backends import fallback, shared
    from lib import http
    _post_json_with(monkeypatch, SimpleNamespace(
        status=308, body=b"", headers={"Location": "https://other.test/v1?key=abc"}))
    with pytest.raises(shared.ProviderRedirected) as redirect:
        shared.post_json("https://x.test", {}, timeout=5)
    assert isinstance(redirect.value, http.FetchError)
    assert fallback.is_fallback_error(redirect.value)  # the chain still switches engines
    assert not fallback.is_transient_fallback_error(redirect.value)  # but never retries in place
    assert str(redirect.value) == shared.PROVIDER_REDIRECTED

    resp = SimpleNamespace(status=401, body=b"{}", headers={}, text=lambda: "{}")
    _post_json_with(monkeypatch, resp)
    with pytest.raises(requests.HTTPError) as auth:
        shared.post_json("https://x.test", {}, timeout=5)
    assert not fallback.is_transient_fallback_error(auth.value)


def test_post_json_headers_are_case_insensitive_and_deadline_is_twice_the_timeout(monkeypatch):
    from types import SimpleNamespace
    from engine_backends import shared
    resp = SimpleNamespace(status=200, body=b"{}", headers={"X-RateLimit-Limit-Requests": "20"})
    seen = _post_json_with(monkeypatch, resp)
    _, headers = shared.post_json("https://x.test", {}, timeout=120)
    assert headers.get("x-ratelimit-limit-requests") == "20"
    assert seen["deadline"] == 240


def test_error_response_text_is_redacted_on_the_exception(monkeypatch):
    from types import SimpleNamespace
    from engine_backends import shared
    body = '{"error": {"message": "bad key sk-abcdefghijklmnop0123456789"}}'
    resp = SimpleNamespace(status=400, body=body.encode(), headers={}, text=lambda: body)
    _post_json_with(monkeypatch, resp)
    with pytest.raises(requests.HTTPError) as exc:
        shared.post_json("https://x.test", {}, timeout=5)
    assert "sk-abcdefghijklmnop0123456789" not in exc.value.response.text
    assert "[REDACTED]" in exc.value.response.text
