"""
language_packs.py -- built-in "language pack" glossaries (honorifics, address
terms, kinship, set phrases) loaded from JSON files in language_pack_data/.

Packs are data only: a file is parsed as JSON and validated against a fixed
shape, nothing in it is ever executed or used as a path. They are optional and
off until a title (or a source-language default) turns them on, and a title's
own glossary term always beats a pack entry for the same source text.

Only entries whose source text occurs in the text being translated are sent,
so the prompt stays short and, for one run, identical across its batches.
"""

import json
import logging
import os
import re
import threading
from typing import Optional

PACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "language_pack_data")
# Packs are read from the app's own folder, but a stray huge file must not be
# loaded whole into memory or into a prompt.
MAX_PACK_BYTES = 128 * 1024
MAX_ENTRIES = 300
MAX_SOURCE_LEN = 40
MAX_TEXT_LEN = 200
MAX_NOTE_LEN = 300
# Short kana/hangul suffixes match inside many words; the cap keeps a run's
# prompt bounded even then.
MAX_PROMPT_ENTRIES = 60
ANY_LANGUAGE = "any"
LANGUAGES = ("zh", "ja", "ko", ANY_LANGUAGE)
ENTRY_CATEGORIES = ("honorific", "kinship", "role", "phrase", "interjection", "streamer")

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_cache: Optional[dict] = None


class PackError(ValueError):
    """A pack file that does not match the schema; the message names the
    file and the entry, never a filesystem path."""


