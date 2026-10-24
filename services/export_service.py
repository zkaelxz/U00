"""
services/export_service.py -- Export-stage services for one drama, used
by the /api/export routes: the readiness summary, subtitle text (SRT/VTT/
ASS, pure, no writes), the three flagging actions, EPUB export
(novel-narration dramas only) and mark_exported (writes only the drama's
`status`).

Every flagging function writes ONLY the flag/flag_note fields, as a
compare-and-set (`save_flags`): a line whose flag, or the text or timing
the flag was computed from (its own or the next line's), changed since it
was read keeps that edit and isn't counted.
Audiobook/burned-in-video export (each its own subprocess dependency,
ffmpeg) live in services/media_export_service.py.

No FastAPI import: plain functions, plain dicts/bytes in and
out, so another service could call them too.
"""
import contextlib
import os
import re
import tempfile
import threading
import unicodedata
from collections import OrderedDict
from typing import Optional
from urllib.parse import quote

import auto_qc
import core as core_module
import db
import media_inspect
import subtitle_formats
from lib.safe_paths import is_device_name
from services import (
    metadata_service,
    ownership_service,
    reading_speed_dismissal_service,
    restructure_service,
)
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                      NotFoundError, UnsupportedOperationError)

_EPUB_FIELDS = ("en", "zh")

_SUBTITLE_FORMATS = ("srt", "vtt", "lrc")
_SUBTITLE_FIELDS = ("en", "zh", "bilingual")


# The stem is capped in UTF-8 bytes, not characters: 120 CJK characters are
# 360 bytes, over the 255-byte filename limit on Linux and macOS.
_MAX_STEM_BYTES = 200
_MAX_LANGUAGE_CHARS = 40
_MAX_LANGUAGE_BYTES = 60
_MIN_TITLE_BYTES = 30
_NAME_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
# What the browser saves a download as, per artifact kind.
ARTIFACT_WHAT = {"audio": "audiobook", "video": "burned-in", "softsub_video": "soft sub",
                 "dubbed_video": "dubbed", "scanlate_zip": "pages", "scanlate_pdf": "pages"}


def _name_part(text) -> str:
    # Control and format characters (bidi overrides, zero-width, BOM) would
    # let a title disguise the real extension or vanish from the saved name.
    kept = "".join(c for c in str(text or "") if not unicodedata.category(c).startswith("C"))
    return " ".join(_NAME_ILLEGAL.sub(" ", kept).split())


def _truncate_bytes(text: str, limit: int) -> str:
    """The longest prefix of `text` that fits `limit` UTF-8 bytes without
    cutting a character."""
    return text.encode("utf-8")[:max(limit, 0)].decode("utf-8", errors="ignore")


def field_language(drama: dict, field: str) -> str:
    """Readable code for the language a subtitle field holds."""
    source = drama.get("source_language") or "zh"
    return {"en": "en", "zh": source, "bilingual": f"{source}+en"}.get(field, "")


def narration_language(drama: dict) -> str:
    original = (drama.get("content_mode") == "novel_narration"
                and drama.get("narration_language") == "original")
    return (drama.get("source_language") or "zh") if original else "en"


def _title_and_tail(drama_id: int, what: str, language: str):
    drama = db.get_drama(drama_id) or {}
    title = _name_part(drama.get("title_en")) or _name_part(drama.get("title_zh"))
    episode = drama.get("episode_number")
    tail = (f" - Ep {episode}" if isinstance(episode, int) else "")
    if what:
        tail += f" - {_name_part(what)}"
    language = _truncate_bytes(_name_part(language)[:_MAX_LANGUAGE_CHARS], _MAX_LANGUAGE_BYTES).strip()
    if language and what:
        tail += f" ({language})"
    return title, tail


def _join_name(drama_id: int, title: str, tail: str, ext: str) -> str:
    ext = "." + re.sub(r"[^A-Za-z0-9]", "", ext.lstrip("."))[:10] if ext.strip(". ") else ""
    room = max(_MIN_TITLE_BYTES, _MAX_STEM_BYTES - len(tail.encode("utf-8")) - len(ext))
    title = _truncate_bytes(title.lstrip(". "), room).rstrip(". ")
    name = (title or f"drama {drama_id}") + tail + ext
    # Windows reads the part before the first dot as the device, so "CON",
    # "nul - Ep 1" with no tail, and "CON.x" all need the guard.
    if is_device_name(name):
        name = "_" + name
    return name


