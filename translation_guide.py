"""
translation_guide.py -- the translation *craft* layer: style presets,
per-term handling policy, idiom/wordplay/allusion notes, and automatic
glossary extraction.

The rest of the app moves text around; this module is about the actual
conventions of translating Chinese web novels and audio dramas well --
keeping 沈清疑 as "Shen Qingyi" rather than "Deep Clear Doubt", handling
成语 (four-character idioms) without flattening them, flagging that a
character's name is a pun, and knowing that 姐姐 between two women in a
baihe story is often not a literal sibling.

Nothing here auto-applies: extracted terms and generated notes are
always shown for review first, because these are judgment calls that
depend on context only a person reading the story can settle.
"""

import re
import json
from core import LANGUAGE_NAMES
from glossary_io import TERM_CATEGORIES, TERM_POLICIES
from translate_engines import PRONOUN_DEFAULT_MARKER, call_llm_json, parse_json_array


# ---------------------------------------------------------------------------
# Style presets -- register and pacing differ a lot by output medium
# ---------------------------------------------------------------------------

STYLE_PRESETS = {
    "audio_drama": {
        "label": "Audio drama (spoken aloud)",
        "guidance": (
            "This will be SPOKEN, not read. Write dialogue that sounds natural out loud: "
            "use contractions, keep sentences short enough to say in one breath, and avoid "
            "constructions that only work on the page (nested clauses, parentheticals, "
            "written-only abbreviations). Read each line back mentally -- if it would make a "
            "voice actor stumble, rephrase it. Preserve emotional beats and pauses implied by "
            "the original punctuation (ellipses, dashes) since they cue delivery."
        ),
    },
    "novel": {
        "label": "Novel / prose",
        "guidance": (
            "This is prose for reading. You have room for literary register, longer sentences, "
            "and narrative rhythm. Preserve the original's tonal shifts between narration and "
            "dialogue. Keep imagery and metaphor rather than flattening to plain description -- "
            "if the original reaches for a poetic image, the translation should too."
        ),
    },
    "subtitle": {
        "label": "Subtitles (read while watching)",
        "guidance": (
            "These are subtitles read at a glance while something else is happening on screen. "
            "Prioritize instant comprehension: front-load the key information, keep lines short, "
            "cut filler that doesn't carry meaning. A subtitle that is technically more faithful "
            "but unreadable in two seconds is worse than a slightly tighter one."
        ),
    },
    "manhua": {
        "label": "Manhua / comic (speech bubbles)",
        "guidance": (
            "These go inside speech bubbles with hard space limits. Be concise -- prefer punchy, "
            "natural comic dialogue over complete formal sentences. Sound effects and exclamations "
            "should feel like comic lettering, not prose. Match the emotional pitch of the art."
        ),
    },
}


# ---------------------------------------------------------------------------
# Baseline craft guidance -- the parts that apply regardless of settings
# ---------------------------------------------------------------------------

BASE_TRANSLATION_PRINCIPLES = """
Core principles:
- Meaning first, then naturalness, then literal word choice. Never sacrifice what a
  line actually MEANS to preserve its surface structure, and never invent meaning
  that isn't there to make a line prettier.
- Preserve character voice. A blunt character stays blunt; a formal one stays formal.
  Register differences between characters are information, not noise to smooth over.
- Preserve subtext. If a line is deliberately indirect, evasive, or loaded, the
  translation should be equally indirect -- do not "helpfully" state the implication
  the original left unsaid.
- 成语 (four-character idioms) and set phrases: prefer a natural English idiom with
  equivalent force. If none fits, translate the sense plainly rather than word-by-word
  literally, and flag it for a translation note if the imagery itself matters.
- Repetition is often deliberate. If a phrase recurs meaningfully, translate it
  consistently so the echo survives.
- Do not add explanatory content inline. Anything that needs explaining goes in a
  translation note, not padded into the dialogue.
"""

