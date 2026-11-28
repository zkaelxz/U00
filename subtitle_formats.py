"""
subtitle_formats.py -- WebVTT and LRC subtitle export, plus the checks
shared by every export format (ASS export is in subtitle_ass.py):

- VTT: same cues as SRT, in WebVTT's header/timestamp format.
- Long lines wrapped at a sentence/clause boundary (or a space), never
  mid-word -- opt-in, so plain SRT export stays exactly what it was.
- Overlapping cues clamped before writing (and the line flagged).
- Reading speed: a translated line with more characters per second than its
  script can comfortably be read at gets flagged.

core.lines_to_srt / lines_to_bilingual_srt are unchanged: SRT output is
byte-identical to before this module existed.
"""

import copy
import hashlib
import html
import math
import re
from typing import Any

import review_thresholds
import segment_splitting
from core import cue_speaker_name, fmt_ts, notes_suffix, sfx_cue_text

# ---------------------------------------------------------------- wrapping

# Characters per subtitle line. CJK is much denser per character, so its
# limit is far tighter; 42 is the common broadcast limit for Latin text.
LINE_CHAR_LIMITS = {"zh": 16, "ja": 16, "ko": 20, "en": 42}
DEFAULT_LINE_CHAR_LIMIT = 42

# Break AFTER one of these when one falls inside the limit.
_SENTENCE_END = "。！？!?…"
_CLAUSE_END = "，、；：,;:"
# Closing punctuation and small kana must stay with the character before them.
NO_LINE_START = frozenset("。、，．！？!?,.;:；：…）)」』】〉》”’ーゃゅょっぁぃぅぇぉゎゕゖャュョッァィゥェォヮヵヶ")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def line_char_limit(language: str) -> int:
    return LINE_CHAR_LIMITS.get(language or "", DEFAULT_LINE_CHAR_LIMIT)


def _is_cjk(text: str) -> bool:
    return bool(text) and len(_CJK.findall(text)) * 2 >= len(text.replace(" ", ""))


def _break_tiers(text: str, language, chinese_script: str):
    """Allowed CJK break offsets by preference (segment.line_break_tiers), or
    None. segment's segmenters are optional, so this module must import and
    wrap without them."""
    if language not in ("ja", "zh"):
        return None
    try:
        import segment
        return segment.line_break_tiers(text, language, chinese_script)
    except ImportError:
        return None


def _cjk_cut(text: str, limit: int, floor: int, language, chinese_script: str) -> int:
    for offsets in _break_tiers(text, language, chinese_script) or ():
        cut = max((o for o in offsets if floor <= o <= limit and text[o] not in NO_LINE_START), default=0)
        if cut:
            return cut
    # No segmenter, or no word end in the window: cut at the limit, but not
    # right before a character that cannot start a line (a lone 。 or small kana).
    cut = limit
    while cut > floor and text[cut] in NO_LINE_START:
        cut -= 1
    return cut


