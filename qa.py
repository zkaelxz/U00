"""
qa.py -- in-app Q&A grounded in a drama's own transcript+translation.
Ask things like "who is Character X to Character Y at this point?" or
"did I translate this consistently?" without leaving the app. Useful
for proofing continuity and catching context you might have missed --
not a general chatbot, it only knows what's in the lines you give it.
"""

from translate_engines import call_with_backoff, GeminiEngine, OllamaEngine, _estimate_ollama_num_ctx


def ask_about_drama(question: str, lines, drama_meta: dict, engine, max_lines: int = 300,
                     chat_history=None):
    """
    lines: the drama's Line objects (zh + en) to use as grounding context.
    Capped at max_lines to keep the prompt a reasonable size -- for a
    very long drama, pass just the current Reader page's lines plus a
    summary, rather than the whole thing.
    chat_history: optional list of {"role": "user"|"assistant", "content": str}
    for a multi-turn conversation; each call is still stateless on the
    engine side, so history is replayed as context every time.
    """
    context_lines = lines[:max_lines]
    transcript = "\n".join(
        f"[{ln.idx}] ({ln.speaker or '?'}) {ln.zh} -> {ln.en}" for ln in context_lines
    )
    meta_bits = ", ".join(f"{k}: {v}" for k, v in drama_meta.items()
                          if k in ("title_en", "title_zh", "author", "summary") and v)

    system_prompt = (
        "You answer questions about a specific drama/novel using ONLY the transcript "
        "provided below -- don't invent plot details that aren't there, and say so "
        "plainly if the transcript doesn't cover something asked about. Line numbers "
        "in brackets refer to the transcript so the person can find the line themselves.\n\n"
        + (f"Drama info: {meta_bits}\n\n" if meta_bits else "")
        + f"Transcript (Chinese -> English), lines 0-{len(context_lines)-1}"
        + (f" of {len(lines)} total -- ask about a later part if it's not in this range"
           if len(lines) > max_lines else "")
        + f":\n\n{transcript}"
    )

    messages = list(chat_history or [])
    messages.append({"role": "user", "content": question})

    return _dispatch_chat(system_prompt, messages, engine)


def _dispatch_chat(system_prompt: str, messages: list, engine, max_tokens: int = 1000) -> str:
    """Shared multi-engine chat dispatch, factored out of ask_about_drama
    so app_help.ask_about_app (Step 18b) can reuse the exact same
    Claude/OpenAI-shaped/Gemini/Ollama request handling -- only the
    system_prompt/grounding differs per caller, the dispatch mechanics
    (auth shape, free-tier throttling, Ollama's num_ctx estimate) don't."""
    if not getattr(engine, "supports_reference", False):
        return "This engine doesn't support free-form Q&A -- use Claude, DeepSeek, or Ollama."

    client = getattr(engine, "client", None)
    if client is not None and hasattr(client, "messages"):
        resp = call_with_backoff(lambda: client.messages.create(
            model=engine.model, max_tokens=max_tokens, system=system_prompt, messages=messages,
        ))
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    if client is not None:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        resp = call_with_backoff(lambda: client.chat.completions.create(
            model=engine.model, messages=full_messages,
        ))
        return resp.choices[0].message.content.strip()
    if isinstance(engine, GeminiEngine):
        # Gemini has no .client (its translate_batch calls the REST endpoint
        # directly) -- same request shape, folding the running chat history
        # into one prompt since generateContent's own multi-turn "contents"
        # format isn't worth the extra plumbing for this one caller.
        import requests
        engine._throttle_for_free_tier()
        history_text = "\n\n".join(
            f"{'You' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in messages)
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{engine.model}:generateContent")
        resp = call_with_backoff(lambda: requests.post(
            url, headers={"x-goog-api-key": engine.api_key},
            json={"systemInstruction": {"parts": [{"text": system_prompt}]},
                  "contents": [{"parts": [{"text": history_text}]}]}, timeout=120))
        resp.raise_for_status()
        try:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            return "This engine doesn't support chat-style Q&A."
    if isinstance(engine, OllamaEngine):
        # Same reasoning as Gemini above: no .client, so it fell through
        # to the generic decline message and Q&A silently didn't work
        # with Ollama at all.
        import requests
        history_text = "\n\n".join(
            f"{'You' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in messages)
        resp = call_with_backoff(lambda: requests.post(f"{engine.base_url}/api/chat", json={
            "model": engine.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": history_text},
            ],
            "stream": False,
            "options": {"num_ctx": _estimate_ollama_num_ctx(system_prompt, history_text)},
        }, timeout=300))
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    return "This engine doesn't support chat-style Q&A."