def download_filename(drama_id: int, what: str, language: str, ext: str) -> str:
    """`<Title> - Ep <n> - <what> (<language>).<ext>` for the Content-Disposition
    name. Only the name the browser saves: stored artifact names stay ID-only
    because job ids and lookups depend on them. Always one path segment: no
    separators, control characters or trailing dots, so it is also safe as a
    zip entry or folder name."""
    title, tail = _title_and_tail(drama_id, what, language)
    return _join_name(drama_id, title, tail, ext)


def folder_name(drama_id: int) -> str:
    """`<Title> - Ep <n>`: the per-title folder in a library export zip,
    truncated the same way as a download name."""
    title, tail = _title_and_tail(drama_id, "", "")
    return _join_name(drama_id, title, tail, "")


def content_disposition(filename: str) -> str:
    """Attachment header with an ASCII fallback and the RFC 5987 UTF-8 name.
    Control characters and quotes never reach the header."""
    clean = _NAME_ILLEGAL.sub("_", filename) or "download"
    # Non-ASCII is dropped, not replaced, so a CJK title leaves the readable
    # rest of the name instead of a run of underscores; browsers that
    # understand filename* never show this one.
    fallback = "".join(c for c in clean if " " <= c <= "~" and c not in '%\\')
    stem, dot, ext = fallback.rpartition(".")
    if not dot:
        stem, ext = fallback, ""
    # "_" is kept: it is the Windows device-name guard.
    stem = re.sub(r"\s+", " ", stem).strip(" .-")
    if not any(c.isalnum() for c in stem):
        stem = "download"
    fallback = stem + (dot + ext if dot else "")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(clean, safe='')}"


def drama_disposition(drama_id: int, what: str, language: str, ext: str) -> str:
    return content_disposition(download_filename(drama_id, what, language, ext))


def dub_track_disposition(drama_id: int, track_name: str) -> str:
    what = "narration track" if track_name.startswith("narration") else "dub track"
    language = narration_language(db.get_drama(drama_id) or {})
    return drama_disposition(drama_id, what, language, "wav")


def subtitle_disposition(drama_id: int, field: str, ext: str, noun: str = "subtitles") -> str:
    drama = db.get_drama(drama_id) or {}
    what = f"bilingual {noun}" if field == "bilingual" else noun
    return content_disposition(download_filename(
        drama_id, what, field_language(drama, field), ext))


def _load_drama_and_lines(drama_id: int):
    drama = ownership_service.require_drama(drama_id)
    lines = db.load_line_objects(drama_id)
    return drama, lines


def break_language(drama: dict) -> dict:
    """The drama's source language and script, as the keyword arguments
    subtitle_formats takes to break CJK lines between words."""
    return {"language": drama.get("source_language") or "zh",
            "chinese_script": drama.get("chinese_script") or "simplified"}


def build_wrap_chars(wrap_chars_en, wrap_chars_source):
    if wrap_chars_en is None and wrap_chars_source is None:
        return None
    return {"en": wrap_chars_en, "zh": wrap_chars_source}


def _load_notes_by_idx(drama_id: int):
    from translation_guide import group_notes_by_line
    notes = db.list_translation_notes(drama_id)
    return group_notes_by_line(notes) if notes else None


_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
# Each speaker_colors entry becomes an ASS Style line; unknown speakers are
# harmless but kept under a cap rather than intersected with the drama's.
MAX_SPEAKER_COLORS = 200
MAX_SPEAKER_LABEL_LEN = 100

_STYLE_KEYS = ("font", "size", "bold", "italic", "primary", "outline", "outline_width",
               "shadow", "alignment", "sfx_alignment", "notes_alignment")
_SIZE_RANGE = (12, 60)
_OUTLINE_WIDTH_RANGE = (0, 10)
_SHADOW_RANGE = (0, 5)


