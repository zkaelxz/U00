"""
subtitle_parse.py -- read timed subtitle and lyric files (SRT, WebVTT, ASS/SSA,
LRC) into plain cues, written from the public format descriptions.

Pure and UI-free: bytes in, a ParsedSubtitle out. Nothing here touches the
database or the disk. `validate_cues` reports what looks wrong (overlaps, order,
zero length, empty text, absurd durations) rather than repairing it, and
anything that makes a file unusable raises SubtitleParseError with a plain
message so the caller never writes a partial import.
"""

import codecs
import html
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Optional

# A subtitle file is a few hundred KB at most; the cap keeps a wrong upload
# (a video, a database) from being read into memory and parsed.
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CUES = 20000
MAX_CUE_TEXT_CHARS = 2000  # the same ceiling a line's text has (lines_service.MAX_LINE_TEXT_CHARS)
# No real subtitle row is this long; refusing one up front bounds every per-row
# regex and keeps a single row from being the whole 2 MB.
MAX_PHYSICAL_LINE_CHARS = 4 * MAX_CUE_TEXT_CHARS
# Only the start of a file decides its format, so sniffing never scans the rest.
SNIFF_CHARS = 8192
# Past this a time stamp is a typo or an attack, not a programme.
MAX_TIMESTAMP_SECONDS = 100 * 3600.0
# How many neighbouring lines a cue is compared with when matching by time.
MATCH_WINDOW = 64
# Longer than any one subtitle is on screen; flagged, never silently changed.
ABSURD_CUE_SECONDS = 600.0
# An LRC line ends where the next stamp starts, so the last one has no end.
LRC_LAST_LINE_SECONDS = 4.0

FORMATS = ("srt", "vtt", "ass", "lrc")
# What the import UI offers; any other Python codec could read non-text bytes.
ALLOWED_ENCODINGS = ("utf-8", "utf-16", "gb18030", "big5", "cp932", "cp949", "cp1252")


class SubtitleParseError(ValueError):
    """The file can't be used; the message is shown to people as it is."""


@dataclass
class Cue:
    start: float
    end: float
    text: str
    number: int = 0  # 1-based position in the file, for problem reports


@dataclass
class Problem:
    code: str
    severity: str  # "warning" keeps importing possible; "error" blocks it
    message: str
    count: int = 1


@dataclass
class ParsedSubtitle:
    format: str
    encoding: str
    encoding_guessed: bool
    cues: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(p.severity == "error" for p in self.problems)


# ---------------------------------------------------------------- decoding

# Tried in this order only when the bytes are not valid UTF-8 and carry no BOM.
# The title's language moves its own legacy encoding to the front because
# GB18030 happily decodes much Shift-JIS and EUC-KR into the wrong characters,
# so "first that decodes" is only a guess; the result says so.
_LEGACY_BY_LANGUAGE = {"ja": ("cp932", "gb18030", "cp949", "big5"),
                       "ko": ("cp949", "gb18030", "cp932", "big5"),
                       "zh": ("gb18030", "big5", "cp932", "cp949")}
_LEGACY_DEFAULT = ("gb18030", "cp932", "cp949", "big5")
_WESTERN_FALLBACK = ("cp1252",)

