"""
scanlate_translate.py -- id-keyed translation of a comic page's text regions.

Chunks a page's regions into engine calls, matches the answers back by id, and
carries a short context string from page to page.
"""

from scanlate_regions import region_excluded_from_auto


_MAX_CONTEXT_CHARS = 1000

# One read of a whole chapter must fit a single request: a chapter's lettering is
# a few thousand characters, and a webtoon of hundreds of pages is cut here
# rather than refused.
_BRIEF_SOURCE_CHARS = 24000
_BRIEF_MAX_CHARS = 1500

# A dense page in one call needs a longer answer than an engine will write, and
# one cut-off answer used to lose every region on the page.
REGIONS_PER_CALL = 40
# DeepSeek answers a larger max_tokens with a 400; the other engines sent
# max_tokens (Claude, OpenAI-compatible clients) all allow at least this much.
_MAX_TOKENS_PER_CALL = 8192


# Small models turned an author credit (村田雄介) into "Village Field Hiroki" and
# "ひらひらしてる" into "fluffy": names were glossed word by word, and a figurative
# phrase was rendered by its parts. A text-only call also called a girl "he" because
# the source dropped the pronoun and the picture was not sent.
_NAMES_NOTE = (" A person's name, including an author or artist credit, is romanised "
               "(Murata Yusuke), never translated word by word. Render an idiom or "
               "figurative phrase by what it means in this scene, not by its parts.")
# Only for the text-only call: the picture call can see who is meant.
_BLIND_NOTE = (" The source often drops who is meant: you cannot see the page, so never guess "
               "he or she. Use the name, \"you\" or \"they\" unless the text or the context "
               "gives the gender.")

# Word-for-word output read as stiff ("Big sis is wearing such a strange outfit!" for
# お姉ちゃん変な格好〜). The fidelity clause is there because a plain "make it natural"
# made a small model drop a thanks and flip a sentence's sense in a test.
_FLOW_NOTE = (" Write the English a skilled comic letterer would: short, natural spoken "
              "dialogue with contractions and the rhythm of real speech, never a "
              "word-for-word gloss. Keep every name, detail, description and nuance: "
              "rephrase freely so it reads smoothly, but add nothing, drop nothing and "
              "keep each sentence's meaning. Keep each reply short enough for a speech "
              "bubble, and spell each name one way.")


def translate_regions_by_id(texts_by_id: dict, engine, drama_meta: dict,
                            previous_context: str = "", usage_cb=None, glossary_terms=None,
                            character_notes: str = "", chapter_brief: str = ""):
    """Id-keyed page translation: translate_page_bubbles (and so the
    extension bridge) and the API jobs go through this. `texts_by_id` maps
    each region's id to its source text. Returns ({id: translation},
    new_context), or (None, previous_context) when nothing could be applied.

    LLM engines get the regions in reading-order chunks of at most
    REGIONS_PER_CALL ids, the rolling context passed from chunk to chunk. A
    chunk's answer is applied only as a whole: no answer, unparseable JSON,
    not an object keyed by id, any sent id missing or not a string, or an id
    that wasn't sent rejects that chunk, and its ids are simply absent from
    the result while the other chunks still apply. Nothing is ever matched by
    position.

    Engines without the JSON prompt path (pure MT, or no LLM client) are called
    once per region with a single text, so each answer belongs to exactly
    one id; they carry no rolling context."""
    from engine_backends.translate_pipeline import build_translation_context

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
    for start in range(0, len(items), REGIONS_PER_CALL):
        chunk_got, context = _translate_region_chunk(
            items[start:start + REGIONS_PER_CALL], engine, drama_meta, context,
            usage_cb, glossary_terms, character_notes, chapter_brief)
        got.update(chunk_got or {})
    if not got:
        return None, previous_context
    return got, context