def _break_point(text: str, limit: int, language=None, chinese_script: str = "simplified") -> int:
    """Where to break `text` (index of the first char of the next line),
    or 0 if it can't be broken without splitting a word."""
    window = text[:limit]
    floor = max(1, limit // 3)  # don't leave a stub of 1-2 characters on the first line
    for marks in (_SENTENCE_END, _CLAUSE_END):
        cut = max((i + 1 for i, ch in enumerate(window)
                   if ch in marks and i + 1 >= floor and text[i + 1:i + 2] not in NO_LINE_START),
                  default=0)
        if cut:
            return cut
    space = window.rfind(" ")
    if space >= floor:
        return space + 1
    if _is_cjk(text):
        return _cjk_cut(text, limit, floor, language, chinese_script)
    nxt = text.find(" ", limit)
    return nxt + 1 if nxt != -1 else 0  # one long word: keep it whole


def wrap_text(text: str, max_chars: int | None, language=None, chinese_script: str = "simplified") -> str:
    """Wraps each existing line of `text` onto several lines of at most
    max_chars where it can: at a sentence end, else a clause break, else a
    space -- never inside a word. A single word longer than the limit stays
    whole rather than being cut. language ("ja"/"zh") lets CJK text, which
    has no spaces, break between words when a segmenter is installed."""
    if not max_chars or max_chars <= 0:
        return text
    out = []
    for para in text.split("\n"):
        rest = para
        while len(rest) > max_chars:
            cut = _break_point(rest, max_chars, language, chinese_script)
            if not cut or cut >= len(rest):
                break
            out.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip()
        out.append(rest)
    return "\n".join(out)


# ---------------------------------------------------------- overlap clamp

OVERLAP_FLAG = "timing_overlap"
# Players drop a cue whose end is not after its start, text and all, so a
# line that starts with (or just after) the one before it is moved this far
# along rather than leaving the earlier one no time on screen. A tenth of a
# second is still a distinct LRC stamp (centiseconds).
MIN_CLAMPED_CUE_S = 0.1


def clamp_overlaps(lines):
    """Returns (copies, clamped_idxs): copies of `lines` where any line
    ending after the next one starts is trimmed back to that start, so no
    format ever gets an invalid, overlapping cue. Where that would leave the
    earlier line under MIN_CLAMPED_CUE_S, the next line's start moves
    instead. The originals are left alone; the caller flags clamped_idxs for
    review. Overlaps can appear after a manual timing edit or merge, even
    though alignment itself never produces them."""
    out = [copy.copy(ln) for ln in lines]
    clamped = []
    # Pairs follow time order, not list order: after a manual retime a later
    # list entry can start earlier, and clamping against it would zero a good cue.
    by_time = sorted(out, key=lambda ln: ln.start)
    for cur, nxt in zip(by_time, by_time[1:]):
        if cur.end > nxt.start:
            if nxt.start < cur.start + MIN_CLAMPED_CUE_S:
                nxt.start = cur.start + MIN_CLAMPED_CUE_S
                nxt.end = max(nxt.end, nxt.start + MIN_CLAMPED_CUE_S)
            cur.end = nxt.start
            clamped.append(cur.idx)
    return out, clamped


# ------------------------------------------------------ duration and gap

# Netflix Timed Text Style Guide, subtitle timing: at least 20 frames
# (5/6 s at 24 fps) on screen, 2 frames between cues so a player redraws the
# next cue instead of holding the old one through it, and 7 s at most.
MIN_DURATION_FRAMES = 20
MIN_GAP_FRAMES = 2
MAX_DURATION_S = 7.0
# A cue is lengthened by at most this much: it can only grow into silence,
# and a long stretch of it is more likely speech the aligner missed.
MAX_EXTENSION_S = 1.0
DEFAULT_FPS = 24.0
_TIMING_EPSILON = 0.0005  # below SRT's millisecond resolution


def _frame_rate(fps) -> float:
    return fps if isinstance(fps, (int, float)) and math.isfinite(fps) and fps > 0 else DEFAULT_FPS


def tidy_cue_timing(lines, fps=None):
    """Returns (copies, report) for the opt-in "Tidy cue timing for players"
    SRT export; `lines` are already overlap-clamped. Only cue ends move,
    and only so far as the audio allows:
    - a cue shorter than 20 frames is lengthened toward that, into free
      silence only: never past the next cue's start minus 2 frames, never
      by more than MAX_EXTENSION_S;
    - the earlier of two cues that touch or sit closer than 2 frames loses
      at most 2 frames, to leave that gap;
    - a cue over 7 s is not cut (the speech may really run that long): its
      cue number is reported for the user to review.
    The report is {"extended": n, "trimmed": n, "left_long": [cue numbers]}.
    Originals are untouched; stored line times are never written."""
    fps = _frame_rate(fps)
    min_dur, gap = MIN_DURATION_FRAMES / fps, MIN_GAP_FRAMES / fps
    out = [copy.copy(ln) for ln in lines]
    extended = trimmed = 0
    for i, ln in enumerate(out):
        nxt = out[i + 1] if i + 1 < len(out) else None
        room = nxt.start - gap - ln.end if nxt else math.inf
        grow = min(min_dur - (ln.end - ln.start), room, MAX_EXTENSION_S)
        if grow > _TIMING_EPSILON:
            ln.end += grow
            extended += 1
        elif nxt is not None and nxt.start - ln.end < gap - _TIMING_EPSILON:
            trim = min(gap - (nxt.start - ln.end), gap)
            # A cue clamping already cut to its floor keeps it: a sub-frame cue is dropped by players.
            if ln.end - trim - ln.start >= MIN_CLAMPED_CUE_S - _TIMING_EPSILON:
                ln.end -= trim
                trimmed += 1
    left_long = [i + 1 for i, ln in enumerate(out) if ln.end - ln.start > MAX_DURATION_S]
    return out, {"extended": extended, "trimmed": trimmed, "left_long": left_long}


def overlap_note(ln, next_start: float) -> str:
    return (f"Overlapped the next line by {ln.end - next_start:.2f}s; exports trim its end to "
            f"{next_start:.2f}s (or, when both start within {MIN_CLAMPED_CUE_S:.1f}s, start the next "
            f"line {MIN_CLAMPED_CUE_S:.1f}s after this one). Adjust the timing to fix it for good.")


def lines_for_clip(lines, start: float, end: float):
    """Copies of the lines overlapping [start, end), timeshifted so the
    clip's own timeline starts at 0 -- for burning subtitles onto a video
    that's been trimmed to that same range (the vertical export). A
    line only partially inside the window is clamped to it, not dropped or
    left running past the clip's own end. Renumbers .idx in order; the
    original `lines` are left untouched."""
    out = []
    for ln in lines:
        if ln.end <= start or ln.start >= end:
            continue
        c = copy.copy(ln)
        c.start = max(ln.start, start) - start
        c.end = min(ln.end, end) - start
        c.word_timings = _shift_words(ln, start)
        out.append(c)
    for i, ln in enumerate(out):
        ln.idx = i
    return out


def _shift_words(ln, by: float):
    """The line's stored words moved `by` seconds earlier (word times are
    absolute audio times, so a clip's karaoke needs them on its own timeline)."""
    words = segment_splitting.line_words(ln)
    if not words:
        return None
    moved = [{**w, "start": max(0.0, w["start"] - by), "end": max(0.0, w["end"] - by)} for w in words]
    return segment_splitting.encode_line_words(ln.zh or "", moved)


# ---------------------------------------------------------- reading speed

# Characters-per-second limits for the English subtitle a viewer reads: 17 is
# Netflix's comfortable value. They are limits for reading, not for how fast
# people talk, so fast speech legitimately trips them (see the modes below).
# The text is judged by its script, so CJK in the English field (an
# untranslated line) gets the stricter value. Hebrew/Arabic/Indic/Thai have no
# Netflix value; they keep the ratio to Latin they had before (7.0 against 8.5).
SCRIPT_CPS = {"cjk": 9.0, "abugida_rtl": 14.0, "latin": 17.0}
DEFAULT_CPS_LIMIT = 15.0
READING_SPEED_FLAG = "reading_speed"

# Per-title choice for people who work on fast talkers, where lengthening a
# cue past the audio is not an option and the default limits flag most lines.
# "relaxed" is Netflix's English ceiling of 20 against its comfortable 17,
# applied as the same ratio to every language.
READING_SPEED_MODES = ("normal", "relaxed", "off")
DEFAULT_READING_SPEED_MODE = "normal"
RELAXED_FACTOR = 20 / 17
# Streamer speech runs near 17 characters/second in English, and cues can't
# be lengthened past the audio. Flags only the extremes: the relaxed limits
# raised by another 20%.
STREAMER_MODE = "streamer"
STREAMER_FACTOR = 1.2

# A cue shorter than this is never flagged: a few characters are read at a
# glance whatever the ratio says, and such a cue often cannot be lengthened
# (it sits between two other lines).
MIN_DENSE_DURATION_S = 1.5

_SCRIPT_PATTERNS = {
    "cjk": _CJK,
    "abugida_rtl": re.compile(r"[\u0590-\u06ff\u0900-\u0dff\u0e00-\u0e7f]"),  # Hebrew/Arabic/Indic/Thai
    "latin": re.compile(r"[A-Za-z\u00c0-\u024f\u0400-\u04ff]"),
}


def script_of(text: str) -> str:
    counts = {name: len(p.findall(text or "")) for name, p in _SCRIPT_PATTERNS.items()}
    best = max(counts, key=lambda name: counts[name])
    return best if counts[best] else ""


def cps_limit(key: str, mode: str = DEFAULT_READING_SPEED_MODE) -> float:
    """The limit for a script name at `mode`. An unknown key gets
    DEFAULT_CPS_LIMIT."""
    base = SCRIPT_CPS.get(key) or DEFAULT_CPS_LIMIT
    factor = {"relaxed": RELAXED_FACTOR, STREAMER_MODE: RELAXED_FACTOR * STREAMER_FACTOR}.get(mode, 1.0)
    return round(base * factor, 2)


def reading_speed(text: str, duration: float) -> float:
    return len((text or "").strip()) / max(duration, 0.01)


def reading_speed_mode_of(drama) -> str:
    """The title's saved mode; a missing or unknown value is the default."""
    mode = (drama or {}).get("reading_speed_mode")
    return mode if mode in READING_SPEED_MODES else DEFAULT_READING_SPEED_MODE


def flagging_mode_of(drama) -> str:
    """The mode the checks run at. A streamer title saved at "normal" (the
    column's default, so it can't be told from a deliberate choice) runs at
    STREAMER_MODE; an explicit Relaxed or Off is honoured as saved. Not
    offered in READING_SPEED_MODES: it is never stored or sent by the API."""
    mode = reading_speed_mode_of(drama)
    if mode == "normal" and review_thresholds.profile_of(drama) == review_thresholds.PROFILE_STREAMER:
        return STREAMER_MODE
    return mode


def dense_lines(lines, field: str = "en", mode: str = DEFAULT_READING_SPEED_MODE) -> list:
    """[(line, chars_per_second, limit)] for every line whose `field` text
    is too dense to read in the time it's on screen; the text's script picks
    the limit. Cues under MIN_DENSE_DURATION_S are skipped; mode "off" finds
    nothing."""
    if mode == "off":
        return []
    out = []
    for ln in lines:
        text = getattr(ln, field, "") or ""
        stripped = text.strip()
        if not stripped:
            continue
        duration = ln.end - ln.start
        script = script_of(text)
        if duration < MIN_DENSE_DURATION_S:
            continue
        limit = cps_limit(script, mode)
        cps = reading_speed(text, duration)
        if cps > limit:
            out.append((ln, cps, limit))
    return out


def _dense_note(ln, field: str, cps: float, limit: float, nxt, gap: float) -> str:
    """What is true about the flag: whether the next cue leaves enough room
    after this one to show it longer. A speech-rate flag is never a timing
    fix, so with no room the note offers none."""
    head = f"{cps:.1f} characters/second -- more than ~{limit:g}/s is hard to read in time. "
    needed = len((getattr(ln, field, "") or "").strip()) / limit - (ln.end - ln.start) if limit else 0
    room = nxt.start - gap - ln.end if nxt is not None else math.inf
    # Beyond MAX_EXTENSION_S the extra time is more likely speech than silence.
    if 0 < needed <= min(room, MAX_EXTENSION_S):
        return head + (f"The next line leaves room: it could be shown {math.ceil(needed * 10) / 10:.1f} s "
                       "longer, or shorten the text.")
    return head + "The speech is this fast: shorten the text."


def text_hash(text) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


def dismissal_key(ln) -> tuple:
    """What a dismissed reading-speed flag is remembered by: the English it
    was dismissed for, so an edit or re-translation can be flagged again."""
    return (getattr(ln, "id", None), text_hash(getattr(ln, "en", "")))


def flag_dense_lines(lines, field: str = "en", mode: str = DEFAULT_READING_SPEED_MODE,
                     fps=None, dismissed=None) -> int:
    """Flags (in place) lines too dense to read, without replacing a flag
    that's already there. Returns how many were newly flagged. Only the flag
    and its note are written: line times are never changed. `lines` are in
    time order, so the next one bounds the free time after a cue.
    `dismissed` is {(line id, text hash)} from
    reading_speed_dismissal: those lines stay unflagged while their
    English is the text that was dismissed."""
    gap = MIN_GAP_FRAMES / _frame_rate(fps)

    following = {id(a): b for a, b in zip(lines, lines[1:])}
    n = 0
    for ln, cps, limit in dense_lines(lines, field, mode):
        if dismissed and dismissal_key(ln) in dismissed:
            continue
        if not ln.flag:
            ln.flag = READING_SPEED_FLAG
            ln.flag_note = _dense_note(ln, field, cps, limit, following.get(id(ln)), gap)
            n += 1
    return n


# ------------------------------------------------------------- SRT files


def srt_file_text(text: str) -> str:
    """SRT text as saved to a file someone plays: led by a UTF-8 BOM when it
    has any non-ASCII character. SRT has no way to name its encoding, and
    some TVs and set-top boxes read a file without a BOM as their local
    code page, which garbles Chinese, Japanese and Korean. An all-ASCII
    file reads the same in every encoding, so it stays byte-identical."""
    return "\ufeff" + text if text and not text.isascii() and not text.startswith("\ufeff") else text


# ---------------------------------------------------------------- WebVTT

def _vtt_ts(seconds: float) -> str:
    # WebVTT differs from SRT only in the millisecond separator.
    return fmt_ts(seconds).replace(",", ".")


def _vtt_escape(text: str) -> str:
    """WebVTT cue text is markup: a bare "<" starts a tag (so "I <3 you"
    would be cut at "<") and "&" starts an entity. Escaping ">" as well
    means no "-->" (however many dashes) can end a cue early."""
    return html.escape(text, quote=False) if text else text


def _cue_text(ln, field, notes_by_idx, wrap_chars, italic_tags=True, escape=False,
              language=None, chinese_script="simplified"):
    """italic_tags: whether an SFX cue gets <i> tags (VTT) or is left for
    the caller to style (ASS's own "SFX" style). escape: entity-escape the
    line's own text for WebVTT, before any <i> tags are added (SRT and ASS
    are not markup of this kind, so they get the text as it is). language
    is the source language: only the "zh" (source) text is broken with it."""
    esc = _vtt_escape if escape else (lambda s: s)
    sfx = getattr(ln, "sfx", False)
    if field == "bilingual":
        en = esc(wrap_text(ln.en, wrap_chars.get("en")) if wrap_chars else ln.en)
        zh = esc(wrap_text(ln.zh, wrap_chars.get("zh"), language, chinese_script) if wrap_chars else ln.zh)
        if sfx:
            en, zh = sfx_cue_text(en, italic_tags), sfx_cue_text(zh, italic_tags)
        text = f"{en}\n{zh}" if en else zh
    else:
        text = getattr(ln, field)
        if wrap_chars:
            text = wrap_text(text, wrap_chars.get(field),
                             language if field == "zh" else None, chinese_script)
        text = esc(text)
        if sfx:
            text = sfx_cue_text(text, italic_tags)
    return text + esc(notes_suffix(ln.idx, notes_by_idx))


def lines_to_vtt(lines, field: str = "en", notes_by_idx: dict | None = None,
                 wrap_chars: dict | None = None,
                 language=None, chinese_script: str = "simplified",
                 speaker_names: dict | None = None) -> str:
    """field: "en", "zh" or "bilingual". wrap_chars: optional
    {"en": n, "zh": n} per-line character limits (see wrap_text).
    speaker_names: see core.cue_speaker_name; a named cue is wrapped in a
    <v Name> voice tag."""
    cues = ["WEBVTT\n"]
    for i, ln in enumerate(lines, start=1):
        text = _cue_text(ln, field, notes_by_idx, wrap_chars, escape=True,
                         language=language, chinese_script=chinese_script)
        # A blank line ends a cue, so the rest of the text would be lost:
        # keep only the lines that have something on them.
        text = "\n".join(part for part in re.split(r"\r\n|\r|\n", text) if part.strip())
        name = cue_speaker_name(ln, speaker_names)
        if name and text:
            text = f"<v {_vtt_escape(name)}>{text}</v>"
        cues.append(f"{i}\n{_vtt_ts(ln.start)} --> {_vtt_ts(ln.end)}\n{text}\n")
    return "\n".join(cues)


# ------------------------------------------------------------------- LRC

def _lrc_ts(seconds: float) -> str:
    # LRC stamps are centiseconds; rounding a whole minute up must carry.
    cs = int(round(max(seconds, 0) * 100))
    m, cs = divmod(cs, 6000)
    return f"[{m:02d}:{cs // 100:02d}.{cs % 100:02d}]"


def lines_to_lrc(lines, field: str = "en", notes_by_idx: dict | None = None,
                 wrap_chars: dict | None = None,
                 language=None, chinese_script: str = "simplified",
                 speaker_names: dict | None = None) -> str:
    """Lyrics-style timed text: one stamp per cue, where a cue starts. A cue
    that ends before the next one starts also gets an empty stamp at its end,
    so a player doesn't keep showing it through the gap (LRC has no end
    times). Multi-line cue text is joined on one line with a space, since a
    line break would start a new, unstamped line. field: "en", "zh" or
    "bilingual" (both texts under one stamp, each on its own stamped line)."""
    cue: dict[str, Any]
    cue = dict(wrap_chars=wrap_chars, italic_tags=False, language=language,
               chinese_script=chinese_script)
    shown = []
    for ln in lines:
        # Each language is flattened on its own, so a two-line translation
        # still leaves exactly one source row under the stamp.
        texts = ([_cue_text(ln, "en", notes_by_idx, **cue), _cue_text(ln, "zh", None, **cue)]
                 if field == "bilingual" else [_cue_text(ln, field, notes_by_idx, **cue)])
        parts = [p for p in (" ".join(t.split()) for t in texts) if p]
        name = cue_speaker_name(ln, speaker_names)
        if name:
            parts = [f"{name}: {p}" for p in parts]
        if parts:
            shown.append((ln, parts))
    out = []
    for i, (ln, parts) in enumerate(shown):
        stamp = _lrc_ts(ln.start)
        out.extend(f"{stamp}{p}" for p in parts)
        # Measured against the next cue written, not the next line: a line
        # with no text in this field writes no stamp, so it cannot end this one.
        nxt = shown[i + 1][0].start if i + 1 < len(shown) else None
        if nxt is None or ln.end < nxt:
            out.append(_lrc_ts(ln.end))
    return "\n".join(out) + ("\n" if out else "")


def wrap_lines(lines, wrap_chars: dict | None, language=None, chinese_script: str = "simplified"):
    """Copies of `lines` with en/zh wrapped (for SRT, whose writer stays
    untouched). wrap_chars None -> the lines unchanged."""
    if not wrap_chars:
        return lines
    out = []
    for ln in lines:
        c = copy.copy(ln)
        c.en = wrap_text(ln.en, wrap_chars.get("en"))
        c.zh = wrap_text(ln.zh, wrap_chars.get("zh"), language, chinese_script)
        out.append(c)
    return out
