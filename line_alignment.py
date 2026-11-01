"""
line_alignment.py -- turns a user transcript plus Whisper timing into timed
lines: merging short lines, character-time reconstruction and coverage checks.
"""

import difflib
import re

from core import Line
from novel_text import split_sentences
from segment_splitting import encode_line_words, line_words


# A line opening with one of these marks a change of speaker in a transcript
# that carries no speaker labels.
_TURN_MARKS = ("-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\uff0d")


_SENTENCE_PUNCT = ".,!?;:\u3002\uff0e\uff0c\uff01\uff1f\u2026~"


def _is_hangul(ch):
    return "\uac00" <= ch <= "\ud7a3" or "\u3131" <= ch <= "\u318e"


def _join_source(first: str, second: str) -> str:
    """Source text of two merged lines. Korean separates words with spaces,
    so a join between two Hangul characters keeps one; Chinese and Japanese
    are written without."""
    first, second = first.rstrip(), second.strip()
    # Sentence punctuation after the last word ("안녕.") doesn't make the join
    # any less a join between two Korean words.
    last_word_char = first.rstrip(_SENTENCE_PUNCT)[-1:]
    if last_word_char and second and _is_hangul(last_word_char) and _is_hangul(second[0]):
        return first + " " + second
    return first + second


_CJK_EDGE = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]")


def join_segment_texts(texts) -> str:
    """Segment texts of one line joined the way a full transcription writes them:
    with a space between Latin or Korean words, and none next to a Chinese or
    Japanese character or punctuation mark."""
    out = ""
    for text in (t.strip() for t in texts):
        if not text:
            continue
        if out and not (_CJK_EDGE.match(out[-1]) or _CJK_EDGE.match(text[0])):
            out += " "
        out += text
    return out


def _merged_words(first, second):
    """Both lines' stored words joined, or None unless each line has valid words
    and they run in time order."""
    words, more = line_words(first), line_words(second)
    if words and more and more[0]["start"] >= words[-1]["start"]:
        return words + more
    return None


def merge_adjacent_short_lines(lines, min_duration: float = 1.2, max_gap: float = 0.5, max_chars: int = 80):
    """
    Combines consecutive short subtitle lines from the same speaker
    into a single natural subtitle, when they're close enough in time
    that splitting them was probably just an artifact of the source
    transcript's line breaks rather than a real pause. This is a real
    merge (produces a new, shorter list), unlike smart_segment_lines()
    in engine_backends which only flags pacing issues without
    changing anything.

    A pair merges when: same speaker (or both unlabeled), the second line
    doesn't open with a speaker-change dash, the gap between them is under
    max_gap seconds, and the combined line wouldn't exceed max_chars. Only
    lines under min_duration are considered candidates for merging -- a line
    that's already a comfortable length is left alone.

    The first line keeps its id. Its stored word timings are carried over
    only when both lines have valid ones, so they always spell its text.
    """
    if not lines:
        return lines

    merged = [lines[0]]
    for ln in lines[1:]:
        prev = merged[-1]
        prev_duration = prev.end - prev.start
        gap = ln.start - prev.end
        same_speaker = ((prev.speaker or None) == (ln.speaker or None)
                        # A hand-set speaker and a sound cue are decisions the merge
                        # would erase (sfx is exported apart from speech).
                        and bool(prev.speaker_manual) == bool(ln.speaker_manual)
                        and not (prev.sfx or ln.sfx)
                        and not ln.zh.lstrip().startswith(_TURN_MARKS))
        zh = _join_source(prev.zh, ln.zh)
        combined_en_len = len(prev.en) + len(ln.en) + 1  # +1 for the joining space
        combined_duration = ln.end - prev.start

        # Both the already-short prev AND the merged result must stay short --
        # otherwise a short line followed by a naturally long one would keep
        # getting absorbed just because prev alone was under the threshold.
        if (prev_duration < min_duration and same_speaker and gap <= max_gap
                and len(zh) <= max_chars and combined_en_len <= max_chars
                and combined_duration < min_duration * 2.5):
            words = _merged_words(prev, ln)
            prev.zh = zh
            prev.en = (prev.en.rstrip() + " " + ln.en.strip()).strip()
            prev.end = ln.end
            prev.word_timings = encode_line_words(zh, words) if words else None
            if not prev.flag and ln.flag:
                prev.flag, prev.flag_note = ln.flag, ln.flag_note
            if getattr(prev, "lang", None) != getattr(ln, "lang", None):
                prev.lang = None
            if getattr(ln, "id", None) is not None:
                prev.merged_ids = list(prev.merged_ids) + [ln.id] + list(ln.merged_ids)
        else:
            merged.append(ln)

    for i, ln in enumerate(merged):
        ln.idx = i
    return merged