BAIHE_SPECIFIC_GUIDANCE = """
Genre notes (baihe / GL):
- Both leads are women. Pronouns must stay unambiguous in English even where Chinese
  relies on context -- but do not over-clarify to the point of clunkiness.
- Kinship-style address between non-relatives (姐姐, 妹妹, 师姐, 师妹) frequently
  carries intimacy, deference, or flirtation rather than literal family relation.
  Getting this wrong changes how the whole relationship reads. When in doubt, keep
  the romanized form and flag it.
- Do not soften, degender, or reframe romantic content between the leads. Ambiguity
  that exists in the original should stay ambiguous; explicitness that exists should
  stay explicit.
- Terms of endearment and shifts in how characters address each other often mark
  relationship progression. Preserve those shifts rather than normalizing to one
  consistent English pet name.
"""

FEMALE_PRONOUN_DEFAULT_GUIDANCE = f"""
{PRONOUN_DEFAULT_MARKER} unless context, an honorific, or a character's known gender
(see below, if given) says otherwise, default an ambiguous third-person
reference to female (she/her/hers) -- most baihe/GL casts are entirely or
almost entirely women, and spoken Mandarin doesn't distinguish 他/她/它
(all pronounced "tā"), so a transcribed pronoun's written character is not
a reliable gender signal to translate literally.
"""


PRONOUN_PRESETS = ["she/her", "he/him", "they/them"]

_LEGACY_PRONOUNS = {"female": "she/her", "male": "he/him"}


def normalize_pronouns(value) -> str:
    """Pronoun text as stored (series_characters.gender or
    characters.pronouns) -> the text shown to the translator. Maps the
    legacy "female"/"male" values; anything else (a preset or a custom
    value like "xe/xem") passes through stripped. "" for unset."""
    value = (value or "").strip()
    return _LEGACY_PRONOUNS.get(value.lower(), value)


def _drama_character_pronouns(c, series_by_name) -> str:
    """A per-drama `characters` row's effective pronouns: its own value,
    else its linked series character's (series_pronouns, from
    db.list_characters_with_series_names), else a same-named series
    character's."""
    own = normalize_pronouns(c.get("pronouns"))
    if own:
        return own
    linked = normalize_pronouns(c.get("series_pronouns"))
    if linked:
        return linked
    return series_by_name.get((c.get("character_name") or "").strip().casefold(), "")


def _series_pronouns_by_name(series_characters) -> dict:
    return {(sc.get("character_name") or "").strip().casefold(): normalize_pronouns(sc.get("gender"))
            for sc in (series_characters or []) if normalize_pronouns(sc.get("gender"))}


def build_character_gender_hints(series_characters, drama_characters=None,
                                 default_female_pronouns: bool = False) -> str:
    """series_characters: rows from db.list_series_characters() (pronoun
    text in their `gender` column). drama_characters: rows from
    db.list_characters_with_series_names() (pronoun text in `pronouns`),
    so a drama with no series still gets hints. Merged by name, with the
    per-drama value winning. Returns a block naming every character with
    pronouns set, so the translator resolves them from the assignment
    rather than from Mandarin's homophone-ambiguous 他/她/它. Empty
    string if nobody has pronouns set -- callers should skip adding this
    block entirely rather than inject an empty header.

    default_female_pronouns: also says what a speaker or character NOT listed
    gets (she/her), so a block naming only the he/him characters can't read
    as "everyone else is male". Never changes a listed character's pronouns.
    """
    series_by_name = _series_pronouns_by_name(series_characters)
    merged = {}
    for sc in series_characters or []:
        p = normalize_pronouns(sc.get("gender"))
        if p:
            merged[sc["character_name"].strip().casefold()] = (sc["character_name"].strip(), p)
    for c in drama_characters or []:
        name = (c.get("character_name") or "").strip()
        p = _drama_character_pronouns(c, series_by_name)
        if name and p:
            merged[name.casefold()] = (name, p)
    if not merged:
        return ""
    lines = ["KNOWN CHARACTER PRONOUNS (resolve this character's pronouns "
             "accordingly, overriding any other default):"]
    for name, p in merged.values():
        lines.append(f"  {name}: {p}")
    if default_female_pronouns:
        lines.append("  Any speaker or character not listed here: she/her, unless context "
                     "or an honorific says otherwise.")
    return "\n".join(lines)


