"""
dictionary.py -- word/phrase definitions for the Reader's click-to-
define feature.

  zh: CC-CEDICT (free, open, CC BY-SA licensed dictionary data).
      Auto-downloads to ./library/cedict.txt on first use if missing
      (needs internet once; cached locally after that).
  ja/ko: no bundled dictionary here (JMdict/KRDICT are heavier to
      parse and license correctly); services/reader_service.py asks
      the LLM engine instead, for words CC-CEDICT doesn't cover too.
"""

import os
import re
import urllib.request

import portable
from core import atomic_write

CEDICT_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
CEDICT_PATH = os.path.join(portable.data_dir(), "library", "cedict.txt")

_cedict_cache = None


def _ensure_cedict():
    """Downloads CC-CEDICT if not already present locally. Requires
    internet on first use only; the file is then kept at CEDICT_PATH,
    so later Reader use is fully offline."""
    if os.path.exists(CEDICT_PATH):
        return
    import gzip
    os.makedirs(os.path.dirname(CEDICT_PATH), exist_ok=True)
    with urllib.request.urlopen(CEDICT_URL, timeout=30) as resp:
        raw = gzip.decompress(resp.read())
    atomic_write(CEDICT_PATH, raw, binary=True)


def load_cedict():
    global _cedict_cache
    if _cedict_cache is not None:
        return _cedict_cache
    _ensure_cedict()
    entries = {}
    # CC-CEDICT line format: Traditional Simplified [pin1 yin1] /def 1/def 2/
    line_re = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+/(.+)/$")
    with open(CEDICT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            m = line_re.match(line.strip())
            if not m:
                continue
            _trad, simp, pinyin, defs = m.groups()
            entries.setdefault(simp, []).append({
                "pinyin": pinyin, "definitions": defs.split("/"),
            })
    _cedict_cache = entries
    return entries


def lookup_cedict(word: str):
    entries = load_cedict()
    hits = entries.get(word)
    if not hits:
        return None
    # Prefer the entry with the most definitions (usually the primary sense)
    best = max(hits, key=lambda h: len(h["definitions"]))
    return {"word": word, "pinyin": best["pinyin"], "definitions": best["definitions"]}