def split_user_transcript(raw_text: str):
    raw_text = raw_text.strip()
    manual_lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    if len(manual_lines) > 1:
        return manual_lines
    # CJK full-width punctuation covers zh/ja; Korean text often also
    # uses standard Latin punctuation, so that's included as a fallback.
    parts = split_sentences(raw_text, latin_end_needs_space=False)
    return [p.strip() for p in parts if p.strip()]


def lines_from_char_times(user_lines, per_line_times, total_audio_end):
    """Shared reconstruction step: given, for each line index, whichever
    character timestamps could be attributed to it, produce ordered,
    non-overlapping Line objects -- interpolating from neighboring known
    lines wherever a line matched nothing.

    Factored out of align_transcript_to_timing() so forced_align.py's
    Qwen3-ForcedAligner path can reuse the same interpolation/ordering
    logic against a different (non-diffed, directly-matched) source of
    per-line timestamps, rather than duplicating it.
    """
    raw_bounds = {}
    for li in range(len(user_lines)):
        t = per_line_times.get(li, [])
        raw_bounds[li] = (min(t), max(t)) if t else None
    known_idxs = [li for li, b in raw_bounds.items() if b is not None]

    # Unmatched lines in a run share the gap between their matched neighbours
    # in proportion to their text; giving each the whole gap made the overlap
    # pass below push every later correctly-timed line later.
    spans = {}
    li = 0
    while li < len(user_lines):
        if raw_bounds[li] is not None:
            li += 1
            continue
        run_end = li
        while run_end < len(user_lines) and raw_bounds[run_end] is None:
            run_end += 1
        prev_known = max([k for k in known_idxs if k < li], default=None)
        next_known = min([k for k in known_idxs if k > li], default=None)
        gap_start = raw_bounds[prev_known][1] if prev_known is not None else 0.0
        gap_end = raw_bounds[next_known][0] if next_known is not None else total_audio_end
        weights = [max(len(re.sub(r"\s+", "", user_lines[k])), 1) for k in range(li, run_end)]
        cursor = gap_start
        for k, w in zip(range(li, run_end), weights):
            width = max(gap_end - gap_start, 0.0) * w / sum(weights)
            spans[k] = (cursor, cursor + width)
            cursor += width
        li = run_end

    lines_out = []
    for li in range(len(user_lines)):
        start, end = raw_bounds[li] if raw_bounds[li] is not None else spans[li]
        lines_out.append(Line(idx=li, start=start, end=max(end, start + 0.5), zh=user_lines[li]))

    for i in range(1, len(lines_out)):
        cur, prev = lines_out[i], lines_out[i - 1]
        if cur.start < prev.end:
            # A matched line's time came from the audio, so the earlier line
            # gives way when it can; otherwise this line moves after it.
            if raw_bounds[i] is not None and cur.start - prev.start >= 0.1:
                prev.end = cur.start
            else:
                cur.start = prev.end + 0.05
        if cur.end <= cur.start:
            cur.end = cur.start + 1.0
    return lines_out


