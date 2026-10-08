"""Switching a model's reasoning off for latency-sensitive callers (Live).

A caller opts in with context["reply_without_thinking"]; batch translation
never sets it, so its requests stay exactly as they were."""

# Engines whose request has a documented switch. Mirrored by
# THINKING_SWITCH_ENGINES in frontend/src/api/live.ts (a test pins the two).
NO_THINKING_ENGINES = ("deepseek", "ollama")

# Thinking is on by default at DeepSeek, so it must be disabled explicitly.
DEEPSEEK_NO_THINKING_BODY = {"thinking": {"type": "disabled"}}

# (base_url, model) pairs whose server refused the `think` field. Ollama
# rejects it for a model without the thinking capability, and re-sending it on
# every cue would double each request.
_think_refused = set()


def wants_no_thinking(context: dict) -> bool:
    return bool(context.get("reply_without_thinking"))


def deepseek_extra_body(context: dict) -> dict:
    """Extra keyword arguments for the SDK call; empty unless asked."""
    return {"extra_body": DEEPSEEK_NO_THINKING_BODY} if wants_no_thinking(context) else {}


def ollama_chat_no_thinking(chat, base_url: str, payload: dict) -> dict:
    """Runs chat(base_url, payload) with `think: false` added.

    Asking Ollama (/api/show) which models can think would cost a request per
    model, so the field is sent and a 400 is taken as "this model can't think":
    the request is repeated once without it and that is remembered. A model that
    thinks without being asked is still handled by the caller's <think>
    stripping, and the separate `thinking` reply field is never read."""
    import requests
    key = (base_url, str(payload.get("model") or ""))
    if key in _think_refused:
        return chat(base_url, payload)
    try:
        return chat(base_url, {**payload, "think": False})
    except requests.HTTPError as exc:
        if getattr(exc.response, "status_code", None) != 400:
            raise
        _think_refused.add(key)
        return chat(base_url, payload)
