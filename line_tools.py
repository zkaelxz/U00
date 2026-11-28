"""
line_tools.py -- per-line operations for polishing and understanding a
single line, rather than whole-drama batch operations.

  - explain_translation():   why was this rendered this way?
  - alternative_translations(): other valid ways to render the same line
  - improve_line():          targeted re-translation of one awkward line
  - grammar_breakdown():     word-by-word structure of the source
"""

from engine_backends.medium_prompts import improve_rubric
from core import LANGUAGE_NAMES  # noqa: F401  (tests/test_shared_constants.py pins this to core's)
from engine_backends.shared import load_json_reply
from engine_backends.llm_tasks import call_llm_json, matching_glossary_terms
from engine_backends.line_text import language_name


def explain_translation(zh: str, en: str, engine, source_language: str = "zh",
                         glossary_terms=None, context: str = ""):
    """Explains the reasoning behind a specific rendering: which choices
    were interpretive, what alternatives existed, what was lost.
    `context` is a read-only block of the surrounding lines (surrounding_lines_block)."""
    if not getattr(engine, "supports_reference", False):
        return "This feature needs an LLM engine (Claude, DeepSeek, or Ollama)."

    lang = language_name(source_language, "Chinese")
    gloss = ""
    if glossary_terms:
        relevant = matching_glossary_terms(zh, glossary_terms)
        if relevant:
            gloss = "\n\nGlossary terms in play: " + ", ".join(
                f"{t['term_original']} → {t['term_translation']}" for t in relevant)

    prompt = (
        f"Explain how this {lang} line was translated:\n\n"
        f"Source: {zh}\nTranslation: {en}\n\n"
        + (f"{context}\n\n" if context else "")
        + "Cover: what the source says literally, which choices in the English were "
        "interpretive rather than direct, anything in the original that didn't fully "
        "survive (tone, wordplay, ambiguity, register), and whether the translation is "
        "reasonable. Be candid if you think a choice is questionable.\n\n"
        "A short paragraph -- explain, don't lecture." + gloss
    )
    return call_llm_json(engine, prompt, max_tokens=800, fallback="", expect="text")


def alternative_translations(zh: str, en: str, engine, count: int = 3,
                              source_language: str = "zh", style_hint: str = "", context: str = ""):
    """Offers other valid renderings of the same line, each with a note
    on what it prioritizes -- so you can pick by intent, not guesswork."""
    if not getattr(engine, "supports_reference", False):
        return []

    lang = language_name(source_language, "Chinese")
    prompt = (
        f"Give {count} alternative English translations of this {lang} line, each taking a "
        f"different valid approach (e.g. more literal, more natural, tighter, more formal).\n\n"
        f"Source: {zh}\nCurrent translation: {en}\n"
        + (f"\n{context}\n" if context else "")
        + (f"\nStyle requirements:\n{style_hint}\n" if style_hint else "")
        + "\nFor each, note in a few words what it prioritizes and what it trades away.\n\n"
        'Return ONLY a JSON array: [{"translation": "...", "approach": "...", '
        '"tradeoff": "..."}]. No preamble, no markdown fences.'
    )
    text = call_llm_json(engine, prompt, max_tokens=1200, fallback="[]", expect="json_array")
    alts = load_json_reply(text, [])
    return alts if isinstance(alts, list) else []


def improve_line(zh: str, en: str, engine, issue: str = "", source_language: str = "zh",
                  style_guidelines: str = "", kind: str = "subtitle", context: str = ""):
    """Re-translates one line that reads awkwardly. `issue` lets you say
    what's wrong ('too stiff', 'loses the sarcasm'); left blank, the
    model judges for itself."""
    if not getattr(engine, "supports_reference", False):
        return en

    lang = language_name(source_language, "Chinese")
    issue_clause = (f"\n\nThe specific problem: {issue}" if issue.strip()
                    else "\n\nIdentify what's weak about it yourself and fix that.")
    prompt = (
        f"This English translation of a {lang} line reads awkwardly. Rewrite it.\n\n"
        f"Source: {zh}\nCurrent translation: {en}"
        + (f"\n\n{context}" if context else "")
        + issue_clause
        + (f"\n\nStyle requirements:\n{style_guidelines}" if style_guidelines else "")
        + improve_rubric(kind)
        + "\n\nReturn ONLY the improved translation as plain text -- no quotes, no "
        "explanation, no preamble."
    )
    result = call_llm_json(engine, prompt, max_tokens=500, fallback="", expect="text",
                           translation_of=zh).strip()
    return result.strip('"').strip() or en


# Lines shown on each side: as Fix flagged does, enough to say who a dropped subject or
# a pronoun refers to and what tone the scene has.
CONTEXT_RADIUS = 3
# A novel paragraph can run to hundreds of characters; a neighbour is only there to be read.
CONTEXT_LINE_CHARS = 200


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= CONTEXT_LINE_CHARS else text[:CONTEXT_LINE_CHARS] + "…"


def surrounding_lines_block(lines, at: int) -> str:
    """The read-only block of up to CONTEXT_RADIUS lines before and after
    lines[at] (script order), with speakers, for a single-line prompt."""
    def row(ln):
        who = f"{ln.speaker}: " if (ln.speaker or "").strip() else ""
        return f"  {who}{_clip(ln.zh)} | English: {_clip(ln.en) or '(none)'}"

    before = [ln for ln in lines[max(0, at - CONTEXT_RADIUS):at] if (ln.zh or "").strip()]
    after = [ln for ln in lines[at + 1:at + 1 + CONTEXT_RADIUS] if (ln.zh or "").strip()]
    speaker = (lines[at].speaker or "").strip()
    if not before and not after and not speaker:
        return ""
    out = ["Surrounding script, for context only (who is speaking, who a pronoun or a "
           "missing subject refers to, the tone). Do not rewrite these lines."]
    if speaker:
        out.append(f"This line is spoken by: {speaker}")
    if before:
        out += ["Lines before it:"] + [row(ln) for ln in before]
    if after:
        out += ["Lines after it:"] + [row(ln) for ln in after]
    return "\n".join(out)


def grammar_breakdown(zh: str, engine, source_language: str = "zh"):
    """Word-by-word structural breakdown of the source line, for
    learning rather than translating."""
    if not getattr(engine, "supports_reference", False):
        return []

    lang = language_name(source_language, "Chinese")
    prompt = (
        f"Break down this {lang} sentence for a learner:\n\n{zh}\n\n"
        "For each word or meaningful unit, give: the word, its reading (pinyin for Chinese, "
        "hiragana for Japanese, romanization for Korean), its literal meaning, and its "
        "grammatical function in this sentence (subject, verb, particle, measure word, etc.).\n\n"
        'Return ONLY a JSON array: [{"word": "...", "reading": "...", "meaning": "...", '
        '"function": "..."}]. No preamble, no markdown fences.'
    )
    text = call_llm_json(engine, prompt, max_tokens=1500, fallback="[]", expect="json_array")
    parts = load_json_reply(text, [])
    return parts if isinstance(parts, list) else []
