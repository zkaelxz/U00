"""
translation_guide.py -- the translation *craft* layer: style presets,
character and pronoun guidance, the glossary block a prompt carries, the
drafted novel style and romanized metadata. Its siblings hold the rest of
the craft: glossary_extract (proposing terms), translation_notes (idiom,
wordplay and allusion notes) and glossary_substitution (hard term swaps).

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
from core import LANGUAGE_NAMES
from glossary_io import TERM_CATEGORIES, TERM_POLICIES, alias_note, confirmed_aliases
from engine_backends.shared import load_json_reply
from engine_backends.prompts import PRONOUN_DEFAULT_MARKER
from engine_backends.llm_tasks import call_llm_json
from engine_backends.line_text import extract_first_json_value
from glossary_extract import _sample_across_text


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


VOICE_NOTE_CHARS = 300
VOICE_NOTE_MAX_CHARACTERS = 20


def build_character_voice_notes(series_characters, drama_characters=None) -> str:
    """CHARACTER VOICE NOTES from the series character notes (speaking style,
    relationships), so a character keeps one voice across lines and episodes.
    Once the title has named characters only those are listed, so a long series
    cast does not fill the prompt; a title with none gets the whole cast. Empty
    string when no listed character has a note."""
    named = {(c.get("character_name") or "").strip().casefold()
             for c in drama_characters or []} - {""}
    # One line per character whatever the stored note holds, so a note cannot add prompt lines of its own.
    one_line = lambda text: " ".join((text or "").split())[:VOICE_NOTE_CHARS]
    entries = []
    for sc in series_characters or []:
        name = " ".join((sc.get("character_name") or "").split())
        note = one_line(sc.get("notes"))
        if name and note and (not named or name.casefold() in named):
            entries.append(f"  {name}: {note}")
    if not entries:
        return ""
    return "\n".join(["CHARACTER VOICE NOTES (keep each character's voice and register the same "
                      "from line to line):"] + entries[:VOICE_NOTE_MAX_CHARACTERS])


def build_character_guidance(series_characters, drama_characters=None,
                             default_female_pronouns: bool = False) -> str:
    """The pronoun hints and the voice notes as one block (either may be absent), so every
    translation path that carries series characters builds them the same way."""
    return "\n\n".join(b for b in (
        build_character_gender_hints(series_characters, drama_characters, default_female_pronouns),
        build_character_voice_notes(series_characters, drama_characters)) if b)


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
                            default_female_pronouns: bool = False, examples=None):
    """Assembles the full craft-guidance block injected into translation
    prompts. glossary_terms: rows from db.list_glossary_terms(), which
    may carry `category` and `policy` columns. examples: translation-memory
    entries (source_text, translation) shown as already approved wording."""
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

    if examples:
        parts.append(build_examples_block(examples))

    if custom_notes.strip():
        parts.append(f"ADDITIONAL PROJECT NOTES:\n{custom_notes.strip()}")

    return "\n\n".join(parts)


EXAMPLE_TEXT_CHARS = 200


def build_examples_block(examples) -> str:
    lines = ["APPROVED EXAMPLES -- earlier lines of this series with the translation already approved; "
             "use the same wording when the same phrase comes up:"]
    # One line per example, whatever the stored text holds, so an entry cannot add prompt lines of its own.
    one_line = lambda text: " ".join((text or "").split())[:EXAMPLE_TEXT_CHARS]
    lines += [f"  {one_line(e['source_text'])} → {one_line(e['translation'])}" for e in examples]
    return "\n".join(lines)


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

    aliases = confirmed_aliases(glossary_terms)
    lines = ["TERM GLOSSARY -- these translations are fixed, use them exactly:"]
    for policy, terms in by_policy.items():
        pol = TERM_POLICIES.get(policy, TERM_POLICIES["keep_pinyin"])
        lines.append(f"\n[{pol['label']}] {pol['guidance']}")
        for t in terms:
            cat = f" ({TERM_CATEGORIES.get(t.get('category'), '')})" if t.get("category") else ""
            note = f" -- {t['notes']}" if t.get("notes") else ""
            lines.append(f"  {t['term_original']}{alias_note(aliases.get(t['term_original']))} "
                         f"→ {t['term_translation']}{cat}{note}")
    return "\n".join(lines)


STYLE_DRAFT_SAMPLE_CHARS = 6000
STYLE_DRAFT_MAX_CHARS = 1500


def draft_novel_style_instructions(novel_text: str, engine, source_language: str = "zh",
                                   usage_cb=None) -> str:
    """One call: reads a sample of the raw novel and drafts the title's style
    instruction (genre, tone, point of view, register, how to treat slang and
    the author's voice) for the person to edit and approve. Never saved here.
    "" when the engine can't follow a prompt, the text is empty or the reply is
    not the expected JSON, so a failed draft just leaves the box empty."""
    if not getattr(engine, "supports_reference", False):
        return ""
    sample = "\n\n".join(_sample_across_text(novel_text, STYLE_DRAFT_SAMPLE_CHARS, 3))
    if not sample.strip():
        return ""
    lang_name = LANGUAGE_NAMES.get(source_language, "Chinese")
    prompt = (
        f"Below are excerpts from a {lang_name} web novel that will be translated into English. "
        "Write the style instruction a translator should follow for THIS novel: genre, tone, "
        "narrative point of view and tense, the register of narration versus dialogue, how to "
        "treat the author's slang, humour and recurring phrases.\n\n"
        "The person wants: English that reads like a published novelist or a skilled fanfiction "
        "author wrote it, not a literal translation; the author's voice and slang kept; no "
        "explanations or invented detail, and no detail lost; units, names and terms of address "
        "kept consistent.\n\n"
        "Write 5-8 short imperative sentences specific to what you see in the excerpts. Do not "
        "quote or summarise the plot. Write in English only and never tell the translator to keep "
        "any Chinese, Japanese or Korean character as written: every word of the translation is English. Return ONLY JSON: {\"instructions\": \"...\"}. "
        f"No preamble, no markdown fences.\n\nExcerpts:\n{sample}")
    text = call_llm_json(engine, prompt, max_tokens=800, fallback=None, usage_cb=usage_cb)
    data = extract_first_json_value(text, expect=dict) if text else None
    if isinstance(data, dict) and isinstance(data.get("instructions"), str):
        return _without_source_script(data["instructions"].strip())[:STYLE_DRAFT_MAX_CHARS]
    return ""


def _without_source_script(text: str) -> str:
    """Drops any sentence that carries CJK characters: a draft line such as
    "keep 凹 literal" was once copied into the translated prose, and the person
    reviewing the draft would not see why it mattered."""
    from engine_backends.translation_guard import novel_script_leak
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    return " ".join(x for x in sentences if not novel_script_leak(x))


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
    data = load_json_reply(text, {})
    return {k: str(v).strip() for k, v in data.items()
            if k in METADATA_FIELDS and str(v).strip()}
