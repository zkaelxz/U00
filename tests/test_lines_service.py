"""Tests for services/lines_service.py (Review per-line writes, Slice 43)."""
import pytest

import db
from core import Line
from services import lines_service as svc
from lib.errors import ConflictError, InvalidInputError, NotFoundError


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

    def test_edit_sample_recorded_but_unchecked_save_teaches_no_memory(self, isolated_db):
        did, ids = _seed(series=True)
        svc.patch_line(did, ids[0], en="Hi there")
        samples = db.list_edit_samples(did)
        assert [(s["ai_version"], s["user_version"]) for s in samples] == [("Hello", "Hi there")]
        assert db.list_translation_memory(db.get_drama(did)["series_id"]) == []

    def test_checking_after_a_save_records_the_saved_english(self, isolated_db):
        did, ids = _seed(series=True)
        svc.patch_line(did, ids[0], en="Hi there")
        svc.set_reviewed(did, ids[0], True)
        sid = db.get_drama(did)["series_id"]
        assert [(e["source_text"], e["translation"]) for e in db.list_translation_memory(sid)] \
            == [("你好", "Hi there")]

    def test_editing_a_checked_line_unchecks_it_and_checking_again_replaces_the_entry(self, isolated_db):
        did, ids = _seed(series=True)
        sid = db.get_drama(did)["series_id"]
        svc.set_reviewed(did, ids[0], True)
        svc.patch_line(did, ids[0], en="Hi there")
        assert not _row(did, ids[0])["reviewed"]
        assert [e["translation"] for e in db.list_translation_memory(sid)] == ["Hello"]
        svc.set_reviewed(did, ids[0], True)
        assert [e["translation"] for e in db.list_translation_memory(sid)] == ["Hi there"]

    def test_unchecking_keeps_the_memory_entry(self, isolated_db):
        did, ids = _seed(series=True)
        svc.set_reviewed(did, ids[0], True)
        svc.set_reviewed(did, ids[0], False)
        assert len(db.list_translation_memory(db.get_drama(did)["series_id"])) == 1

    def test_edit_sample_records_what_made_the_corrected_text(self, isolated_db):
        from services import line_provenance_service
        did, ids = _seed()
        db.save_lines(did, [Line(idx=1, start=1.0, end=2.0, zh="再见", en="Bye", flag="uncertain",
                                 flag_note="hmm", mark="wrong", id=ids[1])], fields=("mark",))
        line_provenance_service.record(did, {ids[1]: ("再见", "Bye")}, "claude", "claude-x", "6", "")
        svc.patch_line(did, ids[1], en="Goodbye")
        [s] = db.list_edit_samples(did)
        assert (s["line_id"], s["engine"], s["model"], s["prompt_version"], s["flag"],
                s["mark"]) == (ids[1], "claude", "claude-x", "6", "uncertain", "wrong")
        # The second correction is of a person's text: no engine to credit it to.
        svc.patch_line(did, ids[1], en="See you")
        newest = db.list_edit_samples(did)[0]
        assert (newest["ai_version"], newest["line_id"], newest["engine"],
                newest["prompt_version"], newest["flag"], newest["mark"]) == (
            "Goodbye", ids[1], None, None, None, None)

    def test_edit_sample_outlives_its_line(self, isolated_db):
        did, ids = _seed()
        svc.patch_line(did, ids[0], en="Hi there")
        _real_save(did, [ln for ln in db.load_line_objects(did) if ln.id != ids[0]])
        [s] = db.list_edit_samples(did)
        assert (s["line_id"], s["user_version"]) == (None, "Hi there")

    def test_source_edit_records_who_heard_it_and_the_line_before(self, isolated_db):
        import raw_transcript
        did, ids = _seed()
        raw_transcript.write_raw_transcript(db.drama_dir(did), [], db.load_line_objects(did),
                                            backend="whisper", model="large-v3")
        db.update_line_fields_if(did, ids[1], {"confidence": 0.4, "mark": "wrong"}, {})
        svc.patch_line(did, ids[1], zh="在见")
        [s] = db.list_source_edits(did)
        assert (s["line_id"], s["path"], s["before_zh"], s["after_zh"], s["engine"], s["model"],
                s["confidence"], s["flag"], s["mark"]) == (
            ids[1], "person", "再见", "在见", "whisper", "large-v3", 0.4, "uncertain", "wrong")
        # The second correction is of a person's text, not the transcription's.
        svc.patch_line(did, ids[1], zh="再见了")
        newest = db.list_source_edits(did)[0]
        assert (newest["before_zh"], newest["engine"], newest["model"]) == ("在见", None, None)
        # Source rows stay out of the English pairs learned style reads.
        assert db.list_edit_samples(did) == []

    def test_unchanged_or_english_only_patch_logs_no_source_edit(self, isolated_db):
        did, ids = _seed()
        svc.patch_line(did, ids[0], zh="你好")
        svc.patch_line(did, ids[0], en="Hi there")
        assert db.list_source_edits(did) == []

    def test_source_edit_follows_a_merge_and_outlives_its_line(self, isolated_db):
        did, ids = _seed()
        svc.patch_line(did, ids[1], zh="在见")
        a, b, c = db.load_line_objects(did)
        a.merged_ids = [b.id]
        _real_save(did, [a, c])
        assert [s["line_id"] for s in db.list_source_edits(did)] == [ids[0]]
        _real_save(did, [c])
        assert [s["line_id"] for s in db.list_source_edits(did)] == [None]

    def test_checking_without_series_records_no_memory_and_no_sample_when_unchanged(self, isolated_db):
        did, ids = _seed()
        svc.patch_line(did, ids[0], en="Hi there")
        svc.set_reviewed(did, ids[0], True)
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

    def test_expected_write_is_one_conditional_update(self, isolated_db):
        did, ids = _seed()
        # sfx bool, float timing tolerance, NULL speaker == "" all match
        svc.patch_line(did, ids[2], en="Z", expected={"speaker": "", "sfx": False, "start": 2.0000000001})
        assert _row(did, ids[2])["en"] == "Z"

    def test_concurrent_change_between_check_and_write_conflicts(self, isolated_db, monkeypatch):
        did, ids = _seed()
        real = db.update_line_fields_if

        def racing(drama_id, line_id, values, expected):
            # another writer lands after the service's read, before its write
            db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Theirs",
                                     speaker="A", id=ids[0])], fields=("en",))
            return real(drama_id, line_id, values, expected)

        monkeypatch.setattr(db, "update_line_fields_if", racing)
        with pytest.raises(ConflictError) as ei:
            svc.patch_line(did, ids[0], zh="MINE", en="Mine", expected={"en": "Hello"})
        assert ei.value.details == {"fields": ["en"]}
        row = _row(did, ids[0])
        assert row["en"] == "Theirs" and row["zh"] == "你好"

    def test_expected_write_on_removed_line_is_404(self, isolated_db, monkeypatch):
        did, ids = _seed()
        real = db.update_line_fields_if

        def racing(drama_id, line_id, values, expected):
            db.save_lines(did, [ln for ln in db.load_line_objects(did) if ln.id != ids[0]])
            return real(drama_id, line_id, values, expected)

        monkeypatch.setattr(db, "update_line_fields_if", racing)
        with pytest.raises(NotFoundError):
            svc.patch_line(did, ids[0], en="X", expected={"en": "Hello"})

    def test_expected_write_clears_flag_and_sets_speaker_manual(self, isolated_db):
        did, ids = _seed()
        svc.patch_line(did, ids[1], en="Fixed", speaker="Bob", expected={"en": "Bye"})
        row = _row(did, ids[1])
        assert row["en"] == "Fixed" and row["speaker"] == "Bob" and not row["flag"]
        assert row["speaker_manual"]

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


