"""Claude (Anthropic) engine."""

from .prompts import build_batch_user_message, build_claude_system_blocks
from .shared import (
    ContentModerationBlocked,
    TruncatedReply,
    make_anthropic_client,
    _add_usage,
    _empty_usage,
    claude_usage,
    request_translations_with_retry,
)


# ---------------------------------------------------------------------------
# Claude (Anthropic)
# ---------------------------------------------------------------------------


class ClaudeEngine:
    name = "claude"
    supports_reference = True

    def __init__(self, api_key: str, model: str = "claude-sonnet-5-5"):
        self.client = make_anthropic_client(api_key)
        self.model = model
        self.last_usage = _empty_usage()

    def build_request_params(self, context: dict, numbered: str) -> dict:
        """One translation request's Messages API params -- shared by
        translate_batch and bulk mode's Message Batches submission, so a
        bulk request is byte-for-byte the prompt a live one would send."""
        return {
            "model": self.model, "max_tokens": 4000,
            "system": build_claude_system_blocks(context),
            "messages": [{"role": "user", "content": build_batch_user_message(context, numbered)}],
        }

    def translate_batch(self, zh_lines, context: dict):
        self.last_usage = _empty_usage()

        def call_model(numbered):
            resp = self.client.messages.create(**self.build_request_params(context, numbered))
            _add_usage(self.last_usage, claude_usage(getattr(resp, "usage", None)))
            # Claude's Messages API sets stop_reason to "refusal"
            # when it declines a request on content-policy grounds -- a
            # real, documented signal, not a guess. Checked before ever
            # falling through to the soft-refusal text heuristic.
            if getattr(resp, "stop_reason", None) == "refusal":
                raise ContentModerationBlocked("claude", "refusal")
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            # Cut off at max_tokens: the pairs that finished are kept and only
            # the missing lines are asked for again.
            cut_off = getattr(resp, "stop_reason", None) in ("max_tokens", "model_context_window_exceeded")
            return TruncatedReply(text) if cut_off else text

        return request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="claude",
                                                line_languages=context.get("line_languages"),
                                                sentence_groups=context.get("sentence_groups"))