def get_ass_style_options() -> dict:
    """Everything a UI needs to render ASS style controls without
    hard-coding it: presets, fonts, alignments, numeric ranges, and the
    default preset name."""
    return {
        "presets": {name: dict(p) for name, p in subtitle_formats.ASS_PRESETS.items()},
        "default_preset": "Clean",
        "fonts": list(subtitle_formats.FONT_CHOICES),
        "custom_font_allowed": True,
        "alignments": dict(subtitle_formats.ALIGNMENTS),
        "size_range": list(_SIZE_RANGE),
        "outline_width_range": list(_OUTLINE_WIDTH_RANGE),
        "shadow_range": list(_SHADOW_RANGE),
    }


def _check_int_range(value, name, rng):
    if isinstance(value, bool) or not isinstance(value, int) or not rng[0] <= value <= rng[1]:
        raise InvalidInputError(f"Style '{name}' must be an integer from {rng[0]} to {rng[1]}.")


def check_color(value, name):
    if not isinstance(value, str) or not _HEX_COLOR.fullmatch(value):
        raise InvalidInputError(f"'{name}' must be a colour like #RRGGBB.")


def build_ass_style(preset: str, style) -> dict:
    if preset not in subtitle_formats.ASS_PRESETS:
        raise InvalidInputError("Unknown ASS preset.")
    style = style or {}
    if not isinstance(style, dict):
        raise InvalidInputError("'style' must be an object.")
    unknown = [k for k in style if k not in _STYLE_KEYS]
    if unknown:
        raise InvalidInputError("Unknown style key: " + ", ".join(sorted(map(str, unknown))) + ".")
    merged = {**subtitle_formats.ASS_PRESETS[preset], **style}
    merged.setdefault("sfx_alignment", None)
    merged.setdefault("notes_alignment", None)

    font = merged["font"]
    if not isinstance(font, str) or not font.strip():
        raise InvalidInputError("Style 'font' must be a non-empty string.")
    if any(ord(c) < 32 or ord(c) == 127 for c in font):
        raise InvalidInputError("Style 'font' must not contain control characters.")
    _check_int_range(merged["size"], "size", _SIZE_RANGE)
    _check_int_range(merged["outline_width"], "outline_width", _OUTLINE_WIDTH_RANGE)
    _check_int_range(merged["shadow"], "shadow", _SHADOW_RANGE)
    for key in ("bold", "italic"):
        if not isinstance(merged[key], bool):
            raise InvalidInputError(f"Style '{key}' must be true or false.")
    check_color(merged["primary"], "primary")
    check_color(merged["outline"], "outline")
    if merged["alignment"] not in subtitle_formats.ALIGNMENTS:
        raise InvalidInputError("Style 'alignment' is not a valid position.")
    for key in ("sfx_alignment", "notes_alignment"):
        if merged[key] is not None and merged[key] not in subtitle_formats.ALIGNMENTS:
            raise InvalidInputError(f"Style '{key}' is not a valid position.")
    return merged


def check_wrap(value, name):
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise InvalidInputError(f"'{name}' must be a non-negative integer.")


