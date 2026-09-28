"""Tests for services/lines_service.py (Review per-line writes, Slice 43)."""
import pytest

import db
from core import Line
from services import lines_service as svc
from services.service_errors import ConflictError, InvalidInputError, NotFoundError


@pytest.fixture
def save_spy(isolated_db, monkeypatch):
    """Records the `fields` of every db.save_lines call made by the service."""
    calls = []
    real = db.save_lines

    def spy(drama_id, lines, fields=None):
        calls.append(fields)
        return real(drama_id, lines, fields=fields)

    monkeypatch.setattr(db, "save_lines", spy)
    return calls


_real_save = db.save_lines  # unspied: seeding may full-sync


def _seed(series=False):
    kw = {"series_id": db.get_or_create_series("S")} if series else {}
    did = db.create_drama(title_en="D", **kw)
    _real_save(did, [
        Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="A"),
        Line(idx=1, start=1.0, end=2.0, zh="再见", en="Bye", flag="uncertain", flag_note="hmm"),
        Line(idx=2, start=2.0, end=3.0, zh="谢谢", en="Thanks"),
    ])
    return did, [r["id"] for r in db.load_lines(did)]


def _row(did, lid):
    return next(r for r in db.load_lines(did) if r["id"] == lid)


class TestPatch:
    def test_only_passed_fields_written_and_other_column_survives(self, save_spy):
        did, ids = _seed()
        # a concurrent writer changes `zh` after this caller could have read it
        db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="CHANGED", en="Hello",
                                 speaker="A", id=ids[0])], fields=("zh",))
        save_spy.clear()
        out = svc.patch_line(did, ids[0], en="Hi there")
        assert save_spy == [("en",)]
        row = _row(did, ids[0])
        assert row["en"] == "Hi there" and row["zh"] == "CHANGED"
        assert out["en"] == "Hi there" and out["id"] == ids[0]

    def test_timing_speaker_sfx(self, save_spy):
        did, ids = _seed()
        svc.patch_line(did, ids[0], start=0.5, end=1.5, sfx=True)
        row = _row(did, ids[0])
        assert (row["start"], row["end"], row["sfx"]) == (0.5, 1.5, 1)
        assert save_spy[-1] == ("start", "end", "sfx")

    def test_en_edit_clears_flag(self, save_spy):
        did, ids = _seed()
        svc.patch_line(did, ids[1], en="Goodbye")
        row = _row(did, ids[1])
        assert row["flag"] is None and not row["flag_note"]
        assert save_spy[-1] == ("en", "flag", "flag_note")

    def test_unchanged_en_keeps_flag(self, save_spy):
        did, ids = _seed()
        svc.patch_line(did, ids[1], en="Bye")
        assert _row(did, ids[1])["flag"] == "uncertain"
        assert save_spy[-1] == ("en",)

    def test_speaker_change_sets_manual(self, save_spy):
        did, ids = _seed()
        svc.patch_line(did, ids[0], speaker="B")
        row = _row(did, ids[0])
        assert row["speaker"] == "B" and row["speaker_manual"] == 1
        assert save_spy[-1] == ("speaker", "speaker_manual")

    def test_speaker_same_does_not_set_manual(self, save_spy):
        did, ids = _seed()
        svc.patch_line(did, ids[0], speaker="A")
        assert _row(did, ids[0])["speaker_manual"] == 0
        assert save_spy[-1] == ("speaker",)

    def test_speaker_blank_clears(self, isolated_db):
        did, ids = _seed()
        svc.patch_line(did, ids[0], speaker="")
        assert _row(did, ids[0])["speaker"] is None

    def test_edit_sample_and_tm_recorded(self, isolated_db):
        did, ids = _seed(series=True)
        svc.patch_line(did, ids[0], en="Hi there")
        samples = db.list_edit_samples(did)
        assert [(s["ai_version"], s["user_version"]) for s in samples] == [("Hello", "Hi there")]
        sid = db.get_drama(did)["series_id"]
        assert [(e["source_text"], e["translation"]) for e in db.list_translation_memory(sid)] \
            == [("你好", "Hi there")]

    def test_no_tm_without_series_and_no_sample_when_unchanged(self, isolated_db):
        did, ids = _seed()
        svc.patch_line(did, ids[0], en="Hi there")
        assert db.list_translation_memory(1) == []
        svc.patch_line(did, ids[2], en="Thanks")
        assert len(db.list_edit_samples(did)) == 1

    def test_stale_expected_conflicts_and_writes_nothing(self, save_spy):
        did, ids = _seed()
        with pytest.raises(ConflictError) as ei:
            svc.patch_line(did, ids[0], en="X", expected={"en": "Old text"})
        assert "Old text" not in ei.value.message
        assert save_spy == []
        assert _row(did, ids[0])["en"] == "Hello"

    def test_matching_expected_applies(self, isolated_db):
        did, ids = _seed()
        svc.patch_line(did, ids[0], en="X", expected={"en": "Hello", "start": 0.0, "speaker": "A"})
        assert _row(did, ids[0])["en"] == "X"

    def test_unknown_line_never_inserts(self, save_spy):
        did, ids = _seed()
        with pytest.raises(NotFoundError):
            svc.patch_line(did, 987654, en="X")
        assert len(db.load_lines(did)) == 3 and save_spy == []

    def test_merged_away_line_is_404(self, isolated_db):
        did, ids = _seed()
        keep = [Line(idx=0, start=0, end=1, zh="a", en="a", id=ids[0]),
                Line(idx=1, start=1, end=3, zh="b", en="b", id=ids[2])]
        db.save_lines(did, keep)  # full sync drops ids[1]
        with pytest.raises(NotFoundError):
            svc.patch_line(did, ids[1], en="X")
        assert len(db.load_lines(did)) == 2

    def test_cross_drama_line_is_404(self, isolated_db):
        did, ids = _seed()
        other, _ = _seed()
        with pytest.raises(NotFoundError):
            svc.patch_line(other, ids[0], en="X")
        assert _row(did, ids[0])["en"] == "Hello"

    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.patch_line(999, 1, en="X")

    @pytest.mark.parametrize("kw", [
        {}, {"start": -1}, {"end": 0.5, "start": 0.5}, {"end": 0.0}, {"start": 5},
        {"start": True}, {"start": float("nan")}, {"en": "x" * 2001}, {"speaker": "s" * 101},
        {"sfx": "yes"}, {"expected": {"flag": "x"}}, {"expected": [1]}, {"en": 5},
    ])
    def test_validation(self, isolated_db, kw):
        did, ids = _seed()
        with pytest.raises(InvalidInputError):
            svc.patch_line(did, ids[0], **kw)
        assert _row(did, ids[0])["en"] == "Hello"