def build_speaker_labels(drama_characters, series_characters=None) -> dict:
    """{speaker_label: shown name} for translate_lines_with_engine's
    character_names, e.g. {"SPEAKER_00": "Xiaoling (she/her)"} -- so the
    translator sees the pronouns on the exact line ("[Xiaoling (she/her)]
    你好"), not only in the separate hints block. Characters with no name
    are left out (a raw diarization label isn't a name)."""
    series_by_name = _series_pronouns_by_name(series_characters)
    labels = {}
    for c in drama_characters or []:
        name = (c.get("character_name") or "").strip()
        if not name:
            continue
        p = _drama_character_pronouns(c, series_by_name)
        labels[c["speaker_label"]] = f"{name} ({p})" if p else name
    return labels


def build_style_guidelines(style_preset: str = "audio_drama", glossary_terms=None,
                            include_genre_notes: bool = True, custom_notes: str = "",
                            default_female_pronouns: bool = False):
    """Assembles the full craft-guidance block injected into translation
    prompts. glossary_terms: rows from db.list_glossary_terms(), which
    may carry `category` and `policy` columns."""
    preset = STYLE_PRESETS.get(style_preset, STYLE_PRESETS["audio_drama"])

    parts = [
        f"OUTPUT MEDIUM: {preset['label']}\n{preset['guidance']}",
        BASE_TRANSLATION_PRINCIPLES,
    ]
    if include_genre_notes:
        parts.append(BAIHE_SPECIFIC_GUIDANCE)
    if default_female_pronouns:
        parts.append(FEMALE_PRONOUN_DEFAULT_GUIDANCE)

    if glossary_terms:
        parts.append(build_glossary_block(glossary_terms))

    if custom_notes.strip():
        parts.append(f"ADDITIONAL PROJECT NOTES:\n{custom_notes.strip()}")

    return "\n\n".join(parts)


def build_glossary_block(glossary_terms) -> str:
    """The TERM GLOSSARY section of build_style_guidelines(), on its own --
    also used by Scanlate's translate call (scanlate.translate_page_with_context),
    so honorifics and other glossary terms reach comic translations through the
    same rules text as Workspace, not a second honorific system. Empty string
    for no terms."""
    if not glossary_terms:
        return ""
    # Group by policy so the instruction reads as rules, not a flat list
    by_policy = {}
    for t in glossary_terms:
        policy = t.get("policy") or "keep_pinyin"
        by_policy.setdefault(policy, []).append(t)

    lines = ["TERM GLOSSARY -- these translations are fixed, use them exactly:"]
    for policy, terms in by_policy.items():
        pol = TERM_POLICIES.get(policy, TERM_POLICIES["keep_pinyin"])
        lines.append(f"\n[{pol['label']}] {pol['guidance']}")
        for t in terms:
            cat = f" ({TERM_CATEGORIES.get(t.get('category'), '')})" if t.get("category") else ""
            note = f" -- {t['notes']}" if t.get("notes") else ""
            lines.append(f"  {t['term_original']} → {t['term_translation']}{cat}{note}")
    return "\n".join(lines)


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
        'Return ONLY a JSON array: [{"term": "...", "suggested_translation": "...", '
        '"category": "...", "policy": "...", "reason": "brief justification"}]. '
        f"No preamble, no markdown fences.{known_block}\n\nText:\n{sample}"
    )

    text = call_llm_json(engine, prompt, max_tokens=4000, fallback="[]", usage_cb=usage_cb)
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
# Translation notes -- idioms, wordplay, allusions, meaningful names
# ---------------------------------------------------------------------------

NOTE_TYPES = {
    "idiom": "成语 / set phrase whose imagery or origin is worth explaining",
    "wordplay": "Pun, homophone, or double meaning that can't survive translation",
    "name_meaning": "Name whose characters carry thematic meaning",
    "allusion": "Reference to poetry, classics, history, or another work",
    "cultural": "Custom, object, or social convention unfamiliar to English readers",
    "honorific": "Form of address whose nuance is lost in direct translation",
}


