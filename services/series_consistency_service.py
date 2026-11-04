"""
services/series_consistency_service.py -- the series' cross-episode consistency report:
per glossary term, every English rendering found in the translated lines of the series' titles.

Read-only and deterministic (no engine, no network). A line where the source holds a term
or alias counts as the glossary rendering when its English has the required word
(glossary_check.missing_terms: the same test the translate run uses). Otherwise the
rendering is the capitalised name the line uses most across the series for that term, so
"Yalo" vs "Yaloch" shows up as a drift without anyone reading every episode. That is a
heuristic: a line with no capitalised name is listed as "unnamed" and never flagged, since
a pronoun is a legitimate rendering.

Fixing is not done here: the report carries the line ids so the UI can hand them to the
existing stronger-engine redo (glossary_flagged_service.start_redo).

Title visibility follows ownership_service; no FastAPI import.
"""
import re
from collections import Counter, defaultdict
from typing import Dict, List

import db
from engine_backends.glossary_check import GLOSSARY_FLAG, missing_terms, required_terms
from services import ownership_service

# --- Tunables (the one place for the numbers) -------------------------------
MAX_TITLES = 100              # newest scan budget; the answer says when it stopped early
MAX_LINES = 50_000            # every line is matched against every term
MAX_TERMS_SHOWN = 200
MAX_LINE_IDS_PER_TITLE = 200  # also the most one redo click can carry
MAX_EXAMPLES = 3
MAX_EXAMPLE_CHARS = 120

_NAME_RUN = re.compile(r"\b[A-Z][a-z]{1,15}(?: [A-Z][a-z]{1,15}){0,2}\b")
# Words that start sentences or are capitalised for other reasons; a rendering is never one.
_COMMON_CAPITALISED = frozenset({
    "the", "and", "but", "you", "she", "his", "her", "was", "are", "not", "for", "with", "this",
    "that", "what", "yes", "okay", "ok", "thank", "thanks", "sorry", "please", "hello",
    "i", "he", "they", "we", "it", "there", "then", "now", "who", "why", "how", "when", "where",
    "just", "well", "oh", "come", "let", "don", "can", "will", "did", "do", "is", "so", "if", "no",
    "maybe", "everyone", "mr", "mrs", "miss"})


def _name_tokens(en: str, known: set) -> List[str]:
    """Capitalised names in an English line, minus common words and the names the glossary
    already assigns to other terms."""
    out = []
    for m in _NAME_RUN.finditer(en):
        words = [w for w in m.group().split() if w.lower() not in _COMMON_CAPITALISED]
        text = " ".join(words)
        if text and text.casefold() not in known:
            out.append(text)
    return out


def _visible_titles(principal, series_id: int) -> List[dict]:
    ids = ownership_service.filter_visible_drama_ids(
        principal, [d["id"] for d in db.list_dramas_by_series(series_id)])
    wanted = set(ids)
    return [d for d in db.list_dramas_by_series(series_id) if d["id"] in wanted]


def build_report(principal, series_id: int) -> dict:
    ownership_service.require_visible(principal, "series", series_id)
    titles = _visible_titles(principal, series_id)
    terms = [t for t in db.list_glossary_terms(series_id) if (t.get("term_translation") or "").strip()]
    by_original = {t["term_original"]: t for t in terms}
    known_names = {t["term_translation"].strip().casefold() for t in terms}

    # term_original -> rows of (drama_id, line_id, idx, en, canonical?)
    hits: Dict[str, list] = defaultdict(list)
    lines_scanned = 0
    glossary_missing_lines = 0
    truncated = len(titles) > MAX_TITLES
    for title in titles[:MAX_TITLES]:
        for row in db.load_line_texts(title["id"]):
            en, zh = (row.en or "").strip(), (row.zh or "").strip()
            if not en or not zh:
                continue
            if lines_scanned >= MAX_LINES:
                truncated = True
                break
            lines_scanned += 1
            required = required_terms(zh, terms)
            if not required:
                continue
            missing = set(missing_terms(zh, en, terms))
            if row.flag == GLOSSARY_FLAG and missing:
                glossary_missing_lines += 1
            for src, _tr in required:
                hits[src].append((title["id"], row.id, row.idx, en, (src, _tr) not in missing))
        if lines_scanned >= MAX_LINES:
            break

    report_terms = [_term_report(by_original[src], rows, known_names) for src, rows in hits.items()]
    report_terms.sort(key=lambda t: (-t["flagged_lines"], -len(t["renderings"]), t["term_original"]))
    drifting = [t for t in report_terms if len([r for r in t["renderings"] if r["kind"] != "unnamed"]) > 1]
    flagged_lines = sum(t["flagged_lines"] for t in report_terms)
    return {
        "series_id": series_id,
        "truncated": truncated,
        "summary": {"titles_scanned": min(len(titles), MAX_TITLES), "lines_scanned": lines_scanned,
                    "terms_checked": len(terms), "terms_found": len(report_terms),
                    "names_with_several_renderings": len(drifting), "flagged_lines": flagged_lines,
                    "glossary_missing_lines": glossary_missing_lines},
        "terms": report_terms[:MAX_TERMS_SHOWN],
    }


def _term_report(term: dict, rows: list, known_names: set) -> dict:
    """Group one term's matched lines by rendering."""
    canonical = term["term_translation"].strip()
    candidates = {id(r): _name_tokens(r[3], known_names) for r in rows if not r[4]}
    pool = Counter(tok for toks in candidates.values() for tok in set(toks))
    groups: Dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r[4]:
            groups[("glossary", canonical)].append(r)
            continue
        toks = candidates[id(r)]
        if toks:
            best = max(toks, key=lambda t: (pool[t], t))
            groups[("other", best)].append(r)
        else:
            groups[("unnamed", "")].append(r)
    renderings = [_rendering(kind, text, grp) for (kind, text), grp in groups.items()]
    renderings.sort(key=lambda r: ({"glossary": 0, "other": 1, "unnamed": 2}[r["kind"]], -r["count"], r["text"]))
    return {"term_id": term["id"], "term_original": term["term_original"], "term_translation": canonical,
            "renderings": renderings,
            "flagged_lines": sum(r["count"] for r in renderings if r["flagged"])}


def _rendering(kind: str, text: str, rows: list) -> dict:
    by_title: Dict[int, List[int]] = defaultdict(list)
    for drama_id, line_id, _idx, _en, _ok in rows:
        by_title[drama_id].append(line_id)
    flagged = kind == "other"
    return {
        "text": text, "kind": kind, "count": len(rows), "flagged": flagged,
        # Line ids are only carried where a redo can be offered.
        "titles": [{"drama_id": d, "line_count": len(ids),
                    "line_ids": ids[:MAX_LINE_IDS_PER_TITLE] if flagged else []}
                   for d, ids in by_title.items()],
        "examples": [{"drama_id": d, "line_id": lid, "idx": idx, "en": en[:MAX_EXAMPLE_CHARS]}
                     for d, lid, idx, en, _ok in rows[:MAX_EXAMPLES]],
    }
