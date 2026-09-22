"""
vocab_export.py -- turns the Reader's persisted click-to-define
history (db.vocab_lookups) into files you can actually study from:
a plain CSV (works with Anki's "Import File" with no extra deps), or
a proper .apkg deck (needs `pip install genanki`) that opens directly
in Anki with fields already mapped.
"""

import csv
import io


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