def build_translation_notes_prompt(batch: list, id_fn=lambda ln: ln.idx) -> str:
    """The translation-notes prompt for one batch, each line numbered by
    id_fn(ln) (position by default, matching generate_translation_notes_llm's
    own "line_idx" output field). bulk_translate.py's bulk submission
    passes id_fn=lambda ln: ln.id -- see
    translate_engines.build_flag_prompt's docstring for why a permanent
    id matters once results can come back hours later."""
    types_desc = "\n".join(f"  - {k}: {v}" for k, v in NOTE_TYPES.items())
    pairs = "\n".join(f"[{id_fn(ln)}] {ln.zh} → {ln.en}" for ln in batch)
    return (
        "Below are source lines paired with their English translations from a baihe "
        "(GL) work. Identify places where something meaningful did NOT survive the "
        "translation and is worth a translation note for readers.\n\n"
        f"Note types:\n{types_desc}\n\n"
        "Be selective -- only flag things a reader would genuinely benefit from knowing. "
        "Do not flag ordinary translation choices, and do not flag the same term more "
        "than once. Write each note as one or two plain sentences a reader can absorb "
        "quickly; don't lecture.\n\n"
        'Return ONLY a JSON array: [{"line_idx": 0, "term": "the original term/phrase", '
        '"note_type": "...", "note": "..."}]. Empty array if nothing is worth noting. '
        "No preamble, no markdown fences.\n\n" + pairs
    )


def generate_translation_notes_llm(lines, engine, batch_size: int = 40, usage_cb=None,
                                   cancel_check=None):
    """
    Reviews translated lines for things that lost something in translation
    and are worth a translation note: idioms, puns, meaningful names,
    literary allusions, cultural specifics, honorific nuance.

    Returns a list of {line_idx, term, note_type, note} -- for review and
    optional export as a notes appendix. Doesn't modify any line text.
    cancel_check: called before each batch; it may raise to stop the
    run between batches (a batch already sent still finishes).
    """
    if not getattr(engine, "supports_reference", False):
        return []

    translated = [ln for ln in lines if ln.en.strip()]
    if not translated:
        return []

    all_notes = []

    for start in range(0, len(translated), batch_size):
        if cancel_check:
            cancel_check()
        batch = translated[start:start + batch_size]
        prompt = build_translation_notes_prompt(batch)
        text = call_llm_json(engine, prompt, max_tokens=3000, fallback="[]", usage_cb=usage_cb)
        notes = parse_json_array(text, 0)
        if isinstance(notes, list):
            for n in notes:
                if isinstance(n, dict) and n.get("note"):
                    n["note_type"] = n.get("note_type") if n.get("note_type") in NOTE_TYPES else "cultural"
                    all_notes.append(n)
    return all_notes


def group_notes_by_line(notes) -> dict:
    """notes: rows from db.list_translation_notes() (each has line_idx,
    term, note). Returns {line_idx: [{"term", "note"}, ...]} -- the shape
    core.lines_to_srt()/lines_to_bilingual_srt() take to inline notes
    into exported subtitles, right on the line each one applies to.
    Notes with no line_idx (not tied to a specific line) are dropped --
    inlining them into the subtitle track has nowhere sensible to go;
    they're still in the Markdown appendix and the in-app Reader.
    The reflection notes (note_type "reflection" -- see
    translate_engines.translate_lines_with_engine's notes_cb) are dropped
    too --
    a translator's own reasoning about a line's wording is for review,
    not a reader-facing aside, and it has no `term` to introduce it
    (unlike an idiom/allusion/etc. note, which names what it's about).
    """
    grouped = {}
    for n in notes:
        idx = n.get("line_idx")
        if idx is None or n.get("note_type") == "reflection":
            continue
        grouped.setdefault(idx, []).append({"term": n.get("term", ""), "note": n.get("note", "")})
    return grouped


