"""
services/glossary_variants_service.py -- find the sound-alike spellings of a glossary
term in a title's source lines, and the names that recur under several spellings.

ASR writes one person several slightly different ways (Yalo / Yaloch / ヤロー), so an
exact-text term or alias matches only some mentions. These two read-only scans point
at the other spellings; they never change the glossary. The user confirms each
candidate, and the existing term routes do the saving.

Offline and free: no engine, no network. Sounds come from glossary_phonetic. A scan reads
at most SCAN_MAX_LINES lines / SCAN_MAX_CHARS characters and says so when it stopped early.

Spellings that merely extend an exact mention (ヤロ + が) are not offered: the exact text
already finds them, and they would flood the list.

Title visibility is checked by the route's `{drama_id}` path guard (api/auth.py). No
FastAPI import.
"""
import re
import unicodedata
from collections import Counter
from typing import Dict, List, Tuple

import db
import glossary_phonetic as ph
from services import glossary_service, vdb_names_service
from services.glossary_common import MAX_TERM_LEN, _drama, _text
from lib.errors import InvalidInputError, NotFoundError

# --- Tunables (the one place for the numbers) -------------------------------
SCAN_MAX_LINES = 5000         # a long episode set; keeps a click under a few seconds
SCAN_MAX_CHARS = 60_000       # the real cost driver: every character starts a few windows
MAX_VARIANT_RESULTS = 30      # more than a person reads; the best are first
MAX_EXAMPLES = 3              # enough to recognise the name in context
SNIPPET_CONTEXT = 14          # characters either side of a hit in an example
MAX_WINDOW_CHARS = 12         # no CJK name is longer; bounds the work per start
KEY_LEN_SLACK_BELOW = 2       # a candidate's key may be this much shorter than the term's...
KEY_LEN_SLACK_ABOVE = 3       # ...or this much longer (ASR adds trailing sounds more than it drops them)
MAX_LATIN_WORDS = 3           # "Shen Qing Yi"
MAX_REF_KEYS = 20             # the term plus its first aliases; more only slows every window
MAX_SIMILARITY_CHECKS = 150_000   # one request's share of comparisons (windows x references)
MAX_NAME_COMPARISONS = 20_000     # the same for clustering names
MAX_NAME_CHARS = 20           # a longer string is a sentence or a label, not a name
MIN_NAME_COUNT = 2            # a cluster heard once is not recurring
MAX_NAME_CANDIDATES = 300     # clustering compares pairs, so the pool is capped
MAX_CLUSTERS = 30
MAX_CLUSTER_SPELLINGS = 12

_CJK_SCRIPTS = ("kana", "han", "hangul")


class _BudgetSpent(Exception):
    """The request used up its comparison budget; what was found so far is returned."""

_KATAKANA_RUN = re.compile(r"[ァ-ヺー]{2,12}")
_LATIN_CAPITAL = re.compile(r"\b[A-Z][a-z]{2,15}\b")
_LATIN_STOPWORDS = frozenset({
    "the", "and", "but", "you", "she", "his", "her", "was", "are", "not", "for", "with", "this",
    "that", "what", "yes", "okay", "ok", "thank", "thanks", "sorry", "please", "hello"})
# A name right before an honorific or a vocative is the cheap, high-precision way to
# spot a person in text that has no word spaces or capital letters.
_HONORIFIC_PATTERNS = {
    "ja": re.compile(r"([一-鿿]{2,4}|[ァ-ヺー]{2,8})"
                     r"(?=さん|くん|君|ちゃん|様|さま|先輩|先生|殿)"),
    "zh": re.compile(r"([一-鿿]{2,4})(?=哥|姐|姊|老师|老師|先生|小姐|同学|同學|大人|殿下)"),
    "ko": re.compile(r"([가-힣]{2,4})(?=씨|님|선배|선생)"),
}
_VOCATIVE = re.compile(r"^\s*([一-鿿가-힣]{2,4})[，,、！!？?]")


def _language(drama: dict) -> str:
    return drama.get("source_language") or "zh"


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().casefold()


