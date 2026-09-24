"""
story_context.py -- the "help me follow the story" layer: who is this
character, how does everyone relate, what happened in earlier chapters,
and how long will this take to get through.

Everything here is grounded strictly in the drama's own lines. These
functions are told not to invent plot beyond what's in the text, and
to say so plainly when the answer isn't there -- a confidently wrong
character summary is worse than "not covered in what's loaded".
"""

import re
import json
from translate_engines import call_llm_json, _parse_json_array


# ---------------------------------------------------------------------------
# Reading / listening time estimates (no LLM needed)
# ---------------------------------------------------------------------------

def estimate_reading_time(lines, wpm: int = 220) -> dict:
    """Estimates reading time from the translated text. 220 wpm is a
    common average for comfortable adult reading of fiction."""
    words = sum(len(ln.en.split()) for ln in lines if ln.en)
    minutes = words / wpm if wpm else 0
    return {"word_count": words, "minutes": round(minutes, 1),
            "display": _format_duration(minutes)}


def estimate_listening_time(lines) -> dict:
    """For timed content, the real duration is just the last line's end
    timestamp -- no estimation needed."""
    if not lines:
        return {"seconds": 0, "display": "0m"}
    seconds = max(ln.end for ln in lines)
    return {"seconds": seconds, "display": _format_duration(seconds / 60)}


def _format_duration(minutes: float) -> str:
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{int(round(minutes))}m"
    hours, mins = divmod(int(round(minutes)), 60)
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def compute_percent_complete(last_line_idx: int, total_lines: int) -> float:
    if not total_lines:
        return 0.0
    return round(min(100.0, ((last_line_idx + 1) / total_lines) * 100), 1)


# ---------------------------------------------------------------------------
# Character lookup & relationship map
# ---------------------------------------------------------------------------

def who_is_character(character_name: str, lines, drama_meta: dict, engine,
                      max_context_lines: int = 250):
    """Answers 'who is this character?' from the drama's own dialogue.
    Prioritizes lines that actually mention the character so the context
    window is spent on relevant material rather than the opening scene."""
    if not getattr(engine, "supports_reference", False):
        return "This feature needs an LLM engine (Claude, DeepSeek, or Ollama)."

    mentioning = [ln for ln in lines if character_name in ln.zh or character_name in (ln.en or "")]
    context_lines = (mentioning or lines)[:max_context_lines]
    excerpt = "\n".join(f"[{ln.idx}] ({ln.speaker or '?'}) {ln.zh} → {ln.en}" for ln in context_lines)

    prompt = (
        f"Using ONLY the excerpt below from \"{drama_meta.get('title_en') or drama_meta.get('title_zh', 'this work')}\", "
        f"describe who {character_name} is: their role in the story, how they relate to the other "
        "characters, and anything notable about how they speak or are addressed. "
        "If the excerpt doesn't establish something, say so rather than guessing. "
        "Do not include plot developments that aren't in the excerpt -- the reader may not "
        "have gotten that far. Keep it to a short paragraph.\n\n" + excerpt
    )
    return call_llm_json(engine, prompt, max_tokens=800, fallback="")


def build_relationship_map(lines, drama_meta: dict, engine, max_context_lines: int = 400):
    """Extracts the cast and how they relate to each other. Returns
    {"characters": [{name, role, description}],
     "relationships": [{from, to, relation, note}]} -- structured so it
    can be rendered as a diagram, not just prose."""
    if not getattr(engine, "supports_reference", False):
        return {"characters": [], "relationships": []}

    excerpt = "\n".join(f"({ln.speaker or '?'}) {ln.zh} → {ln.en}"
                        for ln in lines[:max_context_lines] if ln.en)
    if not excerpt.strip():
        return {"characters": [], "relationships": []}

    prompt = (
        "From the dialogue excerpt below, map out the cast and their relationships.\n\n"
        "For each character: their name as it appears, their role, and a one-line description.\n"
        "For each relationship: who relates to whom, the nature of it (e.g. 'martial sisters', "
        "'rivals', 'romantic interest', 'master and disciple'), and a brief note. Include "
        "romantic and implied-romantic relationships -- this is a baihe (GL) work and those "
        "relationships are usually central, not incidental.\n\n"
        "Base this ONLY on what the excerpt shows. Don't infer relationships the text doesn't "
        "support.\n\n"
        'Return ONLY JSON: {"characters": [{"name": "...", "role": "...", "description": "..."}], '
        '"relationships": [{"from": "...", "to": "...", "relation": "...", "note": "..."}]}. '
        "No preamble, no markdown fences.\n\n" + excerpt
    )
    text = call_llm_json(engine, prompt, max_tokens=2500, fallback="{}")
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        return {"characters": data.get("characters", []),
                "relationships": data.get("relationships", [])}
    except json.JSONDecodeError:
        return {"characters": [], "relationships": []}