def format_notes_as_markdown(notes, drama_title: str = "") -> str:
    """Renders translation notes as a readable appendix -- for export
    alongside subtitles, or as an afterword in an EPUB."""
    if not notes:
        return "# Translation Notes\n\n(No notes recorded.)\n"

    header = f"# Translation Notes{f' — {drama_title}' if drama_title else ''}\n\n"
    by_type = {}
    for n in notes:
        by_type.setdefault(n.get("note_type", "cultural"), []).append(n)

    sections = []
    for note_type, items in by_type.items():
        label = NOTE_TYPES.get(note_type, note_type).split("/")[0].strip()
        lines = [f"## {label.title()}\n"]
        for n in sorted(items, key=lambda x: x.get("line_idx", 0)):
            line_ref = f"Line {n['line_idx'] + 1}" if n.get("line_idx") is not None else ""
            term = f"**{n['term']}**" if n.get("term") else ""
            lines.append(f"- {line_ref} — {term}: {n['note']}")
        sections.append("\n".join(lines))
    return header + "\n\n".join(sections) + "\n"


# Kana, CJK ideographs and Hangul count as letters to Python's \w, but CJK
# text has no spaces between words, so only a Latin-script edge of a variant
# needs a word boundary.
_CJK_CHARS = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff"
_NON_CJK_WORD = f"[^\\W{_CJK_CHARS}]"
_CJK_RE = re.compile(f"[{_CJK_CHARS}]")


def _variant_pattern(variant: str) -> str:
    head = ""
    if re.match(r"\w", variant[0]) and not _CJK_RE.match(variant[0]):
        head = f"(?<!{_NON_CJK_WORD})"
    tail = ""
    if re.match(r"\w", variant[-1]) and not _CJK_RE.match(variant[-1]):
        tail = f"(?!{_NON_CJK_WORD})"
    return head + re.escape(variant) + tail


def _is_free_text_note(variant: str, canonical: str) -> bool:
    """`notes` doubles as the variant list and a free-text note ("her
    childhood friend"); a spaced phrase longer than the name is a note,
    unless it is just the name spaced differently ("Shen Qing Yi")."""
    if not re.search(r"\s", variant) or len(variant) <= len(canonical):
        return False
    squash = lambda t: re.sub(r"[\s\-]+", "", t).casefold()
    return squash(variant) != squash(canonical)


def apply_hard_term_substitutions(text: str, glossary_terms) -> str:
    """
    Hard find-replace for terms marked as non-negotiable (LunaTranslator's
    user-dictionary approach). The LLM glossary asks for consistency; this
    guarantees it for terms where drift is unacceptable -- typically
    character names.

    Applied to the TRANSLATED output, replacing any variant the model
    produced with the canonical form. Idempotent, because it runs again
    over lines it already corrected: a variant matches whole words only
    ("Lin" never inside "Berlin"), and a match that lies inside a canonical
    form already in the text is left alone ("Qingyi" inside "Shen Qingyi").
    Longer variants win where two could match at the same place.
    """
    # enforce_exact terms only, with variants from `notes`. A glossary term's
    # banned_translations are separate and flag-only (auto_qc.build_banned_terms
    # flags the line for review); never rewrite text from them here.
    enforced = [t for t in (glossary_terms or [])
                if t.get("enforce_exact") and t.get("term_translation")]
    if not text or not enforced:
        return text
    canonical_of = {}
    for t in sorted(enforced, key=lambda x: len(x["term_translation"]), reverse=True):
        canonical = t["term_translation"]
        for v in (t.get("notes") or "").split("|"):
            v = v.strip()
            if v and v != canonical and not _is_free_text_note(v, canonical):
                canonical_of.setdefault(v.casefold(), (v, canonical))
    if not canonical_of:
        return text
    variants = sorted(canonical_of.values(), key=lambda vc: len(vc[0]), reverse=True)
    pattern = re.compile("|".join(_variant_pattern(v) for v, _ in variants), re.IGNORECASE)
    protected = [(m.start(), m.end())
                 for canonical in {t["term_translation"] for t in enforced}
                 for m in re.finditer(re.escape(canonical), text)]
    out, pos, scan = [], 0, 0
    while True:
        m = pattern.search(text, scan)
        if m is None:
            break
        if any(s <= m.start() and m.end() <= e for s, e in protected):
            scan = m.start() + 1
            continue
        out += [text[pos:m.start()], canonical_of[m.group(0).casefold()][1]]
        pos = scan = m.end()
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------

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


