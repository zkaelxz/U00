"""Automatic glossary extraction: proposing terms from subtitle lines or a novel's text for review, never applying them."""

import difflib
import json
import re
import unicodedata
from core import LANGUAGE_NAMES
from glossary_io import MIN_ALIAS_LEN, TERM_CATEGORIES, TERM_POLICIES
from engine_backends.run_limits import TranslateRunStopped
from engine_backends.llm_tasks import call_llm_json
from engine_backends.shared import parse_json_array


# ---------------------------------------------------------------------------
# Automatic glossary extraction -- propose terms, don't auto-apply them
# ---------------------------------------------------------------------------

def _sample_lines_across_text(zh_lines: list, max_lines: int) -> list:
    """The line-list counterpart of _sample_across_text (used by
    extract_glossary_from_novel further down this file for the same
    "spread across the whole thing, not a truncating prefix" idea, applied
    to a list of individual lines instead of one long string): max_lines
    lines evenly spread across the WHOLE list, not a prefix: a prefix
    would hide any name or relationship introduced after the first
    max_lines lines from extraction. Spreading the sample means
    the model sees the range of names/relationships across beginning,
    middle and end in one combined view -- the real mechanism VideoLingo's
    own whole-document pass uses (not a separate prose summary first)."""
    n = len(zh_lines)
    if n <= max_lines:
        return list(zh_lines)
    if max_lines <= 1:
        return [zh_lines[0]] if max_lines == 1 else []
    # step sized so i=0 lands on index 0 and i=max_lines-1 lands exactly
    # on index n-1 -- the drama's actual ending is part of "the whole
    # thing" just as much as its opening, and a step of n/max_lines
    # (rather than (n-1)/(max_lines-1)) would fall a few lines short of
    # it, same as the gap _sample_across_text's own stride avoids.
    step = (n - 1) / (max_lines - 1)
    idxs = sorted({round(i * step) for i in range(max_lines)})
    if len(idxs) < max_lines:
        # Rounding can collide two i's onto the same index on a short
        # list -- top up from whatever indices weren't picked yet, so a
        # short list still returns close to max_lines lines.
        chosen = set(idxs)
        remaining = [i for i in range(n) if i not in chosen]
        idxs = sorted(idxs + remaining[:max_lines - len(idxs)])
    return [zh_lines[i] for i in idxs]


