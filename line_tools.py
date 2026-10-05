"""
line_tools.py -- per-line operations for polishing and understanding a
single line, rather than whole-drama batch operations.

  - explain_translation():   why was this rendered this way?
  - alternative_translations(): other valid ways to render the same line
  - improve_line():          targeted re-translation of one awkward line
  - grammar_breakdown():     word-by-word structure of the source
  - pronunciation_audio():   hear a name or phrase in the source language
"""

import re
import json
import os
from core import LANGUAGE_NAMES  # noqa: F401  (tests/test_shared_constants.py pins this to core's)
from translate_engines import call_llm_json, language_name, matching_glossary_terms


def explain_translation(zh: str, en: str, engine, source_language: str = "zh",
                         glossary_terms=None):
    """Explains the reasoning behind a specific rendering: which choices
    were interpretive, what alternatives existed, what was lost."""
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
        "Cover: what the source says literally, which choices in the English were "
        "interpretive rather than direct, anything in the original that didn't fully "
        "survive (tone, wordplay, ambiguity, register), and whether the translation is "
        "reasonable. Be candid if you think a choice is questionable.\n\n"
        "A short paragraph -- explain, don't lecture." + gloss
    )
    return call_llm_json(engine, prompt, max_tokens=800, fallback="")


def alternative_translations(zh: str, en: str, engine, count: int = 3,
                              source_language: str = "zh", style_hint: str = ""):
    """Offers other valid renderings of the same line, each with a note
    on what it prioritizes -- so you can pick by intent, not guesswork."""
    if not getattr(engine, "supports_reference", False):
        return []

    lang = language_name(source_language, "Chinese")
    prompt = (
        f"Give {count} alternative English translations of this {lang} line, each taking a "
        f"different valid approach (e.g. more literal, more natural, tighter, more formal).\n\n"
        f"Source: {zh}\nCurrent translation: {en}\n"
        + (f"Context/style: {style_hint}\n" if style_hint else "")
        + "\nFor each, note in a few words what it prioritizes and what it trades away.\n\n"
        'Return ONLY a JSON array: [{"translation": "...", "approach": "...", '
        '"tradeoff": "..."}]. No preamble, no markdown fences.'
    )
    text = call_llm_json(engine, prompt, max_tokens=1200, fallback="[]")
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        alts = json.loads(text)
        return alts if isinstance(alts, list) else []
    except json.JSONDecodeError:
        return []


def improve_line(zh: str, en: str, engine, issue: str = "", source_language: str = "zh",
                  style_guidelines: str = ""):
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
        + issue_clause
        + (f"\n\nStyle requirements:\n{style_guidelines}" if style_guidelines else "")
        + "\n\nReturn ONLY the improved translation as plain text -- no quotes, no "
        "explanation, no preamble."
    )
    result = call_llm_json(engine, prompt, max_tokens=500, fallback="").strip()
    return result.strip('"').strip() or en


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
    text = call_llm_json(engine, prompt, max_tokens=1500, fallback="[]")
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        parts = json.loads(text)
        return parts if isinstance(parts, list) else []
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Pronunciation -- reuses the existing TTS stack, no new dependency
# ---------------------------------------------------------------------------

SOURCE_LANG_VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
}


def pronunciation_audio(text: str, out_path: str, source_language: str = "zh"):
    """Generates audio of a name or phrase in the SOURCE language, so you
    can hear how 沈清疑 is actually said. Uses edge-tts (free, already a
    dependency for dubbing). Returns the path, or None on failure --
    pronunciation is a nice-to-have, so a failure here shouldn't
    interrupt reading."""
    try:
        import dub
        voice = SOURCE_LANG_VOICES.get(source_language, SOURCE_LANG_VOICES["zh"])
        dub.synthesize_line(text, voice, out_path)
        return out_path if os.path.exists(out_path) else None
    except Exception:
        return None


