"""ASS subtitle export: styles and presets, speaker colours, imported ASS headers kept
on re-export, and CJK rows fitted to the frame."""

import json
import math
import re
from typing import Any

from core import notes_suffix
from subtitle_formats import _cue_text, _is_cjk, wrap_text
from subtitle_parse import ASS_STYLE_COLUMNS


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


# A script is split into records at CR and LF, and some parsers also treat
# the Unicode separators as line ends, so a bare one in the Text field could
# begin a forged Dialogue or section line.
_ASS_LINE_BREAKS = re.compile(r"\r\n|[\n\r\u0085\u2028\u2029]")


_ASS_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# libass has no escape for a backslash: one is drawn as is unless N, n or h
# follows it (a line break, a soft break, a hard space), so a word joiner
# goes between those.
_ASS_BACKSLASH_CODES = re.compile(r"\\(?=[Nnh])")


def _ass_escape(text: str) -> str:
    text = _ASS_BACKSLASH_CODES.sub("\\\\\u2060", text).replace("{", "\\{").replace("}", "\\}")
    # Other control characters have no meaning in dialogue text and a parser
    # may mishandle them, so they become spaces as in _ass_clean.
    return _ASS_CONTROLS.sub(" ", _ASS_LINE_BREAKS.sub(r"\\N", text))


# MarginL and MarginR of every style; ass_cjk_row_limit subtracts both.
_ASS_SIDE_MARGIN = 10


def _style_line(name: str, style: dict[str, Any], primary: str, scale=(1.0, 1.0)) -> str:
    """scale: (x, y) from Baihe's PLAY_RES to the script's, so a style
    written into an imported script's canvas looks the size it would on ours."""
    kx, ky = scale

    def scaled(value, k):
        return value if k == 1 else round(value * k, 2)
    return ("Style: " + ",".join([
        _ass_field(name), _ass_field(style["font"]), str(int(round(scaled(style["size"], ky)))),
        ass_color(primary), ass_color(primary), ass_color(style["outline"]), "&H64000000",
        "-1" if style.get("bold") else "0", "-1" if style.get("italic") else "0", "0", "0",
        "100", "100", "0", "0", "1", str(scaled(style.get("outline_width", 2), ky)),
        str(scaled(style.get("shadow", 1), ky)),
        str(ALIGNMENTS.get(style.get("alignment") or "", 2)), str(round(scaled(_ASS_SIDE_MARGIN, kx))),
        str(round(scaled(_ASS_SIDE_MARGIN, kx))), str(round(scaled(12, ky))), "1"]))


# ----------------------------------------------- imported ASS styles

# What libass assumes for a [V4+ Styles] column a hand-made file leaves out.
_IMPORTED_STYLE_DEFAULTS = {
    "fontname": "Arial", "fontsize": "20", "primarycolour": "&H00FFFFFF",
    "secondarycolour": "&H000000FF", "outlinecolour": "&H00000000", "backcolour": "&H00000000",
    "bold": "0", "italic": "0", "underline": "0", "strikeout": "0", "scalex": "100", "scaley": "100",
    "spacing": "0", "angle": "0", "borderstyle": "1", "outline": "2", "shadow": "2", "alignment": "2",
    "marginl": "10", "marginr": "10", "marginv": "10", "encoding": "1"}
_ASS_LEAD_ONLY = re.compile(r"(?:\{[^{}]*\})*")
_MAX_ASS_NUMBER = 100000


def _ass_int(value, default: int = 0) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        return default
    return number if -_MAX_ASS_NUMBER <= number <= _MAX_ASS_NUMBER else default


def _ass_number(value, default: float) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and 0 <= number <= _MAX_ASS_NUMBER else default


def imported_play_res(script_info: dict):
    """The imported script's canvas as (x, y), filling a missing side the
    way libass does, or None when the script named neither (libass then
    uses 384x288, Baihe's own canvas)."""
    x = _ass_int(script_info.get("playresx")) if script_info.get("playresx") else 0
    y = _ass_int(script_info.get("playresy")) if script_info.get("playresy") else 0
    x, y = max(x, 0), max(y, 0)
    if not x and not y:
        return None
    if not y:
        y = 1024 if x == 1280 else x * 3 // 4
    if not x:
        x = 1280 if y == 1024 else y * 4 // 3
    return (x, y) if x and y else None