class TestDismiss:
    def test_scope(self, save_spy):
        did, ids = _seed()
        out = svc.dismiss_flag(did, ids[1])
        assert save_spy == [("flag", "flag_note")]
        assert out["flag"] is None and not out["flag_note"]
        assert _row(did, ids[1])["en"] == "Bye"

    def test_unknown_line(self, isolated_db):
        did, _ = _seed()
        with pytest.raises(NotFoundError):
            svc.dismiss_flag(did, 123456)


class TestFindReplace:
    def test_applies_fresh_reports_stale(self, save_spy):
        did, ids = _seed(series=True)
        sid = db.get_drama(did)["series_id"]
        db.record_translation_memory(sid, "你好", "Hello")
        db.save_lines(did, [Line(idx=2, start=2, end=3, zh="谢谢", en="Thanks (edited)",
                                 id=ids[2])], fields=("en",))
        save_spy.clear()
        out = svc.apply_find_replace(did, [
            {"id": ids[0], "old_text": "Hello", "new_text": "Howdy"},
            {"id": ids[2], "old_text": "Thanks", "new_text": "Cheers"},
            {"id": 987654, "old_text": "a", "new_text": "b"},
        ])
        assert out["applied"] == 1 and out["stale"] == 2
        assert out["applied_ids"] == [ids[0]] and out["stale_ids"] == [ids[2], 987654]
        assert save_spy == [("en",)]
        assert _row(did, ids[0])["en"] == "Howdy"
        assert _row(did, ids[2])["en"] == "Thanks (edited)"
        assert db.list_translation_memory(sid)[0]["translation"] == "Howdy"
        assert len(db.load_lines(did)) == 3

    def test_all_stale_writes_nothing(self, save_spy):
        did, ids = _seed()
        out = svc.apply_find_replace(did, [{"id": ids[0], "old_text": "nope", "new_text": "x"}])
        assert out["applied"] == 0 and save_spy == []

    @pytest.mark.parametrize("matches", [
        "x", [{"id": "1", "old_text": "a", "new_text": "b"}], [{"id": 1, "old_text": 1, "new_text": "b"}],
        [{"id": 1, "old_text": "a", "new_text": "b"}] * 2, [{}] * 1001,
    ])
    def test_validation(self, isolated_db, matches):
        did, _ = _seed()
        with pytest.raises(InvalidInputError):
            svc.apply_find_replace(did, matches)

    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.apply_find_replace(999, [])


