"""
services/engine_failure_service.py: a failed one-question AI call names a
cause the user can act on (and its engine) instead of one generic
"unavailable", and never echoes the provider's text or a key. Fully mocked.
"""
import pytest
import requests

pytest.importorskip("fastapi")

import translate_engines
from engine_backends.shared import LLMTaskTimeout
from lib import http
from services import engine_failure_service as efs
from services import metadata_service

SECRET = "sk-ant-api03-SECRETSECRETSECRET"


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} for key {SECRET}", response=resp)


class _SdkError(Exception):
    def __init__(self, status):
        super().__init__(f"Error code: {status} {SECRET}")
        self.status_code = status


class APIConnectionError(Exception):  # named like openai's, which has no status
    pass


class APITimeoutError(APIConnectionError):  # openai's timeout subclasses its connection error
    pass


@pytest.mark.parametrize("exc,reason", [
    (translate_engines.FreeTierDailyLimitReached("used up"), "daily_limit"),
    (translate_engines.ContentModerationBlocked("gemini", "SAFETY"), "blocked"),
    (LLMTaskTimeout("late"), "timed_out"),
    (requests.ReadTimeout(SECRET), "timed_out"),
    (http.ResponseTooSlow(), "timed_out"),
    (APITimeoutError(SECRET), "timed_out"),
    (requests.ConnectTimeout(SECRET), "unreachable"),
    (_http_error(401), "key_rejected"),
    (_http_error(403), "key_rejected"),
    (_SdkError(401), "key_rejected"),
    (_http_error(429), "rate_limited"),
    (_SdkError(429), "rate_limited"),
    (_http_error(503), "unavailable"),
    (http.FetchError(), "unreachable"),
    (requests.ConnectionError(SECRET), "unreachable"),
    (APIConnectionError(SECRET), "unreachable"),
    (RuntimeError(SECRET), "failed"),
    (_http_error(400), "failed"),
])
def test_each_cause_gets_its_reason_and_a_fixed_sentence(exc, reason):
    err = efs.engine_failure(exc, "gemini", "The lookup is unavailable.")
    assert err.details == {"reason": reason, "engine": "Gemini"}
    assert SECRET not in str(err)
    if reason == "failed":
        assert str(err) == "The lookup is unavailable."
    else:
        assert "Gemini" in str(err)


def test_an_ollama_failure_keeps_its_own_sentence_and_reason():
    exc = translate_engines.OllamaUnavailableError(
        "ollama_unreachable", "Ollama isn't running. Start it, or pick another engine.")
    err = efs.engine_failure(exc, "ollama", "x")
    assert err.details == {"reason": "ollama_unreachable"}
    assert str(err) == "Ollama isn't running. Start it, or pick another engine."


def test_an_unknown_engine_is_named_generically():
    assert str(efs.engine_failure(_http_error(429), "nope", "x")).startswith("The AI engine")


@pytest.mark.parametrize("call", ["autofill", "romanize"])
def test_metadata_autofill_and_romanize_name_the_cause(isolated_db, monkeypatch, call):
    import db
    drama = db.create_drama(title_en="T", source_language="zh")
    db.update_drama(drama, author="Someone")
    monkeypatch.setattr(metadata_service.settings_service, "resolve_key", lambda *a, **kw: "k")

    class Engine:
        supports_reference = True
    monkeypatch.setattr(metadata_service.translate_engines, "get_engine",
                        lambda *a, **kw: Engine())

    def refused(*a, **kw):
        raise _http_error(401)
    monkeypatch.setattr(metadata_service.metadata_lookup, "extract_metadata_llm", refused)
    import translation_guide
    monkeypatch.setattr(translation_guide, "romanize_metadata", refused)
    with pytest.raises(metadata_service.DependencyUnavailableError) as e:
        if call == "autofill":
            metadata_service.autofill_suggestion(drama, page_text="page", engine_name="gemini")
        else:
            metadata_service.romanize_credits(drama, engine_name="gemini")
    assert e.value.details == {"reason": "key_rejected", "engine": "Gemini"}
    assert SECRET not in str(e.value)
