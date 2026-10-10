"""
bulk_providers.py -- the batch-API clients behind bulk_translate: submit,
poll, read results and cancel for Claude Message Batches and the Gemini
Batch API.
"""
import json

import translate_engines
from lib import http

# A finished Gemini batch carries every result inline in one reply, and
# Gemini accepts up to 20 MB of inline requests, so the reply can be large.
BATCH_RESPONSE_MAX_BYTES = 64 * 1024 * 1024
BATCH_READ_DEADLINE_SECONDS = 300


class BulkAuthError(Exception):
    """The provider refused our credentials while polling (the key was
    rotated or revoked after submission) -- shown on the Bulk jobs panel
    instead of retried forever."""


class ClaudeBatchProvider:
    """Anthropic Message Batches through the official SDK."""

    def __init__(self, engine):
        self.engine = engine
        self.client = engine.client

    def build_request(self, key: str, context: dict, numbered: str) -> dict:
        return {"custom_id": key, "params": self.engine.build_request_params(context, numbered)}

    def build_prompt_request(self, key: str, prompt: str, max_tokens: int = 3000) -> dict:
        """A plain single-user-message request, no system prompt
        and no glossary/style context -- the same shape call_llm_json's
        own Claude branch sends, used by flag/consistency/emotion/
        translation-notes and Reflect's own three passes, none of which
        are the "translate with full context" request build_request
        above is for."""
        return {"custom_id": key,
                "params": {"model": self.engine.model, "max_tokens": max_tokens,
                          "messages": [{"role": "user", "content": prompt}]}}

    def _call(self, fn):
        import anthropic
        try:
            return fn()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise BulkAuthError(translate_engines.redact_secrets(str(e))) from e

    def submit(self, requests_: list) -> str:
        return self._call(lambda: self.client.messages.batches.create(requests=requests_)).id

    def poll(self, batch_id: str) -> str:
        batch = self._call(lambda: self.client.messages.batches.retrieve(batch_id))
        return "ended" if batch.processing_status == "ended" else "pending"

    def results(self, batch_id: str):
        """Yields (request_key, text or None, usage dict, error or None)."""
        for r in self._call(lambda: self.client.messages.batches.results(batch_id)):
            if r.result.type == "succeeded":
                msg = r.result.message
                text = "".join(b.text for b in msg.content if b.type == "text").strip()
                yield r.custom_id, text, translate_engines.claude_usage(msg.usage), None
            else:
                yield r.custom_id, None, {}, r.result.type

    def cancel(self, batch_id: str):
        self._call(lambda: self.client.messages.batches.cancel(batch_id))


class GeminiBatchProvider:
    """Gemini Batch API over REST, like GeminiEngine itself -- key in the
    x-goog-api-key header, never the URL."""
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, engine):
        self.engine = engine

    def build_request(self, key: str, context: dict, numbered: str) -> dict:
        return {"request": self.engine.build_request_body(context, numbered),
                "metadata": {"key": key}}

    def build_prompt_request(self, key: str, prompt: str, max_tokens: int = 3000) -> dict:
        """See ClaudeBatchProvider.build_prompt_request's own
        docstring -- same plain-prompt shape, no systemInstruction."""
        return {"request": {"contents": [{"parts": [{"text": prompt}]}]},
                "metadata": {"key": key}}

    def _send(self, method, url, timeout, **kw):
        # guard=None: fixed vendor URL, no redirects.
        try:
            resp = http.request(method, url, headers={"x-goog-api-key": self.engine.api_key}, timeout=timeout, guard=None,
                max_error_bytes=4096, max_bytes=BATCH_RESPONSE_MAX_BYTES,
                deadline=BATCH_READ_DEADLINE_SECONDS, **kw)
        except http.FetchError as e:
            raise RuntimeError(f"Gemini batch request failed: {e}")
        if resp.status in (401, 403):
            raise BulkAuthError(f"Gemini refused the API key (HTTP {resp.status}).")
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.loads(resp.body)

    def submit(self, requests_: list) -> str:
        return self._send(
            "POST", f"{self.BASE}/models/{self.engine.model}:batchGenerateContent", 120,
            json={"batch": {"display_name": "baihe-bulk-translation",
                            "input_config": {"requests": {"requests": requests_}}}})["name"]

    def _get(self, batch_id: str) -> dict:
        return self._send("GET", f"{self.BASE}/{batch_id}", 60)

    @staticmethod
    def _state(data: dict) -> str:
        for holder in (data, data.get("metadata") or {}, data.get("response") or {}):
            state = holder.get("state")
            if state:
                return state
        return ""

    def poll(self, batch_id: str) -> str:
        data = self._get(batch_id)
        state = self._state(data)
        if state.endswith("SUCCEEDED"):
            return "ended"
        if state.endswith(("FAILED", "EXPIRED", "CANCELLED")):
            return "failed:" + state
        return "ended" if data.get("done") else "pending"

    @staticmethod
    def _inlined(data: dict) -> list:
        resp = data.get("response") or {}
        for holder in (resp, resp.get("output") or {}):
            inl = holder.get("inlinedResponses")
            if isinstance(inl, dict):
                inl = inl.get("inlinedResponses")
            if isinstance(inl, list):
                return inl
        return []

    def results(self, batch_id: str):
        for item in self._inlined(self._get(batch_id)):
            # A response is only ever attributed through the key we sent
            # with its request -- never by its position in the list.
            key = (item.get("metadata") or {}).get("key")
            if item.get("error") or not item.get("response"):
                yield key, None, {}, json.dumps(item.get("error") or "no response")
                continue
            r = item["response"]
            try:
                text = r["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError, TypeError):
                yield key, None, {}, "empty response"
                continue
            yield key, text, translate_engines.gemini_usage(r.get("usageMetadata")), None

    def cancel(self, batch_id: str):
        self._send("POST", f"{self.BASE}/{batch_id}:cancel", 60, json={})


def make_provider(engine_choice: str, engine):
    if engine_choice == "claude":
        return ClaudeBatchProvider(engine)
    if engine_choice == "gemini":
        return GeminiBatchProvider(engine)
    return None