class TestTm:
    def _entry(self, sid, zh="你好", en="Howdy"):
        db.record_translation_memory(sid, zh, en)
        return db.list_translation_memory(sid)[-1]

    def test_accept(self, save_spy):
        did, ids = _seed(series=True)
        e = self._entry(db.get_drama(did)["series_id"])
        out = svc.accept_tm_suggestion(did, ids[0], e["id"])
        assert out["en"] == "Howdy" and save_spy == [("en",)]
        assert db.list_translation_memory(e["series_id"])[0]["use_count"] == e["use_count"] + 1

    def test_other_series_entry_is_404(self, isolated_db):
        did, ids = _seed(series=True)
        other = db.get_or_create_series("Other")
        e = self._entry(other)
        with pytest.raises(NotFoundError):
            svc.accept_tm_suggestion(did, ids[0], e["id"])
        assert _row(did, ids[0])["en"] == "Hello"
        assert db.list_translation_memory(other)[0]["use_count"] == e["use_count"]

    def test_no_series_is_404(self, isolated_db):
        did, ids = _seed()
        with pytest.raises(NotFoundError):
            svc.accept_tm_suggestion(did, ids[0], 1)


class TestNotes:
    def test_add_and_delete(self, isolated_db):
        did, ids = _seed()
        n = svc.add_note(did, ids[0], " 你好 ", "idiom", " a note ")
        assert n["term"] == "你好" and n["note"] == "a note" and n["line_id"] == ids[0]
        assert n["line_idx"] == 0
        assert svc.delete_note(did, n["id"]) == {"deleted": True, "note_id": n["id"]}
        assert db.list_translation_notes(did) == []

    def test_same_line_term_updates(self, isolated_db):
        did, ids = _seed()
        a = svc.add_note(did, ids[0], "t", "idiom", "one")
        b = svc.add_note(did, ids[0], "t", "cultural", "two")
        assert a["id"] == b["id"] and b["note"] == "two"
        assert len(db.list_translation_notes(did)) == 1

    def test_cross_drama_note_delete_is_404_and_kept(self, isolated_db):
        did, ids = _seed()
        other, _ = _seed()
        n = svc.add_note(did, ids[0], "t", "idiom", "x")
        with pytest.raises(NotFoundError):
            svc.delete_note(other, n["id"])
        assert len(db.list_translation_notes(did)) == 1

    def test_add_note_on_other_drama_line_is_404(self, isolated_db):
        did, ids = _seed()
        other, _ = _seed()
        with pytest.raises(NotFoundError):
            svc.add_note(other, ids[0], "t", "idiom", "x")
        assert db.list_translation_notes(other) == []

    @pytest.mark.parametrize("term,nt,note", [
        ("", "idiom", "x"), ("t", "idiom", " "), ("t", "bogus", "x"),
        ("t" * 501, "idiom", "x"), ("t", "idiom", "n" * 2001), (5, "idiom", "x"),
    ])
    def test_validation(self, isolated_db, term, nt, note):
        did, ids = _seed()
        with pytest.raises(InvalidInputError):
            svc.add_note(did, ids[0], term, nt, note)

    def test_unknown_drama_note(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.delete_note(999, 1)


class TestNeverFullSync:
    def test_every_write_is_field_scoped(self, save_spy):
        did, ids = _seed(series=True)
        sid = db.get_drama(did)["series_id"]
        db.record_translation_memory(sid, "你好", "Howdy")
        e = db.list_translation_memory(sid)[0]
        svc.patch_line(did, ids[0], en="a", zh="b", start=0.1, end=0.9, speaker="Z", sfx=True)
        svc.dismiss_flag(did, ids[1])
        svc.apply_find_replace(did, [{"id": ids[2], "old_text": "Thanks", "new_text": "T"}])
        svc.accept_tm_suggestion(did, ids[0], e["id"])
        svc.add_note(did, ids[0], "t", "idiom", "n")
        assert save_spy and all(f is not None and isinstance(f, tuple) for f in save_spy)
