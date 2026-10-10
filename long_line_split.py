"""
long_line_split.py -- the last resort for lines that are too long to read.

segment_splitting.split_long_segments cuts at sentence ends, then commas, then spaces and
real word pauses, and leaves a line whole when none of those exist. A
streamer VOD run through Qwen or Whisper often yields exactly that: ~100
characters over 90 seconds with no punctuation and no stored word timings.
This wraps the core splitter and cuts any piece still over the character
limit into equal runs of characters with proportional times, flagged
timing_uncertain so Review shows the times are guesses.

It also words why a re-segmentation preview found nothing, so the screen
never just says "nothing to do".
"""
import math

import segment_splitting
import resegment

EVEN_SPLIT_NOTE = resegment.EVEN_SPLIT_NOTE


def split_long_segments(segments, *, rules=None, min_pause: float = segment_splitting.MIN_WORD_GAP_SECONDS) -> list:
    """segment_splitting.split_long_segments, then an even cut of any piece still over the
    character limit. Pieces keep the line's other keys; an evenly cut piece
    drops its words (they no longer line up) and carries the approximate flag."""
    limit_c = rules.max_chars if rules else segment_splitting.SPLIT_MAX_CJK_CHARS
    limit_s = rules.max_seconds if rules else segment_splitting.SPLIT_MAX_SECONDS
    count = rules.measure if rules else (lambda t: len(segment_splitting._CJK_RE.findall(t)))
    out = []
    for piece in segment_splitting.split_long_segments(segments, rules=rules, min_pause=min_pause):
        text = piece.get("text") or ""
        if not limit_c or count(text) <= limit_c:
            out.append(piece)
            continue
        out.extend(_even_pieces(piece, text, -(-count(text) // limit_c), limit_s))
    return out


def _even_pieces(piece: dict, text: str, parts: int, limit_s) -> list:
    dur = piece["end"] - piece["start"]
    if limit_s and dur > 0:
        parts = max(parts, math.ceil(dur / limit_s - 1e-9))
    parts = min(parts, len(text.strip()))
    if parts < 2:
        return [piece]
    size = -(-len(text) // parts)
    bare = {k: v for k, v in piece.items() if k != "words"}
    chunks = [text[k:k + size] for k in range(0, len(text), size)]
    out, done = [], 0
    for chunk in chunks:
        start = piece["start"] + dur * done / len(text)
        done += len(chunk)
        end = piece["end"] if done >= len(text) else piece["start"] + dur * done / len(text)
        out.append({**bare, "start": start, "end": end, "text": chunk.strip(),
                    "flag": "timing_uncertain", "flag_note": EVEN_SPLIT_NOTE})
    return [p for p in out if p["text"]]


LONG_LINE_SECONDS = 12.0
LONG_LINE_CHARS = 40


def resegment_reason(lines, language: str, changed) -> str:
    """Why a rules preview cut nothing. Re-segmenting counts characters only, so
    a line that runs long in time but is short in text needs Split long lines."""
    if changed:
        return ""
    cap = resegment.max_line_chars(language)
    real = [ln for ln in lines if not ln.sfx and (ln.zh or "").strip()]
    over = sum(resegment.length(ln.zh) > cap for ln in real)
    slow = sum(ln.end - ln.start > LONG_LINE_SECONDS or resegment.length(ln.zh) > LONG_LINE_CHARS
               for ln in real)
    plural = lambda n: f"{n} line{'s' if n != 1 else ''}"  # noqa: E731
    if over:
        return f"{plural(over)} over {cap} characters could not be cut at a boundary. Try Use AI."
    if slow:
        return (f"No line is over {cap} characters, so there is nothing to cut by length; "
                f"{plural(slow)} run long in time. Use Split long lines, which also cuts by duration.")
    return f"No line is over {cap} characters. Nothing to re-segment."


def nothing_to_split(candidates, cfg, next_hint: str) -> str:
    """Why a Review re-split cut nothing: lines over the limits with no place to
    cut are a different problem from no line being over them. `cfg` is the
    restructure_service._Resplit in use."""
    if cfg.sensitivity == "sentence":
        bare = sum(not segment_splitting._SENTENCE_END_RE.search(ln.zh) for ln in candidates)
        if bare:
            return (f"{bare} line{'s' if bare != 1 else ''} with no sentence end (。！？) to cut "
                    "at. Try Normal or More: they also cut at commas and pauses, then evenly.")
    stuck = 0
    for ln in candidates:
        if cfg.too_long(ln):
            stuck += 1
            continue
        rules = cfg.rules(ln) or segment_splitting.SplitRules(max_chars=segment_splitting.SPLIT_MAX_CJK_CHARS,
                                                 count_latin=False)
        stuck += segment_splitting.exceeds_limits({"start": ln.start, "end": ln.end, "text": ln.zh}, rules)
    if stuck:
        return (f"{stuck} line{'s' if stuck != 1 else ''} over the limits at {cfg.label} "
                "sensitivity, but nothing to cut at: no punctuation and no word pauses.")
    return f"No line is over the limits at {cfg.label} sensitivity. {next_hint}"