def extract_terms_llm(zh_lines, engine, source_language: str = "zh", max_lines: int = 400,
                       known_terms=None, usage_cb=None):
    """
    Scans source text for recurring proper nouns and genre-specific terms
    that should be handled consistently, and proposes a category + policy
    for each. Returns a list of dicts:
      {term, suggested_translation, category, policy, reason}

    Samples up to max_lines lines spread across the whole drama (see
    _sample_lines_across_text) rather than just its first max_lines --
    for a drama longer than that, a term introduced only after the
    opening scenes would otherwise never be proposed at all.

    Always meant for human review before being committed to a glossary --
    category and policy are judgment calls, and the model will sometimes
    propose translating something that should stay pinyin (or vice versa).
    """
    if not getattr(engine, "supports_reference", False):
        return []

    sample = "\n".join(_sample_lines_across_text(zh_lines, max_lines))
    if not sample.strip():
        return []

    known_block = ""
    if known_terms:
        known_block = ("\n\nAlready in the glossary (do NOT propose these again): "
                       + ", ".join(t["term_original"] for t in known_terms))

    lang_name = LANGUAGE_NAMES.get(source_language, "Chinese")
    categories_desc = "\n".join(f"  - {k}: {v}" for k, v in TERM_CATEGORIES.items())
    policies_desc = "\n".join(f"  - {k}: {v['label']} (e.g. {v['example']})"
                               for k, v in TERM_POLICIES.items())

    prompt = (
        f"Below is {lang_name} text from a baihe (GL) web novel or audio drama. "
        "Identify recurring terms that need CONSISTENT handling across the whole work -- "
        "character names, sects/clans, titles, honorifics, cultivation realms, place names, "
        "artifacts, techniques, and untranslatable concepts. Ignore ordinary vocabulary.\n\n"
        f"Categories:\n{categories_desc}\n\nHandling policies:\n{policies_desc}\n\n"
        "For each term, propose the category, the handling policy that best fits the "
        "convention for that kind of term, and a suggested translation following that policy. "
        "If a name appears to be MEANINGFUL (the characters spell out something thematically "
        "relevant, or it's a pun), say so in the reason -- that's worth a translation note "
        "even if the name itself stays pinyin.\n\n"
        "Also include nicknames and slang, including an ordinary-looking word that is used "
        "here in an unusual sense; skip generic vocabulary and descriptions. A spelling that "
        "looks like a transcription variant of another term goes in that term's reason. "
        "When you are inferring a term's meaning from context rather than knowing it, set "
        '"unsure" to true instead of presenting the guess as settled.\n\n'
        'Return ONLY a JSON array: [{"term": "...", "suggested_translation": "...", '
        '"category": "...", "policy": "...", "reason": "brief justification", '
        '"unsure": false}]. '
        f"No preamble, no markdown fences.{known_block}\n\nText:\n{sample}"
    )

    text = call_llm_json(engine, prompt, max_tokens=4000, fallback="[]", usage_cb=usage_cb,
                         expect="json_array")
    entries = parse_json_array(text, 0)
    if not isinstance(entries, list):
        return []
    # Normalize/validate category and policy so bad values can't corrupt the glossary
    cleaned = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("term"):
            continue
        e["category"] = e.get("category") if e.get("category") in TERM_CATEGORIES else "other"
        e["policy"] = e.get("policy") if e.get("policy") in TERM_POLICIES else "keep_pinyin"
        cleaned.append(e)
    return cleaned


# ---------------------------------------------------------------------------
# Glossary extraction from a novel
# ---------------------------------------------------------------------------

def _sample_across_text(text: str, total_chars: int = 24000, chunks: int = 6):
    """Samples evenly across a long text rather than taking the opening.

    Novels introduce characters, sects and places throughout -- a glossary
    built only from chapter one misses everything after it. This takes
    several windows spread across the whole work so later introductions
    are caught too.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= total_chars:
        return [text]
    window = max(total_chars // chunks, 500)
    stride = max((len(text) - window) // max(chunks - 1, 1), 1)
    out = []
    for i in range(chunks):
        start = min(i * stride, max(len(text) - window, 0))
        out.append(text[start:start + window])
    return out


def _merge_novel_entries(all_terms: dict, entries) -> None:
    """Folds one window's parsed reply into `all_terms`, keyed by term."""
    if not isinstance(entries, list):
        return
    for e in entries:
        if not isinstance(e, dict) or not e.get("term"):
            continue
        e["category"] = e.get("category") if e.get("category") in TERM_CATEGORIES else "other"
        e["policy"] = e.get("policy") if e.get("policy") in TERM_POLICIES else "keep_pinyin"
        # later windows refine earlier proposals rather than duplicating,
        # but the earlier renderings stay visible: a term rendered
        # differently per window is the inconsistency a reviewer should see.
        seen = all_terms.get(e["term"])
        e["windows"] = (seen["windows"] if seen else 0) + 1
        renderings = list(seen["renderings"]) if seen else []
        rendering = str(e.get("suggested_translation") or "").strip()
        if rendering and rendering not in renderings:
            renderings.append(rendering)
        for alt in e.get("extra_renderings") or ():
            if alt not in renderings:
                renderings.append(alt)
        e["renderings"] = renderings
        e["aliases"] = list(dict.fromkeys((seen.get("aliases") if seen else []) + (e.get("aliases") or [])))
        all_terms[e["term"]] = e


