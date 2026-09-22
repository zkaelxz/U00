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

import re
import json


def fetch_page_text(url: str, timeout: int = 20, allow_render: bool = True) -> str:
    """Fetches a public page's visible text. Routes through page_fetch so
    JavaScript-rendered pages are handled rather than silently returning
    an empty shell. Raises RuntimeError with an explanation when the page
    can't be read, instead of returning nothing and looking like an
    empty page."""
    import page_fetch
    result = page_fetch.smart_fetch(url, allow_render=allow_render, timeout=timeout)
    if result["method"] == "failed" or (result["needs_manual"] and not result["text"].strip()):
        raise RuntimeError(result["message"])
    return result["text"]


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
    from translate_engines import call_with_backoff
    if hasattr(engine, "client") and hasattr(engine.client, "messages"):
        resp = call_with_backoff(lambda: engine.client.messages.create(
            model=engine.model, max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        ))
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
    elif hasattr(engine, "client"):
        resp = call_with_backoff(lambda: engine.client.chat.completions.create(
            model=engine.model, messages=[{"role": "user", "content": prompt}],
        ))
        text = resp.choices[0].message.content.strip()
    else:
        return {}

    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if v}


def lookup_metadata(url: str, engine, allow_render: bool = True):
    """One-shot helper: fetch + extract. Returns (metadata_dict, status)
    where status explains what happened -- so the caller can tell the
    difference between 'the page had no metadata' and 'we couldn\'t
    actually read the page', which used to look identical."""
    import page_fetch
    result = page_fetch.smart_fetch(url, allow_render=allow_render)
    if not result["text"].strip():
        return {}, {"ok": False, "message": result["message"],
                    "needs_manual": result["needs_manual"]}
    meta = extract_metadata_llm(result["text"], engine)
    if not meta:
        return {}, {"ok": False, "needs_manual": result["needs_manual"],
                    "message": ("Read the page but found no metadata in it. "
                                + result["message"])}
    return meta, {"ok": True, "message": result["message"], "needs_manual": False}


def lookup_metadata_from_text(page_text: str, engine):
    """For the manual-paste path: you copy the page text yourself, we
    extract from it. Always works, since it skips fetching entirely."""
    meta = extract_metadata_llm(page_text, engine)
    return meta, {"ok": bool(meta), "needs_manual": False,
                  "message": "Extracted from pasted text." if meta
                             else "No metadata found in the pasted text."}
