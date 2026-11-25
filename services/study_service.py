"""services/study_service.py -- the Watch page's study breakdown of one line: its original text, each word's reading and
meaning. Never a paid call or a write: meanings come from the local CC-CEDICT file (Chinese, only once it is on disk, so
a tap never starts its download) and from words already looked up in the Reader.
"""
import os

import db
import dictionary
import segment
from lib import ids as lib_ids
from lib.errors import InvalidInputError, NotFoundError
from services import ownership_service

# One line is a sentence, not a chapter: a runaway line must not make the tap slow.
MAX_STUDY_CHARS = 400


def _cached_meanings(drama_id: int, language: str) -> dict:
    return {row["word"]: row for row in db.list_vocab_lookups(drama_id=drama_id) if row.get("language") == language}


def line_study(drama_id: int, line_id: int) -> dict:
    lib_ids.check_id(drama_id, "A title id is a positive whole number.")
    lib_ids.check_id(line_id, "A line id is a positive whole number.")
    drama = ownership_service.require_drama(drama_id)
    line = next((ln for ln in db.load_line_objects(drama_id) if ln.id == line_id), None)
    if line is None:
        raise NotFoundError("That line does not exist.")
    language = line.lang or drama.get("source_language") or "zh"
    original = line.zh or ""
    result = {"line_id": line_id, "language": language, "original": original, "words": [], "needs_install": None}
    if language not in ("zh", "ja", "ko") or not original.strip():
        return result
    if len(original) > MAX_STUDY_CHARS:
        raise InvalidInputError("That line is too long to break down.")
    try:
        pieces = segment.segment_and_annotate(original, language, chinese_script=drama.get("chinese_script") or "simplified")
    except ImportError as e:
        result["needs_install"] = e.name or "a word-splitting package"
        return result
    saved = _cached_meanings(drama_id, language)
    have_cedict = language == "zh" and os.path.exists(dictionary.CEDICT_PATH)
    for word, reading in pieces:
        if not word.strip():
            continue
        hit = dictionary.lookup_cedict(word) if have_cedict else None
        known = saved.get(word)
        if hit:
            reading, meanings = hit["pinyin"], hit["definitions"][:3]
        elif known:
            reading, meanings = reading or known.get("reading"), (known.get("definitions") or [])[:3]
        else:
            meanings = []
        result["words"].append({"word": word, "reading": reading, "meanings": meanings})
    return result