_PRONOUN_LEAD = re.compile(r"^\s*(she/her|he/him|they/them)", re.IGNORECASE)
_EVIDENCE_MIN_MATCH = 0.75
_EVIDENCE_MIN_RUN = 0.5
# A quote that sits in the excerpt but names no pronoun, title or kinship term
# proves nothing about gender (a model once cited "narrow eyes smiled slyly").
# Chinese only: the pronoun instruction below names 她/他, and a Japanese or
# Korean quote would never contain them. Bare 男/女 are left out on purpose, so
# "a woman lay there" (女子) does not vouch for THIS character.
_GENDER_MARKER = re.compile(
    "她|他|牠|祂|妳|公主|王妃|皇后|太后|夫人|小姐|姑娘|女皇|女王|娘子|妻|媳婦|媳妇|"
    "少爺|少爷|王爺|王爷|老爺|老爷|太子|皇帝|國王|国王|丈夫|相公|公子|娘|爹|先生|"
    "兒子|儿子|女兒|女儿|兄|"
    "媽|妈|母親|母亲|父親|父亲|爸|姐|妹|哥|弟|姑|姨|嬸|婶|嫂|奶奶|爺爺|爷爷|叔")


def _quote_is_in(quote: str, text: str) -> bool:
    """Whether a model's quote comes from `text`. Models re-type a line with a
    full-width comma turned ASCII or a traditional character swapped for its
    simplified twin, so a quote counts when punctuation and spacing are ignored
    and three quarters of its characters are found in order in the text, half of
    them in one unbroken run; an invented line shares far less than that with
    the excerpt."""
    def squeeze(value: str) -> str:
        return "".join(ch for ch in unicodedata.normalize("NFKC", value) if ch.isalnum())
    quote, text = squeeze(quote), squeeze(text)
    if not quote:
        return False
    if quote in text:
        return True
    matcher = difflib.SequenceMatcher(None, quote, text, autojunk=False)
    sizes = [b.size for b in matcher.get_matching_blocks()]
    return sum(sizes) / len(quote) >= _EVIDENCE_MIN_MATCH and max(sizes) / len(quote) >= _EVIDENCE_MIN_RUN


def _window_aliases(entry: dict, window_text: str) -> list:
    """The other spellings the model gave for a term, kept only when each is
    written in the excerpt (an invented one would never match the novel) and is
    long enough to be a spelling rather than a single character."""
    raw = entry.get("aliases")
    found = []
    for a in raw if isinstance(raw, list) else []:
        a = a.strip() if isinstance(a, str) else ""
        if len(a) >= MIN_ALIAS_LEN and a != entry["term"] and a in window_text and a not in found:
            found.append(a)
    return found


def _clean_window_entries(entries, window_text: str, require_marker: bool = True) -> list:
    """The usable entries of one window's reply. A term the model did not copy
    from the excerpt is dropped (a normalized or invented spelling would never
    match the novel when the glossary is applied). A pronoun claim needs a quoted
    line from the excerpt, otherwise it is reset to "unclear" so a guessed gender
    never reaches the translation prompt. The ambiguity alternative is kept with
    the renderings, where a reviewer sees it."""
    out = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        term = e.get("term")
        if not isinstance(term, str) or not term.strip() or term.strip() not in window_text:
            continue
        e["term"] = term.strip()
        reason = str(e.get("reason") or "")
        evidence = e.get("evidence")
        if _PRONOUN_LEAD.match(reason) and not (isinstance(evidence, str)
                                                 and (not require_marker or _GENDER_MARKER.search(evidence))
                                                 and _quote_is_in(evidence, window_text)):
            e["reason"] = _PRONOUN_LEAD.sub("unclear", reason, count=1)
        alt = e.get("alt")
        e["extra_renderings"] = [alt.strip()] if isinstance(alt, str) and alt.strip() else []
        e["aliases"] = _window_aliases(e, window_text)
        out.append(e)
    return out


