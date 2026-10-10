"""
segment_splitting.py -- cuts long Whisper segments into subtitle-sized lines on
sentence, clause and word-timing boundaries, and encodes/decodes the Whisper
word times a line keeps so a later re-split can cut on real pauses.
"""

import bisect
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Optional


# A transcribed line longer than either limit is cut into subtitle-sized pieces.
SPLIT_MAX_SECONDS = 8.0
SPLIT_MAX_CJK_CHARS = 40

_SENTENCE_END_RE = re.compile(r"(?:[。！？!?…]+|\.+(?=\s|$))[\"'”’」』）)\]]*\s*")
_CLAUSE_END_RE = re.compile(r"[,，、;；:：]+[\"'”’」』）)\]]*\s*")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
# Only a space run touching CJK on at least one side is a phrase break; a space
# between two Latin/digit tokens ("Q&A NG") stays inside its piece.
_CJK_SPACE_RE = re.compile(r"(?<=[぀-ヿ㐀-鿿가-힯])\s+|\s+(?=[぀-ヿ㐀-鿿가-힯])")


# Pieces under either floor are folded into a neighbour when SplitRules is in use:
# a sub-second or two-word subtitle flashes by too fast to read.
MIN_PIECE_SECONDS = 0.8
MIN_PIECE_CJK_CHARS = 4
MIN_PIECE_WORDS = 2
# A silence between two Whisper words at least this long is a place a line with no
# punctuation may be cut; shorter ones are ordinary breathing inside a phrase. The
# per-title "Pause that can split a long line" setting; keep the bounds in sync
# with frontend/src/pages/workspace/sourceForm.ts.
MIN_WORD_GAP_SECONDS = 0.35
MIN_WORD_GAP_SECONDS_MIN = 0.1
MIN_WORD_GAP_SECONDS_MAX = 2.0
# Gaps this close to the largest candidate count as equally good, so the cut that
# lands nearest the middle wins and a line is not peeled one stub at a time.
_SIMILAR_GAP_RATIO = 0.75

# Words whose trailing "." is not a sentence end.
_ABBREVIATIONS = frozenset((
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc", "inc", "ltd", "co",
    "gen", "col", "lt", "sgt", "capt", "rev", "hon", "fig", "vol", "pp", "approx", "dept"))
_NUMBERED_BEFORE_DIGIT = frozenset(("no", "nos", "fig", "vol", "ch", "p", "pp"))
_QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"))


@dataclass(frozen=True)
class SplitRules:
    """Per-line limits for the re-split sensitivity presets. None leaves that
    limit off. max_chars counts CJK characters, or all non-edge characters in
    text with none (English). per_sentence cuts at every sentence end instead of
    packing sentences up to the limits."""
    max_seconds: Optional[float] = SPLIT_MAX_SECONDS
    max_chars: Optional[int] = None
    per_sentence: bool = False
    # False keeps the fixed-limit rule that only CJK characters count, so a
    # duration cap alone never starts cutting English lines by length.
    count_latin: bool = True

    def measure(self, text: str) -> int:
        cjk = len(_CJK_RE.findall(text))
        return cjk or (len(text.strip()) if self.count_latin else 0)


_QUOTE_CHARS = ('"',) + tuple(c for pair in _QUOTE_PAIRS for c in pair)
# Abbreviations, initials and list numbers are far shorter than this; a bound
# keeps the backward scan linear on a line with no whitespace.
_WORD_LOOKBACK = 64


def _sentence_keep():
    """A `keep` callback for _cut_after that tracks open quotes incrementally.
    Matches arrive in order, so each call counts only the text since the last
    one; rescanning the head per match is quadratic on a long line."""
    seen = 0
    counts = dict.fromkeys(_QUOTE_CHARS, 0)

    def keep(text: str, m) -> bool:
        nonlocal seen
        chunk = text[seen:m.end()]
        seen = m.end()
        for ch in _QUOTE_CHARS:
            counts[ch] += chunk.count(ch)
        inside = (counts['"'] % 2 == 1
                  or any(counts[o] > counts[c] for o, c in _QUOTE_PAIRS))
        return not inside and _real_sentence_end(text, m)
    return keep


