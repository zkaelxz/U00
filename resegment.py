"""
resegment.py -- meaning-based subtitle re-segmentation (Step 6c, idea from
VideoLingo's core/_3_1_split_nlp.py and _3_2_split_meaning.py).

A transcribed line's boundaries come from Whisper's voice-activity
detection -- silence gaps -- not from meaning: a line can end mid-sentence
because the speaker paused, or run several thoughts together because they
didn't. This splits only the lines too long to read as one subtitle, at
the most meaningful boundary available, in this order:

  1. sentence ends (。！？ ...);
  2. clause breaks (，、 ... and, for zh/ja, the spaces Whisper leaves
     between clauses);
  3. a clause-joining word (但是/所以/でも/けど/그리고 ...) -- only where
     segment.py's per-language segmenter confirms it's a whole word, so a
     split never lands inside a longer word. This is the stand-in for
     VideoLingo's spaCy "grammatical root" pass: segment.py knows word
     boundaries but not grammar, and adding spaCy was ruled out;
  4. one LLM pass for anything still too long -- the model may only mark
     where to break ([br]); its answer is matched back to the original
     text and thrown away if it changed anything.

A line that still can't be split at a meaningful boundary is left whole
rather than cut at an arbitrary character count -- export-time wrapping
(subtitle_formats.wrap_text) already handles displaying a long line.

Every piece is a slice of the original text, so nothing here can reword,
add or drop content. Nothing here touches the database either:
resegment_lines() returns new Line objects and leaves the caller's alone.
"""
import dataclasses
import difflib
import json
import re

import subtitle_formats

# Split AFTER one of these (and after any run of closing punctuation,
# quotes and spaces that follows it, so "……" or "。」" stay whole).
_SENTENCE_MARKS = "。！？!?…；;"
_CLAUSE_MARKS = "，、,：:"
_TRAILING = _SENTENCE_MARKS + _CLAUSE_MARKS + "」』”’）)》〉】"

# Clause-joining words. "before": starts the next clause, so split before
# it. "after": ends the current clause, so split after it. Kept to words
# that are unambiguous as connectives -- e.g. Japanese から ("because" /
# "from") is left out, since splitting after 東京から would cut a clause.
_CONNECTIVES_BEFORE = {
    "zh": ("但是", "可是", "不过", "不過", "然后", "然後", "所以", "因为", "因為", "而且",
           "如果", "虽然", "雖然", "于是", "於是", "结果", "結果", "其实", "其實", "只是"),
    "ja": ("でも", "しかし", "だから", "それで", "そして", "それに", "ただ"),
    "ko": ("그리고", "그런데", "하지만", "그래서", "그러나", "근데", "그러니까"),
}
_CONNECTIVES_AFTER = {
    "ja": ("けれども", "けれど", "けど", "ので", "のに"),
}

LLM_MIN_SIMILARITY = 0.9
LLM_MAX_ATTEMPTS = 3


def max_line_chars(language: str) -> int:
    """A line longer than a two-line subtitle cue is "too long" -- the same
    per-language line limits Step 6b's export wrapping uses, doubled."""
    return 2 * subtitle_formats.line_char_limit(language)


def _length(text: str) -> int:
    return len(text.strip())


def word_boundaries(text: str, language: str, chinese_script: str = "simplified"):
    """Offsets where a word starts or ends (0 and len(text) included), from
    segment.py's segmenter -- or None if that segmenter isn't installed, or
    its words don't reassemble into exactly `text`."""
    try:
        import segment
        words = segment.segment_words(text, language, chinese_script)
    except ImportError:
        return None
    if "".join(words) != text:
        return None
    bounds, pos = {0}, 0
    for w in words:
        pos += len(w)
        bounds.add(pos)
    return bounds


def _after_marks(text: str, i: int) -> int:
    """Index just past the mark at i and any closing run after it."""
    j = i + 1
    while j < len(text) and (text[j] in _TRAILING or text[j].isspace()):
        j += 1
    return j


def _sentence_cuts(text, language, bounds):
    cuts = {_after_marks(text, i) for i, ch in enumerate(text) if ch in _SENTENCE_MARKS}
    # "." only ends a sentence when a space follows (not "3.5", not "...").
    cuts |= {m.end() for m in re.finditer(r"(?<!\.)\.(?!\.)\s+", text)}
    return cuts


def _clause_cuts(text, language, bounds):
    cuts = {_after_marks(text, i) for i, ch in enumerate(text) if ch in _CLAUSE_MARKS}
    if language in ("zh", "ja"):
        # No spaces between words in zh/ja, so a space Whisper left inside
        # the text marks a pause between clauses. (Korean spaces separate
        # every word, so they mean nothing here.)
        cuts |= {m.end() for m in re.finditer(r"\S\s+(?=\S)", text)}
    return cuts


