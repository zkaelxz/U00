"""Translation memory as TMX 1.4b: export a series' pairs, import pairs from a TMX file."""
import re
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import db
from services import ownership_service
from lib.errors import InvalidInputError

MAX_TMX_CHARS = 5_000_000
MAX_ENTRIES = 50_000
TARGET_LANGUAGE = "en"
SOURCE_LANGUAGES = ("zh", "ja", "ko")
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
# TMX needs no DTD, and a DTD is how entity-expansion attacks arrive; ElementTree resolves
# internal entities, so any document that declares one is refused before it is parsed.
_DTD = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
# XML 1.0 has no room for most control characters, and a lone surrogate cannot be encoded as UTF-8;
# either would make the file unreadable or the response fail.
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _text(value: str) -> str:
    return escape(_XML_BAD.sub("", value or ""))


def export_tmx(principal, series_id: int, source_language: str = "zh") -> str:
    ownership_service.require_visible(principal, "series", series_id)
    if source_language not in SOURCE_LANGUAGES:
        raise InvalidInputError("source_language must be zh, ja or ko.")
    rows = db.list_translation_memory(series_id)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<tmx version="1.4">',
           f'  <header creationtool="Baihe Studio" creationtoolversion="1" segtype="sentence" '
           f'o-tmf="Baihe" adminlang="en" srclang="{source_language}" datatype="plaintext"/>',
           "  <body>"]
    for r in rows:
        out.append(f'    <tu usagecount="{int(r["use_count"] or 1)}">'
                   f'<tuv xml:lang="{source_language}"><seg>{_text(r["source_text"])}</seg></tuv>'
                   f'<tuv xml:lang="{TARGET_LANGUAGE}"><seg>{_text(r["translation"])}</seg></tuv></tu>')
    out += ["  </body>", "</tmx>", ""]
    return "\n".join(out)


def _seg(tuv) -> str:
    seg = tuv.find("seg")
    # itertext keeps the text around inline markup (<bpt>, <ph>) and drops the markup itself.
    return " ".join("".join(seg.itertext()).split()) if seg is not None else ""


def parse_tmx(text: str) -> list:
    """[(source, translation, use_count)] from a TMX document: the tuv in the header's srclang
    is the source and the other tuv is the translation; a tu without exactly that pair is skipped."""
    if len(text) > MAX_TMX_CHARS:
        raise InvalidInputError("This TMX file is too large.")
    if _DTD.search(text):
        raise InvalidInputError("This TMX file declares a DTD or entity, which is not accepted.")
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except (ElementTree.ParseError, UnicodeEncodeError):
        raise InvalidInputError("This file is not valid TMX.")
    if root.tag != "tmx":
        raise InvalidInputError("This file is not valid TMX.")
    header = root.find("header")
    src = ((header.get("srclang") if header is not None else "") or "").lower().split("-")[0]
    pairs = []
    for tu in root.iter("tu"):
        tuvs = [(v.get(_XML_LANG, "").lower().split("-")[0], _seg(v)) for v in tu.findall("tuv")]
        if len(tuvs) != 2 or (src and src not in (tuvs[0][0], tuvs[1][0])):
            continue
        first, second = tuvs
        source, target = (first, second) if not src or first[0] == src else (second, first)
        if not source[1] or not target[1]:
            continue
        uses = tu.get("usagecount", "1")
        pairs.append((source[1], target[1], min(max(1, int(uses)), 10**9) if uses.isdecimal() and len(uses) < 12 else 1))
        if len(pairs) > MAX_ENTRIES:
            raise InvalidInputError("This TMX file has too many entries.")
    return pairs


def import_tmx(principal, series_id: int, tmx: str) -> dict:
    ownership_service.require_editable(principal, "series", series_id)
    result = db.import_translation_memory(series_id, parse_tmx(tmx))
    return {"series_id": series_id, **result}