def _translate_region_chunk(items: list, engine, drama_meta: dict, previous_context: str,
                            usage_cb, glossary_terms, character_notes: str = "",
                            chapter_brief: str = ""):
    """One LLM call for [(id, text), ...]. Returns ({id: translation},
    new_context), or (None, previous_context) when the answer doesn't name
    exactly the ids sent, each with a string."""
    import json
    from core import LANGUAGE_NAMES
    from engine_backends.llm_tasks import call_llm_json
    from translation_guide import build_glossary_block

    ids = [k for k, _ in items]
    source_name = LANGUAGE_NAMES.get((drama_meta or {}).get("source_language") or "zh",
                                     "source-language")
    context_block = f"\n\nContext from previous pages: {previous_context}" if previous_context else ""
    glossary_block = build_glossary_block(glossary_terms)
    glossary_block = f"\n\n{glossary_block}" if glossary_block else ""
    # The series cast (pronouns, voice notes) sits with the glossary: both are standing
    # reference for the whole title rather than per-page context.
    if character_notes:
        glossary_block += f"\n\n{character_notes}"
    if chapter_brief:
        glossary_block += ("\n\nBrief of the whole chapter (who is speaking, each speaker's voice, "
                           "names and what happens), for consistency across pages. Write each "
                           f"line in its speaker's voice as the brief describes it: {chapter_brief}")
    payload = json.dumps([{"id": k, "text": t} for k, t in items], ensure_ascii=False)
    # OCR of stylised lettering drops Korean word spaces and swaps look-alike
    # characters (불이 read as 붙이 turned "the school caught fire" into "got into
    # school"), so the model is told the text is machine-read, not authoritative.
    ocr_note = (" The text was read by OCR: word spaces may be missing and a character may be "
                "misread, so use the surrounding lines and the context to recover the intended "
                "sentence." if (drama_meta or {}).get("source_language") in ("ko", "ja", "zh")
                else "")
    prompt = (
        f"Translate these {source_name} manga/comic text regions into natural English, keeping "
        "character voice and plot consistent with the context below if any. They are listed in reading "
        "order; each has an id." + _NAMES_NOTE + _BLIND_NOTE + _FLOW_NOTE + ocr_note
        + glossary_block + context_block + f"\n\nRegions on this page (JSON):\n{payload}\n\n"
        'Return ONLY a JSON object: {"translations": {"<id>": "<English text>", ...}, '
        '"context_summary": "1-2 sentence summary of what just happened, to carry into the '
        'next page"}. Use every id exactly once, as given. No preamble, no markdown fences.'
    )
    max_tokens = min(max(1500, 200 * len(ids)), _MAX_TOKENS_PER_CALL)
    text = call_llm_json(engine, prompt, max_tokens=max_tokens, fallback=None, usage_cb=usage_cb)
    return _matched_answer(text, ids, previous_context)


def _matched_answer(text, ids: list, previous_context: str):
    """({id: translation}, new_context) from an engine's reply, or (None,
    previous_context) unless it names exactly the ids sent, each with a string."""
    import re
    from engine_backends.shared import extract_first_json_value
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


