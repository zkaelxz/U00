"""
core.py -- shared pipeline logic with NO UI dependency, so it can be
imported by the API services and background jobs without pulling in a
UI framework.
"""

from dataclasses import dataclass, field, replace

import source_change
from lib.atomic_write import atomic_write  # noqa: F401 -- re-exported: callers use core.atomic_write
from segment_splitting import words_for_text


# The source languages the app handles, and the full names LLM and aligner
# prompts use for them. Callers fall back to "Chinese" for anything else.
SOURCE_LANGUAGES = ("zh", "ja", "ko")
LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}
# What one line's spoken language (Line.lang) may be: a title can mix
# speakers of several languages, English among them.
LINE_LANGUAGES = SOURCE_LANGUAGES + ("en",)
# The three-letter (ISO 639-2) tags media containers and Jellyfin read.
ISO_639_2 = {"en": "eng", "zh": "chi", "ja": "jpn", "ko": "kor"}


# The per-line columns db.save_lines writes. `idx` is the line's current
# position (display order) -- it changes on every merge/split; `id` is the
# permanent identity notes, emotions and background jobs attach to.
LINE_FIELDS = ("idx", "start", "end", "zh", "en", "speaker", "dub_filename", "flag", "flag_note",
               "speaker_manual", "sfx", "lang", "reviewed")


@dataclass
class Line:
    idx: int
    start: float
    end: float
    zh: str
    en: str = ""
    speaker: str | None = None
    dub_filename: str | None = None
    flag: str | None = None       # a key from engine_backends.flag_reasons.FLAG_REASONS / SYSTEM_FLAG_REASONS, or None
    flag_note: str = ""    # brief reason from flag_uncertain_lines, e.g. "ambiguous 'her'"
    # True once someone set this line's speaker by hand -- re-running
    # speaker detection (diarize.merge_speakers) leaves it alone unless
    # told to overwrite corrections.
    speaker_manual: bool = False
    # A non-verbal/SFX cue ("door slams") rather than dialogue --
    # exported bracketed and styled apart from speech (see sfx_cue_text).
    sfx: bool = False
    # This line's spoken language, a LINE_LANGUAGES code; None means the
    # title's source_language, so titles saved before this field existed
    # behave exactly as before.
    lang: str | None = None
    # True once a reviewer marked the translation checked. Editing `en`
    # clears it: the checked text is gone, so the mark would lie.
    reviewed: bool = False
    # Permanent row id (lines.id). None for a line not saved yet.
    id: int | None = field(default=None, compare=False)
    # encode_line_words' stored form, read through line_words. None means "not
    # loaded": db.save_lines then leaves the column alone (and clears it when the
    # text changes), so a caller that never loads it can't wipe or keep stale words.
    word_timings: str | None = field(default=None, compare=False, repr=False)
    # Field values as last loaded from / saved to the database. db.save_lines
    # only writes a field whose value differs from this, so two writers
    # (a background job and the page) can't clobber each other's fields.
    # None means "unknown" -- every field is written.
    orig: dict | None = field(default=None, compare=False, repr=False)
    # Ids of lines merged into this one since the last save -- db.save_lines
    # re-points their notes/emotions here before deleting their rows.
    merged_ids: list = field(default_factory=list, compare=False, repr=False)


