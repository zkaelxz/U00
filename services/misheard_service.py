"""
services/misheard_service.py -- read a Chinese title's source lines and list the words that
may have been mis-heard (misheard_finder has the rules).

Finding is read-only; `apply`, on a person's click, is the only write. The trusted words are the title's glossary terms and
aliases, the series' character names, and the title and summary text. Offline and free.
Title visibility is checked by the route's `{drama_id}` path guard (api/auth.py). No FastAPI
import.
"""
import logging
from typing import List, Tuple

import db
import glossary_phonetic as ph
import segment
import source_change
from lib.errors import ConflictError, InvalidInputError
from misheard_finder import find_misheard
from services import edit_log_service, ownership_service, translate_service

log = logging.getLogger(__name__)

SCAN_MAX_LINES = 5000   # same budget as the other whole-title scans


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason, "lines_scanned": 0, "suggestions": [],
            "truncated": False, "kept_count": 0}


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

    everything = db.load_line_objects(drama_id)
    lines = [(ln.idx, ln.id, ln.zh.strip()) for ln in everything
             if (ln.zh or "").strip() and not ln.sfx][:SCAN_MAX_LINES]
    try:
        # Without OpenCC a Traditional/Simplified pair would look like two words, so the
        # two forms stay separate rather than failing the whole check.
        canon = segment.opencc_t2s().convert
    except ImportError:
        canon = lambda w: w  # noqa: E731
    result = find_misheard(lines, _trusted(drama, cut), cut, canon,
                           lambda w: _in_dictionary(canon(w)))
    text_of = {i: text for i, _lid, text in lines}
    for item in result["suggestions"]:
        for spot in item["lines"]:
            spot["zh"] = text_of[spot["idx"]]
        # Counted over every line, not just the scanned ones: it is what Use everywhere changes.
        item["line_count"] = sum(item["heard"] in (ln.zh or "") and not ln.sfx for ln in everything)
    kept = set(db.list_misheard_keeps(drama_id))
    result["suggestions"] = [s for s in result["suggestions"] if (s["heard"], s["suggested"]) not in kept]
    return {"available": True, "reason": "", "lines_scanned": len(lines), "kept_count": len(kept),
            **result}


def keep_as_heard(drama_id: int, heard: str, suggested: str) -> dict:
    """Remember that this heard spelling is right for this title, so it is not offered again."""
    ownership_service.require_drama(drama_id)
    heard, suggested = heard.strip(), suggested.strip()
    if not heard or not suggested or heard == suggested:
        raise InvalidInputError("Give the heard spelling and a different suggested one.")
    db.add_misheard_keep(drama_id, heard, suggested)
    return {"kept": len(db.list_misheard_keeps(drama_id))}


def show_kept_again(drama_id: int) -> dict:
    ownership_service.require_drama(drama_id)
    return {"cleared": db.clear_misheard_keeps(drama_id)}


def apply(drama_id: int, heard: str, suggested: str, line_id: int | None = None,
          expected_zh: str | None = None) -> dict:
    """Replace `heard` with `suggested` in one line's source text, or in every line that holds
    it. Source text only: a translated line then carries the "Source changed" flag, as for any
    other source edit. Each write is a compare-and-set on the text read here, so a line edited
    meanwhile is skipped. One History snapshot is taken first. {applied, skipped}."""
    ownership_service.require_drama(drama_id)
    heard, suggested = heard.strip(), suggested.strip()
    if not heard or not suggested or heard == suggested:
        raise InvalidInputError("Give the heard spelling and a different suggested one.")
    if line_id is not None and expected_zh is None:
        raise InvalidInputError("expected_zh is needed with line_id.")
    lines = db.load_line_objects(drama_id)
    targets = [ln for ln in lines if heard in (ln.zh or "") and not ln.sfx
               and (line_id is None or ln.id == line_id)]
    if line_id is not None:
        if not targets:
            raise ConflictError("That line changed since you loaded it.")
        # The list shows the stripped text, so that is what comes back.
        if targets[0].zh.strip() != (expected_zh or "").strip():
            raise ConflictError("That line changed since you loaded it.")
    if not targets:
        return {"applied": 0, "skipped": 0}
    writes, new_zh = [], {}
    for ln in targets:
        new = ln.zh.replace(heard, suggested)
        new_zh[ln.id] = new
        writes.append((ln.id, {"zh": new, **source_change.flag_values(ln.zh, new, ln.en)},
                       {"zh": ln.zh, "en": ln.en, "flag": ln.flag}))
    db.save_line_history_snapshot(drama_id, lines, "before mis-heard word fix")
    missed = set(db.update_lines_fields_if_many(drama_id, writes))
    done = [ln for ln in targets if ln.id not in missed]
    if done:
        translate_service.sync_translation_status(drama_id)
        edit_log_service.record_source_edits(
            drama_id, [(ln, new_zh[ln.id]) for ln in done], edit_log_service.PERSON)
    return {"applied": len(done), "skipped": len(missed)}
