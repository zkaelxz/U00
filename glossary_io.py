"""
glossary_io.py -- the term vocabulary (categories, handling policies) and
glossary file import/export (CSV, TSV, JSON).

Kept apart from translation_guide to hold that module under the size
limit; translation_guide re-exports every name defined here.
"""

import json


TERM_CATEGORIES = {
    "person_name": "Personal name (given/full name)",
    "courtesy_name": "Courtesy name (字) / art name (号)",
    "clan_sect": "Clan, sect, or school (宗/门/派/family name)",
    "title_rank": "Title or rank (王爷, 少主, 掌门, 陛下)",
    "honorific": "Honorific / form of address (师姐, 姐姐, 前辈, 道友)",
    "cultivation_realm": "Cultivation realm or stage (筑基, 金丹, 元婴)",
    "place": "Place name (city, palace, mountain, pavilion)",
    "artifact": "Weapon, artifact, or treasure",
    "technique": "Technique, skill, or spell name",
    "culture_item": "Food, clothing, custom, or cultural object",
    "concept": "Philosophical or untranslatable concept (道, 缘分, 气)",
    "other": "Other recurring term",
}

TERM_POLICIES = {
    "keep_pinyin": {
        "label": "Keep as pinyin",
        "example": "沈清疑 → Shen Qingyi",
        "guidance": "Romanize as pinyin. Do not translate the literal meaning of the characters.",
    },
    "translate_meaning": {
        "label": "Translate the meaning",
        "example": "听雨阁 → Listening Rain Pavilion",
        "guidance": "Translate the semantic meaning into natural English.",
    },
    "hybrid": {
        "label": "Hybrid (pinyin + translated category word)",
        "example": "云隐宗 → Yunyin Sect",
        "guidance": (
            "Keep the distinctive proper-noun part as pinyin, but translate the "
            "category word (宗 → Sect, 阁 → Pavilion, 城 → City). This is the most "
            "common convention for sects and organizations."
        ),
    },
    "keep_with_note": {
        "label": "Keep original + translation note",
        "example": "道 → dao [note: the Way]",
        "guidance": (
            "Keep the romanized original because no English equivalent carries the "
            "full meaning, and flag it for a translation note on first appearance."
        ),
    },
    "contextual": {
        "label": "Depends on context",
        "example": "姐姐 → 'jiejie' (intimate address) or 'older sister' (literal)",
        "guidance": (
            "Decide per occurrence based on how it's being used. Note especially that "
            "kinship terms between non-relatives are common as forms of address and "
            "carry intimacy/hierarchy that a literal sibling translation would lose."
        ),
    },
}



GLOSSARY_COLUMNS = ["term_original", "term_translation", "category", "policy",
                    "enforce_exact", "notes"]


def parse_glossary_file(content: str, filename: str = ""):
    """
    Reads a glossary you already have. Accepts CSV, TSV, or JSON, and is
    lenient about column naming -- a two-column sheet of term/translation
    works, and anything extra is picked up if the header matches.

    Returns (entries, warnings) so import problems are visible rather
    than silently dropping rows.
    """
    import csv
    import io as _io

    content = (content or "").strip()
    if not content:
        return [], ["File is empty."]

    warnings = []
    entries = []

    if filename.lower().endswith(".json") or content.startswith("["):
        try:
            data = json.loads(content)
            if not isinstance(data, list):
                return [], ["JSON must be a list of term objects."]
            for row in data:
                if isinstance(row, dict) and row.get("term_original"):
                    entries.append(row)
                else:
                    warnings.append(f"Skipped a row without 'term_original': {str(row)[:60]}")
        except json.JSONDecodeError as e:
            return [], [f"Couldn't parse JSON: {e}"]
    else:
        delimiter = "\t" if (filename.lower().endswith(".tsv") or "\t" in content.split("\n")[0]) else ","
        reader = csv.reader(_io.StringIO(content), delimiter=delimiter)
        rows = [r for r in reader if any(c.strip() for c in r)]
        if not rows:
            return [], ["No rows found."]

        header = [h.strip().lower().replace(" ", "_") for h in rows[0]]
        # A header is only a header if it names at least one known column.
        has_header = any(h in GLOSSARY_COLUMNS or h in ("term", "translation", "original", "english")
                         for h in header)
        alias = {"term": "term_original", "original": "term_original",
                 "translation": "term_translation", "english": "term_translation"}

        if has_header:
            cols = [alias.get(h, h) for h in header]
            body = rows[1:]
        else:
            cols = ["term_original", "term_translation"]
            body = rows
            warnings.append("No header row detected -- assuming column 1 = original term, "
                            "column 2 = translation.")

        for i, row in enumerate(body, start=2 if has_header else 1):
            entry = {}
            for j, col in enumerate(cols):
                if j < len(row) and col in GLOSSARY_COLUMNS:
                    entry[col] = row[j].strip()
            if not entry.get("term_original"):
                warnings.append(f"Row {i}: no original term, skipped.")
                continue
            entries.append(entry)

    # Normalise and validate so a bad category can't corrupt the glossary
    cleaned = []
    for e in entries:
        cat = e.get("category")
        pol = e.get("policy")
        if cat and cat not in TERM_CATEGORIES:
            warnings.append(f"{e['term_original']}: unknown category '{cat}', set to 'other'.")
            cat = "other"
        if pol and pol not in TERM_POLICIES:
            warnings.append(f"{e['term_original']}: unknown policy '{pol}', set to 'keep_pinyin'.")
            pol = "keep_pinyin"
        enforce = str(e.get("enforce_exact", "")).strip().lower() in ("1", "true", "yes", "y")
        cleaned.append({
            "term_original": e["term_original"].strip(),
            "term_translation": (e.get("term_translation") or "").strip(),
            "category": cat or "other",
            "policy": pol or "keep_pinyin",
            "enforce_exact": enforce,
            "notes": (e.get("notes") or "").strip(),
        })
    return cleaned, warnings


def glossary_to_csv(terms) -> str:
    """Exports a glossary as CSV -- for backup, for editing in a
    spreadsheet, or for sharing with someone translating the same series."""
    import csv
    import io as _io
    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(GLOSSARY_COLUMNS)
    for t in terms:
        writer.writerow([
            t.get("term_original", ""), t.get("term_translation", ""),
            t.get("category", ""), t.get("policy", ""),
            "yes" if t.get("enforce_exact") else "", t.get("notes", ""),
        ])
    return buf.getvalue()