def relationship_map_to_mermaid(rel_map: dict) -> str:
    """Renders a relationship map as a Mermaid graph, which Streamlit
    and most Markdown viewers can display directly."""
    chars = rel_map.get("characters", [])
    rels = rel_map.get("relationships", [])
    if not chars and not rels:
        return ""

    def node_id(name):
        return "N" + re.sub(r"\W", "", str(name))[:20]

    lines = ["graph TD"]
    for c in chars:
        name = c.get("name", "")
        role = c.get("role", "")
        label = f"{name}<br/><i>{role}</i>" if role else name
        lines.append(f'    {node_id(name)}["{label}"]')
    for r in rels:
        a, b = r.get("from", ""), r.get("to", "")
        if not a or not b:
            continue
        rel = str(r.get("relation", "")).replace('"', "'")
        lines.append(f'    {node_id(a)} -->|"{rel}"| {node_id(b)}')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chapter / section summaries ("what happened before this")
# ---------------------------------------------------------------------------

def summarize_section(lines, engine, section_label: str = "", spoiler_safe: bool = True):
    """Summarizes a span of lines -- e.g. everything before the page
    you're resuming on, as a 'previously on...' recap.

    spoiler_safe: summarize only the lines given, never speculate about
    or hint at what comes after them."""
    if not getattr(engine, "supports_reference", False):
        return "This feature needs an LLM engine (Claude, DeepSeek, or Ollama)."
    translated = [ln for ln in lines if ln.en]
    if not translated:
        return "Nothing translated in this range yet."

    excerpt = "\n".join(f"({ln.speaker or '?'}) {ln.en}" for ln in translated)
    spoiler_clause = (
        " Summarize ONLY what happens in this excerpt. Do not speculate about what comes "
        "next or reference anything outside it -- the reader hasn't gotten there yet."
        if spoiler_safe else ""
    )
    prompt = (
        f"Write a brief recap of this section{f' ({section_label})' if section_label else ''}, "
        "the kind of 'previously...' summary that reminds someone where they left off. "
        "Cover the main developments and any shift in the relationship between the leads. "
        f"Two or three sentences.{spoiler_clause}\n\n" + excerpt
    )
    return call_llm_json(engine, prompt, max_tokens=600, fallback="")


def explain_reference(phrase: str, lines, engine, source_language: str = "zh"):
    """On-demand explanation of an idiom, allusion, or cultural reference
    the reader hit -- complements the pre-generated translation notes."""
    if not getattr(engine, "supports_reference", False):
        return "This feature needs an LLM engine (Claude, DeepSeek, or Ollama)."

    context = "\n".join(f"{ln.zh} → {ln.en}" for ln in lines
                        if phrase in ln.zh or phrase in (ln.en or ""))[:3000]
    lang = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}.get(source_language, "Chinese")
    prompt = (
        f"Explain this {lang} phrase for an English-speaking reader: \"{phrase}\"\n\n"
        "Cover its literal meaning, its figurative/idiomatic sense if it has one, and any "
        "cultural or literary background worth knowing. If it's a 成语 or set phrase, mention "
        "where it comes from. Keep it to a short paragraph -- informative, not a lecture.\n"
        + (f"\nHow it's used here:\n{context}" if context.strip() else "")
    )
    return call_llm_json(engine, prompt, max_tokens=700, fallback="")


# ---------------------------------------------------------------------------