def generate_ass_text(drama_id: int, field: str = "en", style: Optional[dict] = None,
                      preset: str = "Clean", speaker_colors: Optional[dict] = None,
                      per_speaker_colors: bool = True, include_notes: bool = False,
                      notes_as_separate_line: bool = False,
                      wrap_chars_en: Optional[int] = None,
                      wrap_chars_source: Optional[int] = None) -> str:
    """Generates ASS subtitle text for one drama --
    pure and read-only, returns text only.

    Lines come from the database (saved state), so edits the client has
    not saved yet are not included. field: "en",
    "zh" or "bilingual". The style starts from the named preset; any keys in
    `style` (font, size, bold, italic, primary, outline, outline_width,
    shadow, alignment, sfx_alignment, notes_alignment) override it, and
    missing keys keep the preset's values. speaker_colors is
    {speaker_label: "#RRGGBB"}; when omitted and per_speaker_colors is
    true, subtitle_formats.default_speaker_colors picks one per speaker in
    the drama; per_speaker_colors=False means one style for everyone.
    Overlapping cues keep their saved times (players stack them, which is
    how two speakers talking at once stay readable); SRT is the format that
    trims them. Lines are wrapped as generate_subtitle_text does.
    notes_as_separate_line requires include_notes.

    Not done here: persisting the style per drama (it's per-request), the
    Package zip, "Mark as exported" (mark_exported), and any binary/file
    download.

    Raises NotFoundError (unknown drama), ConflictError (no lines, or none
    with text in `field`) and InvalidInputError (unknown
    field/preset, any invalid style/colour/wrap value, or
    notes_as_separate_line without include_notes).
    """
    if field not in _SUBTITLE_FIELDS:
        raise InvalidInputError("Unknown subtitle field.")
    merged_style = build_ass_style(preset, style)
    if speaker_colors is not None:
        if not isinstance(speaker_colors, dict):
            raise InvalidInputError("'speaker_colors' must be an object.")
        if len(speaker_colors) > MAX_SPEAKER_COLORS:
            raise InvalidInputError("'speaker_colors' has too many entries.")
        for label, color in speaker_colors.items():
            if not isinstance(label, str):
                raise InvalidInputError("Speaker labels must be strings.")
            if len(label) > MAX_SPEAKER_LABEL_LEN:
                raise InvalidInputError("A speaker label is too long.")
            check_color(color, "speaker_colors")
    if notes_as_separate_line and not include_notes:
        raise InvalidInputError("'notes_as_separate_line' requires 'include_notes'.")
    check_wrap(wrap_chars_en, "wrap_chars_en")
    check_wrap(wrap_chars_source, "wrap_chars_source")

    drama, lines = _load_drama_and_lines(drama_id)
    # An ASS file always has a header, so without this an untranslated title
    # gives an empty track, and a burned-in export re-encodes for nothing.
    _require_exportable_text(lines, field)

    if not per_speaker_colors:
        colors = {}
    elif speaker_colors is not None:
        colors = dict(speaker_colors)
    else:
        colors = subtitle_formats.default_speaker_colors(ln.speaker for ln in lines)

    speaker_names = {c["speaker_label"]: c["character_name"]
                     for c in db.list_characters_with_series_names(drama_id)
                     if c.get("character_name")}
    notes_by_idx = _load_notes_by_idx(drama_id) if include_notes else None

    return subtitle_formats.lines_to_ass(
        lines, merged_style, field, notes_by_idx,
        speaker_colors=colors, speaker_names=speaker_names,
        wrap_chars=build_wrap_chars(wrap_chars_en, wrap_chars_source),
        title=drama.get("title_en") or drama.get("title_zh") or "",
        notes_as_separate_line=notes_as_separate_line, **break_language(drama))


def get_export_readiness(drama_id: int) -> dict:
    """Read-only export-readiness summary for one drama: line/translation
    counts plus counts of the issues the flagging actions act on (timing
    overlaps, Auto QC mismatches, reading-speed-dense
    lines). Raises NotFoundError for an unknown drama id. A drama with no
    lines yet returns all-zero/false counts rather than an error."""
    drama = ownership_service.require_drama(drama_id)

    rows = db.load_lines(drama_id)
    lines = core_module.lines_from_rows(rows)

    total_lines = len(lines)
    zh_filled = sum(1 for ln in lines if ln.zh.strip())
    en_filled = sum(1 for ln in lines if ln.en.strip())
    fully_translated = total_lines > 0 and en_filled == total_lines

    _, overlaps = subtitle_formats.clamp_overlaps(lines)
    # Same names / banned terms run_auto_qc_flagging uses, so this count
    # matches what "Flag these for review" would flag.
    series_id = drama.get("series_id")
    glossary_terms = db.list_glossary_terms(series_id) if series_id else []
    names = auto_qc.build_name_list(
        glossary_terms, db.list_series_characters(series_id) if series_id else [])
    qc_issues = auto_qc.find_issues(lines, names, auto_qc.build_banned_terms(glossary_terms))
    dense = subtitle_formats.dense_lines(lines, mode=subtitle_formats.flagging_mode_of(drama))

    return {
        "drama_id": drama_id,
        "total_lines": total_lines,
        "zh_filled": zh_filled,
        "en_filled": en_filled,
        "fully_translated": fully_translated,
        "overlap_count": len(overlaps),
        "auto_qc_issue_count": len(qc_issues),
        "dense_line_count": len(dense),
    }