def _real_sentence_end(text: str, m) -> bool:
    """False for an English "." that only looks like a sentence end: after an
    abbreviation, initial or list number, or before a lowercase continuation.
    Quote state is the caller's (_sentence_keep)."""
    if not m.group().startswith("."):
        return True
    word = re.search(r"(\S+)$", text[max(0, m.start() - _WORD_LOOKBACK):m.start()])
    word = word.group(1).strip("\"'“‘([").lower() if word else ""
    if word.rstrip(".") in _ABBREVIATIONS or word.isdigit() or re.fullmatch(r"(?:[a-z]\.)+[a-z]", word):
        return False
    if len(word) == 1 and word != "i":
        return False
    after = text[m.end():m.end() + 1]
    if after.islower():
        return False
    return not (after.isdigit() and word in _NUMBERED_BEFORE_DIGIT)


def _cut_after(text: str, pattern, keep=None) -> list:
    """Cuts text after every match of pattern (that `keep` accepts, if given);
    the pieces concatenate back to text."""
    pieces, last = [], 0
    for m in pattern.finditer(text):
        if m.end() > last and (keep is None or keep(text, m)):
            pieces.append(text[last:m.end()])
            last = m.end()
    if last < len(text):
        pieces.append(text[last:])
    return pieces


def exceeds_limits(seg, rules: SplitRules) -> bool:
    """Whether a segment is longer than `rules` allow."""
    text = seg.get("text") or ""
    dur = seg["end"] - seg["start"]
    return (bool(text.strip()) and dur > 0
            and ((rules.max_seconds is not None and dur > rules.max_seconds)
                 or (rules.max_chars is not None and rules.measure(text) > rules.max_chars)))