# The note rides with the term into every translation prompt, so a character
# keeps one gender instead of the model guessing from the name.
_PERSON_PRONOUN_INSTRUCTION = (
    "For every person_name, start the reason with the character's pronouns as the "
    "text shows them (she/her, he/him or they/them), taken from the written 她/他, "
    "titles and kinship terms, never guessed from the name; write \"unclear\" if the "
    "excerpt does not say. Any pronoun other than \"unclear\" needs \"evidence\": a short "
    "quote copied from the excerpt where 她/他 or a title refers to THIS person.")


def _novel_glossary_prompt(lang_name: str, categories_desc: str, policies_desc: str,
                           extras: str, sample: str) -> str:
    """One window's extraction prompt; `extras` is the known-terms, conventions and
    paired-translation blocks already joined."""
    return (
        f"Below is an excerpt from a {lang_name} novel. Extract the recurring TERMS that "
        "need consistent handling across a translation -- character names, courtesy names, "
        "sects and clans, titles and ranks, honorifics, cultivation realms, place names, "
        "artifacts, techniques, and untranslatable concepts.\n\n"
        "Extract terminology ONLY. Do not reproduce passages, plot, or dialogue -- the "
        "output is a term list, nothing more.\n\n"
        f"Categories:\n{categories_desc}\n\nHandling policies:\n{policies_desc}\n\n"
        "For each term give the category, the policy fitting its convention, and a "
        "suggested translation following that policy. If a name is MEANINGFUL (its "
        "characters spell out something thematic, or it's a pun), note that in the reason "
        "-- worth a translation note even when the name stays pinyin.\n\n"
        "Only list items that need ONE fixed English rendering across the whole book. Skip "
        "ordinary vocabulary and generic roles (incense, doctor, boss, an app's name, a "
        "plain 'brother' or 'teacher') unless the book uses them as a name or a fixed title. "
        "Copy every term character for character from the excerpt, in the same script "
        "(traditional stays traditional). Give one rendering; if the word is genuinely "
        "ambiguous (for example 阿姨 as aunt or as nanny), pick the likelier one and put the "
        "other in \"alt\".\n\n"
        "suggested_translation is only the English name or term itself, never a description of "
        "a person or thing (not \"the woman in the long green skirt\"), and never contains "
        "Chinese, Japanese or Korean characters.\n\n"
        f"{_PERSON_PRONOUN_INSTRUCTION}\n\n"
        'Return ONLY a JSON array: [{"term": "...", "suggested_translation": "...", '
        '"category": "...", "policy": "...", "reason": "brief justification", '
        '"evidence": "quote, people only", "alt": "other reading, only if ambiguous", '
        '"aliases": ["another spelling or nickname of the same term, copied exactly from the excerpt, only if it appears there"]}]. '
        f"No preamble, no markdown fences.{extras}\n\n"
        f"Excerpt:\n{sample}"
    )