def _export_result(text: str, report: Optional[dict] = None, fps: Optional[float] = None) -> dict:
    report = report or {"extended": 0, "trimmed": 0, "left_long": []}
    return {"text": text, "cues_extended": report["extended"], "cues_trimmed": report["trimmed"],
            "cues_left_long": report["left_long"], "fps": fps}


_FPS_PROBE_TIMEOUT_S = 5
# Above this a "frame rate" is a bad container timebase, not video.
_MAX_PLAUSIBLE_FPS = 240


# Any caller with lines.read can ask for a tidied export, and each probe can
# hold a worker thread for the whole timeout, so a file's answer is kept until
# the file changes. A failed probe is not kept: a timeout or a missing ffprobe
# is transient and must not pin 24 fps. Bounded so it can't grow with the
# library.
_FPS_CACHE_MAX = 256
_fps_cache: "OrderedDict[tuple, float]" = OrderedDict()
_fps_cache_lock = threading.Lock()


def _probe_title_fps(drama_id: int, drama: dict) -> float:
    """The frame rate of the title's own video, else DEFAULT_FPS. It is not
    stored on the title and ffprobe may be missing or slow, so any failure is
    the old 24 fps behaviour rather than an export error."""
    path = metadata_service.stored_media_path(drama_id, drama)
    if not path:
        return subtitle_formats.DEFAULT_FPS
    try:
        st = os.stat(path)
    except OSError:
        return subtitle_formats.DEFAULT_FPS
    key = (path, st.st_mtime_ns, st.st_size)
    with _fps_cache_lock:
        if key in _fps_cache:
            _fps_cache.move_to_end(key)
            return _fps_cache[key]
    try:
        fps = media_inspect.analysis_from_probe(
            media_inspect.run_ffprobe(path, timeout=_FPS_PROBE_TIMEOUT_S),
            os.path.basename(path)).fps
    except (media_inspect.ProbeError, ValueError, TypeError, OSError):
        return subtitle_formats.DEFAULT_FPS
    if not (fps and 0 < fps <= _MAX_PLAUSIBLE_FPS):
        fps = subtitle_formats.DEFAULT_FPS
    with _fps_cache_lock:
        _fps_cache[key] = fps
        _fps_cache.move_to_end(key)
        while len(_fps_cache) > _FPS_CACHE_MAX:
            _fps_cache.popitem(last=False)
    return fps


def _require_exportable_text(lines, field: str):
    if not lines:
        raise ConflictError("This title has no subtitle lines to export.")
    wanted = "zh" if field == "zh" else "en"
    if not any((getattr(ln, wanted, "") or "").strip() for ln in lines):
        raise ConflictError("This title has no translated lines to export yet." if wanted == "en"
                            else "This title has no original-language text to export.")


def generate_subtitle_text(drama_id: int, fmt: str, field: str, **options) -> str:
    """The text of generate_subtitle_export, for callers that don't need
    the adjusted-cue count."""
    return generate_subtitle_export(drama_id, fmt, field, **options)["text"]


