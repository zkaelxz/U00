"""
engine_backends/vision.py -- one request that sends a picture to an engine and
returns the text written in it (Quick translate's "Read from a screenshot").

Each provider takes the image in its own shape, so this is separate from
call_llm_json, whose messages are text only. DeepSeek's chat API takes no
images and is refused here rather than sent a request it would ignore.
"""
import base64

from .gemini import GeminiEngine, gemini_reply
from .llm_tasks import _bounded
from .local import OllamaEngine, _ollama_chat, strip_ollama_thinking
from .openai_compat import OpenAIEngine
from .shared import call_with_backoff, post_json
from .thinking import ollama_chat_with_think

VISION_ENGINES = ("claude", "gemini", "openai", "ollama")

# A subtitle or two; a longer reply means the model is describing the picture.
MAX_REPLY_TOKENS = 600
# OpenAI's reasoning tokens count inside max_completion_tokens, so a reasoning
# model needs room beyond the visible reply or it answers with nothing.
OPENAI_MAX_COMPLETION_TOKENS = 1500

_LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}

# "Do not translate" is the point: the owner wants the raw characters to check
# them, and a model asked to be helpful otherwise answers in English.
_PROMPT = (
    "This picture is a screenshot of a video frame or a page. Copy out the {language}text "
    "written in it, exactly as it appears, character by character. Subtitles burned into the "
    "video are the text to copy. One subtitle per line. Do not translate, explain, romanize or "
    "add anything. If there is no readable text, reply with nothing at all.")


def build_prompt(language: str = "") -> str:
    name = _LANGUAGE_NAMES.get(language)
    return _PROMPT.format(language=f"{name} " if name else "")


def read_image_text(engine, image: bytes, mime: str, language: str = "", on_done=None) -> tuple:
    """(text, {"input_tokens", "output_tokens"}) for the text in `image`.
    Raises RuntimeError for an engine that takes no images; provider errors
    propagate for the caller to turn into a plain message.

    on_done(usage) runs on the request's own thread when the provider call
    ends, with zero counts when it failed. The deadline can abandon a request
    that is still running and billed, so the caller logs from here rather than
    after this function returns."""
    prompt = build_prompt(language)
    data = base64.b64encode(image).decode("ascii")
    name = getattr(engine, "name", type(engine).__name__)
    if name not in VISION_ENGINES:
        raise RuntimeError(f"{name} can't read pictures.")

    def run():
        usage = {}
        try:
            text, usage = _dispatch(engine, prompt, data, mime)
            return text, usage
        finally:
            if on_done is not None:
                on_done(usage)

    return _bounded(run, engine)


def _dispatch(engine, prompt: str, data: str, mime: str) -> tuple:
    client = getattr(engine, "client", None)
    if client is not None and hasattr(client, "messages"):
        return _claude(engine, client, prompt, data, mime)
    if isinstance(engine, GeminiEngine):
        return _gemini(engine, prompt, data, mime)
    if isinstance(engine, OpenAIEngine):
        return _openai(engine, prompt, data, mime)
    if isinstance(engine, OllamaEngine):
        return _ollama(engine, prompt, data)
    raise RuntimeError(f"{getattr(engine, 'name', type(engine).__name__)} can't read pictures.")


def _claude(engine, client, prompt, data, mime):
    resp = call_with_backoff(lambda: client.messages.create(
        model=engine.model, max_tokens=MAX_REPLY_TOKENS,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}},
            {"type": "text", "text": prompt}]}]))
    usage = getattr(resp, "usage", None)
    return ("".join(b.text for b in resp.content if b.type == "text").strip(),
            {"input_tokens": getattr(usage, "input_tokens", 0) or 0,
             "output_tokens": getattr(usage, "output_tokens", 0) or 0})


def _gemini(engine, prompt, data, mime):
    body = {"contents": [{"parts": [{"inlineData": {"mimeType": mime, "data": data}},
                                    {"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": MAX_REPLY_TOKENS}}
    reply, usage = call_with_backoff(lambda: engine.generate(body, post=post_json))
    counts = {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}
    text, _finish_reason = gemini_reply(reply)
    return text, counts


def _openai(engine, prompt, data, mime):
    counted = {}
    text = call_with_backoff(lambda: engine.chat(
        [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}]}],
        usage_cb=lambda i, o, *_: counted.update(input_tokens=i, output_tokens=o),
        extra={"max_completion_tokens": OPENAI_MAX_COMPLETION_TOKENS, "reasoning_effort": "low"}))
    return str(text), {"input_tokens": counted.get("input_tokens", 0),
                       "output_tokens": counted.get("output_tokens", 0)}


def _ollama(engine, prompt, data):
    payload = {"model": engine.model, "stream": False,
               "messages": [{"role": "user", "content": prompt, "images": [data]}],
               "options": {"num_ctx": engine.num_ctx_for(4096), "num_predict": MAX_REPLY_TOKENS}}
    reply = ollama_chat_with_think(_ollama_chat, engine.base_url, payload, False)
    return (strip_ollama_thinking((reply.get("message") or {}).get("content") or "").strip(),
            {"input_tokens": reply.get("prompt_eval_count") or 0,
             "output_tokens": reply.get("eval_count") or 0})