def _array_reply(text: str):
    """(entries, cut_off): the JSON array a reply holds, or ([], True) when it
    starts an array it never closes (the engine hit its output limit)."""
    body = re.sub(r"^```json|^```|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return [], body.startswith("[")
    return (value if isinstance(value, list) else []), False


def _halve_window(sample: str):
    """The window cut in two at the paragraph break nearest its middle, or
    None when it is too short to be worth a second call."""
    if len(sample) < 1200:
        return None
    mid = len(sample) // 2
    cuts = [m.start() for m in re.finditer(r"\n", sample)]
    at = min(cuts, key=lambda c: abs(c - mid)) if cuts else mid
    if not 200 < at < len(sample) - 200:
        at = mid
    return sample[:at], sample[at:]


def _novel_window_entries(sample, build_prompt, engine, usage_cb, response_cache, halved=False,
                          require_marker=True):
    """One window's cleaned entries. A reply cut off at the output limit has no
    closed array, so the window is asked again as two halves (once) rather than
    losing every term in it; a window already halved keeps what it got. Raises
    TranslateRunStopped like the call it makes."""
    prompt = build_prompt(sample)
    text = response_cache[0](prompt) if response_cache else None
    cached = text is not None
    if text is None:
        text = call_llm_json(engine, prompt, max_tokens=6000, fallback="[]",
                             usage_cb=usage_cb, expect="json_array")
    entries, cut_off = _array_reply(text)
    if cut_off and not halved:
        halves = _halve_window(sample)
        if halves:
            return [e for half in halves for e in _novel_window_entries(
                half, build_prompt, engine, usage_cb, response_cache, halved=True,
                require_marker=require_marker)]
    if response_cache and not cached and any(isinstance(e, dict) and e.get("term") for e in entries):
        response_cache[1](prompt, text)
    return _clean_window_entries(entries, sample, require_marker)


def extract_glossary_from_novel(novel_text: str, engine, source_language: str = "zh",
                                 english_translation: str = "", known_terms=None,
                                 progress_cb=None, usage_cb=None, response_cache=None,
                                 conventions: str = ""):
    """
    Builds a term glossary from a novel rather than from drama dialogue.

    Two modes:
      - Source only: proposes a translation for each term following the
        convention for its category (names stay pinyin, sects go hybrid,
        and so on).
      - With `english_translation`: extracts term PAIRS as actually
        rendered in that translation. This is the stronger option --
        it captures the established wording instead of inventing a new
        one, so a drama translated later stays consistent with the novel
        readers already know.

    Only terminology is extracted -- names, places, sects, titles, ranks
    and recurring concepts. No passages of the novel are stored; the
    output is a term list.

    Returns the same shape as extract_terms_llm(), for review before
    anything is committed to a glossary.

    response_cache: optional (get(prompt) -> text or None,
    put(prompt, text)). A re-run after a crash or cancel then re-sends only
    the passages the earlier run never finished. Only a reply with at least
    one usable term is cached, so a failed or empty call is retried.
    """
    if not getattr(engine, "supports_reference", False):
        return []

    src_samples = _sample_across_text(novel_text)
    if not src_samples:
        return []
    en_samples = _sample_across_text(english_translation) if english_translation.strip() else []

    lang_name = LANGUAGE_NAMES.get(source_language, "Chinese")
    categories_desc = "\n".join(f"  - {k}: {v}" for k, v in TERM_CATEGORIES.items())
    policies_desc = "\n".join(f"  - {k}: {v['label']} (e.g. {v['example']})"
                               for k, v in TERM_POLICIES.items())

    known_block = ""
    if known_terms:
        known_block = ("\n\nAlready in the glossary -- do NOT propose these again, and where a new "
                       "term contains or is built from one, use the glossary's English for that part: "
                       + ", ".join(f'{t["term_original"]} = {t["term_translation"]}'
                                   if t.get("term_translation") else t["term_original"]
                                   for t in known_terms))

    conventions_block = ""
    if conventions.strip():
        conventions_block = ("\n\nThe person running this translation set these conventions for "
                             "terms -- follow them when choosing each suggested translation:\n"
                             + conventions.strip())

    all_terms = {}
    total = len(src_samples)
    for i, sample in enumerate(src_samples):
        paired = ""
        if i < len(en_samples):
            paired = (f"\n\nThe corresponding passage from the existing English translation "
                      f"(use it to find how each term was ACTUALLY rendered -- prefer that "
                      f"wording over inventing your own):\n{en_samples[i]}")

        context = known_block + conventions_block + paired
        try:
            entries = _novel_window_entries(
                sample, lambda part: _novel_glossary_prompt(
                    lang_name, categories_desc, policies_desc, context, part),
                engine, usage_cb, response_cache, require_marker=source_language == "zh")
        except TranslateRunStopped:
            break  # the terms from earlier windows are still returned
        _merge_novel_entries(all_terms, entries)
        if progress_cb:
            progress_cb((i + 1) / total)
    return list(all_terms.values())