class TestMarkEpisodeReviewed:
    def test_marks_unflagged_lines_and_remembers_them(self, isolated_db):
        did, ids = _seed(series=True)
        out = svc.mark_episode_reviewed(did)
        assert out == {"reviewed": 2, "remembered": 2, "kept_existing": 0,
                       "skipped_flagged": 1, "skipped_marked": 0, "already_reviewed": 0}
        assert [bool(_row(did, i)["reviewed"]) for i in ids] == [True, False, True]
        series_id = db.get_drama(did)["series_id"]
        assert {(e["source_text"], e["translation"]) for e in db.list_translation_memory(series_id)} == {
            ("你好", "Hello"), ("谢谢", "Thanks")}

    def test_repeat_changes_nothing(self, isolated_db):
        did, _ = _seed(series=True)
        svc.mark_episode_reviewed(did)
        series_id = db.get_drama(did)["series_id"]
        before = db.list_translation_memory(series_id)
        again = svc.mark_episode_reviewed(did)
        assert again["reviewed"] == 0 and again["remembered"] == 0 and again["already_reviewed"] == 2
        assert db.list_translation_memory(series_id) == before

    def test_hand_approved_entry_is_not_replaced(self, isolated_db):
        did, _ = _seed(series=True)
        series_id = db.get_drama(did)["series_id"]
        db.record_translation_memory(series_id, "你好", "Hi")
        out = svc.mark_episode_reviewed(did)
        assert out["kept_existing"] == 1 and out["remembered"] == 1
        stored = {e["source_text"]: e for e in db.list_translation_memory(series_id)}
        assert stored["你好"]["translation"] == "Hi" and stored["你好"]["use_count"] == 1

    def test_title_without_series_is_marked_only(self, isolated_db):
        did, ids = _seed()
        out = svc.mark_episode_reviewed(did)
        assert out["reviewed"] == 2 and out["remembered"] == 0
        assert bool(_row(did, ids[0])["reviewed"])

    def test_line_changed_meanwhile_is_not_marked(self, isolated_db, monkeypatch):
        did, ids = _seed(series=True)
        real = db.update_lines_fields_if_many

        def racing(drama_id, items):
            db.save_lines(drama_id, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Edited", id=ids[0])],
                          fields=("en",))
            return real(drama_id, items)

        monkeypatch.setattr(db, "update_lines_fields_if_many", racing)
        out = svc.mark_episode_reviewed(did)
        assert out["reviewed"] == 1 and not _row(did, ids[0])["reviewed"]
        sources = {e["source_text"] for e in db.list_translation_memory(db.get_drama(did)["series_id"])}
        assert sources == {"谢谢"}

    def test_unknown_title_is_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.mark_episode_reviewed(999)


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
        # One compare-and-set write, never a full sync.
        assert save_spy == []
        assert _row(did, ids[0])["en"] == "Howdy"
        assert _row(did, ids[2])["en"] == "Thanks (edited)"
        assert db.list_translation_memory(sid)[0]["translation"] == "Howdy"
        assert len(db.load_lines(did)) == 3

    def test_edit_saved_after_the_read_is_kept_and_reported_stale(self, save_spy, monkeypatch):
        did, ids = _seed()
        real = db.update_lines_fields_if_many

        def edit_lands_first(drama_id, items):
            # Another tab saves line 0 between the service's read and its write.
            db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello there",
                                     speaker="A", id=ids[0])], fields=("en",))
            return real(drama_id, items)

        monkeypatch.setattr(db, "update_lines_fields_if_many", edit_lands_first)
        out = svc.apply_find_replace(did, [
            {"id": ids[0], "old_text": "Hello", "new_text": "Howdy"},
            {"id": ids[2], "old_text": "Thanks", "new_text": "Cheers"},
        ])
        assert out["applied_ids"] == [ids[2]] and out["stale_ids"] == [ids[0]]
        assert _row(did, ids[0])["en"] == "Hello there"
        assert _row(did, ids[2])["en"] == "Cheers"

    def test_a_rename_over_more_lines_than_one_request_takes_lands_in_chunks(self, isolated_db, monkeypatch):
        from services import review_lines_service
        sid = db.get_or_create_series("S")
        did = db.create_drama(title_en="D", series_id=sid)
        _real_save(did, [Line(idx=i, start=float(i), end=i + 1.0, zh="林舟", en=f"Lin Zhou said {i}.")
                         for i in range(2500)])
        db.record_translation_memory(sid, "林舟", "Lin Zhou said 0.")
        matches = review_lines_service.preview_find_replace(did, "Lin Zhou", "Lin Chou")
        assert len(matches) == 2500
        # Edited after Preview: skipped as stale, the rest still lands.
        edited = matches[1700]["id"]
        db.save_lines(did, [Line(idx=1700, start=1700.0, end=1701.0, zh="林舟", en="Hand edit",
                                 id=edited)], fields=("en",))
        tm_calls = []
        real_tm = db.update_translation_memory_after_replace
        monkeypatch.setattr(db, "update_translation_memory_after_replace",
                            lambda *a: tm_calls.append(a) or real_tm(*a))
        out = {"applied": 0, "stale_ids": []}
        for start in range(0, len(matches), svc.MAX_MATCHES):
            r = svc.apply_find_replace(did, [{k: m[k] for k in ("id", "old_text", "new_text")}
                                             for m in matches[start:start + svc.MAX_MATCHES]])
            out["applied"] += r["applied"]
            out["stale_ids"] += r["stale_ids"]
        assert out == {"applied": 2499, "stale_ids": [edited]}
        assert len(tm_calls) == 2499
        rows = db.load_lines(did)
        assert sum(r["en"].startswith("Lin Chou said") for r in rows) == 2499
        assert next(r for r in rows if r["id"] == edited)["en"] == "Hand edit"
        assert db.list_translation_memory(sid)[0]["translation"] == "Lin Chou said 0."

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
        out = svc.accept_tm_suggestion(did, ids[0], e["id"], "Hello")
        # one compare-and-set on en, never a line-list save
        assert out["en"] == "Howdy" and save_spy == []
        assert db.list_translation_memory(e["series_id"])[0]["use_count"] == e["use_count"] + 1

    def test_accept_refuses_when_the_line_changed(self, isolated_db):
        did, ids = _seed(series=True)
        e = self._entry(db.get_drama(did)["series_id"])
        with pytest.raises(ConflictError):
            svc.accept_tm_suggestion(did, ids[0], e["id"], "an older English")
        assert _row(did, ids[0])["en"] == "Hello"
        assert db.list_translation_memory(e["series_id"])[0]["use_count"] == e["use_count"]
        with pytest.raises(InvalidInputError):
            svc.accept_tm_suggestion(did, ids[0], e["id"], None)

    def test_other_series_entry_is_404(self, isolated_db):
        did, ids = _seed(series=True)
        other = db.get_or_create_series("Other")
        e = self._entry(other)
        with pytest.raises(NotFoundError):
            svc.accept_tm_suggestion(did, ids[0], e["id"], "Hello")
        assert _row(did, ids[0])["en"] == "Hello"
        assert db.list_translation_memory(other)[0]["use_count"] == e["use_count"]

    def test_no_series_is_404(self, isolated_db):
        did, ids = _seed()
        with pytest.raises(NotFoundError):
            svc.accept_tm_suggestion(did, ids[0], 1, "Hello")


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
        svc.accept_tm_suggestion(did, ids[0], e["id"], "a")
        svc.add_note(did, ids[0], "t", "idiom", "n")
        assert save_spy and all(f is not None and isinstance(f, tuple) for f in save_spy)


