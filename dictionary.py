"""
dictionary.py -- word/phrase definitions for the Reader's click-to-
define feature.

  zh: CC-CEDICT (free, open, CC BY-SA licensed dictionary data).
      Auto-downloads to ./library/cedict.txt on first use if missing
      (needs internet once; cached locally after that).
  ja/ko: no bundled dictionary here (JMdict/KRDICT are heavier to
      parse and license correctly) -- definitions are generated
      on-demand via the LLM translation engine instead, batched per
      chapter/episode so it's one call, not one per word.

Both paths converge on the same output shape so the Reader doesn't
need to care which one supplied a given definition.
"""

import os
import re
import json
import urllib.request

CEDICT_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
CEDICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library", "cedict.txt")

_cedict_cache = None


def _ensure_cedict():
    """Downloads CC-CEDICT if not already present locally. Requires
    internet on first run only; the app polls this so subsequent
    Reader use is fully offline."""
    if os.path.exists(CEDICT_PATH):
        return
    import gzip
    os.makedirs(os.path.dirname(CEDICT_PATH), exist_ok=True)
    with urllib.request.urlopen(CEDICT_URL, timeout=30) as resp:
        raw = gzip.decompress(resp.read())
    with open(CEDICT_PATH, "wb") as f:
        f.write(raw)


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


def define_words_llm(words, context_lines, engine, source_language: str = "zh", batch_size: int = 25):
    """words: list of unique word/phrase strings needing a definition
    (already filtered to exclude ones found locally, e.g. via CC-CEDICT).
    context_lines: the surrounding text, so definitions can reflect
    contextual meaning rather than just a dictionary-blind gloss.
    Returns {word: {"reading": str|None, "definitions": [str]}}."""
    if not getattr(engine, "supports_reference", False) or not words:
        return {}
    lang_name = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}.get(source_language, "Chinese")
    context = "\n".join(context_lines[:50])  # cap context size
    out = {}
    unique_words = list(dict.fromkeys(words))
    for start in range(0, len(unique_words), batch_size):
        batch = unique_words[start:start + batch_size]
        numbered = "\n".join(f"{i+1}. {w}" for i, w in enumerate(batch))
        prompt = (
            f"Given this {lang_name} text as context:\n\n{context}\n\n"
            f"For each numbered {lang_name} word/phrase below, give its reading "
            "(pinyin for Chinese, hiragana for Japanese, or null for Korean) and "
            "1-2 short English definitions as used in this context. "
            'Return ONLY a JSON array of objects: [{"reading": "...", "definitions": ["...", "..."]}], '
            "one per input word, in order. No preamble, no markdown fences.\n\n" + numbered
        )
        from translate_engines import call_llm_json
        text = call_llm_json(engine, prompt, max_tokens=3000, fallback=None)
        if text is None:
            continue
        text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = [{"reading": None, "definitions": []} for _ in batch]
        for w, entry in zip(batch, parsed):
            out[w] = {"reading": entry.get("reading"), "definitions": entry.get("definitions", [])}
    return out


def build_word_definitions(words, context_lines, engine, source_language: str = "zh"):
    """Combines local dictionary (Chinese only) + LLM fallback into one
    lookup table for the Reader to embed."""
    definitions = {}
    needs_llm = []
    if source_language == "zh":
        for w in dict.fromkeys(words):
            hit = lookup_cedict(w)
            if hit:
                definitions[w] = {"reading": hit["pinyin"], "definitions": hit["definitions"][:3]}
            else:
                needs_llm.append(w)
    else:
        needs_llm = list(dict.fromkeys(words))

    if needs_llm and engine is not None:
        llm_defs = define_words_llm(needs_llm, context_lines, engine, source_language)
        definitions.update(llm_defs)
    return definitions