def generate_subtitle_export(drama_id: int, fmt: str, field: str,
                             include_notes: bool = False,
                             wrap_chars_en: Optional[int] = None,
                             wrap_chars_source: Optional[int] = None,
                             tidy_timing: bool = False,
                             fps: Optional[float] = None) -> dict:
    """Generates SRT, VTT or LRC subtitle text for one drama -- pure and
    read-only: never writes to the database, never flags a line, never
    writes a file to disk. The caller decides what to do with the
    returned text (e.g. serve it as a download).

    fmt: "srt", "vtt" or "lrc". field: "en", "zh", or "bilingual" (both formats
    support all three -- see subtitle_formats.lines_to_vtt/core.
    lines_to_srt/lines_to_bilingual_srt). SRT and LRC get overlapping cues
    trimmed first (subtitle_formats.clamp_overlaps) because they cannot show
    two cues at once; VTT keeps the saved times, as ASS does.

    include_notes folds in this drama's saved translation notes
    (db.list_translation_notes), appended inline -- ASS's separate-note-line
    option is not modeled here (use
    generate_ass_text for ASS).
    wrap_chars_en/wrap_chars_source optionally cap characters per line
    (subtitle_formats.wrap_lines); None on either side leaves that
    language unwrapped.

    tidy_timing (SRT and bilingual SRT only, default off) runs
    subtitle_formats.tidy_cue_timing at `fps`; without one, the frame rate
    ffprobe reads from the title's video (bounded timeout), else 24: it is
    not stored on the title. The fps used comes back as "fps" (None when
    nothing was tidied). VTT and ASS keep the saved times, LRC the
    overlap-clamped ones. Returns {"text",
    "cues_extended", "cues_trimmed", "cues_left_long" (cue numbers), "fps"}.

    Raises NotFoundError for an unknown drama id, InvalidInputError for
    an unknown fmt/field.
    """
    if fmt not in _SUBTITLE_FORMATS:
        raise InvalidInputError(f"Unknown subtitle format {fmt!r}.")
    if field not in _SUBTITLE_FIELDS:
        raise InvalidInputError(f"Unknown subtitle field {field!r}.")

    drama = ownership_service.require_drama(drama_id)

    rows = db.load_lines(drama_id)
    # Cues in time order: a retimed line can start before the one listed
    # above it, and players, cue numbers and the timing tidy go by order.
    lines = sorted(core_module.lines_from_rows(rows), key=lambda ln: ln.start)
    _require_exportable_text(lines, field)
    export_lines, _ = subtitle_formats.clamp_overlaps(lines)

    wrap_chars = build_wrap_chars(wrap_chars_en, wrap_chars_source)
    notes_by_idx = _load_notes_by_idx(drama_id) if include_notes else None

    if fmt == "vtt":
        return _export_result(subtitle_formats.lines_to_vtt(
            lines, field, notes_by_idx, wrap_chars, **break_language(drama)))
    if fmt == "lrc":
        return _export_result(subtitle_formats.lines_to_lrc(
            export_lines, field, notes_by_idx, wrap_chars, **break_language(drama)))

    if tidy_timing and fps is None:
        fps = _probe_title_fps(drama_id, drama)
    timed, report = (subtitle_formats.tidy_cue_timing(export_lines, fps) if tidy_timing
                     else (export_lines, None))
    wrapped = subtitle_formats.wrap_lines(timed, wrap_chars, **break_language(drama))
    if field == "bilingual":
        text = core_module.lines_to_bilingual_srt(wrapped, notes_by_idx=notes_by_idx)
    else:
        text = core_module.lines_to_srt(wrapped, field, notes_by_idx=notes_by_idx)
    return _export_result(text, report, fps if tidy_timing else None)


def save_flags(drama_id: int, lines, guard_fields, with_next: bool = False) -> set:
    """Writes flag/flag_note only where the database still holds what was
    read, flag and `guard_fields` alike -- and, with_next, the next line's
    `guard_fields` too, for a flag computed from it. `lines` are the whole
    title in order. Returns the ids left unwritten."""
    following = {a.id: [b] for a, b in zip(lines, lines[1:])} if with_next else None
    return db.save_lines(drama_id, lines, fields=("flag", "flag_note"), only_if_unchanged=True,
                         guard_fields=guard_fields, guard_neighbours=following)


def flag_overlapping_lines(drama_id: int) -> dict:
    """Flags every currently-overlapping, not-yet-flagged line for review
    (subtitle_formats.OVERLAP_FLAG). A line already flagged for some other
    reason is left alone (`not ln.flag`). Writes only if there's something new to flag.
    Raises NotFoundError for an unknown drama id. Returns
    {"flagged_count": int} -- 0 is not an error, just nothing to do."""
    _, lines = _load_drama_and_lines(drama_id)

    _, overlaps = subtitle_formats.clamp_overlaps(lines)
    next_start = {a.idx: b.start for a, b in zip(lines, lines[1:])}
    unflagged = [ln for ln in lines if ln.idx in overlaps and not ln.flag]
    for ln in unflagged:
        ln.flag = subtitle_formats.OVERLAP_FLAG
        ln.flag_note = subtitle_formats.overlap_note(ln, next_start[ln.idx])

    kept = save_flags(drama_id, lines, ("start", "end"), with_next=True) if unflagged else set()
    return {"flagged_count": sum(1 for ln in unflagged if ln.id not in kept)}