def _text(value, where: str, field: str, max_len: int, *, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            raise PackError(f"{where}: '{field}' is required.")
        return ""
    if not isinstance(value, str):
        raise PackError(f"{where}: '{field}' must be text.")
    value = value.strip()
    if len(value) > max_len:
        raise PackError(f"{where}: '{field}' is longer than {max_len} characters.")
    return value


def _styles(raw, where: str) -> dict:
    if raw is None:
        return {"default": "", "options": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("options"), dict) or not raw["options"]:
        raise PackError(f"{where}: 'styles' needs a non-empty 'options' object.")
    options = {_text(k, where, "styles option id", 30, required=True):
               _text(v, where, "styles option label", MAX_TEXT_LEN, required=True)
               for k, v in raw["options"].items()}
    default = _text(raw.get("default"), where, "styles.default", 30, required=True)
    if default not in options:
        raise PackError(f"{where}: styles.default '{default}' is not one of its options.")
    return {"default": default, "options": options}


def _not_in(raw, where: str, source: str) -> list:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PackError(f"{where}: 'not_in' must be a list of words.")
    words = [_text(w, where, "not_in word", MAX_SOURCE_LEN, required=True) for w in raw]
    if any(source not in w or w == source for w in words):
        raise PackError(f"{where}: each 'not_in' word must be a longer word containing the source.")
    return words


def _entry(raw, n: int, where: str, styles: dict) -> dict:
    where = f"{where}, entry {n}"
    if not isinstance(raw, dict):
        raise PackError(f"{where}: must be an object.")
    en = raw.get("en")
    if isinstance(en, str):
        text = _text(en, where, "en", MAX_TEXT_LEN, required=True)
        en = {key: text for key in styles["options"]} if styles["options"] else text
    elif isinstance(en, dict):
        if set(en) != set(styles["options"]):
            raise PackError(f"{where}: 'en' must give one rendering for each style: "
                            f"{', '.join(styles['options']) or 'none (this pack has no styles)'}.")
        en = {k: _text(v, where, f"en.{k}", MAX_TEXT_LEN, required=True) for k, v in en.items()}
    else:
        raise PackError(f"{where}: 'en' is required (text, or one text per style).")
    category = raw.get("category") or "honorific"
    if category not in ENTRY_CATEGORIES:
        raise PackError(f"{where}: 'category' must be one of {', '.join(ENTRY_CATEGORIES)}.")
    source = _text(raw.get("source"), where, "source", MAX_SOURCE_LEN, required=True)
    return {
        "source": source,
        "en": en, "category": category,
        "note": _text(raw.get("note"), where, "note", MAX_NOTE_LEN),
        "context": _text(raw.get("context"), where, "context", MAX_NOTE_LEN),
        "not_in": _not_in(raw.get("not_in"), where, source),
    }


def validate_pack(raw, filename: str) -> dict:
    """The pack in normalised form, or PackError saying what is wrong."""
    if not isinstance(raw, dict):
        raise PackError(f"{filename}: the file must hold one JSON object.")
    where = filename
    pack_id = _text(raw.get("id"), where, "id", 40, required=True)
    if not all(c.islower() or c.isdigit() or c == "-" for c in pack_id):
        raise PackError(f"{where}: 'id' may use only lowercase letters, digits and '-'.")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PackError(f"{where}: 'version' must be a whole number from 1.")
    language = raw.get("language")
    if language not in LANGUAGES:
        raise PackError(f"{where}: 'language' must be one of {', '.join(LANGUAGES)}.")
    styles = _styles(raw.get("styles"), where)
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise PackError(f"{where}: 'entries' must be a non-empty list.")
    if len(entries_raw) > MAX_ENTRIES:
        raise PackError(f"{where}: more than {MAX_ENTRIES} entries.")
    entries = [_entry(e, i + 1, where, styles) for i, e in enumerate(entries_raw)]
    sources = [e["source"] for e in entries]
    if len(set(sources)) != len(sources):
        dup = next(s for s in sources if sources.count(s) > 1)
        raise PackError(f"{where}: the source '{dup}' appears twice.")
    return {
        "id": pack_id, "version": version, "language": language,
        "title": _text(raw.get("title"), where, "title", 100, required=True),
        "description": _text(raw.get("description"), where, "description", 500, required=True),
        "styles": styles, "entries": entries,
    }


def load_pack_file(path: str) -> dict:
    name = os.path.basename(path)
    if os.path.getsize(path) > MAX_PACK_BYTES:
        raise PackError(f"{name}: the file is larger than {MAX_PACK_BYTES // 1024} KB.")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (ValueError, UnicodeDecodeError):
        raise PackError(f"{name}: not valid JSON.") from None
    return validate_pack(raw, name)


def all_packs() -> dict:
    """{pack id: pack}. A file that fails validation is logged and left out so
    one bad file cannot break translation."""
    global _cache
    with _lock:
        if _cache is None:
            found = {}
            for name in sorted(os.listdir(PACK_DIR)) if os.path.isdir(PACK_DIR) else []:
                if not name.endswith(".json"):
                    continue
                try:
                    pack = load_pack_file(os.path.join(PACK_DIR, name))
                except (PackError, OSError) as exc:
                    _log.warning("Skipping language pack %s: %s", name, exc)
                    continue
                if pack["id"] in found:
                    _log.warning("Skipping language pack %s: duplicate id", name)
                    continue
                found[pack["id"]] = pack
            _cache = found
        return _cache


def packs_for_language(language: str) -> list:
    return [p for p in all_packs().values() if p["language"] in (language, ANY_LANGUAGE)]


def rendering(entry: dict, pack: dict, style: Optional[str] = None) -> str:
    """The entry's English for `style` (the pack default when unset or unknown)."""
    en = entry["en"]
    if isinstance(en, str):
        return en
    return en.get(style) or en[pack["styles"]["default"]]


def _user_sources(user_terms) -> set:
    taken = set()
    for term in user_terms or []:
        taken.add((term.get("term_original") or "").strip())
        taken.update(a.strip() for a in (term.get("aliases") or "").split("|") if a.strip())
    taken.discard("")
    return taken


def _ascii_edge(c: str) -> bool:
    return c.isascii() and c.isalnum()


def _spans(word: str, text: str) -> list:
    """Where `word` occurs in `text`. A word that starts or ends with an ASCII
    letter or digit must not touch another one, so OP is not found in TOP."""
    pattern = re.escape(word)
    if _ascii_edge(word[0]):
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if _ascii_edge(word[-1]):
        pattern += r"(?![A-Za-z0-9])"
    return [m.span() for m in re.finditer(pattern, text)]


def matching_entries(choices: dict, text: str, user_terms=None,
                     limit: int = MAX_PROMPT_ENTRIES) -> list:
    """Pack entries to send for `text`, as dicts with source, en, note, context,
    category and pack. `choices` is {pack id: style or None} for the packs
    turned on. An entry is dropped when its source is not in `text` or when
    the user's own glossary has a term (or alias) with the same source, so the
    user's wording always wins. Matching is longest-first: text already used by
    a longer entry (or named in an entry's `not_in`) cannot trigger a shorter
    one, so 様 does not fire inside お疲れ様. Order is stable (pack order, then
    file order) so the same text always gives the same prompt."""
    taken = _user_sources(user_terms)
    packs = all_packs()
    candidates, seen = [], set()
    for pack_id in packs:
        if pack_id not in choices:
            continue
        pack, style = packs[pack_id], choices[pack_id]
        for entry in pack["entries"]:
            src = entry["source"]
            if src in taken or src in seen or src not in text:
                continue
            seen.add(src)
            candidates.append((pack_id, pack, style, entry))
    # Blockers first, then entries longest to shortest, each claiming its spans.
    claimed = []

    def free(span):
        return not any(span[0] < c[1] and c[0] < span[1] for c in claimed)

    for _, _, _, entry in candidates:
        for word in entry["not_in"]:
            claimed.extend(_spans(word, text))
    keep = set()
    for k in sorted(range(len(candidates)), key=lambda i: -len(candidates[i][3]["source"])):
        spans = [s for s in _spans(candidates[k][3]["source"], text) if free(s)]
        if spans:
            keep.add(k)
            claimed.extend(spans)
    found = []
    for k, (pack_id, pack, style, entry) in enumerate(candidates):
        if k in keep:
            found.append({"source": entry["source"], "en": rendering(entry, pack, style),
                          "note": entry["note"], "context": entry["context"],
                          "category": entry["category"], "pack": pack_id})
    return found[:limit]


def build_pack_block(entries) -> str:
    """The prompt section for matching_entries() output; "" for none."""
    if not entries:
        return ""
    lines = ["LANGUAGE PACK TERMS -- starter defaults for forms of address and common terms. "
             "Use the given English when the word is used this way. If it is used in its literal "
             "sense (for example a real older sister, not an address), use plain English instead. "
             "A term in the glossary above always wins:"]
    for e in entries:
        hint = "; ".join(p for p in (e["context"], e["note"]) if p)
        lines.append(f"  {e['source']} → {e['en']}" + (f" -- {hint}" if hint else ""))
    return "\n".join(lines)