_BOMS = ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
         (b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"))
_SUSPECT_CHARS = re.compile(r"[\ue000-\uf8ff\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]")


def _utf16_without_bom(data: bytes) -> Optional[str]:
    # ASCII-heavy UTF-16 has a zero in every other byte; which parity says the order.
    sample = data[:4096]
    if len(sample) < 4 or b"\x00" not in sample:
        return None
    even, odd = sample[0::2].count(0), sample[1::2].count(0)
    half = len(sample) // 2
    if odd > half * 0.3 and even < half * 0.05:
        return "utf-16-le"
    if even > half * 0.3 and odd < half * 0.05:
        return "utf-16-be"
    return None


def decode_subtitle_bytes(data: bytes, encoding: Optional[str] = None,
                          language: Optional[str] = None):
    """(text, encoding_name, guessed). `encoding` forces a codec (the user's
    correction after a bad guess); an unknown or failing name is an error,
    never a silent fallback. guessed is True only when UTF-8 was ruled out and
    a legacy codec was picked from the fallback list."""
    if encoding:
        try:
            canonical = codecs.lookup(encoding).name
        except LookupError:
            canonical = None
        if canonical not in ALLOWED_ENCODINGS:
            raise SubtitleParseError(
                f"Unsupported text encoding {encoding[:40]!r}; use one of: "
                f"{', '.join(ALLOWED_ENCODINGS)}.")
        try:
            return data.decode(canonical), canonical, False
        except UnicodeDecodeError:
            raise SubtitleParseError(f"The file is not valid {encoding} text.") from None
    for bom, name in _BOMS:
        if data.startswith(bom):
            try:
                text = data.decode(name)
            except UnicodeDecodeError:
                raise SubtitleParseError(
                    f"The file starts like {name.upper()} text but doesn't decode as it.") from None
            return text, ("utf-8-sig" if name == "utf-8-sig" else name), False
    wide = _utf16_without_bom(data)
    if wide:
        try:
            return data.decode(wide), wide, False
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        pass
    for name in _LEGACY_BY_LANGUAGE.get(language or "", _LEGACY_DEFAULT) + _WESTERN_FALLBACK:
        try:
            text = data.decode(name)
        except UnicodeDecodeError:
            continue
        if not _SUSPECT_CHARS.search(text):
            return text, name, True
    raise SubtitleParseError("Couldn't work out this file's text encoding. "
                             "Save it as UTF-8 and try again.")


# --------------------------------------------------------------- time stamps

def _seconds(h, m, s, frac) -> float:
    total = int(h or 0) * 3600 + int(m) * 60 + int(s) + (int(frac) / 10 ** len(frac) if frac else 0.0)
    if total > MAX_TIMESTAMP_SECONDS:
        raise SubtitleParseError(f"A time stamp is past {int(MAX_TIMESTAMP_SECONDS // 3600)} hours.")
    return total


_SRT_TIME = r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})"
_SRT_LINE = re.compile(rf"^\s*{_SRT_TIME}\s*-->\s*{_SRT_TIME}")
_VTT_TIME = r"(?:(\d{1,3}):)?(\d{2}):(\d{2})\.(\d{1,3})"
_VTT_LINE = re.compile(rf"^\s*{_VTT_TIME}\s+-->\s+{_VTT_TIME}(?:[ \t].*)?$")
_ARROW = re.compile(r"-->")


def _lines_of(text: str) -> list:
    # splitlines() would also split on U+2028 and form feeds, which can sit
    # inside a cue's text.
    rows = re.split(r"\r\n|\r|\n", text.lstrip("\ufeff"))
    for number, row in enumerate(rows, 1):
        if len(row) > MAX_PHYSICAL_LINE_CHARS:
            raise SubtitleParseError(f"Line {number} is longer than {MAX_PHYSICAL_LINE_CHARS} characters.")
    return rows


def _check_cue_count(count: int) -> None:
    # Checked while parsing so a hostile file can't build millions of cues first.
    if count > MAX_CUES:
        raise SubtitleParseError(f"That file has more than {MAX_CUES} cues.")


def _unescape(text: str) -> str:
    try:
        return html.unescape(text)
    except ValueError:
        # A numeric entity with thousands of digits overflows int(); leave it as written.
        return text


def _clean_text(lines: list) -> str:
    return "\n".join(part for part in (ln.strip() for ln in lines) if part)


# --------------------------------------------------------------------- SRT

# Bodies exclude the delimiters that open a new tag, so a row of repeated "<" or
# "{" fails each attempt at the next character instead of rescanning the row.
_SRT_TAGS = re.compile(r"</?(?:i|b|u|s|font|c)\b[^<>]*>|\{\\[^{}]*\}", re.IGNORECASE)


def parse_srt(text: str) -> list:
    rows = _lines_of(text)
    cues, i = [], 0
    while i < len(rows):
        m = _SRT_LINE.match(rows[i])
        if not m:
            if _ARROW.search(rows[i]):
                raise SubtitleParseError(f"Line {i + 1}: can't read the time range "
                                         f"({rows[i].strip()[:40]!r}).")
            i += 1
            continue
        g = m.groups()
        start, end = _seconds(g[0], g[1], g[2], g[3]), _seconds(g[4], g[5], g[6], g[7])
        body, i = [], i + 1
        while i < len(rows) and rows[i].strip():
            body.append(rows[i])
            i += 1
        cues.append(Cue(start, end, _unescape(_clean_text([_SRT_TAGS.sub("", b) for b in body])),
                        len(cues) + 1))
        _check_cue_count(len(cues))
    return cues


# --------------------------------------------------------------------- VTT

_VTT_TAGS = re.compile(r"<[^<>]*>")


