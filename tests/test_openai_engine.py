"""The OpenAI engine: listing, key status, request shape, id-keyed parsing,
retry, error redaction and refusing to run without a key. No network."""
import json

import pytest
import requests

import db
import translate_engines as te
from core import Line
from services import settings_service, translate_run_service, translate_service
from services.service_errors import DependencyUnavailableError, InvalidInputError
from tests.http_fakes import StreamedBody

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

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("engine_backends.shared._cancellable_sleep", lambda s: None)
    return calls


def test_engine_is_listed_with_capabilities_and_note():
    assert te.ENGINES["openai"] is te.OpenAIEngine
    assert te.CAP_INSTRUCTIONS in te.engine_capabilities("openai")
    assert te.ENGINE_NOTES["openai"]
    assert te.builtin_default_model("openai") in te.OPENAI_MODELS
    assert all(m in te.PRICING_PER_MILLION_TOKENS for m in te.OPENAI_MODELS)


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
    engines = {e["name"]: e for e in translate_service.list_engines(str(env))}
    assert engines["openai"]["key_configured"] is True
    assert engines["openai"]["models"] == list(te.OPENAI_MODELS)
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
    engine = te.get_engine("openai", KEY, "gpt-5-mini")
    assert engine.translate_batch(["你好"], {"source_language": "zh", "target_language": "en"}) == ["Hello"]
    call = posts[0]
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["url"] == te.OPENAI_CHAT_URL and KEY not in call["url"]
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
    engine = te.OpenAIEngine(KEY)
    out = engine.translate_batch(["a", "b", "c"], {"source_language": "zh", "target_language": "en"})
    assert out == ["one", "two", "three"]
    assert len(posts) == 2


def test_http_error_is_redacted_and_keeps_the_response(posts):
    body = {"error": {"message": f"Incorrect API key provided: {KEY}"}}
    posts.replies.append(FakeResp(401, body))
    engine = te.OpenAIEngine(KEY)
    with pytest.raises(requests.HTTPError) as exc:
        engine.chat([{"role": "user", "content": "hi"}])
    assert KEY not in str(exc.value)
    assert "401" in str(exc.value)
    assert "Incorrect API key provided" in str(exc.value)  # the provider's explanation still comes through the capped read
    assert exc.value.response.status_code == 401


def test_rate_limit_is_retried_with_backoff_then_succeeds(posts):
    posts.replies.extend([FakeResp(429, {"error": {"message": "slow down"}}), _ok("fine")])
    engine = te.OpenAIEngine(KEY)
    assert te.call_with_backoff(lambda: engine.chat([{"role": "user", "content": "x"}])) == "fine"
    assert len(posts) == 2


def test_refusal_raises_content_moderation_blocked(posts):
    posts.replies.append(FakeResp(200, {"choices": [{"message": {"content": None, "refusal": "no"},
                                                      "finish_reason": "stop"}]}))
    with pytest.raises(te.ContentModerationBlocked):
        te.OpenAIEngine(KEY).chat([{"role": "user", "content": "x"}])


def test_call_llm_json_path_uses_the_header_and_reports_usage(posts):
    posts.replies.append(_ok("[]"))
    seen = []
    assert te.call_llm_json(te.OpenAIEngine(KEY), "p", usage_cb=lambda i, o: seen.append((i, o))) == "[]"
    assert posts[0]["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert seen == [(10, 5)]


def test_cost_estimate_uses_the_price_table_and_unlisted_models_use_the_ceiling_or_highest_rate():
    listed = te.estimate_cost("gpt-5-mini", 1_000_000, 1_000_000)
    assert listed == pytest.approx(0.25 + 2.0)
    # An unpriced GPT-5+ id gets the explicit ceiling (newer releases can cost
    # more than gpt-5); any other unpriced gpt- id gets the family's highest rate.
    assert te.estimate_cost("gpt-9-future", 1_000_000, 0) == pytest.approx(
        te.OPENAI_EXTRA_MODEL_CEILING["input"])
    assert te.estimate_cost("gpt-4", 1_000_000, 0) == pytest.approx(
        max(r["input"] for m, r in te.PRICING_PER_MILLION_TOKENS.items() if m.startswith("gpt-")))


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
        te.get_engine("openai", KEY, model)
    with pytest.raises(InvalidInputError):
        te.OpenAIEngine(KEY, model)


def test_every_offered_model_is_priced_and_accepted():
    for model in te.OPENAI_MODELS:
        assert model in te.PRICING_PER_MILLION_TOKENS
        assert te.get_engine("openai", KEY, model).model == model


def _listed_by_openai(models):
    db.set_app_setting(te.PROVIDER_CHECK_CACHE_KEY, json.dumps(
        {"checked_at": "2026-10-01T00:00:00", "engines": {"openai": {"ok": True, "models": models}}}))


def test_a_gpt5_or_later_model_openai_listed_is_accepted_and_costed_at_the_ceiling_above_every_built_in_rate(isolated_db):
    _listed_by_openai(["gpt-6-luna", "gpt-4", "o3", "gpt-5-codex", "gpt-5-pro"])
    assert te.openai_listed_extra_models() == ["gpt-6-luna"]
    assert te.get_engine("openai", KEY, "gpt-6-luna").model == "gpt-6-luna"
    ceiling = te.OPENAI_EXTRA_MODEL_CEILING
    for m in te.OPENAI_MODELS:
        assert ceiling["input"] > te.PRICING_PER_MILLION_TOKENS[m]["input"]
        assert ceiling["output"] > te.PRICING_PER_MILLION_TOKENS[m]["output"]
    assert te.estimate_cost("gpt-6-luna", 0, 1_000_000) == pytest.approx(ceiling["output"])
    assert te.estimate_cost("gpt-6-luna", 1_000_000, 0) == pytest.approx(ceiling["input"])


def test_a_malformed_provider_cache_adds_nothing_instead_of_crashing(isolated_db):
    for models in (5, True, [{"a": 1}], [["x"]], {"gpt-6-luna": 1}):
        db.set_app_setting(te.PROVIDER_CHECK_CACHE_KEY, json.dumps(
            {"engines": {"openai": {"ok": True, "models": models}}}))
        assert te.openai_listed_extra_models() == []


@pytest.mark.parametrize("model", ["gpt-4", "o3", "gpt-5-codex", "gpt-5-pro", "gpt-9-unlisted"])
def test_listing_does_not_make_older_or_non_chat_or_unlisted_models_usable(isolated_db, model):
    _listed_by_openai(["gpt-6-luna", "gpt-4", "o3", "gpt-5-codex", "gpt-5-pro"])
    with pytest.raises(InvalidInputError):
        te.get_engine("openai", KEY, model)


def test_a_failed_check_adds_nothing(isolated_db):
    db.set_app_setting(te.PROVIDER_CHECK_CACHE_KEY, json.dumps(
        {"engines": {"openai": {"ok": False, "models": ["gpt-6-luna"]}}}))
    assert te.openai_listed_extra_models() == []