def split_long_segments(segments, max_seconds: float = SPLIT_MAX_SECONDS,
                        max_cjk_chars: int = SPLIT_MAX_CJK_CHARS, *,
                        rules: Optional[SplitRules] = None,
                        min_pause: float = MIN_WORD_GAP_SECONDS) -> list:
    """Cuts over-long segments ({"start","end","text",...}) at sentence-ending
    punctuation, then at commas, then at spaces next to CJK text, packing
    neighbouring pieces up to the limits.

    When a segment carries Whisper word timings that spell exactly its text,
    pieces are timed by their own first and last word, and a line with no
    punctuation is cut at the real pauses between words. Without words, or when
    they cannot be mapped onto the text exactly, the old estimate is the
    fallback: each piece gets a share of the original span proportional to its
    character count, so boundaries are guesses but pieces stay contiguous,
    increasing and inside the span. That fallback still cuts at punctuation
    (a failed word mapping does not leave a punctuated line whole, because a
    punctuation cut is what this function did before words existed); a line
    with no punctuation is left whole.
    Text with no usable cut, and short segments, are returned as they are.
    Other keys (speaker) are copied onto every piece.

    `rules` (the re-split presets) replaces the two limits and also skips cuts
    inside quotes or after abbreviations, and folds pieces under the
    MIN_PIECE_* floors into a neighbour; without it behaviour is unchanged.

    `min_pause`: the shortest silence between words a line with no punctuation
    may be cut at."""
    out = []
    for seg in segments:
        text = seg.get("text") or ""
        dur = seg["end"] - seg["start"]
        total = len("".join(text.split()))
        if rules:
            limit_s, limit_c, count = rules.max_seconds, rules.max_chars, rules.measure
        else:
            limit_s, limit_c = max_seconds, max_cjk_chars
            count = lambda t: len(_CJK_RE.findall(t))  # noqa: E731
        over = ((limit_s is not None and dur > limit_s)
                or (limit_c is not None and count(text) > limit_c))
        wants_cut = over or bool(rules and rules.per_sentence)
        if total == 0 or dur <= 0 or not wants_cut:
            out.append(seg)
            continue

        def estimated_fits(piece, at=0):
            n = len("".join(piece.split()))
            return ((limit_s is None or dur * n / total <= limit_s)
                    and (limit_c is None or count(piece) <= limit_c))

        def cut(fits):
            """The punctuation pieces (strings that concatenate to `text`); `fits`
            gets the piece and its character offset in `text`."""
            def pack(units, at):
                chunks, cur, start = [], "", at
                for u in units:
                    if cur and not fits(cur + u, start):
                        chunks.append(cur)
                        start += len(cur)
                        cur = ""
                    cur += u
                if cur:
                    chunks.append(cur)
                return chunks

            sentences = _cut_after(text, _SENTENCE_END_RE, _sentence_keep() if rules else None)
            pieces, at = [], 0
            for chunk in sentences if rules and rules.per_sentence else pack(sentences, 0):
                # one sentence that is still too long: fall back to its commas
                subs = [chunk] if fits(chunk, at) else pack(_cut_after(chunk, _CLAUSE_END_RE), at)
                sub_at = at
                for sub in subs:
                    # Whisper often separates CJK phrases with plain spaces instead of commas
                    pieces.extend([sub] if fits(sub, sub_at)
                                  else pack(_cut_after(sub, _CJK_SPACE_RE), sub_at))
                    sub_at += len(sub)
                at += len(chunk)
            return pieces

        index = _WordIndex.build(text, seg.get("words")) if seg.get("words") else None
        if index:
            def fits_words(i, j):
                return ((limit_s is None or index.seconds(i, j) <= limit_s)
                        and (limit_c is None or index.measure(i, j, rules) <= limit_c))

            def fits_real(piece, at):
                words = index.word_range(at, at + len(piece))
                return estimated_fits(piece) if words is None else fits_words(*words)

            split = _split_on_words(seg, text, cut(fits_real), index, fits_words, rules,
                                    min_pause)
            if split is not None:
                out.extend(split)
                continue
        pieces = cut(estimated_fits)
        pieces = [p for p in pieces if p.strip()]
        if rules:
            pieces = _fold_small(
                pieces, lambda p: _is_stub(p, dur * len("".join(p.split())) / total))
        if len(pieces) < 2:
            out.append(seg)
            continue
        bare = {k: v for k, v in seg.items() if k != "words"}
        done = 0
        for i, p in enumerate(pieces):
            start = seg["start"] + dur * done / total
            done += len("".join(p.split()))
            end = seg["end"] if i == len(pieces) - 1 else seg["start"] + dur * done / total
            # estimated cuts: the words no longer line up with the text
            out.append({**bare, "start": start, "end": end, "text": p.strip()})
    return out


def _fold_small(pieces: list, small, join="".join) -> list:
    """Merges each piece for which small(piece) holds into its previous
    neighbour (the next one for the first piece), so no stub survives. A merged
    piece may pass the max limits; that beats an unreadable flash."""
    # One forward pass: a piece that is not small stays so once a neighbour is
    # appended, so only the incoming piece ever needs checking. Parts are joined
    # at the end to keep a run of stubs linear.
    out, carry = [], []
    for p in pieces:
        parts = carry + [p]
        carry = []
        if not small(join(parts)):
            out.append(parts)
        elif out:
            out[-1].extend(parts)
        else:
            carry = parts
    if carry:
        out.append(carry)
    return [join(parts) for parts in out]


def _is_stub(text: str, seconds: float) -> bool:
    cjk = len(_CJK_RE.findall(text))
    return (cjk < MIN_PIECE_CJK_CHARS if cjk else len(text.split()) < MIN_PIECE_WORDS) \
        or seconds < MIN_PIECE_SECONDS


