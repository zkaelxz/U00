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
from services.glossary_common import MAX_TERM_LEN
from services import edit_log_service, glossary_service, name_fix_service, ownership_service, translate_service

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


def _text_tools(drama: dict):
    """(cut, canon) for a Chinese title, or None when the word cutter is not installed."""
    if segment.missing_word_segmenters("zh"):
        return None
    script = drama.get("chinese_script") or "simplified"

    def cut(text: str) -> List[str]:
        return segment.segment_words(text, "zh", script)

    try:
        # Without OpenCC a Traditional/Simplified pair would look like two words, so the
        # two forms stay separate rather than failing the whole check.
        canon = segment.opencc_t2s().convert
    except ImportError:
        canon = lambda w: w  # noqa: E731
    return cut, canon


def _replace(text: str, heard: str, suggested: str, tools) -> str:
    """Replace `heard` everywhere in `text` except inside a longer ordinary word: 亞羅 must not
    change inside 亞羅斯. Without the word cutter every occurrence changes."""
    protected: List[Tuple[int, int]] = []
    if tools is not None:
        cut, canon = tools
        pos = 0
        for word in cut(text):
            at = text.find(word, pos)
            if at < 0:
                continue
            pos = at + len(word)
            if len(word) > len(heard) and heard in word and _in_dictionary(canon(word)):
                protected.append((at, at + len(word)))
    out, i = [], 0
    while (j := text.find(heard, i)) >= 0:
        out.append(text[i:j])
        inside = any(a <= j and j + len(heard) <= b for a, b in protected)
        out.append(heard if inside else suggested)
        i = j + len(heard)
    out.append(text[i:])
    return "".join(out)


def _glossary_term_id(terms: list, suggested: str):
    """The series glossary term whose text (or alias) is `suggested`; only a series term can
    take a remembered spelling."""
    for term in terms:
        if suggested == term["term_original"] or suggested in term["aliases"]:
            return term["id"]
    return None


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

    tools = _text_tools(drama)
    assert tools is not None  # missing_word_segmenters was empty above
    cut, canon = tools
    everything = db.load_line_objects(drama_id)
    lines = [(ln.idx, ln.id, ln.zh.strip()) for ln in everything
             if (ln.zh or "").strip() and not ln.sfx][:SCAN_MAX_LINES]
    kept = set(db.list_misheard_keeps(drama_id))
    result = find_misheard(lines, _trusted(drama, cut), cut, canon,
                           lambda w: _in_dictionary(canon(w)), skip=kept)
    by_idx = {ln.idx: ln for ln in everything}
    series_terms = glossary_service.list_glossary_terms(drama_id)
    for item in result["suggestions"]:
        for spot in item["lines"]:
            ln = by_idx[spot["idx"]]
            spot.update(zh=ln.zh.strip(), start=ln.start, end=ln.end)
        # Counted over every line, not just the scanned ones: it is what Use everywhere changes.
        item["line_count"] = sum(
            not ln.sfx and item["heard"] in (ln.zh or "")
            and _replace(ln.zh, item["heard"], item["suggested"], tools) != ln.zh
            for ln in everything)
        item["term_id"] = (_glossary_term_id(series_terms, item["suggested"])
                           if item["source"] == "glossary" else None)
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
    drama = ownership_service.require_drama(drama_id)
    heard, suggested = heard.strip(), suggested.strip()
    if not heard or not suggested or heard == suggested:
        raise InvalidInputError("Give the heard spelling and a different suggested one.")
    # One line the person chose is replaced as asked; "everywhere" leaves longer words alone.
    tools = _text_tools(drama) if line_id is None else None
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
    writes, new_zh, changing = [], {}, []
    for ln in targets:
        new = _replace(ln.zh, heard, suggested, tools)
        if new == ln.zh:
            continue
        changing.append(ln)
        new_zh[ln.id] = new
        writes.append((ln.id, {"zh": new, **source_change.flag_values(ln.zh, new, ln.en)},
                       {"zh": ln.zh, "en": ln.en, "flag": ln.flag}))
    if not writes:
        return {"applied": 0, "skipped": 0}
    db.save_line_history_snapshot(drama_id, lines, "before mis-heard word fix")
    missed = set(db.update_lines_fields_if_many(drama_id, writes))
    done = [ln for ln in changing if ln.id not in missed]
    if done:
        translate_service.sync_translation_status(drama_id)
        edit_log_service.record_source_edits(
            drama_id, [(ln, new_zh[ln.id]) for ln in done], edit_log_service.PERSON)
    return {"applied": len(done), "skipped": len(missed)}


def remember_spelling(drama_id: int, heard: str, suggested: str, term_id: int) -> dict:
    """Use the glossary term's spelling on every line (as `apply` does, leaving longer words
    alone) AND save the heard spelling as an alias of the term, so later transcriptions in the
    series fix it by themselves. The term must be the one the suggestion named; nothing is
    written when it is not."""
    ownership_service.require_drama(drama_id)
    heard, suggested = heard.strip(), suggested.strip()
    terms = {t["id"]: t for t in glossary_service.list_glossary_terms(drama_id)}
    term = terms.get(term_id)
    if term is None or suggested not in (term["term_original"], *term["aliases"]):
        raise InvalidInputError("That glossary term is not the one suggested.")
    if not (name_fix_service.MIN_SPELLING_LEN <= len(heard) <= MAX_TERM_LEN) or heard == suggested:
        raise InvalidInputError("A spelling must be 2 to %d characters." % MAX_TERM_LEN)
    if any(heard == t["term_original"] for t in terms.values()):
        raise InvalidInputError("That spelling is another glossary term.")
    result = apply(drama_id, heard, term["term_original"])
    added = 0
    if heard not in term["aliases"]:
        glossary_service.upsert_glossary_term(drama_id, {"id": term_id, "aliases": term["aliases"] + [heard]})
        added = 1
    return {**result, "aliases_added": added}
