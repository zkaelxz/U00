"""The video site's own captions saved with a downloaded title: kept as hints, or used as the lines."""
import contextlib
import os

import db
import subtitle_parse
from core import Line
from lib.errors import InvalidInputError

FILE_NAME = "site_captions.vtt"
# A day of auto captions is a few hundred KB; anything bigger is not a caption track.
MAX_BYTES = 2 * 1024 * 1024
# A spoken line is never this long; a cue that is came from a hostile or broken file, and the
# repeat check below costs time in proportion to its length.
MAX_CUE_CHARS = 500
# Whisper's prompt only reads its tail (about 224 tokens), and a longer one is paid for in every request.
ASR_HINT_CHARS = 400
BRIEF_REFERENCE_CHARS = 3000
_EXTENSIONS = (".vtt", ".srt")


def pick(tmp_dir: str, langs) -> str | None:
    """The first caption file in `tmp_dir` for the earliest of `langs` that has one, else None."""
    names = os.listdir(tmp_dir)
    for lang in langs:
        for ext in _EXTENSIONS:
            for name in sorted(names):
                path = os.path.join(tmp_dir, name)
                if (name.startswith("downloaded_") and name.endswith(f".{lang}{ext}")
                        and os.path.isfile(path) and not os.path.islink(path)
                        and os.path.getsize(path) <= MAX_BYTES):
                    return path
    return None


def save(drama_id: int, src_path: str) -> bool:
    """Keeps the file as the title's site captions (as WebVTT, whatever it came as). False when it
    holds no readable cue, so the title keeps what it had."""
    with open(src_path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    try:
        cues = _cleaned(subtitle_parse.parse_subtitle(data, os.path.basename(src_path)).cues)
    except subtitle_parse.SubtitleParseError:
        return False
    if not cues:
        return False
    rows = ["WEBVTT", ""] + [f"{_stamp(c.start)} --> {_stamp(c.end)}\n{c.text}\n" for c in cues]
    dest = os.path.join(db.drama_dir(drama_id), FILE_NAME)
    tmp = dest + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(rows))
    os.replace(tmp, dest)
    return True


def _stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    return f"{h:02d}:{m:02d}:{rest // 1000:02d}.{rest % 1000:03d}"


def _repeated(before: str, text: str) -> int:
    """How many leading characters of `text` repeat the end of `before`. A short overlap is chance
    (a reply of a few characters often repeats the last ones), so it counts only from 4 characters."""
    for k in range(min(len(before), len(text), MAX_CUE_CHARS), 3, -1):
        if before.endswith(text[:k]):
            return k
    return 0


def _cleaned(cues) -> list:
    """Cues without the rolling repeats machine captions have: each cue shows the line before it
    again, so a cue inside the previous text is dropped and one that extends it keeps only the new part."""
    out, shown = [], ""
    for cue in cues:
        text = " ".join(cue.text.split())
        before, shown = shown, text
        if not text or len(text) > MAX_CUE_CHARS or text == before or (len(text) >= 4 and text in before):
            continue
        text = text[_repeated(before, text):].strip()
        out.append(subtitle_parse.Cue(cue.start, cue.end, text, len(out) + 1))
    return out


def _read(drama_id: int) -> list:
    path = os.path.join(db.drama_dir(drama_id), FILE_NAME)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "rb") as f:
            return subtitle_parse.parse_subtitle(f.read(MAX_BYTES + 1), FILE_NAME).cues
    except (OSError, subtitle_parse.SubtitleParseError):
        return []


def remove(drama_id: int) -> None:
    """Forgets the saved captions: they belong to the media they came with."""
    with contextlib.suppress(OSError):
        os.remove(os.path.join(db.drama_dir(drama_id), FILE_NAME))


def has_captions(drama_id: int) -> bool:
    return os.path.isfile(os.path.join(db.drama_dir(drama_id), FILE_NAME))


def hint_text(drama_id: int, max_chars: int) -> str:
    """The captions' words as one run of text from the start, for a prompt; "" when the title has none."""
    return " ".join(c.text for c in _read(drama_id))[:max_chars].strip()


def use_as_lines(drama_id: int) -> int:
    """Makes the saved captions the title's lines (timing and text from the site, no recognition).
    Refuses a title that already has lines: this replaces nothing. Returns the line count."""
    cues = _read(drama_id)
    if not cues:
        raise InvalidInputError("This title has no site captions saved.")
    if db.load_line_ids(drama_id):
        raise InvalidInputError("This title already has lines; the site's captions were not used.")
    db.save_lines(drama_id, [Line(idx=i, start=c.start, end=max(c.end, c.start + 0.1), zh=c.text)
                             for i, c in enumerate(cues)], expect_unchanged=[])
    db.update_drama(drama_id, status="aligned")
    return len(cues)
