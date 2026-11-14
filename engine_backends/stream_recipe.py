"""The plain stream prompt: a short instruction block, a names-only glossary and a no-tone brief.

Tested on one Chinese stream with gemma4:31b-cloud: long rules, wide profiles and
tone words made it drop capitals and end punctuation or invent emotion, so this
recipe says only who talks, the register and the names. On by default for Gemma 4
stream runs; the `stream_plain_prompt` setting switches it off."""

from .llm_feature_prompts import glossary_term_forms
from .shared import language_name

# Starts every plain-recipe guidelines block. The engines see only the guidelines
# text (a live batch run, bulk and a resumed job all carry it), so it doubles
# as the switch for the short instruction layout, with no second flag to keep in step.
STREAM_RECIPE_MARKER = "Write normal sentences with capital letters and end punctuation."

BRIEF_PROMPT = (
    "Below is the complete text of one {language} live-stream episode (a speaker's name "
    "in [brackets] starts a line when it is known). Write a brief for the translator who "
    "will translate it in batches without seeing the rest: who talks to whom and how they "
    "address each other, and the register of the speech (for example casual, formal or "
    "intimate). Say nothing else: no names or terms list, no tense, no punctuation, "
    "no plot and no emotions. Under 80 words.\n\n{source}\n\n"
    'Return ONLY a JSON object: {{"brief": "<the brief in English>"}}. No preamble, '
    "no markdown fences.")

def build_brief_prompt(source_text: str, source_language=None) -> str:
    return BRIEF_PROMPT.format(language=language_name(source_language, "source-language"),
                               source=source_text)


_GLOSSARY_HEADING = "Names and terms (use these exact English forms):"


def is_active(style_guidelines: str | None) -> bool:
    return (style_guidelines or "").startswith(STREAM_RECIPE_MARKER)


# Food, clothing and customs are ordinary words: a glossary entry like 牛肉 = beef
# turned 肉 into "beef" everywhere. Names, titles and ruled slang stay.
_ORDINARY_CATEGORIES = ("culture_item",)


def _name_terms(glossary_terms) -> list:
    return [t for t in glossary_terms or [] if t.get("category") not in _ORDINARY_CATEGORIES]


def required_terms(glossary_terms, style_guidelines: str | None) -> list:
    """The glossary terms a run makes the English use. Under the plain recipe only the
    ones its prompt lists: a line saying "steak" for a dropped 牛肉 = beef entry must not
    be re-asked (a paid request) and flagged for it."""
    return _name_terms(glossary_terms) if is_active(style_guidelines) else (glossary_terms or [])


def names_glossary(glossary_terms) -> str:
    """The glossary as a flat source -> English list of names and special terms.
    Policy rules and category labels are left out: a rule block is what the recipe
    drops. The rows are the title's accepted entries (a model's proposals are never
    applied unreviewed), so a slang ruling a person made is kept. A term's aliases
    stand beside it: the run keeps a term when a line holds an alias, and a model
    shown only the main spelling would not connect that line to the English name."""
    rows = [f"{' / '.join(dict.fromkeys(glossary_term_forms(t)))} -> {t['term_translation']}"
            + (f" ({t['notes']})" if t.get("notes") else "")
            for t in _name_terms(glossary_terms) if t.get("term_translation")]
    return _GLOSSARY_HEADING + "\n" + "\n".join(rows) if rows else ""


def build_guidelines(glossary_terms, pronoun_default: str = "", character_notes: str = "") -> str:
    parts = [STREAM_RECIPE_MARKER, names_glossary(glossary_terms), pronoun_default.strip(),
             character_notes.strip()]
    return "\n\n".join(p for p in parts if p)


def build_instructions(source_language_name: str, locale_instruction: str, style_guidelines: str,
                       extras: str, brief: str) -> str:
    """The whole system prompt for a plain-recipe run. `extras` is the person's own
    notes (style note, series and title instructions), already one block."""
    return (
        f"You are translating {source_language_name} live-stream speech into short, natural "
        "English captions. You will be given numbered lines to translate in each request"
        ", each optionally prefixed with the speaker's name in [brackets]; never include "
        "the bracketed name in your translation.\n\n"
        + locale_instruction + "\n"
        + style_guidelines + "\n\n"
        + (f"Who talks to whom, and register:\n{brief}\n\n" if brief else "")
        + extras
        + "Return ONLY a JSON object mapping each line's number (as a string) to its "
        "translation, e.g. {\"1\": \"...\", \"2\": \"...\"} -- include EVERY number you "
        "were given, and no numbers you weren't. No preamble, no markdown fences, no "
        "commentary.")