def decode_ass_json(value):
    """A stored ass_event / ass_styles value as a dict, or None. Rows can
    come from a backup someone edited, so anything malformed reads as none."""
    if not value:
        return None
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _imported_style_line(row: dict) -> str:
    return "Style: " + ",".join(
        _ass_field(row.get(col) or _IMPORTED_STYLE_DEFAULTS.get(col, "")) for col in ASS_STYLE_COLUMNS)


def _imported_styles(header) -> dict:
    """{name: style row} for the header's styles that a Style line can be
    written for; later duplicates win, as in libass."""
    rows = (header or {}).get("styles")
    out = {}
    for row in rows if isinstance(rows, list) else ():
        if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"].strip():
            out[_ass_field(row["name"])] = {k: v for k, v in row.items()
                                            if k in ASS_STYLE_COLUMNS and isinstance(v, str)}
    return out


def ass_header_for(stored, lines):
    """(header, key) for an ASS export of `lines` from the title's stored
    ass_styles: the header of the import the lines' events came from. Each
    import is kept under its own key, so an undo that brings back an older
    import's lines brings back its canvas and styles too. (None, None) when
    no line has an event from a stored import."""
    stored = decode_ass_json(stored)
    headers = stored.get("headers") if stored else None
    if not isinstance(headers, dict):
        return None, None
    for ln in lines:
        record = decode_ass_json(getattr(ln, "ass_event", None))
        key = record.get("header") if record else None
        if isinstance(key, str) and isinstance(headers.get(key), dict):
            return headers[key], key
    return None, None


def _own_style_name(name: str, taken) -> str:
    # An imported style keeps its name so the file diffs clean; Baihe's
    # style of the same name moves aside.
    while name in taken:
        name = f"Baihe {name}"
    return name


def _header_record(ln, header, key):
    if not header:
        return None
    record = decode_ass_json(getattr(ln, "ass_event", None))
    return record if record is not None and record.get("header") == key else None


def _script_info_rows(title: str, info: dict, play_res) -> list:
    wrap_style = _ass_int(info.get("wrapstyle"), 0) if "wrapstyle" in info else 0
    scaled_border = str(info.get("scaledborderandshadow", "yes")).strip().lower()
    rows = [
        "[Script Info]",
        f"Title: {_ass_clean(title).strip()}" if title else "Title: Baihe Studio export",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res[0]}",
        f"PlayResY: {play_res[1]}",
        f"WrapStyle: {wrap_style if 0 <= wrap_style <= 3 else 0}",
        f"ScaledBorderAndShadow: {'no' if scaled_border == 'no' else 'yes'}",
    ]
    matrix = _ass_clean(info.get("ycbcr matrix")).strip()
    return rows + [f"YCbCr Matrix: {matrix}"] if matrix else rows


def _imported_row_limit(row: dict, scale, frame_aspect) -> int:
    # ass_cjk_row_limit measures on Baihe's canvas, so the imported size is scaled back to it.
    return ass_cjk_row_limit({"size": _ass_number(row.get("fontsize"), 20) / scale[1],
                              "outline_width": _ass_number(row.get("outline"), 2) / scale[1]},
                             frame_aspect)


def _imported_dialogue(record: dict, start: str, end: str, style_name: str, name: str, text: str) -> str:
    margins = ",".join(str(_ass_int(record.get(k))) for k in ("marginl", "marginr", "marginv"))
    effect = _ass_clean(record.get("effect")).replace(",", " ").strip()
    return (f"Dialogue: {_ass_int(record.get('layer'))},{start},{end},{style_name},"
            f"{name},{margins},{effect},{text}")


def _event_text(ln, record: dict, field: str, plain: str, fitted: str) -> str:
    """An imported event's Text: its original Text while the exported field
    is the source and the line still reads as imported, else the leading
    override blocks in front of the line's current text. Overrides inside
    the text only fit the words they were written around, so an edit drops
    them."""
    raw, imported_plain, lead = record.get("raw"), record.get("plain"), record.get("lead")
    if field == "zh" and isinstance(raw, str) and plain == ln.zh == imported_plain:
        return _ass_clean(raw)
    if not isinstance(lead, str) or not _ASS_LEAD_ONLY.fullmatch(lead):
        lead = ""
    return _ass_clean(lead) + _ass_escape(fitted)