def align_transcript_to_timing(user_lines, whisper_segments):
    w_chars, w_times = [], []
    for seg in whisper_segments:
        text = re.sub(r"\s+", "", seg["text"])
        n = max(len(text), 1)
        dur = max(seg["end"] - seg["start"], 0.01)
        for i, ch in enumerate(text):
            w_chars.append(ch)
            w_times.append((seg["start"] + dur * (i / n), seg["start"] + dur * ((i + 1) / n)))
    w_stream = "".join(w_chars)

    u_chars, u_line_of_char = [], []
    for li, line in enumerate(user_lines):
        for ch in re.sub(r"\s+", "", line):
            u_chars.append(ch)
            u_line_of_char.append(li)
    u_stream = "".join(u_chars)

    sm = difflib.SequenceMatcher(a=w_stream, b=u_stream, autojunk=False)
    per_line_times = {li: [] for li in range(len(user_lines))}
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            li = u_line_of_char[block.b + k]
            # Both ends of the character's slot: a line then ends where its last
            # character ends, not where that character starts.
            per_line_times[li].extend(w_times[block.a + k])

    total_audio_end = whisper_segments[-1]["end"] if whisper_segments else 0.0
    return lines_from_char_times(user_lines, per_line_times, total_audio_end)


# ---------------------------------------------------------------------------
# Coverage & timing diagnostics -- for after alignment, to find lines that
# probably need attention before spending money translating them
# ---------------------------------------------------------------------------

def diagnose_line_coverage(lines, long_duration_seconds: float = 12.0,
                            gap_seconds: float = 3.0, chars_per_second: float = 4.5):
    """
    Scans aligned lines for the two patterns that most often mean real
    dialogue got missed or mangled, without needing to listen to the
    whole file to find them:

      - long_lines: a single line spanning an unusually long duration
        relative to its text length. Almost always means the voice
        activity detector merged multiple real lines of dialogue into
        one segment (see min_silence_duration_ms), so only the first
        sentence ended up as the line's text and everything spoken
        after it in that span has no subtitle at all.

      - large_gaps: a silent stretch between two consecutive lines
        longer than expected. Sometimes real silence; sometimes quiet
        dialogue (internal monologue, whispers) that the VAD didn't
        detect as speech at all.

    chars_per_second: a rough speaking-rate baseline (~4.5 chars/sec is
    reasonable for spoken Mandarin) used only to flag SEVERE outliers,
    not to judge normal pacing variation.

    Returns {"long_lines": [...], "large_gaps": [...], "blank_zh": [...],
    "blank_en": [...]} -- each entry has enough info to jump straight to
    the line in the review table.
    """
    long_lines, large_gaps, blank_zh, blank_en = [], [], [], []

    for ln in lines:
        duration = ln.end - ln.start
        char_count = len(ln.zh.strip())
        if not ln.zh.strip():
            blank_zh.append({"idx": ln.idx, "start": ln.start, "end": ln.end})
            continue
        if ln.zh.strip() and not ln.en.strip():
            blank_en.append({"idx": ln.idx, "zh": ln.zh, "start": ln.start})

        expected_duration = char_count / chars_per_second
        if duration > long_duration_seconds and duration > expected_duration * 2.5:
            long_lines.append({
                "idx": ln.idx, "start": ln.start, "end": ln.end, "duration": duration,
                "zh": ln.zh, "char_count": char_count,
                "note": (f"{duration:.1f}s for {char_count} character(s) -- "
                        f"plausibly several merged lines, not one"),
            })

    sorted_lines = sorted(lines, key=lambda l: l.start)
    for prev, cur in zip(sorted_lines, sorted_lines[1:]):
        gap = cur.start - prev.end
        if gap > gap_seconds:
            large_gaps.append({
                "after_idx": prev.idx, "before_idx": cur.idx,
                "gap_start": prev.end, "gap_end": cur.start, "gap_seconds": gap,
            })

    long_lines.sort(key=lambda x: -x["duration"])
    large_gaps.sort(key=lambda x: -x["gap_seconds"])
    return {"long_lines": long_lines, "large_gaps": large_gaps,
            "blank_zh": blank_zh, "blank_en": blank_en}
