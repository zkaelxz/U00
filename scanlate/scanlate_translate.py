"""
scanlate_translate.py -- id-keyed translation of a comic page's text regions.

Chunks a page's regions into engine calls, matches the answers back by id, and
carries a short context string from page to page.
"""

import re
import unicodedata

from en_cleanup import drop_em_dashes
from scanlate.scanlate_regions import region_excluded_from_auto


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

# A model left a term it could not place in the source script ("An accomplice of the
# 予告犯 (Yokokuhan)"), which is typeset onto the page as written and is unreadable to
# the person it is for; a prompt rule alone did not stop it, so the answers are also
# checked (recheck_source_script).
_ENGLISH_ONLY_NOTE = (" The answer is English only: never leave Japanese, Chinese or Korean "
                      "characters in it, and never add a romanised reading in brackets. Translate "
                      "a term by its meaning (予告犯 becomes \"the Notice Criminal\"); only a "
                      "person's name is romanised.")

# Word-for-word output read as stiff ("Big sis is wearing such a strange outfit!" for
# お姉ちゃん変な格好〜). The fidelity clause is there because a plain "make it natural"
# made a small model drop a thanks and flip a sentence's sense in a test.
_FLOW_NOTE = (" Write the English a skilled comic letterer would: short, natural spoken "
              "dialogue with contractions and the rhythm of real speech, never a "
              "word-for-word gloss. Read all the regions first, as a reader would, and "
              "translate them as one scene: who speaks to whom, what the last line set up, "
              "what the next answers. Before you answer, silently check your lines in order; where "
              "one doesn't follow from the line before it, or only makes sense as a gloss "
              "of the source, reword it so the exchange flows, even when that departs from "
              "the source's phrasing or sentence shape, except glossary terms, "
              "honorifics and names. Keep every name, detail and "
              "nuance, and each line's meaning: add nothing and drop nothing, but the "
              "wording is yours. Keep each reply short enough for a speech "
              "bubble, and spell each name one way. Neighbouring regions are often one "
              "sentence split across bubbles: translate them as that sentence, broken at "
              "the same point, so each bubble reads on from the one before.")

# The owner found comic English flattened into generic modern speech: a senpai became "my senior",
# a wuxia sect elder spoke like an office manager.
_TONE_NOTE = (" Keep the source's flavour: follow the glossary's forms of address, and where it "
              "has none keep an honorific or term of address romanised (-san, -nim, senpai, "
              "oppa, shixiong) rather than dropping or anglicising it; keep cultural terms, and "
              "match the genre's voice (wuxia, xianxia, fantasy, romance, school) instead of "
              "plain modern English.")
# The picture call used to have no pronoun rule at all, and a girl drawn on the page could
# still come out "he" when the source dropped the subject.
_SEEN_NOTE = (" The source often drops who is meant: decide he or she from the art, the cast "
              "list and the context; when none of them shows it, use the name or \"they\".")


# A chapter read page by page, several pages at once, spelled one name three ways; the names
# each answer reports are kept for the chapter and sent with its later pages.
_MAX_CHAPTER_NAMES = 60
# Where a reply's "cast" map rides in the names dict; no name written on a page has this form.
CAST_KEY = "\0cast"
_MAX_NAME_CHARS = 40


def _answer_format(collect_names: bool, summary: bool = True) -> str:
    names = (', "names": {"<a person, place or group name exactly as written on this page>": '
             '"<its English spelling>", ...}, "cast": {"<English name of a character on this '
             'page>": "she" or "he", only where the page or the context shows it}'
             if collect_names else "")
    # The summary is written after every translation and the call is not streamed, so the page
    # waits for it; pages translated side by side only ever read another page's summary.
    carry = (', "context_summary": "1-2 sentence summary of what just happened, to carry into '
             'the next page"' if summary else "")
    return ('Return ONLY a JSON object: {"translations": {"<id>": "<English text>", ...}'
            f'{carry}{names}}}. Use every id exactly once, as given. No preamble, no markdown fences.')