class _WordIndex:
    """A segment's Whisper words laid over the character offsets of its text.

    `build` returns None unless the words' text, apart from whitespace, is exactly
    the segment's text and their times are numbers: then a cut chosen by the
    words can never reword, add or drop text. Word i spans text[cs[i]:ce[i]] and
    ts[i]..te[i] seconds; ends and starts are clamped to be non-decreasing because
    Whisper's neighbouring words overlap a little."""

    def __init__(self, kept, cs, ce, ts, te, cjk):
        self.kept, self.cs, self.ce, self.ts, self.te, self.cjk = kept, cs, ce, ts, te, cjk
        self._sparse = None

    @classmethod
    def build(cls, text: str, words):
        kept, cs, ce, ts, te, cjk = [], [], [], [], [], [0]
        pos, size = 0, len(text)
        for w in words or ():
            try:
                token = str(w["word"]).strip()
                start, end = float(w["start"]), float(w["end"])
            except (KeyError, TypeError, ValueError):
                return None
            if not token:
                continue
            if not (math.isfinite(start) and math.isfinite(end) and end >= start):
                return None
            while pos < size and text[pos].isspace():
                pos += 1
            if not text.startswith(token, pos):
                return None
            kept.append(w)
            cs.append(pos)
            pos += len(token)
            ce.append(pos)
            ts.append(max(start, ts[-1]) if ts else start)
            te.append(max(end, ts[-1], te[-1]) if te else end)
            cjk.append(cjk[-1] + len(_CJK_RE.findall(token)))
        if not kept or text[pos:].strip():
            return None
        return cls(kept, cs, ce, ts, te, cjk)

    def word_range(self, a: int, b: int):
        """(i, j) of the words inside text[a:b], or None when a word straddles
        either edge or the range holds none."""
        i, j = bisect.bisect_left(self.cs, a), bisect.bisect_left(self.cs, b)
        if j <= i or (i and self.ce[i - 1] > a) or self.ce[j - 1] > b:
            return None
        return i, j

    def seconds(self, i: int, j: int) -> float:
        return self.te[j - 1] - self.ts[i]

    def measure(self, i: int, j: int, rules: Optional["SplitRules"]) -> int:
        cjk = self.cjk[j] - self.cjk[i]
        if rules is None:
            return cjk
        return cjk or ((self.ce[j - 1] - self.cs[i]) if rules.count_latin else 0)

    def _max_gap(self, lo: int, hi: int) -> float:
        """Largest silence before any of the words lo..hi (inclusive), by a sparse
        table so a cut is found in O(log n) however many words the line has."""
        if self._sparse is None:
            table = [[-math.inf] + [self.ts[k] - self.te[k - 1] for k in range(1, len(self.ts))]]
            width = 1
            while 2 * width <= len(self.ts):
                prev = table[-1]
                table.append([max(prev[k], prev[k + width]) for k in range(len(prev) - width)])
                width *= 2
            self._sparse = table
        level = (hi - lo + 1).bit_length() - 1
        row = self._sparse[level]
        return max(row[lo], row[hi - (1 << level) + 1])

    def _nearest_gap(self, i: int, j: int, lo: int, hi: int, threshold: float):
        """The word k in lo..hi whose preceding silence is at least `threshold`
        and sits nearest the middle of words i..j-1, or None."""
        middle = (self.ts[i] + self.te[j - 1]) / 2
        pivot = min(max(bisect.bisect_left(self.ts, middle), lo), hi)
        found = []
        if self._max_gap(lo, pivot) >= threshold:
            a, b = lo, pivot
            while a < b:  # largest x with a qualifying gap in x..pivot
                mid = (a + b + 1) // 2
                a, b = (mid, b) if self._max_gap(mid, pivot) >= threshold else (a, mid - 1)
            found.append(a)
        if pivot < hi and self._max_gap(pivot + 1, hi) >= threshold:
            a, b = pivot + 1, hi
            while a < b:  # smallest y with a qualifying gap in pivot+1..y
                mid = (a + b) // 2
                a, b = (a, mid) if self._max_gap(pivot + 1, mid) >= threshold else (mid + 1, b)
            found.append(a)
        return min(found, key=lambda k: abs((self.te[k - 1] + self.ts[k]) / 2 - middle),
                   default=None)

    def gap_cuts(self, i: int, j: int, fits, min_pause: float = MIN_WORD_GAP_SECONDS) -> list:
        """Word ranges covering i..j-1 that fit, cut only at pauses of at least
        `min_pause` and never leaving a side under the MIN_PIECE_* floors.
        A range with no admissible pause is returned as it is."""
        done, stack = [], [(i, j)]
        while stack:
            a, b = stack.pop()
            k = None if fits(a, b) or b - a < 2 else self._pick_cut(a, b, min_pause)
            if k is None:
                done.append((a, b))
            else:
                stack.extend(((k, b), (a, k)))
        return done

    def _pick_cut(self, i: int, j: int, min_pause: float):
        cjk = self.cjk[j] - self.cjk[i]
        if cjk:
            lo = bisect.bisect_left(self.cjk, self.cjk[i] + MIN_PIECE_CJK_CHARS)
            hi = bisect.bisect_right(self.cjk, self.cjk[j] - MIN_PIECE_CJK_CHARS) - 1
        else:
            lo, hi = i + MIN_PIECE_WORDS, j - MIN_PIECE_WORDS
        lo = max(lo, i + 1, bisect.bisect_left(self.te, self.ts[i] + MIN_PIECE_SECONDS) + 1)
        hi = min(hi, j - 1, bisect.bisect_right(self.ts, self.te[j - 1] - MIN_PIECE_SECONDS) - 1)
        if lo > hi:
            return None
        biggest = self._max_gap(lo, hi)
        if biggest < min_pause:
            return None
        return self._nearest_gap(i, j, lo, hi, max(min_pause,
                                                   biggest * _SIMILAR_GAP_RATIO))


