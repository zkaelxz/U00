"""Owns the built-in comic glossary: which language-pack entries a comic page's translation carries, and the person's own lines from Settings that change or remove them."""

import re

import language_packs
from services import settings_service

# Comics get these packs without a per-title choice: a title's pack choice is made for
# subtitles, and Quick read pages often belong to no title at all.
_PACK_SUFFIXES = ("address", "common", "comic")
# Whatever a person writes in Settings, one line becomes at most this much of the prompt.
_MAX_CUSTOM_LINES = 300


def custom_entries(text) -> dict:
    """{source: English or ""} from the Settings lines `source = English`. An empty English
    removes the built-in entry; `#` starts a comment line; a line without `=` is ignored."""
    out = {}
    for line in str(text or "").splitlines()[:_MAX_CUSTOM_LINES]:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        source, _, english = line.partition("=")
        source = source.strip()
        if source and len(source) <= language_packs.MAX_SOURCE_LEN:
            out[source] = english.strip()[:language_packs.MAX_TEXT_LEN]
    return out


def _script(ch: str) -> str:
    code = ord(ch)
    if 0x30A0 <= code <= 0x30FF:
        return "katakana"
    if 0x3040 <= code <= 0x309F:
        return "hiragana"
    if 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
        return "hangul"
    if 0x4E00 <= code <= 0x9FFF:
        return "han"
    return ""


def _stands_alone(entry: dict, text: str) -> bool:
    """Whether a sound effect or a katakana word occurs in `text` as a word of its own. Such
    entries are short and common inside other words, and OCR of stylised lettering produces
    runs like シクシーンバッズ in which シーン is no longer the word for silence."""
    source = entry["source"]
    if entry["category"] != "sfx" and not all(_script(c) == "katakana" for c in source):
        return True
    first, last = _script(source[0]), _script(source[-1])
    for match in re.finditer(re.escape(source), text):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if not (before and _script(before) == first) and not (after and _script(after) == last):
            return True
    return False


def reference_block(language: str, bubbles, user_terms=None) -> str:
    """The prompt section for one page: the built-in entries and the person's lines whose
    source text occurs in the page. A series or title glossary term with the same source
    wins over both, and a person's line wins over the built-in entry."""
    if language not in ("zh", "ja", "ko"):
        return ""
    text = "\n".join((b.get("source_text") or "") for b in bubbles if not b.get("skip"))
    if not text.strip():
        return ""
    choices = {f"{language}-{suffix}": None for suffix in _PACK_SUFFIXES}
    found, _ = language_packs.select_entries(
        {k: v for k, v in choices.items() if k in language_packs.all_packs()},
        text, user_terms, language=language)
    entries = [e for e in found if e["category"] != "streamer" and _stands_alone(e, text)]
    own = custom_entries(settings_service.get("comic_glossary_custom"))
    taken = {t.get("term_original") for t in (user_terms or [])}
    entries = [e for e in entries if e["source"] not in own]
    for source, english in own.items():
        if english and source in text and source not in taken:
            entries.append({"source": source, "en": english, "note": "", "context": "",
                            "category": "term", "pack": "custom"})
    return language_packs.build_pack_block(entries)


def with_reference(character_notes: str, language: str, bubbles, user_terms=None) -> str:
    """`character_notes` followed by this page's reference block, since both are standing
    reference for the translator and share one argument of the translate calls."""
    block = reference_block(language, bubbles, user_terms)
    return "\n\n".join(part for part in (character_notes, block) if part)