def translate_regions_with_image(regions: list, image: bytes, mime: str, engine, drama_meta: dict,
                                 previous_context: str = "", usage_cb=None, glossary_terms=None,
                                 character_notes: str = "", chapter_brief: str = ""):
    """The id-keyed page translation of translate_regions_by_id, with the page
    itself sent along. `regions` is [{"id", "text", "box": {x, y, w, h}}] with
    the boxes in the pixels of `image`. The model reads the picture to correct
    what the OCR got wrong, who is speaking and what the art shows; its answer
    is still applied only by id. Returns ({id: translation}, new_context), or
    (None, previous_context) when the answer is unusable. Raises RuntimeError
    for an engine that takes no pictures, and whatever the provider raises, so
    the caller can fall back to the text-only path."""
    import json
    from core import LANGUAGE_NAMES
    from engine_backends.vision import ask_about_image
    from translation_guide import build_glossary_block

    ids = [str(r["id"]) for r in regions]
    source_name = LANGUAGE_NAMES.get((drama_meta or {}).get("source_language") or "zh",
                                     "source-language")
    reference = build_glossary_block(glossary_terms)
    reference = f"\n\n{reference}" if reference else ""
    if character_notes:
        reference += f"\n\n{character_notes}"
    if chapter_brief:
        reference += ("\n\nBrief of the whole chapter (who is speaking, each speaker's voice, "
                      "names and what happens), for consistency across pages. Write each "
                      f"line in its speaker's voice as the brief describes it: {chapter_brief}")
    if any(r.get("sound_effect") for r in regions):
        # The answer is set small beside the art, so a sentence would not fit.
        reference += ("\n\nRegions marked sound_effect are onomatopoeia that stay on the art: "
                      "answer each with one short English sound word (for example \"thud\"), "
                      "not a sentence.")
    context_block = f"\n\nContext from previous pages: {previous_context}" if previous_context else ""
    payload = json.dumps(
        [{"id": r["id"], "text": r["text"],
          "box": [r["box"]["x"], r["box"]["y"], r["box"]["w"], r["box"]["h"]],
          **({"sound_effect": True} if r.get("sound_effect") else {})} for r in regions],
        ensure_ascii=False)
    # The OCR text is kept in the request as a cheap anchor (boxes in x, y,
    # width, height), but the picture is what the model is told to trust: OCR
    # drops Korean word spaces and swaps look-alike characters.
    prompt = (
        f"This picture is one page of a {source_name} manga/comic. Translate its text regions into "
        "natural English, keeping each character's voice and the plot consistent with the context "
        "below if any. The regions were read by OCR and are listed in reading order, each with an "
        "id and its box in the picture's pixels as [x, y, width, height]. The OCR may have missed "
        "word spaces or misread characters: check each region against the picture and translate "
        "what is actually written. Use the art to tell who is speaking and what is happening "
        "(tone, sound effects, who 'you' is). Translate only the listed regions, one answer per id; "
        "a site watermark, logo or address strip is not dialogue: answer \"\" (an empty string) for "
        "it, never a note about it, because your answer is typeset onto the page as written."
        + _NAMES_NOTE + _FLOW_NOTE + reference + context_block + f"\n\nRegions on this page (JSON):\n{payload}\n\n"
        'Return ONLY a JSON object: {"translations": {"<id>": "<English text>", ...}, '
        '"context_summary": "1-2 sentence summary of what just happened, to carry into the '
        'next page"}. Use every id exactly once, as given. No preamble, no markdown fences.')
    max_tokens = min(max(1500, 200 * len(ids)), _MAX_TOKENS_PER_CALL)
    text, usage = ask_about_image(engine, prompt, image, mime, max_tokens=max_tokens)
    if usage_cb:
        usage_cb(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return _matched_answer(text, ids, previous_context)


def translate_page_bubbles(bubbles: list, engine, drama_meta: dict, previous_context: str = "",
                           glossary_terms=None, usage_cb=None, character_notes: str = "",
                           page_image: dict | None = None) -> str:
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
    left untranslated.

    `page_image` ({"data", "mime", "scale"}, optionally "engine" and "usage_cb" for
    a picture engine other than `engine`) sends the page with its regions first,
    as a Scanlate run does; any failure of that request falls back to the text
    alone, so a picture can only add to the translation."""
    eligible = [b for b in bubbles
                if not b.get("skip") and not region_excluded_from_auto(b)
                and (b.get("source_text") or "").strip()]
    if not eligible:
        return previous_context
    # Each region goes out under an explicit key and is applied through a
    # key -> region map (translate_regions_by_id): a short, reordered or
    # padded answer applies nothing, never a shifted one.
    by_key = {str(i): b for i, b in enumerate(eligible)}
    result = None
    new_context = previous_context
    if page_image is not None and len(by_key) <= REGIONS_PER_CALL:
        scale = page_image["scale"]
        regions = [{"id": k, "text": b["source_text"],
                    "box": {n: round(b[n] * scale) for n in ("x", "y", "w", "h")},
                    **({"sound_effect": True} if b.get("kind") == "sfx" else {})}
                   for k, b in by_key.items()]
        try:
            result, new_context = translate_regions_with_image(
                regions, page_image["data"], page_image["mime"],
                page_image.get("engine") or engine, drama_meta, previous_context=previous_context,
                usage_cb=page_image.get("usage_cb") or usage_cb, glossary_terms=glossary_terms,
                character_notes=character_notes)
        except Exception:
            result, new_context = None, previous_context
    if result is None:
        result, new_context = translate_regions_by_id(
            {k: b["source_text"] for k, b in by_key.items()}, engine, drama_meta,
            previous_context=previous_context, usage_cb=usage_cb, glossary_terms=glossary_terms,
            character_notes=character_notes)
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


def build_chapter_brief(page_texts: list, engine, drama_meta: dict, usage_cb=None) -> str:
    """One call over a chapter's whole OCR text (`page_texts`: one string per
    page, in reading order) that returns a short brief of who speaks, the names
    and what happens, for the per-page translation calls to carry. Returns ""
    for an engine that cannot follow a prompt, no text, or an answer that is not
    the expected JSON: a missing brief must never stop the run."""
    import re
    from core import LANGUAGE_NAMES
    from engine_backends.llm_tasks import call_llm_json
    from engine_backends.shared import extract_first_json_value
    if not getattr(engine, "supports_reference", False) or getattr(engine, "client", True) is None:
        return ""
    numbered = [f"Page {i}: {t}" for i, t in enumerate(page_texts, start=1) if t.strip()]
    source = "\n".join(numbered)[:_BRIEF_SOURCE_CHARS]
    if not source:
        return ""
    source_name = LANGUAGE_NAMES.get((drama_meta or {}).get("source_language") or "zh",
                                     "source-language")
    prompt = (
        f"Below is all the text of one {source_name} comic chapter, page by page, read by OCR "
        "(word spaces may be missing and a character may be misread). Write a brief for the "
        "translator who will translate it page by page: who the characters are and how they "
        "address each other, each main speaker's voice and personality as the chapter shows it "
        "(how formal, how long their lines run, their attitude and manner of speaking, judged "
        "from what they say and do), names and recurring terms with the English to use for each, "
        "and a few sentences on what happens. Describe voices and facts only: do not write English "
        "renderings of specific lines, because the translator would copy them, and give one English term "
        "for each name or term, never alternatives. Keep it under 250 words.\n\n" + source + "\n\n"
        'Return ONLY a JSON object: {"brief": "<the brief in English>"}. No preamble, no markdown fences.')
    text = call_llm_json(engine, prompt, max_tokens=900, fallback=None, usage_cb=usage_cb)
    if not text:
        return ""
    data = extract_first_json_value(
        re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip())
    brief = data.get("brief") if isinstance(data, dict) else None
    return brief.strip()[:_BRIEF_MAX_CHARS] if isinstance(brief, str) else ""
