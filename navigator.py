"""
navigator.py -- helps you navigate a site in a language you don't
read: translates a public page's visible text/menu labels and asks the
LLM for plain-language step-by-step guidance for a stated goal (e.g. "find this title's audio drama section" or "get to
the episode list"). This only ever describes how to use a site's own
public interface -- it doesn't log in, purchase, or fetch anything on
your behalf, and it never touches the underlying creative content.
"""


def translate_labels(labels, target_language: str, engine):
    """Translates a list of short UI labels into target_language.
    Returns {original: translated}; a label the model didn't return a
    translation for is left out rather than guessed at. Results are
    matched back by id, never by list position."""
    from translate_engines import (call_with_backoff, call_llm_json,
                                   request_translations_with_retry)
    if not labels:
        return {}
    if not getattr(engine, "supports_reference", False):
        # Pure-MT engines can still handle this via their normal batch call
        translations = call_with_backoff(lambda: engine.translate_batch(labels, {}))
        return {l: t for l, t in zip(labels, translations) if t}

    def call_model(numbered: str) -> str:
        prompt = (
            f"Translate these website UI labels (menu items, buttons, headings) into "
            f"{target_language}. Keep translations short, matching the style of UI text "
            "(not full sentences). Return ONLY a JSON object mapping each label's number "
            "(as a string) to its translation, e.g. {\"1\": \"Home\", \"2\": \"Search\"}. "
            "No preamble, no markdown fences.\n\n" + numbered
        )
        return call_llm_json(engine, prompt, max_tokens=2000, fallback="{}")

    translations = request_translations_with_retry(labels, None, call_model)
    return {l: t for l, t in zip(labels, translations) if t}


def generate_navigation_steps(site_name_or_url: str, goal: str, target_language: str,
                               engine, translated_labels: dict = None):
    """Produces plain-language, numbered steps for accomplishing `goal`
    on the named/linked site, in target_language. Uses the LLM's
    general knowledge of common platform layouts, plus the translated
    on-page labels if provided (grounds the steps in what's actually
    on the current page rather than a guess). This describes how to
    use the site's own interface only -- it does not perform any
    actions, log in, or fetch content on your behalf."""
    from translate_engines import call_llm_json
    if not getattr(engine, "supports_reference", False):
        return "This engine doesn't support free-form instructions -- use Claude, DeepSeek, Gemini, or Ollama for this feature."

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
    return call_llm_json(engine, prompt, max_tokens=1500, fallback="")
