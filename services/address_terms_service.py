"""services/address_terms_service.py -- the common forms of address (姐姐, さん, 오빠) in a title's source lines, and saving one rendering per term as glossary entries.

Offline and free: a fixed per-language list is counted in the saved lines; nothing is sent to an engine.
The scan never writes. Saving goes through glossary_service.upsert_glossary_term, so the glossary's own
limits and series handling apply. Title visibility is checked by the route's `{drama_id}` path guard.
No FastAPI import.
"""
import re
from typing import Dict, List

from services import glossary_service
from services.glossary_common import MAX_TERM_LEN, _drama, _text
from services.glossary_variants_service import _snippet, _source_lines
from lib.errors import InvalidInputError

CATEGORY = "honorific"      # the glossary's "Honorific / form of address"
MIN_COUNT = 2               # a form heard once is not a habit worth fixing
MAX_RESULTS = 20
MAX_EXAMPLES = 3
MAX_SAVE = 50
KEPT_NOTE = "Address term: keep the source form (romanize it)."

# Short suffixes are matched only after a name character: bare "さん" is inside たくさん,
# bare "씨" inside 씨발. Words are matched wherever they stand.
_WORDS: Dict[str, List[str]] = {
    "zh": ["姐姐", "哥哥", "弟弟", "妹妹", "大哥", "大姐", "师兄", "师姐", "师弟", "师妹", "師兄", "師姐",
           "師弟", "師妹", "师父", "師父", "师傅", "老师", "老師", "先生", "小姐", "姑娘", "公子", "少爷",
           "夫人", "前辈", "前輩", "大人", "陛下", "殿下", "娘子", "相公", "夫君", "爷爷", "奶奶", "叔叔", "阿姨"],
    "ja": ["先輩", "後輩", "先生", "師匠", "お兄ちゃん", "お姉ちゃん", "お兄さん", "お姉さん", "お兄様", "お姉様",
           "兄さん", "姉さん", "兄貴", "姉御", "おじさん", "おばさん"],
    "ko": ["오빠", "형", "형님", "누나", "언니", "선배", "후배", "선생님", "아저씨", "아줌마", "동생"],
}
_SUFFIXES: Dict[str, List[str]] = {
    "zh": [],
    "ja": ["さん", "くん", "ちゃん", "さま"],
    "ko": ["씨", "님"],
}
_NAME_BEFORE = {"zh": "", "ja": "一-鿿ァ-ヺーA-Za-z", "ko": "가-힣"}
# Particles that may follow a Korean word, so 형이 counts but 형사 does not.
_KO_PARTICLES = "이가은는을를도의야아랑과와한에께만"


def _pattern(language: str) -> "re.Pattern[str]":
    words, suffixes = _WORDS[language], _SUFFIXES[language]
    parts = [(t, "") for t in words] + [(t, f"(?<=[{_NAME_BEFORE[language]}])") for t in suffixes]
    if language == "ko":
        parts = [(t, look or "(?<![가-힣])") for t, look in parts]
    # Longest first so お兄さん is one hit and not 兄さん or さん.
    parts.sort(key=lambda p: -len(p[0]))
    body = "|".join(look + re.escape(t) for t, look in parts)
    tail = f"(?:(?![가-힣])|(?=[{_KO_PARTICLES}]))" if language == "ko" else ""
    return re.compile(f"(?:{body}){tail}")


_PATTERNS = {lang: _pattern(lang) for lang in _WORDS}


def _known(language: str) -> List[str]:
    return _WORDS.get(language, []) + _SUFFIXES.get(language, [])


def _in_glossary(drama_id: int) -> set:
    taken = set()
    for t in glossary_service.effective_glossary_terms(drama_id):
        taken.add(t["term_original"])
        taken.update(t["aliases"])
    return taken


def find_address_terms(drama_id: int) -> dict:
    """The curated address terms that occur at least MIN_COUNT times in the source lines and are
    not yet a glossary term or alias, most frequent first, each with counts and example lines."""
    drama = _drama(drama_id)
    language = drama.get("source_language") or "zh"
    out = {"language": language, "available": language in _PATTERNS, "lines_scanned": 0,
           "truncated": False, "terms": []}
    if not out["available"]:
        return out
    lines, out["truncated"] = _source_lines(drama_id)
    out["lines_scanned"] = len(lines)
    counts: Dict[str, int] = {}
    examples: Dict[str, list] = {}
    for idx, text in lines:
        for m in _PATTERNS[language].finditer(text):
            term = m.group()
            counts[term] = counts.get(term, 0) + 1
            shown = examples.setdefault(term, [])
            if len(shown) < MAX_EXAMPLES and not any(e["line_idx"] == idx for e in shown):
                shown.append({"line_idx": idx, "snippet": _snippet(text, m.start(), m.end())})
    taken = _in_glossary(drama_id)
    found = [t for t, n in counts.items() if n >= MIN_COUNT and t not in taken]
    found.sort(key=lambda t: (-counts[t], t))
    out["terms"] = [{"term": t, "count": counts[t], "examples": examples[t]} for t in found[:MAX_RESULTS]]
    return out


def save_address_terms(drama_id: int, choices: list) -> dict:
    """Add one glossary term per choice ({"term", "translation"} or {"term", "keep_as_is": True}).
    A term that is not on the curated list for the title's language is refused, and one already in
    the glossary is left untouched and reported in "skipped_existing". Returns {"saved", "skipped_existing"}."""
    drama = _drama(drama_id)
    language = drama.get("source_language") or "zh"
    if not isinstance(choices, (list, tuple)) or not choices or len(choices) > MAX_SAVE:
        raise InvalidInputError("Choose at least one address term.")
    known, taken = set(_known(language)), _in_glossary(drama_id)
    planned, skipped = {}, []
    for c in choices:
        term = _text(c.get("term"), "term", MAX_TERM_LEN, required=True)
        if term not in known:
            raise InvalidInputError(f"{term} is not an address term for this title's language.")
        if term in taken:
            skipped.append(term)
            continue
        if c.get("keep_as_is"):
            planned[term] = {"term_original": term, "term_translation": term,
                             "category": CATEGORY, "policy": "keep_pinyin", "notes": KEPT_NOTE}
        else:
            translation = _text(c.get("translation"), "translation", MAX_TERM_LEN, required=True)
            planned[term] = {"term_original": term, "term_translation": translation,
                             "category": CATEGORY, "policy": "translate_meaning"}
    for fields in planned.values():
        glossary_service.upsert_glossary_term(drama_id, fields)
    return {"saved": list(planned), "skipped_existing": skipped}