def chapter_names_block(names: dict | None) -> str:
    """The prompt section for names earlier pages of the chapter already spelled; "" for none."""
    if not names:
        return ""
    return ("Names already used on earlier pages of this chapter; spell them exactly the same: "
            + "; ".join(f"{source} → {english}" for source, english in names.items()))


# Pronouns are settled by a majority of the pages that reported them, so one misread page
# cannot flip a character; the cap bounds what one title keeps.
_MAX_CAST = 60
_PRONOUNS = {"she": "she", "her": "she", "female": "she", "he": "he", "him": "he", "male": "he"}


def cast_block(cast: dict | None) -> str:
    """The prompt section for characters whose pronouns earlier pages settled; "" for none."""
    settled = settled_pronouns(cast)
    if not settled:
        return ""
    return ("Characters of this title and their pronouns, from earlier pages; use them unless "
            "this page clearly shows otherwise: "
            + "; ".join(f"{name}: {pronoun}" for name, pronoun in settled.items()))


def settled_pronouns(cast: dict | None) -> dict:
    """{English name: "she"/"he"} for each character whose votes are not tied."""
    out = {}
    for name, votes in (cast or {}).items():
        she, he = int(votes.get("she", 0)), int(votes.get("he", 0))
        if she != he:
            out[name] = "she" if she > he else "he"
    return out


def _keep_cast(found: dict, cast: dict, known_english) -> None:
    """Adds this answer's pronoun votes to `cast`. Only a name the page's own translation or the
    title's known names use counts, so a description ("the girl") never becomes a character."""
    known = "\n".join(known_english)
    for name, pronoun in found.items():
        if not (isinstance(name, str) and isinstance(pronoun, str)):
            continue
        name, pronoun = name.strip(), _PRONOUNS.get(pronoun.strip().lower())
        if not (name and pronoun and len(name) <= _MAX_NAME_CHARS and name in known):
            continue
        if name not in cast and len(cast) >= _MAX_CAST:
            continue
        votes = cast.setdefault(name, {})
        votes[pronoun] = int(votes.get(pronoun, 0)) + 1


def _keep_chapter_names(found: dict, chapter_names: dict, source_texts) -> None:
    """Adds the names a reply reported to `chapter_names`, the first spelling winning. Only a
    name written in one of the page's own regions is kept, so a model's guess never becomes
    the chapter's spelling."""
    page_text = "\n".join(source_texts)
    for source, english in found.items():
        if len(chapter_names) >= _MAX_CHAPTER_NAMES:
            return
        if not (isinstance(source, str) and isinstance(english, str)):
            continue
        source, english = source.strip(), english.strip()
        if (source and english and len(source) <= _MAX_NAME_CHARS
                and len(english) <= _MAX_NAME_CHARS and source in page_text):
            chapter_names.setdefault(source, english)


def translate_regions_by_id(texts_by_id: dict, engine, drama_meta: dict,
                            previous_context: str = "", usage_cb=None, glossary_terms=None,
                            character_notes: str = "", chapter_brief: str = "",
                            names_out: dict | None = None, summary: bool = True):
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
            out[key] = drop_em_dashes(result[0], text)
        return out, previous_context

    items = list(zip(ids, texts_by_id.values()))
    got, context = {}, previous_context
    for start in range(0, len(items), REGIONS_PER_CALL):
        chunk_got, context = _translate_region_chunk(
            items[start:start + REGIONS_PER_CALL], engine, drama_meta, context,
            usage_cb, glossary_terms, character_notes, chapter_brief, names_out, summary)
        got.update(chunk_got or {})
    if not got:
        return None, previous_context
    return got, context