def _mark(did, lid):
    row = _row(did, lid)
    return row["mark"], row["reviewed"]


class TestSetMark:
    def test_each_value_clear_and_idempotent(self, isolated_db):
        did, ids = _seed()
        for mark in ("wrong", "mixed", "mixed", None, None):
            out = svc.set_mark(did, ids[0], mark)
            assert out["mark"] == mark and out["id"] == ids[0] and out["reviewed"] is False
            assert _mark(did, ids[0]) == (mark, 0)

    def test_marking_a_checked_line_unchecks_it_and_checking_clears_the_mark(self, isolated_db):
        did, ids = _seed()
        svc.set_reviewed(did, ids[0], True)
        assert svc.set_mark(did, ids[0], "wrong", expected_en="Hello")["reviewed"] is False
        assert _mark(did, ids[0]) == ("wrong", 0)
        out = svc.set_reviewed(did, ids[0], True, expected_en="Hello")
        assert (out["mark"], out["reviewed"]) == (None, True)

    def test_a_changed_english_is_a_conflict_and_writes_nothing(self, isolated_db):
        did, ids = _seed()
        with pytest.raises(ConflictError):
            svc.set_mark(did, ids[0], "wrong", expected_en="Hello (old)")
        assert _mark(did, ids[0]) == (None, 0)

    def test_an_untranslated_line_cannot_be_marked(self, isolated_db):
        did = db.create_drama(title_en="D")
        _real_save(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="  ")])
        lid = db.load_lines(did)[0]["id"]
        for expected in (None, "  "):
            with pytest.raises(InvalidInputError):
                svc.set_mark(did, lid, "wrong", expected_en=expected)
        assert svc.set_mark(did, lid, None)["mark"] is None
        assert _mark(did, lid) == (None, 0)

    @pytest.mark.parametrize("bad", ["", "Wrong", "good", 1, True])
    def test_an_unknown_mark_is_refused(self, isolated_db, bad):
        did, ids = _seed()
        with pytest.raises(InvalidInputError):
            svc.set_mark(did, ids[0], bad)

    def test_a_line_of_another_title_is_not_found(self, isolated_db):
        did, ids = _seed()
        other, _ = _seed()
        with pytest.raises(NotFoundError):
            svc.set_mark(other, ids[0], "wrong")
        with pytest.raises(NotFoundError):
            svc.set_mark(999, ids[0], "wrong")
        assert _mark(did, ids[0]) == (None, 0)

    def test_marking_a_flagged_line_keeps_its_flag_and_dismissing_keeps_the_mark(self, isolated_db):
        did, ids = _seed()
        out = svc.set_mark(did, ids[1], "mixed")
        assert (out["flag"], out["mark"]) == ("uncertain", "mixed")
        out = svc.dismiss_flag(did, ids[1])
        assert (out["flag"], out["mark"]) == (None, "mixed")


