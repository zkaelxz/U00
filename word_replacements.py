"""Word replacements: the whole-word swaps a series applies to every line a translation run writes."""
import re

import db


def build_replacer(pairs):
    """`text -> text` swapping each find text for its replacement, whole words only and
    ignoring case ("Lin" never inside "Berlin"). One pass over the text, longer find texts
    first, so a replacement is never itself replaced and the swap is idempotent as long as no
    replacement contains another find text."""
    table = {}
    for find, replace in pairs:
        find = (find or "").strip()
        if find:
            table.setdefault(find.casefold(), replace or "")
    if not table:
        return lambda text: text
    alts = "|".join(re.escape(k) for k in sorted(table, key=len, reverse=True))
    pattern = re.compile(r"(?<!\w)(?:" + alts + r")(?!\w)", re.IGNORECASE)
    return lambda text: pattern.sub(lambda m: table[m.group(0).casefold()], text) if text else text


def with_replacements(substitute_terms, drama_id: int, has_enforced: bool):
    """(text -> text, active): the exact-term substituter followed by the series' word
    replacements, and whether either has anything to do. A drama with no series has none."""
    drama = db.get_drama(drama_id) or {}
    series_id = drama.get("series_id")
    pairs = ([(r["find_text"], r["replace_text"]) for r in db.list_word_replacements(series_id)]
             if series_id else [])
    if not pairs:
        return substitute_terms, has_enforced
    replace = build_replacer(pairs)
    return (lambda text: replace(substitute_terms(text))), True