def _connective_cuts(text, language, bounds):
    if bounds is None:
        return set()
    cuts = set()
    for word in _CONNECTIVES_BEFORE.get(language, ()):
        for m in re.finditer(re.escape(word), text):
            if m.start() in bounds and m.end() in bounds:
                cuts.add(m.start())
    for word in _CONNECTIVES_AFTER.get(language, ()):
        for m in re.finditer(re.escape(word), text):
            if m.start() in bounds and m.end() in bounds:
                cuts.add(_after_marks(text, m.end() - 1))
    return cuts


_TIERS = (_sentence_cuts, _clause_cuts, _connective_cuts)


def rule_split_spans(text: str, language: str, max_chars: int, bounds=None) -> list:
    """(start, end) spans covering `text`, split by the rule tiers only.
    A span is split only if it's longer than max_chars, at the tier-1 cut
    nearest its middle (then tier 2, then 3) that leaves both sides at
    least a quarter of max_chars -- so no one- or two-character stubs --
    then each side is split the same way. A span no tier can split is
    returned whole, however long."""
    candidates = [tier(text, language, bounds) for tier in _TIERS]
    min_chars = max(2, max_chars // 4)

    def split(s, e):
        if _length(text[s:e]) <= max_chars:
            return [(s, e)]
        mid = (s + e) / 2
        for cuts in candidates:
            ok = [c for c in cuts if s < c < e
                  and _length(text[s:c]) >= min_chars and _length(text[c:e]) >= min_chars]
            if ok:
                c = min(ok, key=lambda c: abs(c - mid))
                return split(s, c) + split(c, e)
        return [(s, e)]

    return split(0, len(text))


# ---------------------------------------------------------------- LLM pass

def _llm_prompt(text: str, language: str, max_chars: int) -> str:
    lang = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}.get(language, "Chinese")
    return (
        f"This {lang} subtitle line is too long to read in one go ({_length(text)} characters; "
        f"a subtitle should be at most about {max_chars}). Split it into two or more shorter "
        f"subtitles at natural meaning boundaries -- between clauses or ideas, never inside a "
        f"word or a name.\n\n"
        f"Rules:\n"
        f"- Insert the marker [br] at each split point.\n"
        f"- Do NOT change, add, remove, reorder, correct or translate any character. With the "
        f"[br] markers removed, your text must be exactly the original.\n"
        f'- Return only JSON: {{"text": "<the original line with [br] markers inserted>"}}\n\n'
        f"Line:\n{text}"
    )