def _translate_region_chunk(items: list, engine, drama_meta: dict, previous_context: str,
                            usage_cb, glossary_terms, character_notes: str = "",
                            chapter_brief: str = "", names_out: dict | None = None,
                            summary: bool = True):
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
        glossary_block += ("\n\nBrief of the whole chapter (who is speaking, names and what "
                           f"happens), for consistency across pages: {chapter_brief}")
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
        "order; each has an id." + _NAMES_NOTE + _ENGLISH_ONLY_NOTE + _BLIND_NOTE + _FLOW_NOTE + _TONE_NOTE
        + ocr_note
        + glossary_block + context_block + f"\n\nRegions on this page (JSON):\n{payload}\n\n"
        + _answer_format(names_out is not None, summary)
    )
    max_tokens = min(max(1500, 200 * len(ids)), _MAX_TOKENS_PER_CALL)
    text = call_llm_json(engine, prompt, max_tokens=max_tokens, fallback=None, usage_cb=usage_cb)
    return _matched_answer(text, ids, previous_context, names_out, dict(items))


def _matched_answer(text, ids: list, previous_context: str, names_out: dict | None = None,
                    sources: dict | None = None):
    """({id: translation}, new_context) from an engine's reply, or (None,
    previous_context) unless it names exactly the ids sent, each with a string.
    `names_out` receives the reply's "names" map of an applied answer, unchecked, and its
    "cast" map under CAST_KEY."""
    import re
    from engine_backends.line_text import extract_first_json_value
    if not text:
        return None, previous_context
    data = extract_first_json_value(
        re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip(), expect=dict)
    if not isinstance(data, dict) or not isinstance(data.get("translations"), dict):
        return None, previous_context
    got = {str(k): v for k, v in data["translations"].items()}
    # A model once closed the translations object after its first id and wrote the
    # rest beside it; those ids are still answered by id, so they are read, not lost.
    got.update({k: data[k] for k in ids if k not in got and isinstance(data.get(k), str)})
    if set(got) != set(ids) or not all(isinstance(v, str) for v in got.values()):
        return None, previous_context
    got = {k: drop_em_dashes(v, (sources or {}).get(k, "")) for k, v in got.items()}
    summary = data.get("context_summary")
    new_context = (summary.strip()[:_MAX_CONTEXT_CHARS]
                   if isinstance(summary, str) and summary.strip() else previous_context)
    if names_out is not None and isinstance(data.get("names"), dict):
        names_out.update(data["names"])
    if names_out is not None and isinstance(data.get("cast"), dict):
        names_out.setdefault(CAST_KEY, {}).update(data["cast"])
    return got, new_context


# A rejected answer is usually a one-off (a dropped id, a cut-off reply), so a second
# ask for just the missing regions mostly works; the cap keeps a page whose engine
# keeps failing from costing one call per region.
_MAX_SOLO_RETRIES = 6


def retry_missing_regions(texts_by_id: dict, got: dict, engine, drama_meta: dict,
                          previous_context: str = "", usage_cb=None, glossary_terms=None,
                          character_notes: str = "", chapter_brief: str = "") -> dict:
    """`got` plus a text-only retry for the ids of `texts_by_id` it lacks: first
    the missing ones together, then (if some are still missing) a few alone, so
    one region the model keeps tripping on cannot hold back the others. Returns
    a new dict; an engine error on a retry is the same as no answer, because the
    caller already holds a partial result worth keeping, except the stop signals
    the run code lets through, which are raised."""
    from engine_backends.run_limits import TranslateRunStopped
    from engine_backends.shared import LLMTaskTimeout, TranslationCancelled
    from memory_headroom import HeadroomError
    out = dict(got)
    missing = {k: v for k, v in texts_by_id.items() if k not in out}
    if not missing:
        return out
    groups = [missing] if len(missing) > 1 else []
    groups += [{k: v} for k, v in list(missing.items())[:_MAX_SOLO_RETRIES]]
    for group in groups:
        group = {k: v for k, v in group.items() if k not in out}
        if not group:
            continue
        try:
            answer, _ = translate_regions_by_id(
                group, engine, drama_meta, previous_context=previous_context,
                usage_cb=usage_cb, glossary_terms=glossary_terms,
                character_notes=character_notes, chapter_brief=chapter_brief)
        except (TranslationCancelled, TranslateRunStopped, LLMTaskTimeout, HeadroomError):
            # A cancel, a spend limit, a timed-out request still running or a refused
            # memory check must stop the page, not cost one more paid call per group.
            raise
        except Exception:
            continue
        out.update(answer or {})
    return out