class TestEnglishWritersClearTheMark:
    """Rule 1 lives in db/lines.py, so these services clear a mark with no code of their own."""

    def test_an_edit_clears_it_and_the_same_english_keeps_it(self, isolated_db):
        did, ids = _seed()
        svc.set_mark(did, ids[0], "wrong")
        svc.set_mark(did, ids[2], "wrong")
        assert svc.patch_line(did, ids[0], en="Hello")["mark"] == "wrong"
        assert svc.patch_line(did, ids[0], en="Hi")["mark"] is None
        assert svc.patch_line(did, ids[2], en="Cheers", expected={"en": "Thanks"})["mark"] is None

    def test_find_and_replace_clears_it(self, isolated_db):
        did, ids = _seed()
        svc.set_mark(did, ids[0], "mixed")
        svc.set_mark(did, ids[2], "mixed")
        svc.apply_find_replace(did, [{"id": ids[0], "old_text": "Hello", "new_text": "Howdy"}])
        assert [_mark(did, i)[0] for i in (ids[0], ids[2])] == [None, "mixed"]

    def test_a_translate_jobs_field_scoped_save_clears_it(self, isolated_db):
        import translation_finish
        did, ids = _seed()
        svc.set_mark(did, ids[0], "wrong")
        svc.set_mark(did, ids[2], "wrong")
        lines = db.load_line_objects(did)
        save_cb, _, skipped = translation_finish.own_lines_callbacks(did, lines, lambda _ls: None)
        lines[0].en = "Hello again"
        with db.job_line_fields("translate", ("en", "flag", "flag_note")):
            save_cb([lines[0], lines[2]])
        assert not skipped
        assert [_mark(did, i)[0] for i in (ids[0], ids[2])] == [None, "wrong"]


