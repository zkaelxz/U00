"""
vocab_export's Anki decks with a stand-in for genanki, so these run on a
core-only install: card fields are HTML in Anki, so imported words,
definitions and sentences go in escaped; a failed clip or write never
leaves the clip folder behind.
"""
import os
import sys
import types

import pytest

import vocab_export


class _Line:
    def __init__(self, idx, zh, en, start, end):
        self.idx, self.zh, self.en, self.start, self.end = idx, zh, en, start, end


@pytest.fixture
def anki(monkeypatch, tmp_path):
    # A private temp folder, so another worker's clip folders are never counted.
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(vocab_export.tempfile, "tempdir", str(tmp_path / "tmp"))
    seen = {"notes": [], "media": None, "fail_write": False}
    mod = types.ModuleType("genanki")

    class Deck:
        def __init__(self, *_a):
            pass

        def add_note(self, note):
            seen["notes"].append(note.fields)

    class Package:
        def __init__(self, deck):
            self.media_files = []

        def write_to_file(self, path):
            if seen["fail_write"]:
                raise OSError("disk full")
            seen["media"] = [os.path.exists(p) for p in self.media_files]
            with open(path, "wb") as fh:
                fh.write(b"apkg")

    mod.Model = lambda *a, **k: object()
    mod.Note = lambda model, fields: types.SimpleNamespace(fields=fields)
    mod.Deck, mod.Package = Deck, Package
    monkeypatch.setitem(sys.modules, "genanki", mod)
    return seen


HOSTILE = '<img src=x onerror="alert(1)">'


def test_word_cards_escape_markup(anki, tmp_path):
    rows = [{"word": HOSTILE, "reading": "<b>r</b>", "definitions": ["a & b", "<script>x</script>"]}]
    vocab_export.export_vocab_apkg(rows, "D", str(tmp_path / "v.apkg"))
    (front, back), = anki["notes"]
    assert "<img" not in front and "&lt;img" in front and "&lt;b&gt;r&lt;/b&gt;" in front
    assert back == "a &amp; b; &lt;script&gt;x&lt;/script&gt;"


def test_sentence_cards_escape_the_text_but_keep_their_own_layout(anki, tmp_path):
    rows = [{"id": 1, "word": "<i>w</i>", "reading": None, "definitions": ["<u>d</u>"],
             "first_seen_line_idx": 0}]
    lines = [_Line(0, f"zh {HOSTILE}", "<script>en</script>", 0, 0)]
    vocab_export.export_vocab_apkg_sentence(rows, lines, "D", str(tmp_path / "s.apkg"))
    (front, back), = anki["notes"]
    assert "<img" not in front and "&lt;img" in front
    assert back == ("&lt;script&gt;en&lt;/script&gt;<br><br><b>&lt;i&gt;w&lt;/i&gt;</b>: "
                    "&lt;u&gt;d&lt;/u&gt;")


def _audio_rows(tmp_path):
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"audio")
    rows = [{"id": i, "word": f"w{i}", "reading": None, "definitions": [],
             "first_seen_line_idx": i} for i in range(3)]
    lines = [_Line(i, f"s{i}", f"e{i}", float(i), float(i) + 1) for i in range(3)]
    return str(audio), rows, lines


def _clip_dirs():
    import tempfile
    return {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("baihe_anki_")}


def test_a_failed_clip_is_counted_and_the_card_kept_without_sound(anki, tmp_path, monkeypatch):
    audio, rows, lines = _audio_rows(tmp_path)

    def clip(src, start, end, out, **_k):
        if start == 1.0:
            raise RuntimeError("ffmpeg failed")
        with open(out, "wb") as fh:
            fh.write(b"clip")
    monkeypatch.setattr(vocab_export, "_extract_audio_slice", clip)
    report = {}
    assert _clip_dirs() == set()
    vocab_export.export_vocab_apkg_sentence(rows, lines, "D", str(tmp_path / "s.apkg"),
                                            audio_path=audio, report=report)
    assert report == {"clips_missing": 1}
    assert ["[sound:" in front for front, _ in anki["notes"]] == [True, False, True]
    assert anki["media"] == [True, True]
    assert _clip_dirs() == set()


def test_a_spent_audio_budget_leaves_the_rest_without_clips(anki, tmp_path, monkeypatch):
    audio, rows, lines = _audio_rows(tmp_path)
    monkeypatch.setattr(vocab_export, "_extract_audio_slice",
                        lambda src, s, e, out, **_k: open(out, "wb").close())
    report = {}
    vocab_export.export_vocab_apkg_sentence(rows, lines, "D", str(tmp_path / "s.apkg"),
                                            audio_path=audio, audio_budget_seconds=0,
                                            report=report)
    assert report == {"clips_missing": 3}
    assert not any("[sound:" in front for front, _ in anki["notes"])


@pytest.mark.parametrize("where", ["write", "card"])
def test_a_failure_part_way_removes_the_clip_folder(anki, tmp_path, monkeypatch, where):
    audio, rows, lines = _audio_rows(tmp_path)
    monkeypatch.setattr(vocab_export, "_extract_audio_slice",
                        lambda src, s, e, out, **_k: open(out, "wb").close())
    if where == "write":
        anki["fail_write"] = True
    else:
        rows[1] = {"id": 1, "first_seen_line_idx": 1}      # no word: the card can't be built
    with pytest.raises((OSError, KeyError)):
        vocab_export.export_vocab_apkg_sentence(rows, lines, "D", str(tmp_path / "s.apkg"),
                                                audio_path=audio)
    assert _clip_dirs() == set()
    assert not (tmp_path / "s.apkg").exists()
