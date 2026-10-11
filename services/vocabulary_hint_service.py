"""
vocabulary_hint_service.py -- the optional "Vocabulary: a, b, c" hint sent to
Qwen3-ASR (the processor's `prompt=`), built from a title's character names and
series glossary. One builder for the app and the CLI (both start runs through
transcribe_service.start_transcribe_run); the per-title switch is the
`vocabulary_hint` column, NULL or 0 = off.

Only names and glossary originals/aliases that people typed into the title go
in: never settings, keys, paths or URLs, so the text is safe to show and log.
"""

import re
from typing import Optional

import db

PREFIX = "Vocabulary: "
# Every segment's request carries the whole hint, and a long list makes the
# model write the words where they were not said, so keep it short: the same
# 40-term limit as Whisper's name prompt (core.build_initial_prompt) and a
# character cap that holds for names written in any script.
MAX_TERMS = 40
MAX_CHARS = 300
# A glossary "term" that is really a phrase would eat the budget and bias the
# decoder toward a whole sentence.
MAX_TERM_CHARS = 30

# Diarization labels such as SPEAKER_00 are not names anyone says.
_SPEAKER_LABEL = re.compile(r"^speaker[_ ]?\d+$", re.IGNORECASE)
_ALIAS_SPLIT = re.compile(r"[|,，、]")


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _candidates(drama_id: int, series_id) -> list:
    """Character names first (what the speech says most), then glossary terms,
    each followed by its aliases."""
    out = []
    for character in db.list_characters(drama_id):
        out.append(character.get("character_name"))
    if series_id:
        for sc in db.list_series_characters(series_id):
            out.append(sc.get("character_name"))
            out.extend(_ALIAS_SPLIT.split(sc.get("aliases") or ""))
        for term in db.list_glossary_terms(series_id):
            out.append(term.get("term_original"))
            out.extend(_ALIAS_SPLIT.split(term.get("aliases") or ""))
    return out


def build_vocabulary_hint(drama_id: int) -> str:
    """"Vocabulary: a, b, c" for this title, or "" when it has no usable names
    (the caller then sends no prompt at all)."""
    drama = db.get_drama(drama_id)
    if drama is None:
        return ""
    seen, kept, used = set(), [], len(PREFIX)
    for raw in _candidates(drama_id, drama.get("series_id")):
        term = _clean(raw)
        key = term.casefold()
        if (not term or len(term) > MAX_TERM_CHARS or key in seen
                or _SPEAKER_LABEL.match(term)):
            continue
        cost = len(term) + (2 if kept else 0)
        if len(kept) >= MAX_TERMS or used + cost > MAX_CHARS:
            break
        seen.add(key)
        kept.append(term)
        used += cost
    return PREFIX + ", ".join(kept) if kept else ""


def hint_for_run(drama: dict) -> Optional[str]:
    """The prompt for this title's Qwen3-ASR run: None (no `prompt` sent, the
    request is exactly as without the feature) unless the title's switch is on
    and it has names to send."""
    if not drama.get("vocabulary_hint"):
        return None
    return build_vocabulary_hint(drama["id"]) or None