def normalize_line_lang(value):
    """A Line.lang value from outside (API body, CLI): None or "" -> None,
    a LINE_LANGUAGES code in any case -> that code lower-cased. Anything
    else raises InvalidInputError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    code = value.strip().lower() if isinstance(value, str) else None
    if code not in LINE_LANGUAGES:
        from lib.errors import InvalidInputError
        raise InvalidInputError(f"lang must be one of {', '.join(LINE_LANGUAGES)} or empty.",
                                details={"allowed": list(LINE_LANGUAGES)})
    return code


def _stored_line_lang(value):
    # A row from an imported backup (or a hand-edited database) may hold a
    # code this version doesn't know; reading it as the title's default is
    # safer than refusing to load the lines.
    code = value.strip().lower() if isinstance(value, str) else None
    return code if code in LINE_LANGUAGES else None


def line_from_row(row) -> "Line":
    """The one shared db row (db.load_lines dict) -> Line conversion. Carries
    id and every field through, so nothing (flags, speaker, dub clip) is
    silently dropped by a caller that forgets one, and records `orig` so a
    later save writes only what actually changed."""
    ln = Line(idx=row["idx"], start=row["start"], end=row["end"], zh=row.get("zh") or "",
              en=row.get("en") or "", speaker=row.get("speaker"),
              dub_filename=row.get("dub_filename"), flag=row.get("flag"),
              flag_note=row.get("flag_note") or "", speaker_manual=bool(row.get("speaker_manual")),
              sfx=bool(row.get("sfx")), lang=_stored_line_lang(row.get("lang")), id=row.get("id"),
              reviewed=bool(row.get("reviewed")), word_timings=row.get("word_timings"))
    ln.orig = {f: getattr(ln, f) for f in LINE_FIELDS}
    if "word_timings" in row:
        ln.orig["word_timings"] = ln.word_timings
    return ln


def lines_from_rows(rows) -> list:
    return [line_from_row(r) for r in rows]


# Fields a snapshot/version records since the undo fix; older ones lack them.
SAVED_MARK_FIELDS = ("flag", "flag_note", "sfx", "lang", "reviewed")


def adopt_ids(restored, current, recorded=()) -> list:
    """For restoring a saved snapshot/translation version over the current
    lines: gives each restored line the permanent id (and `orig`) of the
    current line it replaces -- by id when the snapshot recorded one, else
    by position (snapshots from before line ids existed have no ids) -- so notes and
    emotions stay attached instead of being deleted with the old rows.
    Fields a snapshot doesn't store (dub_filename in a version; flag,
    flag_note, sfx and lang in one saved before they were recorded) are carried
    over from the matched line rather than wiped. `recorded` names the
    SAVED_MARK_FIELDS the snapshot did store: those keep the snapshot's own
    value, since after a merge the matched line may hold another line's flag.

    Positional fallback only applies to a line whose snapshot never
    recorded an id at all (older snapshot). A line whose id *was* recorded but
    no longer resolves -- it was merged away since the snapshot was taken
    -- must not fall back to matching by position: idx numbering shifts
    after a merge, so that would silently reattach the snapshot's notes,
    emotions and flags to whatever unrelated line now sits at that
    position. Such a line gets a fresh id instead (below)."""
    by_id = {ln.id: ln for ln in current if getattr(ln, "id", None) is not None}
    by_idx = {ln.idx: ln for ln in current}
    used = set()
    for ln in restored:
        id_was_recorded = ln.id is not None
        match = by_id.get(ln.id) if id_was_recorded else None
        if match is not None and match.id in used:
            match = None
        if match is None and not id_was_recorded:
            match = by_idx.get(ln.idx)
            if match is not None and match.id in used:
                match = None
        if match is None:
            ln.id = None
            continue
        used.add(match.id)
        ln.id, ln.orig = match.id, match.orig
        for f in ("flag", "flag_note", "dub_filename", "lang"):
            if f not in recorded and getattr(ln, f) in (None, ""):
                setattr(ln, f, getattr(match, f))
        if "sfx" not in recorded:
            ln.sfx = ln.sfx or match.sfx
        if "reviewed" not in recorded:
            # Carried as today's value; the save unchecks it anyway if the English differs.
            ln.reviewed = match.reviewed
        # Older snapshots/versions didn't record
        # speaker_manual -- restoring the same speaker the line has now
        # keeps its hand-corrected mark instead of silently dropping it.
        if ln.speaker == match.speaker:
            ln.speaker_manual = ln.speaker_manual or match.speaker_manual
    return restored


def lines_from_saved(rows) -> list:
    """A saved line-history snapshot's or translation version's line dicts
    (db.get_line_history_snapshot / get_translation_version) -> Lines. A
    line's word timings come back only when they were stored for its text."""
    return [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r.get("zh") or "",
                 en=r.get("en") or "", speaker=r.get("speaker"),
                 dub_filename=r.get("dub_filename"), flag=r.get("flag") or None,
                 flag_note=r.get("flag_note") or "", sfx=bool(r.get("sfx")),
                 speaker_manual=bool(r.get("speaker_manual")),
                 lang=_stored_line_lang(r.get("lang")), id=r.get("id"),
                 reviewed=bool(r.get("reviewed")),
                 word_timings=words_for_text(r.get("word_timings"), r.get("zh") or ""))
            for r in rows]


def saved_matches_lines(rows, current) -> bool:
    """True when a saved snapshot/version was taken over exactly the current
    lines (same permanent ids) -- no merge, split or re-segmentation since."""
    saved_ids = [r.get("id") for r in rows]
    current_ids = [ln.id for ln in current]
    return (None not in saved_ids and None not in current_ids
            and len(saved_ids) == len(current_ids) and set(saved_ids) == set(current_ids))


