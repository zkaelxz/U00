"""DeepSeek and OpenAI engines (OpenAI-style chat APIs)."""

import json

from .pricing import OPENAI_CHAT_URL, OPENAI_MODELS, openai_listed_extra_models
from .prompts import build_batch_user_message, build_stable_system_text
from .local import strip_ollama_thinking
from .thinking import deepseek_extra_body
from .shared import (
    ContentModerationBlocked,
    SDK_REQUEST_TIMEOUT,
    _add_usage,
    _empty_usage,
    make_openai_client,
    post_json,
    request_translations_with_retry,
)

# An error reply is a short JSON object; only its message is kept.
ERROR_BODY_MAX_BYTES = 64 * 1024


# ---------------------------------------------------------------------------
# DeepSeek (OpenAI-compatible chat API) -- cheap, strong on Chinese
# ---------------------------------------------------------------------------

class DeepSeekEngine:
    name = "deepseek"
    supports_reference = True

    def __init__(self, api_key: str, model: str = "deepseek-flash"):
        self.client = make_openai_client(api_key, base_url="https://api.deepseek.com")
        self.model = model
        self.last_usage = _empty_usage()

    def translate_batch(self, zh_lines, context: dict):
        system_text = build_stable_system_text(context)
        self.last_usage = _empty_usage()

        def call_model(numbered):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": build_batch_user_message(context, numbered)},
                ],
                **deepseek_extra_body(context),
            )
            usage = getattr(resp, "usage", None)
            if usage:
                # DeepSeek reports its automatic prefix-cache hits as
                # prompt_cache_hit_tokens; the OpenAI-standard field is
                # prompt_tokens_details.cached_tokens. Either way it's a
                # subset of prompt_tokens.
                details = getattr(usage, "prompt_tokens_details", None)
                cached = (getattr(usage, "prompt_cache_hit_tokens", None)
                          or getattr(details, "cached_tokens", None) or 0)
                _add_usage(self.last_usage, {
                    "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "cache_read_tokens": cached or 0})
            message = resp.choices[0].message
            # The OpenAI-compatible refusal shape -- content is
            # None/empty and a separate `refusal` field explains why,
            # rather than the requested translation. A real, documented
            # signal, not a guess; guards the bare .content.strip() this
            # replaced, which crashed with a raw AttributeError on this
            # exact shape (None has no .strip()).
            refusal = getattr(message, "refusal", None)
            if not message.content and refusal:
                raise ContentModerationBlocked("deepseek", refusal)
            # Reasoning arrives in a separate `reasoning_content` field that is
            # never read; the strip covers a gateway that inlines it as <think>.
            return strip_ollama_thinking(message.content or "")

        return request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="deepseek",
                                                line_languages=context.get("line_languages"))


# ---------------------------------------------------------------------------
# OpenAI -- plain Chat Completions REST call, key in an Authorization header
# ---------------------------------------------------------------------------

class OpenAIEngine:
    """OpenAI's Chat Completions endpoint called with `requests` (no SDK, so
    no extra dependency). It has no `.client` on purpose: call_llm_json and
    qa._dispatch_chat reach it through chat()."""
    name = "openai"
    supports_reference = True

    def __init__(self, api_key: str, model: str = "gpt-5-mini", url: str = OPENAI_CHAT_URL):
        # OpenAI accepts models (o-series, gpt-4) priced well above anything in
        # PRICING_PER_MILLION_TOKENS; a client-chosen name that reached here would
        # be costed too low (or at $0) and slip past the spending caps. Only the
        # built-in list and GPT-5+ models OpenAI itself listed are taken.
        if model not in OPENAI_MODELS and model not in openai_listed_extra_models():
            from lib.errors import InvalidInputError
            raise InvalidInputError("That model isn't offered for this engine.")
        self.api_key = api_key
        self.model = model
        self.url = url
        self.last_usage = _empty_usage()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def build_request_body(self, messages: list) -> dict:
        return {"model": self.model, "messages": messages}

    def chat(self, messages: list, usage_cb=None) -> str:
        """One Chat Completions call: the reply text. Raises
        ContentModerationBlocked on a refusal or a content_filter stop, and
        requests.HTTPError (message already redacted, response kept so rate
        limits are still recognised) on an HTTP error."""
        data, _ = post_json(self.url, self.build_request_body(messages), timeout=SDK_REQUEST_TIMEOUT,
                            headers=self._headers(), label="OpenAI",
                            error_detail_bytes=ERROR_BODY_MAX_BYTES)
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        parsed = {"input_tokens": usage.get("prompt_tokens") or 0,
                  "output_tokens": usage.get("completion_tokens") or 0,
                  "cache_read_tokens": cached}
        _add_usage(self.last_usage, parsed)
        if usage_cb:
            usage_cb(parsed["input_tokens"], parsed["output_tokens"])
        choices = data.get("choices") or []
        if not choices:
            raise ContentModerationBlocked("openai", "no choices returned")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if not content and message.get("refusal"):
            raise ContentModerationBlocked("openai", message["refusal"])
        if not content and choices[0].get("finish_reason") == "content_filter":
            raise ContentModerationBlocked("openai", "content_filter")
        return content.strip()

    def translate_batch(self, zh_lines, context: dict):
        system_text = build_stable_system_text(context)
        self.last_usage = _empty_usage()

        def call_model(numbered):
            return self.chat([
                {"role": "system", "content": system_text},
                {"role": "user", "content": build_batch_user_message(context, numbered)},
            ])

        return request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="openai",
                                                line_languages=context.get("line_languages"))