def parse_vtt(text: str) -> list:
    rows = _lines_of(text)
    if not rows or not rows[0].startswith("WEBVTT"):
        raise SubtitleParseError("This isn't a WebVTT file: it should start with WEBVTT.")
    blocks, cur = [], []
    for row in rows[1:]:
        if row.strip():
            cur.append(row)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    cues = []
    for block in blocks:
        if block[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        # An identifier line may come first; a timing line is always the first or second.
        at = next((k for k, row in enumerate(block[:2]) if _ARROW.search(row)), None)
        if at is None:
            continue  # the header's own metadata lines
        m = _VTT_LINE.match(block[at])
        if not m:
            raise SubtitleParseError(f"Can't read the time range {block[at].strip()[:40]!r}.")
        g = m.groups()
        start, end = _seconds(g[0], g[1], g[2], g[3]), _seconds(g[4], g[5], g[6], g[7])
        body = [_unescape(_VTT_TAGS.sub("", row)) for row in block[at + 1:]]
        cues.append(Cue(start, end, _clean_text(body), len(cues) + 1))
        _check_cue_count(len(cues))
    return cues


# ----------------------------------------------------------------- ASS/SSA

# SSA v4 had no Format line to rely on in some hand-made files; this is its
# documented Dialogue layout.
_SSA_DEFAULT_FORMAT = ("marked", "start", "end", "style", "name", "marginl", "marginr",
                       "marginv", "effect", "text")
_ASS_TIME = re.compile(r"^\s*(\d{1,3}):(\d{2}):(\d{2})[.:](\d{1,3})\s*$")
_ASS_OVERRIDE = re.compile(r"\{[^{}]*\}")
_ASS_DRAWING_ON = re.compile(r"\\p([1-9]\d*)")


def _ass_time(value: str, number: int) -> float:
    m = _ASS_TIME.match(value)
    if not m:
        raise SubtitleParseError(f"Dialogue {number}: can't read the time {value.strip()[:20]!r}.")
    return _seconds(*m.groups())


def _ass_text(raw: str) -> Optional[str]:
    """The visible text, or None for a vector drawing (\\p1 and up), whose
    "text" is path commands."""
    if any(_ASS_DRAWING_ON.search(block) for block in _ASS_OVERRIDE.findall(raw)):
        return None
    text = _ASS_OVERRIDE.sub("", raw)
    # \N is a forced line break; \n only a soft one and \h a non-breaking space.
    text = text.replace("\\N", "\n").replace("\\n", " ").replace("\\h", " ")
    return _clean_text(text.split("\n"))


def parse_ass(text: str) -> list:
    in_events, columns, cues = False, None, []
    for row in _lines_of(text):
        stripped = row.strip()
        if stripped.startswith("["):
            in_events = stripped.lower() == "[events]"
            continue
        if not in_events:
            continue
        key, _, value = stripped.partition(":")
        key = key.lower()
        if key == "format":
            columns = tuple(c.strip().lower() for c in value.split(","))
        elif key == "dialogue":
            cols = columns or _SSA_DEFAULT_FORMAT
            if not {"start", "end", "text"} <= set(cols):
                raise SubtitleParseError("The [Events] Format line has no Start, End and Text columns.")
            # Text is last and may hold commas, so the split stops before it.
            parts = value.lstrip().split(",", len(cols) - 1)
            if len(parts) < len(cols):
                raise SubtitleParseError(f"Dialogue {len(cues) + 1} has too few fields.")
            fields_ = dict(zip(cols, parts))
            body = _ass_text(fields_["text"])
            if body is None:
                continue
            n = len(cues) + 1
            cues.append(Cue(_ass_time(fields_["start"], n), _ass_time(fields_["end"], n), body, n))
            _check_cue_count(len(cues))
    return cues


# --------------------------------------------------------------------- LRC

_LRC_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_LRC_WORD_STAMP = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
_LRC_OFFSET = re.compile(r"^\s*\[offset:\s*([+-]?\d+)\s*\]\s*$", re.IGNORECASE)


def _lrc_offset(digits: str) -> float:
    # Digits are counted before int(), which refuses very long strings with a ValueError.
    if len(digits.lstrip("+-")) > 9 or abs(int(digits)) > MAX_TIMESTAMP_SECONDS * 1000:
        raise SubtitleParseError("The [offset:] tag is out of range.")
    return int(digits) / 1000.0


def parse_lrc(text: str) -> list:
    offset = 0.0
    stamped = []  # (start seconds, text) in file order
    for number, row in enumerate(_lines_of(text), 1):
        m = _LRC_OFFSET.match(row)
        if m:
            # Positive offsets make the lyrics appear sooner, so they are subtracted.
            offset = _lrc_offset(m.group(1))
            continue
        starts, pos = [], 0
        while True:
            m = _LRC_STAMP.match(row, pos)
            if not m:
                break
            starts.append(_seconds(0, m.group(1), m.group(2), m.group(3) or ""))
            pos = m.end()
        if not starts:
            continue  # [ti:], [ar:], [by:] and other tags carry no time
        words = _LRC_WORD_STAMP.sub("", row[pos:]).strip()
        if len(words) > MAX_CUE_TEXT_CHARS:
            raise SubtitleParseError(f"Line {number}: a lyric line has more than "
                                     f"{MAX_CUE_TEXT_CHARS} characters.")
        # One row can repeat its words under many stamps; stop before that many entries exist.
        _check_cue_count(len(stamped) + len(starts))
        stamped.extend((s, words) for s in starts)
    # A stamp that sorts equal keeps file order, so two-language lyrics sharing
    # one time stay together.
    stamped.sort(key=lambda item: item[0])
    grouped = []  # [start, [texts], joined length]
    for start, words in stamped:
        if grouped and grouped[-1][0] == start:
            if words:
                # The cap is enforced before the join so repeated stamps can't build huge texts.
                if grouped[-1][2] + 1 + len(words) > MAX_CUE_TEXT_CHARS:
                    raise SubtitleParseError(
                        f"The lyrics at one time stamp have more than {MAX_CUE_TEXT_CHARS} characters.")
                grouped[-1][1].append(words)
                grouped[-1][2] += 1 + len(words)
        else:
            grouped.append([start, [words] if words else [], len(words)])
    cues = []
    for k, (start, texts, _length) in enumerate(grouped):
        if not texts:
            continue  # an empty stamp only marks where the previous line stops
        end = grouped[k + 1][0] if k + 1 < len(grouped) else start + LRC_LAST_LINE_SECONDS
        cues.append(Cue(max(start - offset, 0.0), max(end - offset, 0.0), "\n".join(texts), len(cues) + 1))
    return cues


# ------------------------------------------------------------- orchestration

_PARSERS = {"srt": parse_srt, "vtt": parse_vtt, "ass": parse_ass, "lrc": parse_lrc}
_EXTENSIONS = {"srt": "srt", "vtt": "vtt", "ass": "ass", "ssa": "ass", "lrc": "lrc"}


def sniff_format(text: str, filename: str = "") -> str:
    """The format by content first (a renamed file is common), then extension."""
    head = text.lstrip("\ufeff \t\r\n")[:SNIFF_CHARS]
    if head.startswith("WEBVTT"):
        return "vtt"
    if re.search(r"^\[(?:script info|events|v4\+? styles)\]", head, re.IGNORECASE | re.MULTILINE):
        return "ass"
    if _SRT_LINE.search(head) or re.search(r"^[ \t]*\d+:\d{2}:\d{2}[,.]\d+[ \t]*-->", head, re.MULTILINE):
        return "srt"
    if _LRC_STAMP.search(head):
        return "lrc"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXTENSIONS:
        return _EXTENSIONS[ext]
    raise SubtitleParseError("This doesn't look like an SRT, VTT, ASS or LRC subtitle file.")


def validate_cues(cues: list) -> list:
    """Problems found in `cues` (already in file order); the cues are not changed."""
    found = {}  # code -> [severity, text, count, first cue number]

    def note(code, severity, text, number):
        if code in found:
            found[code][2] += 1
        else:
            found[code] = [severity, text, 1, number]
    for k, cue in enumerate(cues):
        if cue.end < cue.start:
            note("negative_length", "error", "A cue ends before it starts", cue.number)
        elif cue.end == cue.start:
            note("zero_length", "warning", "A cue has zero length", cue.number)
        elif cue.end - cue.start > ABSURD_CUE_SECONDS:
            note("absurd_duration", "warning",
                 f"A cue is longer than {int(ABSURD_CUE_SECONDS // 60)} minutes", cue.number)
        if not cue.text.strip():
            note("empty_text", "warning", "A cue has no text and is skipped", cue.number)
        elif len(cue.text) > MAX_CUE_TEXT_CHARS:
            note("text_too_long", "error",
                 f"A cue has more than {MAX_CUE_TEXT_CHARS} characters", cue.number)
        if k:
            prev = cues[k - 1]
            if cue.start < prev.start:
                note("out_of_order", "warning", "A cue comes before the previous one in time "
                     "(cues are sorted by start)", cue.number)
            elif cue.start < prev.end:
                note("overlap", "warning", "A cue starts before the previous one ends", cue.number)
    return [Problem(code, severity,
                    f"{text} ({count} cue{'s' if count > 1 else ''}, first at cue {first}).", count)
            for code, (severity, text, count, first) in found.items()]


def parse_subtitle(data: bytes, filename: str = "", encoding: Optional[str] = None,
                   language: Optional[str] = None) -> ParsedSubtitle:
    """Decode, detect the format, parse and validate. Raises SubtitleParseError
    for an unusable file. Cues come back with empty ones dropped and the rest
    in time order; times are the file's own."""
    if len(data) > MAX_FILE_BYTES:
        raise SubtitleParseError(f"That file is too large (at most {MAX_FILE_BYTES // (1024 * 1024)} MB).")
    if not data.strip():
        raise SubtitleParseError("That file is empty.")
    text, used, guessed = decode_subtitle_bytes(data, encoding, language)
    fmt = sniff_format(text, filename)
    cues = _PARSERS[fmt](text)
    _check_cue_count(len(cues))
    if not cues:
        raise SubtitleParseError(f"No cues found in this {fmt.upper()} file.")
    problems = validate_cues(cues)
    cues = sorted((c for c in cues if c.text.strip()), key=lambda c: c.start)
    if not cues:
        raise SubtitleParseError("Every cue in this file is empty.")
    parsed = ParsedSubtitle(fmt, used, guessed, cues, problems)
    if guessed:
        parsed.problems.append(Problem(
            "encoding_guess", "warning",
            f"The text isn't UTF-8; it was read as {used}. Check the sample, and pick another "
            "encoding if the characters look wrong."))
    return parsed


# ------------------------------------------------- two-language files, matching

def split_bilingual(cues: list, translation_first: bool = False) -> tuple:
    """(source cues, translation cues) from a file that carries two lines per
    cue. A cue without exactly two lines can't be split reliably, so it is
    kept whole as source with no translation, and counted in the third value."""
    source, translated, unsplit = [], [], 0
    for cue in cues:
        parts = [p for p in cue.text.split("\n") if p.strip()]
        if len(parts) == 2:
            first, second = parts
            src, tr = (second, first) if translation_first else (first, second)
        else:
            src, tr = cue.text, ""
            unsplit += 1
        source.append(Cue(cue.start, cue.end, src, cue.number))
        translated.append(Cue(cue.start, cue.end, tr, cue.number))
    return source, translated, unsplit


def looks_bilingual(cues: list) -> bool:
    """Most cues have exactly two lines. Single-line cues that wrap onto a
    second line in an ordinary file make this a hint for the user, not a
    decision."""
    usable = [c for c in cues if c.text.strip()]
    two = sum(1 for c in usable if len([p for p in c.text.split("\n") if p.strip()]) == 2)
    return len(usable) >= 2 and two >= 0.8 * len(usable)


def match_cues_to_lines(cues: list, lines: list) -> dict:
    """{line index: [cues]} -- each cue goes to the line it overlaps most,
    when that overlap is more than half of the cue's own duration (a
    zero-length cue goes to the line that contains its start). Matching by
    time, never by position, so a file with a different number of cues still
    lands on the right lines. Cues that fit no line are left out; the caller
    counts them from the difference. `lines` need start/end, in time order."""
    starts = [ln.start for ln in lines]
    reach, farthest = [], float("-inf")
    for ln in lines:
        farthest = max(farthest, ln.end)
        reach.append(farthest)
    matched = {}
    for cue in cues:
        point = cue.end <= cue.start
        # Lines starting at or after the cue's end can't overlap it; lines whose
        # running maximum end is not past its start can't either.
        high = bisect_right(starts, cue.start) if point else bisect_left(starts, cue.end)
        low = bisect_right(reach, cue.start)
        best, best_overlap = None, 0.0
        # Looking back only a few lines keeps one cue from costing the whole
        # list when lines overlap heavily; tidy lines never need more.
        for k in range(high - 1, max(low, high - MATCH_WINDOW) - 1, -1):
            ln = lines[k]
            if point:
                overlap = 1.0 if ln.start <= cue.start < ln.end else 0.0
            else:
                overlap = min(cue.end, ln.end) - max(cue.start, ln.start)
            # Walking backwards, an equal overlap is the earlier line, which wins a tie.
            if overlap > 0 and overlap >= best_overlap:
                best, best_overlap = k, overlap
        if best is None:
            continue
        duration = cue.end - cue.start
        if duration > 0 and best_overlap <= duration * 0.5:
            continue
        matched.setdefault(best, []).append(cue)
    return matched
