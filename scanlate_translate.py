"""
scanlate_translate.py -- id-keyed translation of a comic page's text regions.

Chunks a page's regions into engine calls, matches the answers back by id, and
carries a short context string from page to page.
"""

from scanlate_regions import region_excluded_from_auto


_MAX_CONTEXT_CHARS = 1000

# A dense page in one call needs a longer answer than an engine will write, and
# one cut-off answer used to lose every region on the page.
_REGIONS_PER_CALL = 40
# DeepSeek answers a larger max_tokens with a 400; the other engines sent
# max_tokens (Claude, OpenAI-compatible clients) all allow at least this much.
_MAX_TOKENS_PER_CALL = 8192


def translate_regions_by_id(texts_by_id: dict, engine, drama_meta: dict,
                            previous_context: str = "", usage_cb=None, glossary_terms=None):
    """Id-keyed page translation: translate_page_bubbles (and so the
    extension bridge) and the API jobs go through this. `texts_by_id` maps
    each region's id to its source text. Returns ({id: translation},
    new_context), or (None, previous_context) when nothing could be applied.

    LLM engines get the regions in reading-order chunks of at most
    _REGIONS_PER_CALL ids, the rolling context passed from chunk to chunk. A
    chunk's answer is applied only as a whole: no answer, unparseable JSON,
    not an object keyed by id, any sent id missing or not a string, or an id
    that wasn't sent rejects that chunk, and its ids are simply absent from
    the result while the other chunks still apply. Nothing is ever matched by
    position.

    Engines without the JSON prompt path (pure MT, or no LLM client) are called
    once per region with a single text, so each answer belongs to exactly
    one id; they carry no rolling context."""
    from translate_engines import build_translation_context

    ids = [str(k) for k in texts_by_id]
    if not ids:
        return {}, previous_context
    if (not getattr(engine, "supports_reference", False)
            or getattr(engine, "client", True) is None):
        # The shared context carries the drama's source_language (NLLB defaults
        # to Chinese without it).
        context = build_translation_context(engine, drama_meta, glossary_terms=glossary_terms)
        out = {}
        for key, text in zip(ids, texts_by_id.values()):
            result = engine.translate_batch([text], dict(context))
            if usage_cb and hasattr(engine, "last_usage"):     # per call: it is reset each time
                usage_cb(engine.last_usage.get("input_tokens", 0),
                         engine.last_usage.get("output_tokens", 0))
            if not isinstance(result, (list, tuple)) or len(result) != 1 \
                    or not isinstance(result[0], str):
                return None, previous_context
            out[key] = result[0]
        return out, previous_context

    items = list(zip(ids, texts_by_id.values()))
    got, context = {}, previous_context
    for start in range(0, len(items), _REGIONS_PER_CALL):
        chunk_got, context = _translate_region_chunk(
            items[start:start + _REGIONS_PER_CALL], engine, drama_meta, context,
            usage_cb, glossary_terms)
        got.update(chunk_got or {})
    if not got:
        return None, previous_context
    return got, context


def _translate_region_chunk(items: list, engine, drama_meta: dict, previous_context: str,
                            usage_cb, glossary_terms):
    """One LLM call for [(id, text), ...]. Returns ({id: translation},
    new_context), or (None, previous_context) when the answer doesn't name
    exactly the ids sent, each with a string."""
    import json
    import re
    from translate_engines import LANGUAGE_NAMES, extract_first_json_value, call_llm_json
    from translation_guide import build_glossary_block

    ids = [k for k, _ in items]
    source_name = LANGUAGE_NAMES.get((drama_meta or {}).get("source_language") or "zh",
                                     "source-language")
    context_block = f"\n\nContext from previous pages: {previous_context}" if previous_context else ""
    glossary_block = build_glossary_block(glossary_terms)
    glossary_block = f"\n\n{glossary_block}" if glossary_block else ""
    payload = json.dumps([{"id": k, "text": t} for k, t in items], ensure_ascii=False)
    prompt = (
        f"Translate these {source_name} manga/comic text regions into natural English, keeping "
        "character voice and plot consistent with the context below if any. They are listed in reading "
        "order; each has an id."
        + glossary_block + context_block + f"\n\nRegions on this page (JSON):\n{payload}\n\n"
        'Return ONLY a JSON object: {"translations": {"<id>": "<English text>", ...}, '
        '"context_summary": "1-2 sentence summary of what just happened, to carry into the '
        'next page"}. Use every id exactly once, as given. No preamble, no markdown fences.'
    )
    max_tokens = min(max(1500, 200 * len(ids)), _MAX_TOKENS_PER_CALL)
    text = call_llm_json(engine, prompt, max_tokens=max_tokens, fallback=None, usage_cb=usage_cb)
    if not text:
        return None, previous_context
    data = extract_first_json_value(
        re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip())
    if not isinstance(data, dict) or not isinstance(data.get("translations"), dict):
        return None, previous_context
    got = {str(k): v for k, v in data["translations"].items()}
    if set(got) != set(ids) or not all(isinstance(v, str) for v in got.values()):
        return None, previous_context
    summary = data.get("context_summary")
    new_context = (summary.strip()[:_MAX_CONTEXT_CHARS]
                   if isinstance(summary, str) and summary.strip() else previous_context)
    return got, new_context


def translate_page_bubbles(bubbles: list, engine, drama_meta: dict, previous_context: str = "",
                           glossary_terms=None, usage_cb=None) -> str:
    """Translates a page's bubbles from their CURRENT source_text -- so an
    OCR mistake fixed by hand in the review step is what
    reaches the translation call, not the raw OCR output. Skipped regions
    and SFX left out of the automated pass (region_excluded_from_auto())
    aren't sent at all and keep whatever translated_text they had; neither
    are regions with no source text.

    Mutates `bubbles` in place and returns the new rolling context for the
    next page. Answers are matched to bubbles by explicit id
    (translate_regions_by_id). When no answer could be applied (unreadable,
    short, padded, reordered-by-position or naming an unknown id) no bubble is
    touched; when only some chunks of a dense page failed, the rest are
    applied. Either way it raises ValueError, naming how many regions were
    left untranslated."""
    eligible = [b for b in bubbles
                if not b.get("skip") and not region_excluded_from_auto(b)
                and (b.get("source_text") or "").strip()]
    if not eligible:
        return previous_context
    # Each region goes out under an explicit key and is applied through a
    # key -> region map (translate_regions_by_id): a short, reordered or
    # padded answer applies nothing, never a shifted one.
    by_key = {str(i): b for i, b in enumerate(eligible)}
    result, new_context = translate_regions_by_id(
        {k: b["source_text"] for k, b in by_key.items()}, engine, drama_meta,
        previous_context=previous_context, usage_cb=usage_cb, glossary_terms=glossary_terms)
    if result is None:
        raise ValueError("The translation service's answer couldn't be matched to the bubbles "
                         "by id (unreadable, short, padded or with unknown ids) -- not applied, "
                         "since there's no safe way to trust it.")
    for key, text in result.items():
        by_key[key]["translated_text"] = text or ""
    untranslated = len(by_key) - len(result)
    if untranslated:
        raise ValueError(f"{untranslated} of {len(by_key)} regions weren't translated: part of "
                         "the answer couldn't be matched to them by id. The others were applied.")
    return new_context
