"""engine_backends/llm_feature_prompts.py -- The prompt builders and pure helpers of the
single-prompt LLM features (segmentation, consistency, summary, flagging).

Moved out of engine_backends/llm_tasks.py to keep it under the size ceiling; that
module re-exports these names. The functions that call `call_llm_json` stay there
because tests patch `engine_backends.llm_tasks.call_llm_json`. This module imports
nothing from llm_tasks, so it can be imported on its own.
"""

import re

from .flag_reasons import FLAG_REASONS, SYSTEM_FLAG_REASONS
from .shared import language_name


def smart_segment_lines(en_lines, target_wpm: float = 160, min_seconds: float = 1.2,
                        too_long_margin: float = 1.15, short_ratio: float = 0.4):
    """Post-translation pacing pass (idea borrowed from KrillinAI/
    VideoLingo): flags English lines that are too short to read
    comfortably at their assigned duration, or too long to say
    naturally within it, using a simple words-per-minute estimate.
    Returns a list of {idx, issue, suggestion} for lines worth a second
    look before dubbing -- this doesn't auto-edit anything, just tells
    you where the pacing is likely to feel rushed or dragged out.
    `severity` (how many times over the slot, or how empty it is) lets a
    caller list the worst first."""
    flags = []
    for ln in en_lines:
        # Untranslated lines are already counted as untranslated; flagging them here too inflates the coverage badge.
        if not (ln.en or "").strip():
            continue
        duration = max(ln.end - ln.start, 0.01)
        word_count = len(ln.en.split())
        needed_seconds = word_count / (target_wpm / 60)
        if needed_seconds > duration * too_long_margin:
            flags.append({"idx": ln.idx, "issue": "too_long_for_slot",
                          "severity": needed_seconds / duration,
                          "detail": f"~{needed_seconds:.1f}s needed, only {duration:.1f}s available"})
        elif duration > min_seconds and needed_seconds < duration * short_ratio:
            flags.append({"idx": ln.idx, "issue": "very_short_relative_to_slot",
                          "severity": duration / max(needed_seconds, 0.01),
                          "detail": f"~{needed_seconds:.1f}s needed, {duration:.1f}s available -- "
                                    "may sound like a long pause"})
    return flags


def build_consistency_prompt(batch: list, source_language=None) -> str:
    """The consistency-check prompt for one window of already-translated
    lines. Position-based (1., 2., ...), not id-keyed -- an issue names a
    TERM ("a character's name spelled two ways"), never a specific line,
    so there's no per-line id for the model to echo back here. Shared by
    check_consistency_llm (live) and bulk_translate.py's bulk submission:
    both send byte-for-byte the same prompt for the same
    window of lines."""
    pairs = "\n".join(f"{i+1}. {ln.zh} -> {ln.en}" for i, ln in enumerate(batch))
    return (
        f"Below are {language_name(source_language, 'source-language')} source lines paired with "
        "their English translations, from the "
        "same drama. Look for CONSISTENCY issues: the same source-language name, term, or recurring "
        "phrase translated differently in different lines (e.g. a character's name spelled "
        "two ways, or a recurring term like a nickname/title rendered inconsistently). "
        "Ignore normal translation variation for ordinary sentences -- only flag terms that "
        "should clearly stay fixed (names, titles, recurring phrases) but don't.\n\n"
        'Return ONLY a JSON array of objects: [{"term": "...", "variants": ["...", "..."], '
        '"note": "brief description"}]. Empty array if nothing found. No preamble, no '
        "markdown fences.\n\n" + pairs
    )


def build_episode_summary_prompt(lines) -> str:
    """The per-episode running-summary prompt -- one call over the
    WHOLE finished episode's English text, not a per-batch window (unlike
    build_consistency_prompt above), since the point is a fixed, once-per-
    episode artifact that the next episode's translation can afford to
    read in full."""
    body = "\n".join(ln.en.strip() for ln in lines if (ln.en or "").strip())
    return (
        "Below is the complete English translation of one finished episode from an "
        "ongoing series. Write a short running summary for continuity into the NEXT "
        "episode of the same series: key events, any unresolved plot threads or "
        "questions, and each named character's state at the end of this episode "
        "(relationships, secrets revealed or still hidden, where they ended up). A "
        "few sentences of plain prose -- no preamble, no episode-by-episode recap of "
        "earlier episodes, just what a translator picking up the next episode cold "
        "would need to resolve a callback or an ambiguous reference correctly.\n\n"
        'Return ONLY a JSON object: {"summary": "..."}. No markdown fences, no other '
        "keys.\n\n" + body
    )


def matching_glossary_terms(zh: str, glossary_terms) -> list:
    """Glossary entries whose source term (or a recorded alias)
    literally appears in zh -- the same "in play for this line" heuristic
    line_tools.explain_translation already used, factored out so the
    "what happened here?" view can show the same real, non-fabricated
    match set instead of re-deriving it differently."""
    def _term_forms(t):
        aliases = [a.strip() for a in re.split(r"[|,，、]", t.get("aliases") or "") if a.strip()]
        return [t.get("term_original", "")] + aliases
    return [t for t in (glossary_terms or []) if any(f and f in zh for f in _term_forms(t))]


def build_flag_prompt(batch: list, id_fn=lambda ln: ln.idx, source_language=None) -> str:
    """The review-queue prompt for one batch of already-translated lines,
    each numbered by id_fn(ln) (its position by default, matching what
    flag_uncertain_lines' own by_idx lookup expects back). bulk_translate.py's
    bulk submission passes id_fn=lambda ln: ln.id instead --
    results can come back hours later, by which point a position-based id
    could point at an entirely different line if the drama was edited in
    the meantime, where the permanent line id can't."""
    reasons_desc = "\n".join(f"  - {k}: {v}" for k, v in FLAG_REASONS.items())
    pairs = "\n".join(f"[{id_fn(ln)}] {ln.zh} -> {ln.en}" for ln in batch)
    return (
        f"Below are {language_name(source_language, 'source-language')} source lines paired with "
        "their English translations. Flag ONLY "
        "the lines that genuinely need a second look -- most lines need none at all, and "
        "over-flagging defeats the point (the person reviewing this can't tell a real issue "
        "from noise). Reasons worth flagging:\n\n"
        f"{reasons_desc}\n\n"
        'Return ONLY a JSON array: [{"line_idx": 0, "reason": "uncertain_translation", '
        '"note": "brief reason"}]. Empty array if nothing needs flagging (the common case). '
        "No preamble, no markdown fences.\n\n" + pairs
    )


def flaggable_lines(lines) -> list:
    """Translated lines without a system flag, which the model must not replace."""
    return [ln for ln in lines if (ln.en or "").strip() and ln.flag not in SYSTEM_FLAG_REASONS]