def translate_regions_with_image(regions: list, image: bytes, mime: str, engine, drama_meta: dict,
                                 previous_context: str = "", usage_cb=None, glossary_terms=None,
                                 character_notes: str = "", chapter_brief: str = "",
                                 names_out: dict | None = None, summary: bool = True):
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
        reference += ("\n\nBrief of the whole chapter (who is speaking, names and what "
                      f"happens), for consistency across pages: {chapter_brief}")
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
        + _NAMES_NOTE + _ENGLISH_ONLY_NOTE + _SEEN_NOTE + _FLOW_NOTE + _TONE_NOTE + reference + context_block
        + f"\n\nRegions on this page (JSON):\n{payload}\n\n"
        + _answer_format(names_out is not None, summary))
    max_tokens = min(max(1500, 200 * len(ids)), _MAX_TOKENS_PER_CALL)
    text, usage = ask_about_image(engine, prompt, image, mime, max_tokens=max_tokens)
    if usage_cb:
        usage_cb(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    sources = {str(r["id"]): r.get("text") or "" for r in regions}
    return _matched_answer(text, ids, previous_context, names_out, sources)


# Kana, CJK ideographs and Hangul; CJK punctuation is left out because a translation
# may keep a quote mark.
_SOURCE_SCRIPT = re.compile(
    "[\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
    "\uff66-\uff9f]")


def has_source_script(text, source: str = "") -> bool:
    # An answer identical to its source is an echoed watermark or stamp, which other code
    # already handles; asking again would only repeat it.
    return isinstance(text, str) and text != source and bool(_SOURCE_SCRIPT.search(text))


def recheck_source_script(texts_by_id: dict, result: dict, engine, drama_meta: dict,
                          previous_context: str = "", usage_cb=None, glossary_terms=None,
                          character_notes: str = "", chapter_brief: str = "", summary: bool = True):
    """(result, still_leaking_ids): `result` with one text-only retry for the answers that
    still contain Japanese, Chinese or Korean characters. A retried answer replaces the
    first only when it has fewer such characters, so the retry can never make a region
    worse; the ids still containing any are returned for the caller to flag. An engine
    error on the retry keeps the first answer, except the stop signals, which are raised."""
    from engine_backends.run_limits import TranslateRunStopped
    from engine_backends.shared import LLMTaskTimeout, TranslationCancelled
    from memory_headroom import HeadroomError
    out = dict(result)
    if not getattr(engine, "supports_reference", False) or getattr(engine, "client", True) is None:
        # A pure MT engine takes no instructions, so asking it again gives the same answer.
        return out, [k for k, v in out.items() if k in texts_by_id
                     and has_source_script(v, texts_by_id[k])]
    leaking = {k: v for k, v in out.items() if k in texts_by_id and has_source_script(v, texts_by_id[k])}
    if leaking:
        found = "\n".join(f"{k}: {v}" for k, v in leaking.items())
        notes = "\n\n".join(part for part in (character_notes, (
            "Your earlier answer left original-language characters in these answers, which must "
            "be English only. Translate every term, keep a person's name romanised, and answer "
            f"each of these ids again:\n{found}")) if part)
        try:
            answer, _ = translate_regions_by_id(
                {k: texts_by_id[k] for k in leaking}, engine, drama_meta,
                previous_context=previous_context, usage_cb=usage_cb,
                glossary_terms=glossary_terms, character_notes=notes,
                chapter_brief=chapter_brief, summary=summary)
        except (TranslationCancelled, TranslateRunStopped, LLMTaskTimeout, HeadroomError):
            raise
        except Exception:
            answer = None
        for k, text in (answer or {}).items():
            if k in leaking and isinstance(text, str) and (
                    len(_SOURCE_SCRIPT.findall(text)) < len(_SOURCE_SCRIPT.findall(leaking[k]))):
                out[k] = text
    return out, [k for k, v in out.items() if k in texts_by_id and has_source_script(v, texts_by_id[k])]


# Marks NFKC leaves in their CJK form. "……" is one ellipsis in CJK lettering and widens to six dots.
_CJK_MARKS = str.maketrans({"。": ".", "、": ",", "・": ".", "〜": "~", "「": '"', "」": '"',
                            "『": '"', "』": '"'})


def marks_only_english(text: str):
    """The English for a region lettered only in punctuation and symbols ("……", "？！"), or None
    when it has a letter or digit of any script. Such a region needs no model: a whole picture
    call for one "……" balloon took seconds, and a model that answered it with "" cost a retry."""
    # Invisible format and control characters (a stray zero-width space) are not lettering: a
    # region of only those would get an answer that shows nothing, so it goes to the model.
    text = "".join(ch for ch in (text or "") if unicodedata.category(ch)[0] != "C").strip()
    if not text or any(unicodedata.category(ch)[0] in "LN" for ch in text):
        return None
    english = re.sub(r"\.{3,}", "...", unicodedata.normalize("NFKC", text).translate(_CJK_MARKS))
    english = drop_em_dashes(english).strip()
    return english or None


def _answer_marks_locally(bubbles: list) -> list:
    """Answers the punctuation-only bubbles (marks_only_english) in place; returns the others."""
    rest = []
    for b in bubbles:
        english = marks_only_english(b["source_text"])
        if english is None:
            rest.append(b)
        else:
            b["translated_text"] = english
    return rest


def previous_page_block(lines) -> str:
    """The prompt section for the page before this one ([{"source", "english"}], english
    possibly empty); "" for none. It is context only, so the answer must not contain it."""
    if not lines:
        return ""
    rows = [f"{i}. {line['source']}" + (f" => {line['english']}" if line.get("english") else "")
            for i, line in enumerate(lines, 1)]
    return ("The previous page, given only so pronouns, names and a sentence that continues onto "
            "this page come out right. Do not translate these lines and do not answer for them; a "
            "line without '=>' was not translated yet:\n" + "\n".join(rows))



def _log_lost_picture(regions: int, failure: str) -> None:
    # Without these lines a page that lost its picture (worth 1-3 chrF) and
    # paid a second call looked the same in the log as any other page.
    import applog
    logger = applog.get_logger()
    if regions > REGIONS_PER_CALL:
        logger.info("Comic translate: %d regions is over %d, page sent without its picture",
                    regions, REGIONS_PER_CALL)
    else:
        logger.warning("Comic translate: no usable answer with the picture (%s), "
                       "translating the page again from its text only",
                       failure or "answer did not match the regions")

def translate_page_bubbles(bubbles: list, engine, drama_meta: dict, previous_context: str = "",
                           glossary_terms=None, usage_cb=None, character_notes: str = "",
                           page_image: dict | None = None, previous_lines=None,
                           chapter_names: dict | None = None, cast: dict | None = None,
                           summary: bool = True) -> str:
    """Translates a page's bubbles from their CURRENT source_text -- so an
    OCR mistake fixed by hand in the review step is what
    reaches the translation call, not the raw OCR output. Skipped regions
    and SFX left out of the automated pass (region_excluded_from_auto())
    aren't sent at all and keep whatever translated_text they had; neither
    are regions with no source text. A region lettered only in punctuation is
    answered here (marks_only_english) and not sent either.

    Mutates `bubbles` in place and returns the new rolling context for the
    next page. Answers are matched to bubbles by explicit id
    (translate_regions_by_id). When no answer could be applied (unreadable,
    short, padded, reordered or with unknown ids) only the punctuation-only
    bubbles get their local answer; when only some chunks of a dense page
    failed, the rest are applied. Either way it raises ValueError, naming how many regions were
    left untranslated.

    `page_image` ({"data", "mime", "scale"}, optionally "engine" and "usage_cb" for
    a picture engine other than `engine`, or "readers", a list of such
    {"engine", "usage_cb", optional "on_failure"(exc)} tried in order) sends the page with its regions first,
    as a Scanlate run does; when no reader answers, the page goes as text alone,
    so a picture can only add to the translation.

    `chapter_names` ({source spelling: English}) is sent as names to keep and gets the names
    this page's answer reports, so the next page of the chapter spells them the same. `cast`
    ({English name: {"she": votes, "he": votes}}) is sent as the characters' pronouns and gets
    this page's votes; it is only collected alongside `chapter_names`. `summary=False` stops
    asking for a context summary (one the model sends anyway is still returned)."""
    eligible = [b for b in bubbles
                if not b.get("skip") and not region_excluded_from_auto(b)
                and (b.get("source_text") or "").strip()]
    eligible = _answer_marks_locally(eligible)
    if not eligible:
        return previous_context
    # Standing reference and the page before share the notes argument, which every request
    # already places after the glossary.
    character_notes = "\n\n".join(
        part for part in (character_notes, previous_page_block(previous_lines),
                          chapter_names_block(chapter_names), cast_block(cast)) if part)
    found_names = {} if chapter_names is not None else None
    # Each region goes out under an explicit key and is applied through a
    # key -> region map (translate_regions_by_id): a short, reordered or
    # padded answer applies nothing, never a shifted one.
    by_key = {str(i): b for i, b in enumerate(eligible)}
    result = None
    new_context = previous_context
    failure = ""
    if page_image is not None and len(by_key) <= REGIONS_PER_CALL:
        scale = page_image["scale"]
        regions = [{"id": k, "text": b["source_text"],
                    "box": {n: round(b[n] * scale) for n in ("x", "y", "w", "h")},
                    **({"sound_effect": True} if b.get("kind") == "sfx" else {})}
                   for k, b in by_key.items()]
        readers = page_image.get("readers") or [
            {"engine": page_image.get("engine"), "usage_cb": page_image.get("usage_cb")}]
        for reader in readers:
            try:
                result, new_context = translate_regions_with_image(
                    regions, page_image["data"], page_image["mime"],
                    reader.get("engine") or engine, drama_meta, previous_context=previous_context,
                    usage_cb=reader.get("usage_cb") or usage_cb, glossary_terms=glossary_terms,
                    character_notes=character_notes, names_out=found_names, summary=summary)
            except Exception as exc:
                failure = type(exc).__name__
                on_failure = reader.get("on_failure")
                if on_failure is not None:
                    on_failure(exc)
                result, new_context = None, previous_context
            if result is not None:
                break
    if page_image is not None and result is None and by_key:
        _log_lost_picture(len(by_key), failure)
    if result is None:
        result, new_context = translate_regions_by_id(
            {k: b["source_text"] for k, b in by_key.items()}, engine, drama_meta,
            previous_context=previous_context, usage_cb=usage_cb, glossary_terms=glossary_terms,
            character_notes=character_notes, names_out=found_names, summary=summary)
    found_cast = (found_names or {}).pop(CAST_KEY, None)
    if result is not None:
        result, _ = recheck_source_script(
            {k: b["source_text"] for k, b in by_key.items()}, result, engine, drama_meta,
            previous_context=previous_context, usage_cb=usage_cb, glossary_terms=glossary_terms,
            character_notes=character_notes, summary=summary)
    if found_names and chapter_names is not None:
        _keep_chapter_names(found_names, chapter_names, (b["source_text"] for b in eligible))
    if found_cast and cast is not None and result is not None:
        _keep_cast(found_cast, cast, [*result.values(), *(chapter_names or {}).values()])
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
    from engine_backends.line_text import extract_first_json_value
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
        "address each other, names and recurring terms with the English to use for each, and a "
        "few sentences on what happens. Keep it under 200 words.\n\n" + source + "\n\n"
        'Return ONLY a JSON object: {"brief": "<the brief in English>"}. No preamble, no markdown fences.')
    text = call_llm_json(engine, prompt, max_tokens=700, fallback=None, usage_cb=usage_cb)
    if not text:
        return ""
    data = extract_first_json_value(
        re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip(), expect=dict)
    brief = data.get("brief") if isinstance(data, dict) else None
    return brief.strip()[:_BRIEF_MAX_CHARS] if isinstance(brief, str) else ""