def _split_on_words(seg, text, pieces, index, fits, rules, min_pause=MIN_WORD_GAP_SECONDS):
    """Cuts `seg` where its punctuation pieces (strings that concatenate to
    `text`) and then the largest pauses between words say, with each piece timed
    by its own first and last word and its text sliced from `text`. Returns None
    when a cut would fall inside a word or a piece has no usable word time, so the
    caller can fall back to the character-share estimate."""
    ranges, pos = [], 0
    for p in pieces:
        if p.strip():
            words = index.word_range(pos, pos + len(p))
            if words is None:
                return None
            ranges.append((pos, pos + len(p)) + words)
        pos += len(p)
    if not ranges:
        return None

    def join(parts):
        return parts[0][0], parts[-1][1], parts[0][2], parts[-1][3]

    if rules:
        ranges = _fold_small(
            ranges, lambda r: _is_stub(text[r[0]:r[1]], index.seconds(r[2], r[3])), join)

    cut = []
    for a, b, i, j in ranges:
        parts = sorted(index.gap_cuts(i, j, fits, min_pause))
        for n, (pi, pj) in enumerate(parts):
            cut.append((a if n == 0 else index.cs[pi], b if n == len(parts) - 1 else index.cs[pj],
                        pi, pj))
    if len(cut) < 2:
        return [seg]
    out, floor = [], seg["start"]
    for a, b, i, j in cut:
        start = max(index.ts[i], floor)
        end = min(index.te[j - 1], seg["end"])
        piece = text[a:b].strip()
        if end <= start or not piece:
            return None
        out.append({**seg, "start": start, "end": end, "text": piece,
                    "words": index.kept[i:j]})
        floor = end
    return out


# A line's Whisper words are stored with it (lines.word_timings) only up to these
# sizes: a Whisper line has a few dozen words, and a line merged far past that is
# not worth a large row on every save.
MAX_STORED_WORDS = 1000
MAX_STORED_WORD_BYTES = 32_000
# Far past any real recording; a stored time beyond it can only be a hand-made row.
_MAX_WORD_MS = 100 * 3600 * 1000


