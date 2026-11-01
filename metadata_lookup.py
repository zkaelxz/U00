"""
metadata_lookup.py -- auto-populates drama metadata (title, author,
studio, director, voice actors, summary) from a public listing/info
page you paste a URL to (a JJWXC book page, a Fanjiao show page, a
MyDramaList entry, etc.).

This only ever extracts bibliographic/descriptive metadata -- the same
class of information a library catalog or Goodreads entry holds
(title, author's name, a synopsis, a cast list). It never fetches or
stores the underlying creative work (chapters, audio, video) itself.
That's still entirely on you to source legitimately, same as always --
this just saves you retyping a title and summary by hand.
"""



def extract_metadata_llm(page_text: str, engine, max_chars: int = 6000):
    """Asks the translation engine's underlying LLM to pull structured
    bibliographic fields out of a page's visible text. Returns a dict
    with whatever fields it could confidently find -- always review
    before saving, since page layouts vary and it can miss or misread
    fields on unfamiliar sites."""
    if not getattr(engine, "supports_reference", False):
        return {}

    snippet = page_text[:max_chars]
    prompt = (
        "Below is the visible text scraped from a webpage listing a Chinese/Japanese/"
        "Korean audio drama, novel, or manhwa/manga. Extract only bibliographic metadata "
        "-- title, author, studio/publisher, director, voice actors, and a short summary. "
        "Do not include any long passages of the actual story/chapter content, only "
        "cataloging information.\n\n"
        'Return ONLY a JSON object with these keys (use null for anything not found): '
        '{"title_en": "...", "title_zh": "...", "author": "...", "studio": "...", '
        '"director": "...", "voice_actors": "comma, separated, names", "summary": "1-3 sentences"}. '
        "No preamble, no markdown fences.\n\n" + snippet
    )
    from engine_backends.shared import extract_first_json_value, load_json_reply
    from engine_backends.llm_tasks import call_llm_json
    text = call_llm_json(engine, prompt, max_tokens=1000, fallback=None)
    if text is None:
        return {}

    data = load_json_reply(text, None)
    if data is None:  # models often wrap the object in a sentence
        data = extract_first_json_value(text)
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if v}