def _snippet(text: str, start: int, end: int) -> str:
    lo, hi = max(0, start - SNIPPET_CONTEXT), min(len(text), end + SNIPPET_CONTEXT)
    return ("…" if lo else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


def _source_lines(drama_id: int):
    """Source text of each non-sound-effect line, within the scan budget. Returns
    ([(idx, text)], truncated)."""
    out: List[Tuple[int, str]] = []
    chars = 0
    for ln in db.load_line_objects(drama_id):
        text = (ln.zh or "").strip()
        if not text or ln.sfx:
            continue
        if len(out) >= SCAN_MAX_LINES or chars + len(text) > SCAN_MAX_CHARS:
            return out, True
        out.append((ln.idx, text))
        chars += len(text)
    return out, False


def _unavailable(reason: str, **extra) -> dict:
    return {"available": False, "reason": reason, "lines_scanned": 0, "truncated": False, **extra}


def _resolve_term(terms: List[dict], term_id_or_text) -> Tuple[str, List[str]]:
    """The term text and its aliases. An id must name a term; text that matches a term's
    original or alias picks up that term's aliases, and any other text is searched as is
    (a name the owner is still working out)."""
    if isinstance(term_id_or_text, bool):
        raise InvalidInputError("term must be text or a term id.")
    if isinstance(term_id_or_text, int):
        for t in terms:
            if t["id"] == term_id_or_text:
                return t["term_original"], list(t["aliases"])
        raise NotFoundError("Glossary term not found.")
    text = _text(term_id_or_text, "term", MAX_TERM_LEN, required=True)
    for t in terms:
        if t["term_original"] == text or text in t["aliases"]:
            return t["term_original"], list(t["aliases"])
    return text, []


def _latin_runs(text: str) -> List[Tuple[int, int]]:
    """Spans of 1..MAX_LATIN_WORDS Latin words separated by single spaces."""
    words = []
    i = 0
    while i < len(text):
        if ph.char_script(text[i]) == "latin":
            j = i
            while j < len(text) and ph.char_script(text[j]) == "latin":
                j += 1
            words.append((i, j))
            i = j
        else:
            i += 1
    spans = []
    for a in range(len(words)):
        end = words[a][1]
        spans.append((words[a][0], end))
        for b in range(a + 1, min(len(words), a + MAX_LATIN_WORDS)):
            if words[b][0] - words[b - 1][1] != 1 or text[words[b - 1][1]] != " ":
                break
            spans.append((words[a][0], words[b][1]))
    return spans


def _windows(text: str, language: str, first_letters: frozenset, min_len: int, max_len: int):
    """(start, end, key) for every stretch of `text` that could be a name of the right
    sound length: character windows in CJK runs, whole words in Latin ones."""
    units = ph.phonetic_units(text, language)
    scripts = [ph.char_script(c) for c in text]
    n = len(text)
    for s, e in _latin_runs(text):
        key = ph.key_from_units(units[s:e])
        if min_len <= len(key) <= max_len and key[:1] in first_letters:
            yield s, e, key
    for s in range(n):
        if scripts[s] not in _CJK_SCRIPTS:
            continue
        if ph.key_from_units(units[s:s + 2])[:1] not in first_letters:
            continue
        e = s
        while e < min(n, s + MAX_WINDOW_CHARS) and scripts[e] in _CJK_SCRIPTS:
            e += 1
            key = ph.key_from_units(units[s:e])
            if len(key) > max_len:
                break
            if len(key) >= min_len:
                yield s, e, key


def find_variants(drama_id: int, term_id_or_text) -> dict:
    """Spellings in the title's source lines that sound like the term or one of its
    aliases. Never includes the term itself; aliases that appear are included and marked
    `is_alias` so the caller doesn't offer them again."""
    drama = _drama(drama_id)
    language = _language(drama)
    terms = glossary_service.list_glossary_terms(drama_id)
    term_text, aliases = _resolve_term(terms, term_id_or_text)
    base = {"term": term_text}
    status = ph.availability(language)
    if not status["available"]:
        return {**base, **_unavailable(status["hint"], candidates=[])}
    try:
        term_key = ph.phonetic_key(term_text, language)
        if not term_key:
            raise InvalidInputError("That text has no sound to compare (it has no letters).")
        if len(term_key) > ph.MAX_KEY_LEN:
            raise InvalidInputError("That text is too long to be a name.")
        refs = [(term_text, term_key)]
        seen_keys = {term_key}
        for text in aliases:
            key = ph.phonetic_key(text, language)
            # An alias that long can't be a name; one with the same sound adds no new match.
            if key and len(key) <= ph.MAX_KEY_LEN and key not in seen_keys and len(refs) < MAX_REF_KEYS:
                seen_keys.add(key)
                refs.append((text, key))
        exact = {_norm(t) for t, _ in refs}
        alias_set = {_norm(a) for a in aliases}
        other_terms = {_norm(t["term_original"]) for t in terms if t["term_original"] != term_text}
        first_letters = frozenset(k[0] for _, k in refs)
        min_len = max(1, min(len(k) for _, k in refs) - KEY_LEN_SLACK_BELOW)
        max_len = max(len(k) for _, k in refs) + KEY_LEN_SLACK_ABOVE

        score_cache: Dict[str, float] = {}
        checks = 0

        def score(key: str) -> float:
            nonlocal checks
            if key not in score_cache:
                checks += len(refs)
                if checks > MAX_SIMILARITY_CHECKS:
                    raise _BudgetSpent
                best = 0.0
                for _, ref_key in refs:
                    if ph.similar(key, ref_key, language):
                        best = max(best, 1.0 - ph.key_distance(key, ref_key))
                score_cache[key] = best
            return score_cache[key]

        lines, truncated = _source_lines(drama_id)
        found: Dict[str, dict] = {}
        for idx, text in lines:
            hits = []
            try:
                for s, e, key in _windows(text, language, first_letters, min_len, max_len):
                    piece = text[s:e]
                    is_exact = _norm(piece) in exact
                    sc = 1.0 if is_exact else score(key)
                    if sc > 0:
                        hits.append((sc, s, e, piece, key, is_exact))
            except _BudgetSpent:
                truncated = True
                break
            claimed = [(s, e) for _, s, e, _, _, ex in hits if ex]
            taken: List[Tuple[int, int]] = []
            for sc, s, e, piece, key, is_exact in sorted(
                    hits, key=lambda h: (-h[0], not h[5], abs(len(h[4]) - len(term_key)), h[2] - h[1])):
                if _norm(piece) == _norm(term_text):
                    continue  # the term itself neither counts nor hides a same-sound neighbour
                if any(s < te and ts < e for ts, te in taken):
                    continue
                if not is_exact and sc < 1.0 and any(s < ce and cs < e for cs, ce in claimed):
                    continue
                taken.append((s, e))
                entry = found.setdefault(piece, {"text": piece, "count": 0, "score": sc,
                                                 "examples": []})
                entry["count"] += 1
                entry["score"] = max(entry["score"], sc)
                if len(entry["examples"]) < MAX_EXAMPLES:
                    entry["examples"].append({"line_idx": idx, "snippet": _snippet(text, s, e)})
    except ph.PhoneticUnavailable as exc:
        return {**base, **_unavailable(str(exc), candidates=[])}

    candidates = sorted(found.values(), key=lambda c: (-c["score"], -c["count"], c["text"]))
    for c in candidates:
        c["score"] = round(c["score"], 2)
        c["is_alias"] = _norm(c["text"]) in alias_set
        c["is_term"] = _norm(c["text"]) in other_terms
    return {**base, "available": True, "reason": "", "lines_scanned": len(lines),
            "truncated": truncated, "candidates": candidates[:MAX_VARIANT_RESULTS]}


def _name_candidates(lines, speakers: Counter, language: str) -> Dict[str, dict]:
    """text -> {count, sources, examples} for strings that look like names."""
    found: Dict[str, dict] = {}

    def add(text: str, source: str, idx: int, line: str, start: int, end: int):
        entry = found.setdefault(text, {"text": text, "count": 0, "sources": set(), "examples": []})
        entry["count"] += 1
        entry["sources"].add(source)
        if len(entry["examples"]) < MAX_EXAMPLES:
            entry["examples"].append({"line_idx": idx, "snippet": _snippet(line, start, end)})

    for idx, line in lines:
        for m in _KATAKANA_RUN.finditer(line):
            add(m.group(), "katakana", idx, line, m.start(), m.end())
        for m in _LATIN_CAPITAL.finditer(line):
            if m.group().lower() not in _LATIN_STOPWORDS:
                add(m.group(), "latin", idx, line, m.start(), m.end())
        pattern = _HONORIFIC_PATTERNS.get(language)
        for m in (pattern.finditer(line) if pattern else ()):
            add(m.group(1), "honorific", idx, line, m.start(1), m.end(1))
        v = _VOCATIVE.match(line)
        if v:
            add(v.group(1), "vocative", idx, line, v.start(1), v.end(1))
    for label, n in speakers.items():
        entry = found.setdefault(label, {"text": label, "count": 0, "sources": set(), "examples": []})
        entry["count"] += n
        entry["sources"].add("speaker")
    return found


def discover_names(drama_id: int) -> dict:
    """The recurring-name clusters, plus the downloaded VTuber names this title found in
    its lines when it opted in (read-only suggestions; a name a term already covers is
    marked, not hidden)."""
    result = _discover_clusters(drama_id)
    result["known_vtuber_names"] = _known_vtuber_names(drama_id)
    return result


def _known_vtuber_names(drama_id: int) -> List[dict]:
    if not vdb_names_service.is_enabled(drama_id):
        return []
    lines, _ = _source_lines(drama_id)
    terms = glossary_service.list_glossary_terms(drama_id)
    known = {_norm(s): t["term_original"] for t in terms for s in [t["term_original"], *t["aliases"]]}
    return [{"suggested_original": name, "existing_term": known.get(_norm(name)), "total": count,
             "spellings": [{"text": name, "count": count, "sources": [], "is_alias": False,
                            "is_term": _norm(name) in known}],
             "examples": []}
            for name, count in vdb_names_service.matches_for_text("\n".join(t for _, t in lines))]


def _discover_clusters(drama_id: int) -> dict:
    """Recurring name-like strings (speaker labels, katakana runs, capitalised Latin
    words, names before an honorific or at the start of a line), grouped into clusters
    that sound alike. A cluster of several spellings is "these may be one name"; a
    cluster with one spelling is a frequent name that has no term yet. Clusters whose
    spellings are all already terms or aliases are left out."""
    drama = _drama(drama_id)
    language = _language(drama)
    terms = glossary_service.list_glossary_terms(drama_id)
    status = ph.availability(language)
    if not status["available"]:
        return _unavailable(status["hint"], clusters=[])

    lines_all = db.load_line_objects(drama_id)
    speakers = Counter((ln.speaker or "").strip() for ln in lines_all if (ln.speaker or "").strip())
    lines, truncated = _source_lines(drama_id)
    found = _name_candidates(lines, speakers, language)

    known: Dict[str, str] = {}
    for t in terms:
        for text in [t["term_original"], *t["aliases"]]:
            known[_norm(text)] = t["term_original"]
    aliases = {_norm(a) for t in terms for a in t["aliases"]}

    # Single mentions stay in the pool: a spelling heard once is exactly what a recurring
    # name's cluster should absorb. Clusters that stay too small are dropped below.
    pool = sorted((c for c in found.values() if len(c["text"]) <= MAX_NAME_CHARS),
                  key=lambda c: (-c["count"], c["text"]))[:MAX_NAME_CANDIDATES]
    clusters: List[dict] = []
    comparisons = 0
    try:
        for cand in pool:
            key = ph.phonetic_key(cand["text"], language)
            if len(key) < 2:
                continue
            cand["key"] = key
            for cluster in clusters:
                comparisons += 1
                if comparisons > MAX_NAME_COMPARISONS:
                    raise _BudgetSpent
                if ph.similar(key, cluster["key"], language):
                    cluster["members"].append(cand)
                    break
            else:
                clusters.append({"key": key, "members": [cand]})
    except _BudgetSpent:
        truncated = True
    except ph.PhoneticUnavailable as exc:
        return _unavailable(str(exc), clusters=[])

    out = []
    for cluster in clusters:
        members = cluster["members"][:MAX_CLUSTER_SPELLINGS]
        if sum(m["count"] for m in members) < MIN_NAME_COUNT:
            continue
        if all(_norm(m["text"]) in known for m in members):
            continue
        existing = next((known[_norm(m["text"])] for m in members if _norm(m["text"]) in known), None)
        examples = []
        for m in members:
            examples.extend(m["examples"][:1])
        for m in members:
            examples.extend(m["examples"][1:])
        out.append({
            "suggested_original": members[0]["text"],
            "existing_term": existing,
            "total": sum(m["count"] for m in members),
            "spellings": [{"text": m["text"], "count": m["count"],
                           "sources": sorted(m["sources"]),
                           "is_alias": _norm(m["text"]) in aliases,
                           "is_term": known.get(_norm(m["text"])) == m["text"]} for m in members],
            "examples": examples[:MAX_EXAMPLES],
        })
    out.sort(key=lambda c: (-(len(c["spellings"]) > 1), -c["total"], c["suggested_original"]))
    return {"available": True, "reason": "", "lines_scanned": len(lines), "truncated": truncated,
            "clusters": out[:MAX_CLUSTERS]}