def restore_saved_lines(rows, current, translation_only: bool = False) -> list:
    """The one restore path Workspace's Restore (a line-history snapshot)
    and Activate (a translation version) share, so the two can't drift.

    translation_only (Activate): when the version was saved over exactly
    the current lines, only each line's `en` changes -- speaker (and its
    hand-corrected mark), source text and timing stay as they are now,
    since activating a version picks a translation, not a rollback of the
    whole line. A version saved over a different line structure (merged,
    split, re-segmented since, or from before line ids existed) can only be
    restored whole, since its translations belong to its own lines."""
    if translation_only and saved_matches_lines(rows, current):
        en_by_id = {r["id"]: r.get("en") or "" for r in rows}
        return [replace(ln, en=en_by_id[ln.id]) for ln in current]
    recorded = {f for f in SAVED_MARK_FIELDS if rows and all(f in r for r in rows)}
    restored = adopt_ids(lines_from_saved(rows), current, recorded)
    if "flag" not in recorded:
        # adopt_ids carried today's flags over, but the source and English
        # come back together, so a source change no longer applies.
        for ln in restored:
            source_change.clear_for_new_english(ln)
    return restored


def fmt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def notes_suffix(line_idx: int, notes_by_idx: dict | None) -> str:
    """notes_by_idx: {line_idx: [{"term", "note"}, ...]}, from
    db.list_translation_notes() grouped by line -- see
    translation_guide.group_notes_by_line(). Renders as a bracketed
    aside appended to that line's subtitle text, e.g. "[Qijutang: lit.
    'Hall of Sitting Together', used as a joke]", so a reader watching
    the exported video (not just the in-app Reader) sees why a wordplay
    or reference reads the way it does, right where it happens.
    """
    if not notes_by_idx or line_idx not in notes_by_idx:
        return ""
    asides = "; ".join(f"{n['term']}: {n['note']}" for n in notes_by_idx[line_idx])
    return f"\n[{asides}]"


def sfx_cue_text(text: str, italic_tags: bool = True) -> str:
    """A non-verbal/SFX cue's subtitle text -- bracketed, so it
    reads as "[door slams]" rather than as something a character said.
    Already-bracketed text isn't double-bracketed. italic_tags wraps it in
    <i>...</i>, which SRT/VTT players (and an SRT burn-in) render; ASS
    passes False and styles the cue through its own "SFX" style instead."""
    text = (text or "").strip()
    if not text:
        return ""
    inner = text[1:-1].strip() if (text[0], text[-1]) in (("[", "]"), ("(", ")")) else text
    return f"<i>[{inner}]</i>" if italic_tags else f"[{inner}]"


def _srt_text(text: str) -> str:
    """A blank line ends a cue in SRT, so blank lines inside one are dropped."""
    return "\n".join(part for part in (text or "").splitlines() if part.strip())


def lines_to_srt(lines, field="en", notes_by_idx: dict | None = None) -> str:
    out = []
    for i, ln in enumerate(lines, start=1):
        text = getattr(ln, field)
        if getattr(ln, "sfx", False):
            text = sfx_cue_text(text)
        text = _srt_text(text + notes_suffix(ln.idx, notes_by_idx))
        out.append(f"{i}\n{fmt_ts(ln.start)} --> {fmt_ts(ln.end)}\n{text}\n")
    return "\n".join(out)


def lines_to_bilingual_srt(lines, notes_by_idx: dict | None = None) -> str:
    out = []
    for i, ln in enumerate(lines, start=1):
        en, zh = ln.en, ln.zh
        if getattr(ln, "sfx", False):
            en, zh = sfx_cue_text(en), sfx_cue_text(zh)
        en, zh = _srt_text(en), _srt_text(zh)
        text = f"{en}\n{zh}" if en else zh
        text = _srt_text(text + notes_suffix(ln.idx, notes_by_idx))
        out.append(f"{i}\n{fmt_ts(ln.start)} --> {fmt_ts(ln.end)}\n{text}\n")
    return "\n".join(out)


# Names that moved out of this module. Resolved on first use rather than
# imported here because line_alignment imports Line from this module, so an
# eager import would fail when line_alignment is imported first.
_MOVED_NAMES = {
    "whisper_hallucination": ("filter_hallucinated_segments",),
    "whisper_transcribe": (
        "MIN_SILENCE_MS_MIN", "MIN_SILENCE_MS_MAX", "DEFAULT_AUTOTUNE_CANDIDATES_MS",
        "WHISPER_REPEAT_GUARD_KWARGS", "DEFAULT_HALLUCINATION_SILENCE_SEC", "build_initial_prompt",
        "transcribe_for_timing", "GROQ_DEFAULT_MODEL", "GroqTranscriptionError",
        "transcribe_with_groq",
    ),
    "novel_text": (
        "chunk_novel_text", "novel_paragraph_ends", "extract_novel_excerpt_for_prompt",
        "combine_initial_prompt", "load_novel_text_for_context",
    ),
    "audio_extract": ("extract_audio_slice",),
    "line_alignment": (
        "merge_adjacent_short_lines", "split_user_transcript", "align_transcript_to_timing",
        "diagnose_line_coverage",
    ),
}


def __getattr__(name):
    import importlib
    for module, names in _MOVED_NAMES.items():
        if name in names:
            return getattr(importlib.import_module(module), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
