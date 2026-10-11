"""
subtitle_formats.py -- WebVTT and ASS subtitle export, plus the checks
shared by every export format:

- VTT: same cues as SRT, in WebVTT's header/timestamp format.
- ASS: real styling -- font, size, bold/italic, fill and outline colour,
  outline width, alignment, and one colour per speaker -- the format that
  makes styled "clip streamer"-look subtitles possible. SRT/VTT can't carry
  any of that.
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

import review_thresholds
from core import notes_suffix, sfx_cue_text

# ---------------------------------------------------------------- wrapping

# Characters per subtitle line. CJK is much denser per character, so its
# limit is far tighter; 42 is the common broadcast limit for Latin text.
LINE_CHAR_LIMITS = {"zh": 16, "ja": 16, "ko": 20, "en": 42}
DEFAULT_LINE_CHAR_LIMIT = 42

# Break AFTER one of these when one falls inside the limit.
_SENTENCE_END = "。！？!?…"
_CLAUSE_END = "，、；：,;:"
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def line_char_limit(language: str) -> int:
    return LINE_CHAR_LIMITS.get(language or "", DEFAULT_LINE_CHAR_LIMIT)


def _is_cjk(text: str) -> bool:
    return bool(text) and len(_CJK.findall(text)) * 2 >= len(text.replace(" ", ""))


def _break_point(text: str, limit: int) -> int:
    """Where to break `text` (index of the first char of the next line),
    or 0 if it can't be broken without splitting a word."""
    window = text[:limit]
    floor = max(1, limit // 3)  # don't leave a stub of 1-2 characters on the first line
    for marks in (_SENTENCE_END, _CLAUSE_END):
        cut = max((i + 1 for i, ch in enumerate(window) if ch in marks and i + 1 >= floor), default=0)
        if cut:
            return cut
    space = window.rfind(" ")
    if space >= floor:
        return space + 1
    if _is_cjk(text):
        return limit  # CJK has no spaces: any character boundary is a word boundary
    nxt = text.find(" ", limit)
    return nxt + 1 if nxt != -1 else 0  # one long word: keep it whole


def wrap_text(text: str, max_chars: int) -> str:
    """Wraps each existing line of `text` onto several lines of at most
    max_chars where it can: at a sentence end, else a clause break, else a
    space -- never inside a word. A single word longer than the limit stays
    whole rather than being cut."""
    if not max_chars or max_chars <= 0:
        return text
    out = []
    for para in text.split("\n"):
        rest = para
        while len(rest) > max_chars:
            cut = _break_point(rest, max_chars)
            if not cut or cut >= len(rest):
                break
            out.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip()
        out.append(rest)
    return "\n".join(out)


# ---------------------------------------------------------- overlap clamp

OVERLAP_FLAG = "timing_overlap"


def clamp_overlaps(lines):
    """Returns (copies, clamped_idxs): copies of `lines` where any line
    ending after the next one starts is trimmed back to that start, so no
    format ever gets an invalid, overlapping cue. The originals are left
    alone; the caller flags clamped_idxs for review. Overlaps can appear
    after a manual timing edit or merge, even though alignment itself
    never produces them."""
    out = [copy.copy(ln) for ln in lines]
    clamped = []
    for cur, nxt in zip(out, out[1:]):
        if cur.end > nxt.start:
            cur.end = max(nxt.start, cur.start)
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
            if ln.end - trim > ln.start:
                ln.end -= trim
                trimmed += 1
    left_long = [i + 1 for i, ln in enumerate(out) if ln.end - ln.start > MAX_DURATION_S]
    return out, {"extended": extended, "trimmed": trimmed, "left_long": left_long}


def overlap_note(ln, next_start: float) -> str:
    return (f"Overlapped the next line by {ln.end - next_start:.2f}s; exports trim its end to "
            f"{next_start:.2f}s. Adjust the timing to fix it for good.")


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
        out.append(c)
    for i, ln in enumerate(out):
        ln.idx = i
    return out


# ---------------------------------------------------------- reading speed

# Characters-per-second limits, from the Netflix Timed Text Style Guides
# (one page per language on partnerhelp.netflixstudios.com): English 17 for
# comfortable reading, Chinese 9, Japanese 4 and Korean 12. They are limits
# for the viewer's reading, not for how fast people talk, so fast speech
# legitimately trips them (see the modes below).
CPS_BY_LANGUAGE = {"en": 17.0, "zh": 9.0, "ja": 4.0, "ko": 12.0}
# Text whose language isn't known is judged by its script. Latin and CJK use
# the English and Chinese values. Hebrew/Arabic/Indic/Thai have no Netflix
# value; they keep the ratio to Latin they had before (7.0 against 8.5).
SCRIPT_CPS = {"cjk": CPS_BY_LANGUAGE["zh"], "abugida_rtl": 14.0, "latin": CPS_BY_LANGUAGE["en"]}
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

# A cue shorter than this, or with less text than MIN_DENSE_CHARS, is never
# flagged: a few characters are read at a glance whatever the ratio says, and
# such a cue often cannot be lengthened (it sits between two other lines).
# At the English, Chinese and Korean limits 1.5 s already holds more than
# 11 characters before it can be dense; the character floor is what spares
# short Japanese cues (4/s would flag 7 characters in 1.5 s).
MIN_DENSE_DURATION_S = 1.5
MIN_DENSE_CHARS = 11

_SCRIPT_PATTERNS = {
    "cjk": _CJK,
    "abugida_rtl": re.compile(r"[\u0590-\u06ff\u0900-\u0dff\u0e00-\u0e7f]"),  # Hebrew/Arabic/Indic/Thai
    "latin": re.compile(r"[A-Za-z\u00c0-\u024f\u0400-\u04ff]"),
}


def script_of(text: str) -> str:
    counts = {name: len(p.findall(text or "")) for name, p in _SCRIPT_PATTERNS.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] else ""


def cps_limit(key: str, mode: str = DEFAULT_READING_SPEED_MODE) -> float:
    """The limit for a language code or script name at `mode`. An unknown
    key gets DEFAULT_CPS_LIMIT."""
    base = CPS_BY_LANGUAGE.get(key) or SCRIPT_CPS.get(key) or DEFAULT_CPS_LIMIT
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


def dense_lines(lines, field: str = "en", mode: str = DEFAULT_READING_SPEED_MODE,
                language: str = None) -> list:
    """[(line, chars_per_second, limit)] for every line whose `field` text
    is too dense to read in the time it's on screen. `language` (en, zh, ja
    or ko) picks that language's limit; without it the text's script does.
    Cues under the MIN_DENSE_* floor are skipped; mode "off" finds nothing."""
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
        if duration < MIN_DENSE_DURATION_S or len(stripped) < MIN_DENSE_CHARS:
            continue
        limit = cps_limit(language if language in CPS_BY_LANGUAGE else script, mode)
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
    reading_speed_dismissal_service: those lines stay unflagged while their
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


# ---------------------------------------------------------------- WebVTT

def _vtt_ts(seconds: float) -> str:
    ms = int(round(max(seconds, 0) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _vtt_escape(text: str) -> str:
    """WebVTT cue text is markup: a bare "<" starts a tag (so "I <3 you"
    would be cut at "<") and "&" starts an entity. Escaping ">" as well
    means no "-->" (however many dashes) can end a cue early."""
    return html.escape(text, quote=False) if text else text


def _cue_text(ln, field, notes_by_idx, wrap_chars, italic_tags=True, escape=False):
    """italic_tags: whether an SFX cue gets <i> tags (VTT) or is left for
    the caller to style (ASS's own "SFX" style). escape: entity-escape the
    line's own text for WebVTT, before any <i> tags are added (SRT and ASS
    are not markup of this kind, so they get the text as it is)."""
    esc = _vtt_escape if escape else (lambda s: s)
    sfx = getattr(ln, "sfx", False)
    if field == "bilingual":
        en = esc(wrap_text(ln.en, wrap_chars.get("en")) if wrap_chars else ln.en)
        zh = esc(wrap_text(ln.zh, wrap_chars.get("zh")) if wrap_chars else ln.zh)
        if sfx:
            en, zh = sfx_cue_text(en, italic_tags), sfx_cue_text(zh, italic_tags)
        text = f"{en}\n{zh}" if en else zh
    else:
        text = getattr(ln, field)
        if wrap_chars:
            text = wrap_text(text, wrap_chars.get(field))
        text = esc(text)
        if sfx:
            text = sfx_cue_text(text, italic_tags)
    return text + esc(notes_suffix(ln.idx, notes_by_idx))


def lines_to_vtt(lines, field: str = "en", notes_by_idx: dict = None, wrap_chars: dict = None) -> str:
    """field: "en", "zh" or "bilingual". wrap_chars: optional
    {"en": n, "zh": n} per-line character limits (see wrap_text)."""
    cues = ["WEBVTT\n"]
    for i, ln in enumerate(lines, start=1):
        text = _cue_text(ln, field, notes_by_idx, wrap_chars, escape=True)
        # A blank line ends a cue, so the rest of the text would be lost:
        # keep only the lines that have something on them.
        text = "\n".join(part for part in re.split(r"\r\n|\r|\n", text) if part.strip())
        cues.append(f"{i}\n{_vtt_ts(ln.start)} --> {_vtt_ts(ln.end)}\n{text}\n")
    return "\n".join(cues)


# ------------------------------------------------------------------- LRC

def _lrc_ts(seconds: float) -> str:
    # LRC stamps are centiseconds; rounding a whole minute up must carry.
    cs = int(round(max(seconds, 0) * 100))
    m, cs = divmod(cs, 6000)
    return f"[{m:02d}:{cs // 100:02d}.{cs % 100:02d}]"


def lines_to_lrc(lines, field: str = "en", notes_by_idx: dict = None, wrap_chars: dict = None) -> str:
    """Lyrics-style timed text: one stamp per cue, where a cue starts. A cue
    that ends before the next one starts also gets an empty stamp at its end,
    so a player doesn't keep showing it through the gap (LRC has no end
    times). Multi-line cue text is joined on one line with a space, since a
    line break would start a new, unstamped line. field: "en", "zh" or
    "bilingual" (both texts under one stamp, each on its own stamped line)."""
    out = []
    for i, ln in enumerate(lines):
        # Each language is flattened on its own, so a two-line translation
        # still leaves exactly one source row under the stamp.
        texts = ([_cue_text(ln, "en", notes_by_idx, wrap_chars, italic_tags=False),
                  _cue_text(ln, "zh", None, wrap_chars, italic_tags=False)]
                 if field == "bilingual" else [_cue_text(ln, field, notes_by_idx, wrap_chars, italic_tags=False)])
        parts = [" ".join(t.split()) for t in texts]
        parts = [p for p in parts if p]
        if not parts:
            continue
        stamp = _lrc_ts(ln.start)
        out.extend(f"{stamp}{p}" for p in parts)
        nxt = lines[i + 1].start if i + 1 < len(lines) else None
        if nxt is None or ln.end < nxt:
            out.append(_lrc_ts(ln.end))
    return "\n".join(out) + ("\n" if out else "")


# ------------------------------------------------------------------- ASS

FONT_CHOICES = ["Arial", "Arial Black", "Segoe UI", "Microsoft YaHei", "Noto Sans CJK SC",
                "Meiryo", "Malgun Gothic"]

ALIGNMENTS = {  # ASS numpad-style \an codes
    "bottom-center": 2, "bottom-left": 1, "bottom-right": 3,
    "top-center": 8, "top-left": 7, "top-right": 9,
}

# Sizes are on libass's default 288-line canvas (what it uses for SRT), so
# the same numbers mean the same thing in an .ass file and in an SRT
# burn-in's force_style. "Clean" is exactly the hardsub look the app used
# before styling existed.
PLAY_RES = (384, 288)
ASS_PRESETS = {
    "Clean": {"font": "Arial", "size": 24, "bold": False, "italic": False,
              "primary": "#FFFFFF", "outline": "#000000", "outline_width": 2, "shadow": 1,
              "alignment": "bottom-center"},
    # Bold, bigger and a thick high-contrast outline: the common shape of
    # clip-channel subtitles, legible over busy video. A starting point to
    # tune, not a copy of any one channel's look.
    "Streamer clip": {"font": "Arial Black", "size": 30, "bold": True, "italic": False,
                      "primary": "#FFFFFF", "outline": "#000000", "outline_width": 4, "shadow": 1,
                      "alignment": "bottom-center"},
}

# SFX/non-verbal cues get their own ASS style -- the export's
# style in italics, in a muted colour, so "[door slams]" never reads as a
# line someone said.
SFX_STYLE_NAME = "SFX"
SFX_COLOR = "#B8C4CE"
# style["sfx_alignment"] / style["notes_alignment"] (keys of ALIGNMENTS)
# place SFX cues and separate-line notes somewhere other than the dialogue
# -- e.g. at the top while speech stays at the bottom. Unset (None) keeps
# them at the dialogue's own position.

SPEAKER_PALETTE = ["#FFFFFF", "#FFE066", "#7FDBFF", "#FF9FF3", "#9BE564", "#FFB347", "#C7A8FF",
                   "#FF6B6B"]


def default_speaker_colors(speakers) -> dict:
    """One distinct colour per speaker label, in a stable order."""
    return {sp: SPEAKER_PALETTE[i % len(SPEAKER_PALETTE)]
            for i, sp in enumerate(sorted(s for s in set(speakers) if s))}


def ass_color(hex_rgb: str) -> str:
    """#RRGGBB -> ASS &H00BBGGRR (ASS is BGR, with a leading alpha byte)."""
    h = (hex_rgb or "#FFFFFF").lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", h):
        h = "FFFFFF"
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ass_ts(seconds: float) -> str:
    cs = int(round(max(seconds, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _style_line(name: str, style: dict, primary: str) -> str:
    return ("Style: " + ",".join([
        _ass_field(name), _ass_field(style["font"]), str(int(style["size"])),
        ass_color(primary), ass_color(primary), ass_color(style["outline"]), "&H64000000",
        "-1" if style.get("bold") else "0", "-1" if style.get("italic") else "0", "0", "0",
        "100", "100", "0", "0", "1", str(style.get("outline_width", 2)),
        str(style.get("shadow", 1)),
        str(ALIGNMENTS.get(style.get("alignment"), 2)), "10", "10", "12", "1"]))


def _ass_clean(value) -> str:
    # control characters (incl. \r/\n) would start new ASS lines
    return re.sub(r"[\x00-\x1f\x7f\x85\u2028\u2029]", " ", str(value or ""))


def _ass_field(value: str) -> str:
    # commas separate Style/Dialogue fields; control chars would inject lines
    return _ass_clean(value).replace(",", " ").strip() or "Default"


def lines_to_ass(lines, style: dict, field: str = "en", notes_by_idx: dict = None,
                 speaker_colors: dict = None, speaker_names: dict = None,
                 wrap_chars: dict = None, title: str = "",
                 notes_as_separate_line: bool = False) -> str:
    """style: a dict shaped like ASS_PRESETS' values. speaker_colors:
    {speaker_label: "#RRGGBB"} -- each speaker gets its own Style with that
    fill colour (so libass colours them without any override tags); None
    means one style for everyone. speaker_names: {speaker_label: name},
    written into each Dialogue's Name field. notes_as_separate_line: Step
    6i -- instead of appending core._notes_suffix's note onto the same
    cue's Text (this format's own default, and SRT/VTT's only option),
    renders it as its own second Dialogue line right after the main one,
    in a dedicated "Notes" style at ~70% of the main text size.

    SFX cues (Line.sfx) get their own italic "SFX" style. Both it and the
    Notes style can be positioned apart from the dialogue via
    style["sfx_alignment"] / style["notes_alignment"]; where they share a
    position with other text, libass stacks them rather than overlapping."""
    speaker_colors = speaker_colors or {}
    style_for = {sp: f"Speaker {i + 1}" for i, sp in enumerate(sorted(speaker_colors))}
    header = [
        "[Script Info]",
        f"Title: {_ass_clean(title).strip()}" if title else "Title: Baihe Subtitler export",
        "ScriptType: v4.00+",
        f"PlayResX: {PLAY_RES[0]}",
        f"PlayResY: {PLAY_RES[1]}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line("Default", style, style["primary"]),
    ]
    header += [_style_line(style_for[sp], style, speaker_colors[sp]) for sp in sorted(speaker_colors)]
    header.append(_style_line(
        SFX_STYLE_NAME,
        {**style, "italic": True, "alignment": style.get("sfx_alignment") or style.get("alignment")},
        SFX_COLOR))
    if notes_as_separate_line:
        note_style = dict(style, size=max(1, round(style.get("size", 24) * 0.7)),
                          alignment=style.get("notes_alignment") or style.get("alignment"))
        header.append(_style_line("Notes", note_style, style["primary"]))
    events = ["", "[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for ln in lines:
        cue_notes = None if notes_as_separate_line else notes_by_idx
        text = _ass_escape(_cue_text(ln, field, cue_notes, wrap_chars, italic_tags=False))
        start, end = _ass_ts(ln.start), _ass_ts(ln.end)
        if getattr(ln, "sfx", False):
            events.append(f"Dialogue: 0,{start},{end},{SFX_STYLE_NAME},,0,0,0,,{text}")
        else:
            name = _ass_field((speaker_names or {}).get(ln.speaker) or ln.speaker or "")
            events.append(f"Dialogue: 0,{start},{end},"
                          f"{style_for.get(ln.speaker, 'Default')},{name if ln.speaker else ''},0,0,0,,{text}")
        if notes_as_separate_line:
            note_text = notes_suffix(ln.idx, notes_by_idx).lstrip("\n")
            if note_text:
                events.append(f"Dialogue: 0,{start},{end},Notes,,0,0,0,,{_ass_escape(note_text)}")
    return "\n".join(header + events) + "\n"


def wrap_lines(lines, wrap_chars: dict):
    """Copies of `lines` with en/zh wrapped (for SRT, whose writer stays
    untouched). wrap_chars None -> the lines unchanged."""
    if not wrap_chars:
        return lines
    out = []
    for ln in lines:
        c = copy.copy(ln)
        c.en = wrap_text(ln.en, wrap_chars.get("en"))
        c.zh = wrap_text(ln.zh, wrap_chars.get("zh"))
        out.append(c)
    return out
