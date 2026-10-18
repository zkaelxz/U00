"""
engine_backends/vision.py -- one request that sends a picture to an engine and
returns the text written in it (Quick translate's "Read from a screenshot").

Each provider takes the image in its own shape, so this is separate from
call_llm_json, whose messages are text only. DeepSeek's chat API takes no
images and is refused here rather than sent a request it would ignore.
"""
import base64

from .gemini import GeminiEngine
from .llm_tasks import _bounded
from .local import OllamaEngine, _ollama_chat, strip_ollama_thinking
from .openai_compat import OpenAIEngine
from .shared import call_with_backoff, post_json
from .thinking import ollama_chat_with_think

VISION_ENGINES = ("claude", "gemini", "openai", "ollama")

# A subtitle or two; a longer reply means the model is describing the picture.
MAX_REPLY_TOKENS = 600

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


def read_image_text(engine, image: bytes, mime: str, language: str = "") -> tuple:
    """(text, {"input_tokens", "output_tokens"}) for the text in `image`.
    Raises RuntimeError for an engine that takes no images; provider errors
    propagate for the caller to turn into a plain message."""
    prompt = build_prompt(language)
    data = base64.b64encode(image).decode("ascii")
    name = getattr(engine, "name", type(engine).__name__)
    if name not in VISION_ENGINES:
        raise RuntimeError(f"{name} can't read pictures.")
    return _bounded(lambda: _dispatch(engine, prompt, data, mime), engine)


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
    engine._throttle_for_free_tier()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{engine.model}:generateContent"
    body = {"contents": [{"parts": [{"inlineData": {"mimeType": mime, "data": data}},
                                    {"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": MAX_REPLY_TOKENS}}
    reply, _ = call_with_backoff(lambda: post_json(
        url, body, timeout=120, headers={"x-goog-api-key": engine.api_key}, label="Gemini"))
    usage = reply.get("usageMetadata") or {}
    counts = {"input_tokens": usage.get("promptTokenCount", 0) or 0,
              "output_tokens": usage.get("candidatesTokenCount", 0) or 0}
    try:
        parts = reply["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        return "", counts
    return "".join(p.get("text", "") for p in parts).strip(), counts


def _openai(engine, prompt, data, mime):
    counted = {}
    text = call_with_backoff(lambda: engine.chat(
        [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}]}],
        usage_cb=lambda i, o, *_: counted.update(input_tokens=i, output_tokens=o)))
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