def _parse_marked_text(raw: str):
    raw = re.sub(r"^```json|^```|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    marked = data.get("text") if isinstance(data, dict) else None
    return marked if isinstance(marked, str) else None


def match_llm_breaks(text: str, marked: str, bounds=None):
    """Maps an LLM's [br]-marked version of `text` back onto the ORIGINAL
    text: returns (start, end) spans of `text` itself, or None if the
    answer can't be trusted -- fewer than two parts, overall similarity
    under LLM_MIN_SIMILARITY, a break that lands in text the model
    changed, a break inside a word (when word boundaries are known), or
    any single part under the similarity threshold against the original
    slice it maps to. The model's own characters are never used, so a
    reworded or hallucinated answer can only fail to split, never
    silently rewrite the line."""
    parts = [p for p in (marked or "").split("[br]") if p.strip()]
    if len(parts) < 2:
        return None

    orig_idx = [i for i, ch in enumerate(text) if not ch.isspace()]
    orig_norm = "".join(text[i] for i in orig_idx)
    parts_norm = [re.sub(r"\s+", "", p) for p in parts]
    joined = "".join(parts_norm)
    sm = difflib.SequenceMatcher(None, orig_norm, joined, autojunk=False)
    if sm.ratio() < LLM_MIN_SIMILARITY:
        return None
    blocks = sm.get_matching_blocks()

    cuts, pos = [], 0
    for p in parts_norm[:-1]:
        pos += len(p)
        mapped = next((b.a + (pos - b.b) for b in blocks if b.size and b.b <= pos <= b.b + b.size),
                      None)
        if mapped is None or not 0 < mapped < len(orig_norm):
            return None
        cut = orig_idx[mapped - 1] + 1  # just after the last original character before the break
        if bounds is not None and cut not in bounds:
            return None
        cuts.append(cut)
    if cuts != sorted(set(cuts)):
        return None

    edges = [0] + cuts + [len(text)]
    spans = list(zip(edges, edges[1:]))
    for (s, e), p in zip(spans, parts_norm):
        piece = re.sub(r"\s+", "", text[s:e])
        if not piece or difflib.SequenceMatcher(None, piece, p, autojunk=False).ratio() < LLM_MIN_SIMILARITY:
            return None
    return spans


def llm_split_spans(text: str, engine, language: str, max_chars: int, bounds=None,
                    usage_cb=None, attempts: int = LLM_MAX_ATTEMPTS):
    """Asks `engine` for break points, retrying up to `attempts` times on an
    answer match_llm_breaks rejects. None if none of them was usable."""
    from translate_engines import call_llm_json
    prompt = _llm_prompt(text, language, max_chars)
    for _ in range(attempts):
        marked = _parse_marked_text(call_llm_json(engine, prompt, max_tokens=1000,
                                                  fallback="{}", usage_cb=usage_cb))
        spans = match_llm_breaks(text, marked, bounds) if marked else None
        if spans:
            return spans
    return None


# ---------------------------------------------------------------- timing

def split_times(line, pieces, segments=None) -> list:
    """Cut times between consecutive pieces of `line`, so piece k runs from
    cuts[k-1] (or line.start) to cuts[k] (or line.end) -- contiguous, no
    gap or overlap, and the outer edges are exactly the original line's.

    Uses the app's existing timing reconstruction (core.align_transcript_to_timing,
    the same character-level alignment the Transcribe & Align step uses)
    against the stored transcription segments overlapping this line, when
    there are any and they give a usable answer; otherwise splits the
    line's time in proportion to each piece's length."""
    start, end, n = line.start, line.end, len(pieces)
    weights = [max(_length(p), 1) for p in pieces]
    total = sum(weights)
    proportional = [start + (end - start) * sum(weights[:k]) / total for k in range(1, n)]

    window = [s for s in (segments or []) if s.get("end", 0) > start and s.get("start", 0) < end]
    if window:
        import core
        aligned = core.align_transcript_to_timing(pieces, window)
        cuts = [aligned[k].start for k in range(1, n)]
        if all(start < c < end for c in cuts) and all(a < b for a, b in zip(cuts, cuts[1:])):
            return cuts
    return proportional


# ---------------------------------------------------------------- lines

def resegment_lines(lines, language: str = "zh", engine=None, segments=None,
                    chinese_script: str = "simplified", max_chars: int = None,
                    usage_cb=None, boundaries_fn=word_boundaries):
    """Returns (new_lines, changed).

    new_lines: fresh Line objects for the whole drama, renumbered in order.
    `lines` itself is never modified. A line that isn't split is an
    unchanged copy -- same permanent id, translation, flag and speaker --
    so saving new_lines leaves it (and its notes) alone. A line that IS
    split becomes new lines with no id and no translation/flag, carrying
    only its speaker: saving them deletes the original row, and with it
    the notes and emotion tag that described the old, longer line.

    changed: [(original_line, [piece texts]), ...] for each split line.

    engine: optional LLM engine for the one LLM pass; None skips it.
    segments: the drama's stored transcription segments
    (raw_transcript.load_latest()["segments"]), for timing; optional."""
    from core import Line
    max_chars = max_chars or max_line_chars(language)
    new_lines, changed = [], []
    for ln in lines:
        text = ln.zh or ""
        if _length(text) <= max_chars:
            new_lines.append(dataclasses.replace(ln, merged_ids=list(ln.merged_ids)))
            continue
        bounds = boundaries_fn(text, language, chinese_script)
        spans = []
        for s, e in rule_split_spans(text, language, max_chars, bounds):
            if engine is not None and _length(text[s:e]) > max_chars:
                local = {b - s for b in bounds if s <= b <= e} if bounds is not None else None
                sub = llm_split_spans(text[s:e], engine, language, max_chars, local, usage_cb)
                if sub:
                    spans.extend((s + a, s + b) for a, b in sub)
                    continue
            spans.append((s, e))
        pieces = [text[s:e].strip() for s, e in spans if text[s:e].strip()]
        if len(pieces) < 2:
            new_lines.append(dataclasses.replace(ln, merged_ids=list(ln.merged_ids)))
            continue
        cuts = split_times(ln, pieces, segments)
        edges = [ln.start] + cuts + [ln.end]
        for k, piece in enumerate(pieces):
            new_lines.append(Line(idx=0, start=edges[k], end=edges[k + 1], zh=piece,
                                  speaker=ln.speaker, speaker_manual=ln.speaker_manual))
        changed.append((ln, pieces))
    for i, ln in enumerate(new_lines):
        ln.idx = i
    return new_lines, changed