def flag_dense_lines(drama_id: int) -> dict:
    """Flags every line too dense to read in its on-screen time
    (subtitle_formats.flag_dense_lines) at the title's reading-speed mode.
    A line already flagged for some other reason is left alone.
    Raises NotFoundError for an unknown drama id. Returns
    {"flagged_count": int}."""
    drama, lines = _load_drama_and_lines(drama_id)

    unflagged = [ln for ln in lines if not ln.flag]
    newly_flagged = subtitle_formats.flag_dense_lines(
        lines, mode=subtitle_formats.flagging_mode_of(drama),
        dismissed=reading_speed_dismissal_service.load_dismissed(drama_id))
    if newly_flagged:
        kept = save_flags(drama_id, lines, ("en", "start", "end"), with_next=True)
        newly_flagged = sum(1 for ln in unflagged if ln.flag and ln.id not in kept)
    return {"flagged_count": newly_flagged}


def get_reading_speed_mode(drama_id: int) -> dict:
    """{"mode": ...} for the title. Raises NotFoundError for an unknown drama id."""
    drama = ownership_service.require_drama(drama_id)
    return {"mode": subtitle_formats.reading_speed_mode_of(drama)}


def set_reading_speed_mode(drama_id: int, mode: str) -> dict:
    """Saves the title's reading-speed check strictness. Existing flags are
    left as they are: flag_dense_lines never replaces a flag, so a stricter
    or looser mode only applies to lines flagged from now on (see
    clear_reading_speed_flags to re-check)."""
    if mode not in subtitle_formats.READING_SPEED_MODES:
        raise InvalidInputError(
            "mode must be one of " + ", ".join(subtitle_formats.READING_SPEED_MODES) + ".")
    ownership_service.require_drama(drama_id)
    db.update_drama(drama_id, reading_speed_mode=mode)
    return {"mode": mode}


READING_SPEED_CLEAR_LABEL = "before clearing reading-speed flags"


def clear_reading_speed_flags(drama_id: int, recheck: bool = False) -> dict:
    """Clears the flag and its note on every line whose flag is exactly
    reading_speed, after a "before clearing reading-speed flags" snapshot
    (undo from the line history). Other flags, notes and text are untouched.
    recheck=True then flags again at the title's current mode, so a changed
    setting takes effect on lines that were flagged before. Raises
    NotFoundError for an unknown drama id, ConflictError while a job runs.
    Returns {"cleared_count", "flagged_count", "history_id"}; history_id is
    None when there was nothing to clear."""
    with restructure_service.exclusive_write(drama_id):
        drama, lines = _load_drama_and_lines(drama_id)
        targets = [ln for ln in lines if ln.flag == subtitle_formats.READING_SPEED_FLAG]
        history_id = None
        if targets:
            history_id = db.save_line_history_snapshot(drama_id, lines, READING_SPEED_CLEAR_LABEL)
            for ln in targets:
                ln.flag = None
                ln.flag_note = ""
        unflagged = [ln for ln in lines if not ln.flag]
        reflagged = (subtitle_formats.flag_dense_lines(
            lines, mode=subtitle_formats.flagging_mode_of(drama),
            dismissed=reading_speed_dismissal_service.load_dismissed(drama_id)) if recheck else 0)
        cleared = len(targets)
        if targets or reflagged:
            # The drama lock doesn't keep out single-line edits (they don't take it).
            kept = save_flags(drama_id, lines, ("en", "start", "end"), with_next=recheck)
            cleared = sum(1 for ln in targets if ln.id not in kept)
            reflagged = sum(1 for ln in unflagged if ln.flag and ln.id not in kept)
    return {"cleared_count": cleared, "flagged_count": reflagged, "history_id": history_id}


