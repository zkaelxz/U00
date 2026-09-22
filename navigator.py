"""
navigator.py -- helps you navigate a site in a language you don't
read: fetches a public page's visible text/menu labels, translates
them, and asks the LLM for plain-language step-by-step guidance for a
stated goal (e.g. "find this title's audio drama section" or "get to
the episode list"). This only ever describes how to use a site's own
public interface -- it doesn't log in, purchase, or fetch anything on
your behalf, and it never touches the underlying creative content.
"""

import re
import json


def fetch_visible_labels(url: str, timeout: int = 20, max_labels: int = 150):
    """Pulls short visible text snippets (menu items, buttons, headings)
    from a page -- the kind of thing that makes up site navigation --
    rather than full body text. Requires `pip install requests
    beautifulsoup4`."""
    import requests
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (compatible; SiteNavigator/1.0)"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    candidates = soup.find_all(["a", "button", "nav", "h1", "h2", "h3", "label"])
    labels = []
    seen = set()
    for el in candidates:
        text = el.get_text(strip=True)
        if text and 1 <= len(text) <= 40 and text not in seen:
            labels.append(text)
            seen.add(text)
        if len(labels) >= max_labels:
            break
    return labels


def translate_labels(labels, target_language: str, engine):
    """Translates a list of short UI labels into target_language.
    Returns {original: translated}."""
    from translate_engines import call_with_backoff
    if not labels:
        return {}
    if not getattr(engine, "supports_reference", False):
        # Pure-MT engines can still handle this via their normal batch call
        translations = call_with_backoff(lambda: engine.translate_batch(labels, {}))
        return dict(zip(labels, translations))

    numbered = "\n".join(f"{i+1}. {l}" for i, l in enumerate(labels))
    prompt = (
        f"Translate these website UI labels (menu items, buttons, headings) into "
        f"{target_language}. Keep translations short, matching the style of UI text "
        "(not full sentences). Return ONLY a JSON array of strings, one per input label, "
        "in the same order. No preamble, no markdown fences.\n\n" + numbered
    )
    if hasattr(engine, "client") and hasattr(engine.client, "messages"):
        resp = call_with_backoff(lambda: engine.client.messages.create(
            model=engine.model, max_tokens=2000,
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
        translated = json.loads(text)
    except json.JSONDecodeError:
        translated = labels
    return dict(zip(labels, translated))


def generate_navigation_steps(site_name_or_url: str, goal: str, target_language: str,
                               engine, translated_labels: dict = None):
    """Produces plain-language, numbered steps for accomplishing `goal`
    on the named/linked site, in target_language. Uses the LLM's
    general knowledge of common platform layouts, plus the translated
    on-page labels if provided (grounds the steps in what's actually
    on the current page rather than a guess). This describes how to
    use the site's own interface only -- it does not perform any
    actions, log in, or fetch content on your behalf."""
    if not getattr(engine, "supports_reference", False):
        return "This engine doesn't support free-form instructions -- use Claude, DeepSeek, or Ollama for this feature."

    labels_block = ""
    if translated_labels:
        pairs = "\n".join(f"- {orig} -> {trans}" for orig, trans in list(translated_labels.items())[:80])
        labels_block = f"\n\nLabels currently visible on the page (original -> translated):\n{pairs}"

    prompt = (
        f"I don't read the language {site_name_or_url} is in. My goal: {goal}\n"
        f"Give me clear, numbered, step-by-step instructions in {target_language} for how to "
        "do this using the site's normal interface -- e.g. what to click, search, or scroll to. "
        "Describe navigation only; don't tell me to log in with specific credentials or take any "
        "action on my behalf. If you're not certain about this specific site's current layout, say so "
        "and give your best general guidance based on how sites like this typically work."
        + labels_block
    )
    from translate_engines import call_with_backoff
    if hasattr(engine, "client") and hasattr(engine.client, "messages"):
        resp = call_with_backoff(lambda: engine.client.messages.create(
            model=engine.model, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        ))
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    elif hasattr(engine, "client"):
        resp = call_with_backoff(lambda: engine.client.chat.completions.create(
            model=engine.model, messages=[{"role": "user", "content": prompt}],
        ))
        return resp.choices[0].message.content.strip()
    return ""