def extract_glossary_from_novel(novel_text: str, engine, source_language: str = "zh",
                                 english_translation: str = "", known_terms=None,
                                 progress_cb=None, usage_cb=None, response_cache=None):
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
        known_block = ("\n\nAlready in the glossary -- do NOT propose these again: "
                       + ", ".join(t["term_original"] for t in known_terms))

    all_terms = {}
    total = len(src_samples)
    for i, sample in enumerate(src_samples):
        paired = ""
        if i < len(en_samples):
            paired = (f"\n\nThe corresponding passage from the existing English translation "
                      f"(use it to find how each term was ACTUALLY rendered -- prefer that "
                      f"wording over inventing your own):\n{en_samples[i]}")

        prompt = (
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
            'Return ONLY a JSON array: [{"term": "...", "suggested_translation": "...", '
            '"category": "...", "policy": "...", "reason": "brief justification"}]. '
            f"No preamble, no markdown fences.{known_block}{paired}\n\n"
            f"Excerpt:\n{sample}"
        )

        text = response_cache[0](prompt) if response_cache else None
        cached = text is not None
        if not cached:
            text = call_llm_json(engine, prompt, max_tokens=4000, fallback="[]",
                                 usage_cb=usage_cb)
        entries = parse_json_array(text, 0)
        if (response_cache and not cached and isinstance(entries, list)
                and any(isinstance(e, dict) and e.get("term") for e in entries)):
            response_cache[1](prompt, text)
        if isinstance(entries, list):
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
                e["renderings"] = renderings
                all_terms[e["term"]] = e
        if progress_cb:
            progress_cb((i + 1) / total)
    return list(all_terms.values())


# ---------------------------------------------------------------------------
# Metadata romanization
# ---------------------------------------------------------------------------

METADATA_FIELDS = {
    "author": "Author / writer name",
    "studio": "Studio, publisher, or platform",
    "director": "Director",
    "voice_actors": "Cast list (comma-separated)",
}


def romanize_metadata(drama_meta: dict, engine, source_language: str = "zh", usage_cb=None):
    """
    Produces readable versions of the credits while leaving the originals
    untouched -- 一半山川 stays, and gains "Yiban Shanchuan" beside it.

    Personal names are romanized rather than translated (a name is a name,
    not a phrase to render). Studios and platforms usually have an
    established English or official name worth using instead of a bare
    transliteration -- 晋江文学城 is normally written "JJWXC" rather than
    "Jinjiang Literature City".

    Returns {field: romanized_value} for whichever fields had content.
    """
    if not getattr(engine, "supports_reference", False):
        return {}

    present = {k: (drama_meta.get(k) or "").strip()
               for k in METADATA_FIELDS if (drama_meta.get(k) or "").strip()}
    if not present:
        return {}

    lang = LANGUAGE_NAMES.get(source_language, "Chinese")
    listing = "\n".join(f"{k}: {v}" for k, v in present.items())

    prompt = (
        f"Below are credits from a {lang} work. Give a readable English form for each.\n\n"
        "Rules:\n"
        "- PERSONAL NAMES (author, director, voice actors): romanize them "
        "(pinyin for Chinese, Hepburn for Japanese, Revised Romanization for Korean). "
        "Don't translate the meaning of the characters -- a name is a name.\n"
        "- STUDIOS AND PLATFORMS: if there's an established English or official name, use "
        "that instead of a transliteration (e.g. 晋江文学城 is normally written 'JJWXC'). "
        "Otherwise romanize.\n"
        "- Keep comma-separated lists in the same order, comma-separated.\n"
        "- If you're unsure of an established name, romanize rather than guess.\n\n"
        'Return ONLY a JSON object keyed by the same field names: '
        '{"author": "...", "studio": "..."}. No preamble, no markdown fences.\n\n'
        + listing
    )

    text = call_llm_json(engine, prompt, max_tokens=800, fallback="{}", usage_cb=usage_cb)
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return {k: str(v).strip() for k, v in data.items()
            if k in METADATA_FIELDS and str(v).strip()}