def run_auto_qc_flagging(drama_id: int) -> dict:
    """Runs Auto QC's factual-detail check over this drama's lines and
    updates flags in place, with the glossary-name-list/banned-terms inputs
    from db.list_glossary_terms/db.list_series_characters (the same inputs
    get_export_readiness counts with). Raises
    NotFoundError for an unknown drama id. Returns
    {"flagged", "cleared", "already_flagged", "checked"} -- see
    auto_qc.run_auto_qc's own docstring for exactly what each counts."""
    drama, lines = _load_drama_and_lines(drama_id)

    series_id = drama.get("series_id")
    glossary_terms = db.list_glossary_terms(series_id) if series_id else []
    names = auto_qc.build_name_list(
        glossary_terms, db.list_series_characters(series_id) if series_id else [])
    banned_terms = auto_qc.build_banned_terms(glossary_terms)

    before = {ln.id: ln.flag for ln in lines}
    result = auto_qc.run_auto_qc(lines, names, banned_terms)
    if result["flagged"] or result["cleared"]:
        kept = save_flags(drama_id, lines, ("zh", "en"))
        for ln in lines:
            if ln.id in kept and ln.flag == auto_qc.AUTO_QC_FLAG:
                result["flagged"] -= 1
            elif ln.id in kept and before[ln.id] == auto_qc.AUTO_QC_FLAG and not ln.flag:
                result["cleared"] -= 1
    return result


# ebooklib and the packages it imports itself: a gap in any of them is a missing install,
# and the name shown must be the one that is actually missing.
_EPUB_PACKAGES = ("ebooklib", "lxml", "six")


def _log_epub_import_failure(exc: ImportError) -> None:
    """Type and module name only: the message and path can hold a filesystem path."""
    try:
        import applog
        applog.get_logger().warning("EPUB export could not import: %s (module %r)",
                                    type(exc).__name__, getattr(exc, "name", None))
    except Exception:
        pass


def generate_epub(drama_id: int, field: str = "en") -> bytes:
    """Exports one novel-narration drama's lines as an .epub
    (`epub_io.export_epub`). Read-only from the caller's point of view
    (returns bytes to serve as a download). Each call builds its own
    temporary file in the drama's folder and removes it, so two downloads
    at once (the translation and the original) never read each other's
    book. [[IMG:...]] placeholders resolve against the drama's
    `epub_images` folder.

    field: "en" for the translation, "zh" for the raw source text.

    Raises NotFoundError for an unknown drama id, UnsupportedOperationError
    if the drama isn't in novel-narration mode (this only makes sense for
    novel content, not audio/video dramas), InvalidInputError for an
    unknown field, and DependencyUnavailableError if `ebooklib` isn't
    installed (`pip install ebooklib`)."""
    if field not in _EPUB_FIELDS:
        raise InvalidInputError(f"Unknown EPUB field {field!r}.")

    drama, lines = _load_drama_and_lines(drama_id)
    if drama.get("content_mode") != "novel_narration":
        raise UnsupportedOperationError(
            f"Drama {drama_id} isn't in novel-narration mode -- EPUB export only "
            "applies to novel content.")
    # Otherwise an untranslated novel downloads as a book of empty chapters.
    _require_exportable_text(lines, field)

    import epub_io

    ddir = db.drama_dir(drama_id)
    title = drama.get("title_en") or drama.get("title_zh") or "Untitled"
    fd, out_path = tempfile.mkstemp(prefix=".epub_export_", suffix=".part", dir=ddir)
    os.close(fd)
    try:
        epub_io.export_epub(lines, title, drama.get("author", ""), out_path, field=field,
                            images_dir=os.path.join(ddir, "epub_images"),
                            source_language=drama.get("source_language"))
        with open(out_path, "rb") as f:
            return f.read()
    except ImportError as exc:
        # ebooklib is imported inside export_epub, not at epub_io's import time.
        package = (exc.name or "").split(".")[0]
        _log_epub_import_failure(exc)
        if package not in _EPUB_PACKAGES:
            raise  # any other broken import is a bug (500 with a traceback), not a missing install
        raise DependencyUnavailableError(
            f"EPUB export needs the {package} package, which isn't installed. "
            "Install it in Diagnostics > Packages.",
            details={"reason": "missing_package", "package": package}) from None
    finally:
        with contextlib.suppress(OSError):
            os.remove(out_path)


def mark_exported(drama_id: int) -> dict:
    """The Export stage's "Mark as exported": sets only the drama's status
    to "exported" (no other field is touched). Raises NotFoundError."""
    ownership_service.require_drama(drama_id)
    db.update_drama(drama_id, status="exported")
    return {"drama_id": drama_id, "status": "exported"}