class TestMarkEpisodeReviewedSkipsMarked:
    def test_marked_lines_are_skipped_reported_and_not_remembered(self, isolated_db):
        did, ids = _seed(series=True)
        svc.set_mark(did, ids[0], "wrong")
        svc.set_mark(did, ids[1], "mixed")  # flagged too: counted in both skips
        out = svc.mark_episode_reviewed(did)
        assert out == {"reviewed": 1, "remembered": 1, "kept_existing": 0,
                       "skipped_flagged": 1, "skipped_marked": 2, "already_reviewed": 0}
        assert [_mark(did, i) for i in ids] == [("wrong", 0), ("mixed", 0), (None, 1)]
        series_id = db.get_drama(did)["series_id"]
        assert {e["source_text"] for e in db.list_translation_memory(series_id)} == {"谢谢"}

    def test_a_line_marked_after_the_read_is_not_checked(self, isolated_db, monkeypatch):
        did, ids = _seed(series=True)
        real = db.update_lines_fields_if_many

        def racing(drama_id, items):
            db.update_line_fields_if(drama_id, ids[0], {"mark": "wrong"}, {})
            return real(drama_id, items)

        monkeypatch.setattr(db, "update_lines_fields_if_many", racing)
        out = svc.mark_episode_reviewed(did)
        assert out["reviewed"] == 1 and _mark(did, ids[0]) == ("wrong", 0)
        sources = {e["source_text"] for e in db.list_translation_memory(db.get_drama(did)["series_id"])}
        assert sources == {"谢谢"}


class TestGiveFeedback:
    def test_thumbs_down_marks_wrong_and_keeps_the_better_wording_as_an_edit_sample(self, isolated_db):
        did, ids = _seed()
        out = svc.give_feedback(did, ids[0], "Hello", "Hi there")
        # The line itself is untouched: feedback is a suggestion, not an edit.
        assert (out["en"], out["mark"]) == ("Hello", "wrong")
        samples = db.list_edit_samples(did)
        assert [(s["ai_version"], s["user_version"], s["line_id"], s["mark"]) for s in samples] == [
            ("Hello", "Hi there", ids[0], "wrong")]

    def test_without_wording_it_only_marks(self, isolated_db):
        did, ids = _seed()
        for better in (None, "   ", "Hello"):
            svc.give_feedback(did, ids[0], "Hello", better)
        assert _mark(did, ids[0]) == ("wrong", 0)
        assert db.list_edit_samples(did) == []

    def test_a_changed_english_is_a_conflict_and_records_nothing(self, isolated_db):
        did, ids = _seed()
        with pytest.raises(ConflictError):
            svc.give_feedback(did, ids[0], "Hello (old)", "Hi there")
        assert _mark(did, ids[0]) == (None, 0)
        assert db.list_edit_samples(did) == []
