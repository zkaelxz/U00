"""Switching a model's reasoning off or on in the request.

Live sets context["reply_without_thinking"]. Translation runs get it from
build_translation_context: off unless the run asks to think harder, which sets
context["reply_with_thinking"] and sends the "on" form explicitly. A context
with neither key sends no field and the model does what it does by default."""

import argparse

# Engines whose request has a documented switch. Mirrored by
# THINKING_SWITCH_ENGINES in frontend/src/api/live.ts (a test pins the two).
NO_THINKING_ENGINES = ("deepseek", "ollama")

# Thinking is on by default at DeepSeek, so it must be disabled explicitly.
DEEPSEEK_NO_THINKING_BODY = {"thinking": {"type": "disabled"}}
DEEPSEEK_THINKING_BODY = {"thinking": {"type": "enabled"}}

# (base_url, model) pairs whose server refused the `think` field. Ollama
# rejects it for a model without the thinking capability, and re-sending it on
# every cue would double each request.
_think_refused = set()


def title_thinking(drama_meta) -> bool:
    """Whether a run for this title asks the model to think. Read from the
    database rather than the caller's drama dict, which can predate the choice
    saved when this run was accepted (e.g. `cli.py translate --thinking`)."""
    drama_id = (drama_meta or {}).get("id")
    if not isinstance(drama_id, int):
        return False
    import db
    return bool((db.get_drama(drama_id) or {}).get("translate_thinking"))


def think_flag(parser):
    """Adds --thinking / --no-thinking to a CLI subparser; returns it. The
    choice is saved for the title like the other Translate toggles."""
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None,
                        help="Think before answering (DeepSeek/Ollama): slower, costs more.")
    return parser


def thinking_choice(context: dict):
    """False = switch it off, True = ask for it, None = send no field.
    Off wins if both keys are set: a stray flag must not make a run cost more."""
    if context.get("reply_without_thinking"):
        return False
    return True if context.get("reply_with_thinking") else None


def deepseek_extra_body(context: dict) -> dict:
    """Extra keyword arguments for the SDK call; empty unless a choice was made."""
    choice = thinking_choice(context)
    if choice is None:
        return {}
    return {"extra_body": DEEPSEEK_THINKING_BODY if choice else DEEPSEEK_NO_THINKING_BODY}


def _refuses_think(exc) -> bool:
    """Whether a 400 says the `think` field is what Ollama objects to
    (e.g. "... does not support thinking"); any other 400 is a different fault."""
    # The streamed response is already closed by the time it gets here, so the
    # request layer attaches the body it read; .text is the fallback for
    # callers that raise with a buffered response.
    body = getattr(exc, "body_text", None)
    if body is None:
        try:
            body = exc.response.text or ""
        except Exception:
            return False
    return "think" in body.lower()


def ollama_chat_with_think(chat, base_url: str, payload: dict, think: bool = False) -> dict:
    """Runs chat(base_url, payload) with `think` set to the given value.

    Asking Ollama (/api/show) which models can think would cost a request per
    model, so the field is sent. A 400 whose body names thinking means "this
    model can't think": the request is repeated once without the field and that
    is remembered. Any other 400 is also retried once without it, but not
    remembered, so a one-off bad request can't turn the switch off until
    restart. A model that thinks without being asked is still handled by the
    caller's <think> stripping, and the `thinking` reply field is never read."""
    import requests
    key = (base_url, str(payload.get("model") or ""))
    if key in _think_refused:
        return chat(base_url, payload)
    try:
        return chat(base_url, {**payload, "think": bool(think)})
    except requests.HTTPError as exc:
        if getattr(exc.response, "status_code", None) != 400:
            raise
        if _refuses_think(exc):
            _think_refused.add(key)
        return chat(base_url, payload)
