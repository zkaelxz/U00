"""
services/misheard_service.py -- read a Chinese title's source lines and list the words that
may have been mis-heard (misheard_finder has the rules).

Read-only: it never changes a line. The trusted words are the title's glossary terms and
aliases, the series' character names, and the title and summary text. Offline and free.
Title visibility is checked by the route's `{drama_id}` path guard (api/auth.py). No FastAPI
import.
"""
import logging
from typing import List, Tuple

import db
import glossary_phonetic as ph
import segment
from misheard_finder import find_misheard
from services import ownership_service

log = logging.getLogger(__name__)

SCAN_MAX_LINES = 5000   # same budget as the other whole-title scans


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason, "lines_scanned": 0, "suggestions": [],
            "truncated": False}


def _trusted(drama: dict, cut) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for term in db.list_run_glossary_terms(drama):
        out.append((term["term_original"], "glossary"))
        out.extend((a.strip(), "glossary") for a in (term.get("aliases") or "").split("|"))
    series_id = drama.get("series_id")
    for ch in (db.list_series_characters(series_id) if series_id else []):
        out.append((ch["character_name"], "name"))
        out.extend((a.strip(), "name") for a in (ch.get("aliases") or "").split("|"))
    for text in (drama.get("title_zh"), drama.get("summary")):
        out.extend((w, "title") for w in cut(text or ""))
    return out


def _in_dictionary(word: str) -> bool:
    # jieba's frequency table holds only words it knows; a zero entry is a prefix placeholder.
    return bool(segment._load_jieba().dt.FREQ.get(word))


def find_for_title(drama_id: int) -> dict:
    """{available, reason, lines_scanned, truncated, suggestions[]}. Chinese titles only."""
    drama = ownership_service.require_drama(drama_id)
    if (drama.get("source_language") or "zh") != "zh":
        return _unavailable("Mis-heard word suggestions work for Chinese titles only.")
    status = ph.availability("zh")
    if not status["available"]:
        return _unavailable(status["hint"])
    missing = segment.missing_word_segmenters("zh")
    if missing:
        return _unavailable(f"Chinese word cutting needs the {missing[0]} package "
                            f"(pip install {missing[0]}).")

    script = drama.get("chinese_script") or "simplified"

    def cut(text: str) -> List[str]:
        return segment.segment_words(text, "zh", script)

    lines = [(ln.idx, ln.id, ln.zh.strip()) for ln in db.load_line_objects(drama_id)
             if (ln.zh or "").strip() and not ln.sfx][:SCAN_MAX_LINES]
    try:
        # Without OpenCC a Traditional/Simplified pair would look like two words, so the
        # two forms stay separate rather than failing the whole check.
        canon = segment.opencc_t2s().convert
    except ImportError:
        canon = lambda w: w  # noqa: E731
    result = find_misheard(lines, _trusted(drama, cut), cut, canon,
                           lambda w: _in_dictionary(canon(w)))
    return {"available": True, "reason": "", "lines_scanned": len(lines), **result}
