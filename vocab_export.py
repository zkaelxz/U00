"""
vocab_export.py -- turns the Reader's persisted click-to-define
history (db.vocab_lookups) into files you can actually study from:
a plain CSV (works with Anki's "Import File" with no extra deps), or
a proper .apkg deck (needs `pip install genanki`) that opens directly
in Anki with fields already mapped.

export_vocab_apkg_sentence() is the richer companion: a second,
opt-in card type with the source sentence and (for a drama with a real
audio track) an embedded audio clip, instead of just the isolated word.
"""

import csv
import io
import os
import tempfile
import time

from core import extract_audio_slice as _extract_audio_slice


def export_vocab_csv(vocab_rows) -> str:
    """vocab_rows: list of dicts from db.list_vocab_lookups().
    Returns CSV text: front = word + reading, back = definitions."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Front", "Back"])
    for row in vocab_rows:
        reading = f" ({row['reading']})" if row.get("reading") else ""
        front = f"{row['word']}{reading}"
        back = "; ".join(row.get("definitions") or [])
        writer.writerow([front, back])
    return buf.getvalue()


def export_vocab_apkg(vocab_rows, deck_name: str, out_path: str):
    """Requires `pip install genanki`. Produces a real .apkg file that
    opens directly in Anki with Front/Back fields already set up --
    nicer than CSV import but needs the extra dependency."""
    import genanki
    import random

    model_id = random.randrange(1 << 30, 1 << 31)
    deck_id = random.randrange(1 << 30, 1 << 31)

    model = genanki.Model(
        model_id, "Baihe Subtitler Vocab",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "{{Front}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Back}}',
        }],
    )
    deck = genanki.Deck(deck_id, deck_name)
    for row in vocab_rows:
        reading = f" ({row['reading']})" if row.get("reading") else ""
        front = f"{row['word']}{reading}"
        back = "; ".join(row.get("definitions") or [])
        deck.add_note(genanki.Note(model=model, fields=[front, back]))

    genanki.Package(deck).write_to_file(out_path)
    return out_path


def export_vocab_apkg_sentence(vocab_rows, lines, deck_name: str, out_path: str,
                                audio_path: str = None, clip_timeout: float = None,
                                audio_budget_seconds: float = None):
    """Requires `pip install genanki`. Richer companion to
    export_vocab_apkg(): one card per vocab row, front = the full source
    sentence the word was looked up in (plus an embedded audio clip of
    that line, when `audio_path` is given), back = the line's
    translation + the word's definition + its reading.

    `lines`: the drama's line list (objects with `.idx`/`.zh`/`.en`/
    `.start`/`.end`, e.g. core.lines_from_rows()'s output) -- used to
    resolve each vocab row's `first_seen_line_idx` back to its sentence.
    A row whose line can't be found (deleted/re-indexed since the
    lookup was saved) is skipped rather than producing a sentence-less
    card.

    `audio_path`: the drama's own source audio/video file. Pass None
    for a novel, or any drama with no real audio track -- those cards
    come out sentence-only, cleanly, not with a missing/broken sound
    reference.

    `clip_timeout`: per-clip ffmpeg timeout in seconds (None = none).
    `audio_budget_seconds`: once this much time has gone on clips, the
    remaining cards come out sentence-only (None = no budget).
    """
    import genanki
    import random

    by_idx = {ln.idx: ln for ln in lines}
    has_audio = bool(audio_path and os.path.exists(audio_path))
    tmp_dir = tempfile.mkdtemp(prefix="baihe_anki_") if has_audio else None

    model_id = random.randrange(1 << 30, 1 << 31)
    deck_id = random.randrange(1 << 30, 1 << 31)
    model = genanki.Model(
        model_id, "Baihe Subtitler Vocab (sentence)",
        fields=[{"name": "Sentence"}, {"name": "Back"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "{{Sentence}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Back}}',
        }],
    )
    deck = genanki.Deck(deck_id, deck_name)
    media_files = []
    audio_deadline = (time.monotonic() + audio_budget_seconds
                      if audio_budget_seconds is not None else None)

    for row in vocab_rows:
        line = by_idx.get(row.get("first_seen_line_idx"))
        if line is None:
            continue
        reading = f" ({row['reading']})" if row.get("reading") else ""
        word_label = f"{row['word']}{reading}"
        definitions = "; ".join(row.get("definitions") or [])
        translation = line.en or ""
        back = f"{translation}<br><br><b>{word_label}</b>: {definitions}"

        sentence = line.zh or ""
        if (has_audio and line.end and line.end > (line.start or 0)
                and (audio_deadline is None or time.monotonic() < audio_deadline)):
            clip_name = f"clip_{row.get('id')}.wav"
            clip_path = os.path.join(tmp_dir, clip_name)
            try:
                _extract_audio_slice(audio_path, line.start, line.end, clip_path,
                                     **({"timeout": clip_timeout} if clip_timeout else {}))
                media_files.append(clip_path)
                sentence = f"{sentence}[sound:{clip_name}]"
            except Exception:
                pass  # this one card just comes out without an audio clip

        deck.add_note(genanki.Note(model=model, fields=[sentence, back]))

    package = genanki.Package(deck)
    if media_files:
        package.media_files = media_files
    try:
        package.write_to_file(out_path)
    finally:
        if tmp_dir:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_path