def text_fingerprint(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def encode_line_words(text: str, words) -> Optional[str]:
    """The stored form of a line's words: its text's fingerprint and, per word,
    [first char, end char, start ms, end ms]. None when the words don't spell
    exactly `text` or pass the MAX_STORED_* caps."""
    if not words or len(words) > MAX_STORED_WORDS:
        return None
    index = _WordIndex.build(text, words)
    if index is None:
        return None
    rows = [[a, b, round(t0 * 1000), round(t1 * 1000)]
            for a, b, t0, t1 in zip(index.cs, index.ce, index.ts, index.te)]
    payload = json.dumps({"h": text_fingerprint(text), "w": rows}, separators=(",", ":"))
    return payload if len(payload) <= MAX_STORED_WORD_BYTES else None


def words_for_text(payload, text: str) -> Optional[str]:
    """`payload` (a stored word_timings value) when it was computed against
    exactly `text`, else None: for carrying words through a snapshot without
    ever attaching them to other text. line_words checks the rest on use."""
    if not isinstance(payload, str) or len(payload) > MAX_STORED_WORD_BYTES:
        return None
    try:
        data = json.loads(payload)
        return payload if isinstance(data, dict) and data.get("h") == text_fingerprint(text) else None
    except (ValueError, RecursionError):
        return None


def line_words(ln) -> Optional[list]:
    """A line's stored words as {start, end, word} dicts, or None unless they
    were computed against exactly its current text and fall inside its time.
    Any text edit changes the fingerprint, so stale words are never used."""
    payload, text = getattr(ln, "word_timings", None), ln.zh or ""
    if not isinstance(payload, str) or len(payload) > MAX_STORED_WORD_BYTES:
        return None
    try:
        data = json.loads(payload)
        if data["h"] != text_fingerprint(text):
            return None
        words, pos = [], 0
        for a, b, t0, t1 in data["w"]:
            # encode_line_words writes only ints, so anything else (a float,
            # inf/nan, a bool) is a hand-made backup row, never a transcription.
            if not (all(type(v) is int for v in (a, b, t0, t1))
                    and pos <= a < b <= len(text) and 0 <= t0 <= t1 <= _MAX_WORD_MS):
                return None
            words.append({"word": text[a:b], "start": t0 / 1000, "end": t1 / 1000})
            pos = b
    # A backup can carry any string: deep nesting makes json.loads recurse.
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return None
    index = _WordIndex.build(text, words)
    # Times are absolute audio times, so a re-timed line keeps its words; a row
    # whose words lie wholly outside its span describes some other audio.
    if index is None or index.te[-1] <= ln.start or index.ts[0] >= ln.end:
        return None
    return words


def line_word_index(ln) -> Optional["_WordIndex"]:
    words = line_words(ln)
    return _WordIndex.build(ln.zh or "", words) if words else None


def span_words(index: "_WordIndex", a: int, b: int, piece: str) -> Optional[str]:
    """Stored words for a piece cut from text[a:b] (`piece` is that slice,
    stripped or not), or None when a word straddles a cut."""
    found = index.word_range(a, b)
    return encode_line_words(piece, index.kept[found[0]:found[1]]) if found else None


def pause_offsets(index: "_WordIndex", min_pause: float = MIN_WORD_GAP_SECONDS) -> set:
    """Character offsets where a word starts after a silence of at least
    `min_pause`: real pauses a line may be cut at."""
    return {index.cs[k] for k in range(1, len(index.cs))
            if index.ts[k] - index.te[k - 1] >= min_pause}


def word_cut_times(index: "_WordIndex", spans, start: float, end: float) -> Optional[list]:
    """Cut times between consecutive (a, b) character spans of a line: each
    is the next piece's first word start. None unless every span holds whole
    words and the cuts increase strictly inside (start, end)."""
    ranges = [index.word_range(a, b) for a, b in spans]
    if any(r is None for r in ranges):
        return None
    cuts = [index.ts[i] for i, _j in ranges[1:]]
    if all(start < c < end for c in cuts) and all(x < y for x, y in zip(cuts, cuts[1:])):
        return cuts
    return None


def tighten_to_words(start: float, end: float, words) -> tuple:
    """A segment's own start/end come from its VAD chunk, so a line can show
    during silence before the voice starts or stay up after it stops. Narrow
    them to the first and last spoken word, never widening, and keep the
    segment's times when there are no usable word times."""
    try:
        spoken = [w for w in (words or []) if w.end > w.start]
        if not spoken:
            return start, end
        new_start = max(start, min(w.start for w in spoken))
        new_end = min(end, max(w.end for w in spoken))
    except (AttributeError, TypeError):
        return start, end
    return (new_start, new_end) if new_end > new_start else (start, end)


def _word_dicts(words) -> list:
    """faster-whisper Word objects as plain {start, end, word} dicts; empty when
    the segment has none (word timestamps off, or an older release)."""
    try:
        return [{"start": w.start, "end": w.end, "word": w.word} for w in words or ()]
    except AttributeError:
        return []
