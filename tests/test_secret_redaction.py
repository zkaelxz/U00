"""B-26: every provider key format the app resolves is redacted by both
shared.redact_secrets and diagnostics_report.redact_for_support."""
import pytest

import diagnostics_report
from engine_backends import shared

SECRETS = {
    "hf": "hf_" + "aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3k",
    "groq": "gsk_" + "Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56Qr78St90Uv12Wx34Yz56",
    "deepl_free": "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b:fx",
    "gemini": "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
    "openai": "sk-" + "Ab12Cd34Ef56Gh78Ij90Kl12",
    "openai_proj": "sk-proj-" + "Ab12Cd34Ef56Gh78Ij90Kl12_x-y",
    "anthropic": "sk-ant-api03-" + "Ab12Cd34Ef56Gh78Ij90Kl12",
    "jellyfin_header": 'Token="' + "0123456789abcdef0123456789abcdef" + '"',
    "jellyfin_single_quoted": "Token='" + "0123456789abcdef0123456789abcdef" + "'",
    "jellyfin_unquoted": "Token=" + "0123456789abcdef0123456789abcdef",
    "refresh_token_assign": "refresh_token=" + "0123456789abcdef0123456789abcdef",
    "json_token": '"token":"' + "0123456789abcdef0123456789abcdef" + '"',
    "dict_repr_token": "'access_token': '" + "0123456789abcdef0123456789abcdef" + "'",
    "x_api_key_text": "X-Api-Key: " + "0123456789abcdef0123456789abcdef",
    "apikey_param": "?apikey=" + "0123456789abcdef0123456789abcdef",
    "api_hyphen_key_param": "&api-key=" + "0123456789abcdef0123456789abcdef",
    "apikey_assign": "apikey=" + "0123456789abcdef0123456789abcdef",
    "api_key_param": "?api_key=" + "0123456789abcdef0123456789abcdef",
    "token_param": "&access_token=" + "0123456789abcdef0123456789abcdef",
}

REDACTORS = [shared.redact_secrets, diagnostics_report.redact_for_support]


@pytest.mark.parametrize("redactor", REDACTORS)
@pytest.mark.parametrize("name", sorted(SECRETS))
def test_secret_redacted(redactor, name):
    out = redactor(f"request failed with token {SECRETS[name]} (401)")
    assert SECRETS[name] not in out
    assert "[REDACTED]" in out
    assert "request failed with token" in out


@pytest.mark.parametrize("redactor", REDACTORS)
def test_deepl_pro_key_after_auth_header_redacted(redactor):
    key = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
    out = redactor(f"header DeepL-Auth-Key {key} rejected")
    assert key not in out


@pytest.mark.parametrize("redactor", REDACTORS)
@pytest.mark.parametrize("text", [
    "loaded hf_model from cache",
    "hf_hub_download failed for pyannote",
    "gsk_short and sk-short",
    "job 0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b finished",
    "the Groq engine and the DeepL engine",
    "invalid token: expired",
    "api key rejected by the server",
])
def test_ordinary_text_untouched(redactor, text):
    assert redactor(text) == text


def test_signed_url_and_bare_signature_params_are_stripped_for_support():
    text = diagnostics_report.redact_for_support(
        "GET https://cdn.example.com/a/b?token=abc&sig=xyz failed; X-Amz-Signature=deadbeef "
        "access_token=tok1 sig=s2")
    for leaked in ("abc", "xyz", "deadbeef", "tok1", "s2", "?"):
        assert leaked not in text
    assert "https://cdn.example.com/a/b failed" in text


@pytest.mark.parametrize("text", [
    "GET https://api.example.com/v1?key=AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q failed",
    "Authorization: Bearer abcdefghij0123456789 rejected",
    "sk-" + "Ab12Cd34Ef56Gh78Ij90Kl12" + " in the body",
])
def test_fetch_and_adaptive_modules_use_the_shared_redactor(text):
    from services import safe_fetch
    from sources import adaptive
    expected = shared.redact_secrets(text)
    assert expected != text
    assert safe_fetch.redact_secrets(text) == expected
    assert adaptive.redact_secrets(text) == expected


@pytest.mark.parametrize("redactor", REDACTORS)
def test_an_unclosed_quote_does_not_hide_the_rest_of_the_message(redactor):
    out = redactor('auth failed Token="0123456789abcdef0123456789abcdef then retry in 5 s')
    assert "0123456789abcdef0123456789abcdef" not in out
    assert out.endswith("then retry in 5 s")


@pytest.mark.parametrize("text", ["max_tokens=4096 exceeded", "token=5 is too small"])
def test_short_or_unrelated_token_params_are_left_readable(text):
    # redact_for_support scrubs every credential-looking param on purpose, so only the shared redactor is held to this.
    assert shared.redact_secrets(text) == text