def _ass_clean(value) -> str:
    # control characters (incl. \r/\n) would start new ASS lines
    return re.sub(r"[\x00-\x1f\x7f\x85\u2028\u2029]", " ", str(value or ""))


def _ass_field(value: str) -> str:
    # commas separate Style/Dialogue fields; control chars would inject lines
    return _ass_clean(value).replace(",", " ").strip() or "Default"


def ass_cjk_row_limit(style: dict, frame_aspect=None) -> int:
    """How many CJK characters fit on one row at this style. libass breaks
    a row only at spaces, so an unbroken Chinese, Japanese or Korean run
    would go off both edges of the picture. libass scales glyphs and
    outline by the frame's height and keeps them unstretched, so the canvas
    is PLAY_RES[1] tall and frame_aspect (width / height) times that wide,
    while the side margins are script coordinates stretched to the frame's
    width (measured with libass 0.17). A frame wider than 4:3, or an
    unknown one, counts as 4:3: VSFilter-style renderers stretch glyphs
    with the width, so a wider frame gives them no extra characters.

    A CJK glyph is taken as one font size wide. Common fallback fonts are
    narrower (WenQuanYi and Microsoft YaHei about 0.8), but SimSun, SimHei
    and MS Gothic draw a full font size, and a row sized for 0.8 runs off
    the picture in those."""
    width = PLAY_RES[0]
    if isinstance(frame_aspect, (int, float)) and math.isfinite(frame_aspect) and frame_aspect > 0:
        width = min(width, PLAY_RES[1] * frame_aspect)
    margins = 2 * _ASS_SIDE_MARGIN * width / PLAY_RES[0]
    room = width - margins - 2 * style.get("outline_width", 2)
    return max(1, int(room // max(1, int(style.get("size", 24)))))


def _fit_cjk_rows(text: str, limit: int, language, chinese_script: str) -> str:
    """Breaks only the rows that are CJK and longer than `limit`, so
    Latin text is still wrapped by libass to the real video width."""
    return "\n".join(wrap_text(row, limit, language, chinese_script)
                     if len(row) > limit and _is_cjk(row) else row
                     for row in text.split("\n"))


_EAR_SIDES = {"left": 1, "right": 3}
# An event that places itself: an \a or \an tag, \pos or \move.
_OWN_PLACEMENT = re.compile(r"\\(?:an?\d|pos\s*\(|move\s*\()")


def _ear_tag(ear_placement, ln, style: dict, record, imported_row) -> str:
    """The \\an tag that moves a one-ear line to its side of its style's own row (bottom,
    middle or top); "" for any other line, and for an imported event that places itself:
    libass keeps an event's first alignment tag, so ours would move a positioned sign."""
    side = (ear_placement or {}).get(getattr(ln, "id", None))
    if side not in _EAR_SIDES or (record is not None and any(
            isinstance(record.get(k), str) and _OWN_PLACEMENT.search(record[k]) for k in ("lead", "raw"))):
        return ""
    if imported_row is not None:
        code = _ass_int(imported_row.get("alignment"), 2)
    else:
        name = style.get("sfx_alignment") if getattr(ln, "sfx", False) else None
        code = ALIGNMENTS.get(name or style.get("alignment") or "", 2)
    row = (code - 1) // 3 if 1 <= code <= 9 else 0
    return f"{{\\an{3 * row + _EAR_SIDES[side]}}}"


def lines_to_ass(lines, style: dict, field: str = "en", notes_by_idx: dict | None = None,
                 speaker_colors: dict | None = None, speaker_names: dict | None = None,
                 wrap_chars: dict | None = None, title: str = "",
                 notes_as_separate_line: bool = False,
                 language=None, chinese_script: str = "simplified",
                 frame_aspect=None, ass_header: dict | None = None,
                 ass_header_key: str | None = None, ear_placement: dict | None = None) -> str:
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
    position with other text, libass stacks them rather than overlapping.

    ass_header, ass_header_key: an imported file's styles and canvas and
    the key its events carry (ass_header_for). Those lines are written back
    with their own style, layer, margins, effect and override tags
    (_event_text); the rest get Baihe's styles, scaled to that canvas.
    ear_placement: {line_id: "left" | "right"} moves those lines to that side (_ear_tag)."""
    speaker_colors = speaker_colors or {}
    lines = list(lines)
    records = [_header_record(ln, ass_header, ass_header_key) for ln in lines]
    # With no line from that import, the header would only move Baihe's styles.
    if not any(records):
        ass_header = None
    imported = _imported_styles(ass_header)
    info = (ass_header or {}).get("script_info")
    info = info if isinstance(info, dict) else {}
    play_res = imported_play_res(info) if ass_header else None
    play_res = play_res or PLAY_RES
    scale = (play_res[0] / PLAY_RES[0], play_res[1] / PLAY_RES[1])
    default_name = _own_style_name("Default", imported)
    sfx_name = _own_style_name(SFX_STYLE_NAME, imported)
    notes_name = _own_style_name("Notes", imported)
    style_for = {sp: _own_style_name(f"Speaker {i + 1}", imported)
                 for i, sp in enumerate(sorted(speaker_colors))}
    header = _script_info_rows(title, info, play_res) + [
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line(default_name, style, style["primary"], scale),
    ]
    header += [_style_line(style_for[sp], style, speaker_colors[sp], scale) for sp in sorted(speaker_colors)]
    header.append(_style_line(
        sfx_name,
        {**style, "italic": True, "alignment": style.get("sfx_alignment") or style.get("alignment")},
        SFX_COLOR, scale))
    if notes_as_separate_line:
        note_style = dict(style, size=max(1, round(style.get("size", 24) * 0.7)),
                          alignment=style.get("notes_alignment") or style.get("alignment"))
        header.append(_style_line(notes_name, note_style, style["primary"], scale))
        note_row_limit = ass_cjk_row_limit(note_style, frame_aspect)
    header += [_imported_style_line(row) for row in imported.values()]
    events = ["", "[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    row_limit = ass_cjk_row_limit(style, frame_aspect)
    imported_limits = {}
    for ln, record in zip(lines, records):
        cue_notes = None if notes_as_separate_line else notes_by_idx
        plain = _cue_text(ln, field, cue_notes, wrap_chars, italic_tags=False,
                          language=language, chinese_script=chinese_script)
        start, end = _ass_ts(ln.start), _ass_ts(ln.end)
        if getattr(ln, "sfx", False):
            style_name, name = sfx_name, ""
        else:
            style_name = style_for.get(ln.speaker, default_name)
            name = _ass_field((speaker_names or {}).get(ln.speaker) or "") if ln.speaker else ""
        if record is None:
            text = _ear_tag(ear_placement, ln, style, None, None) + _ass_escape(_fit_cjk_rows(plain, row_limit, language, chinese_script))
            events.append(f"Dialogue: 0,{start},{end},{style_name},{name},0,0,0,,{text}")
        else:
            own = _ass_field(record.get("style") or "")
            limit = row_limit
            if own in imported:
                style_name = own
                if own not in imported_limits:
                    imported_limits[own] = _imported_row_limit(imported[own], scale, frame_aspect)
                limit = imported_limits[own]
            if not ln.speaker:
                name = _ass_clean(record.get("name")).replace(",", " ").strip()
            text = _ear_tag(ear_placement, ln, style, record, imported.get(own)) + _event_text(
                ln, record, field, plain, _fit_cjk_rows(plain, limit, language, chinese_script))
            events.append(_imported_dialogue(record, start, end, style_name, name, text))
        if notes_as_separate_line:
            note_text = notes_suffix(ln.idx, notes_by_idx).lstrip("\n")
            if note_text:
                note_text = _fit_cjk_rows(note_text, note_row_limit, language, chinese_script)
                events.append(f"Dialogue: 0,{start},{end},{notes_name},,0,0,0,,{_ass_escape(note_text)}")
    return "\n".join(header + events) + "\n"
