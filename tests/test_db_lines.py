"""Tests for a title's lines in library.db: saving and loading, marks, history, line references and confidence."""

import contextlib
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from core import Line


class TestLinesSaveLoad:
    def test_save_and_load_roundtrip(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="A")]
        isolated_db.save_lines(did, lines)
        loaded = isolated_db.load_lines(did)
        assert len(loaded) == 1
        assert loaded[0]["zh"] == "你好"
        assert loaded[0]["en"] == "Hello"
        assert loaded[0]["speaker"] == "A"

    def test_save_lines_replaces_previous_set(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="a")])
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="b", en="b")])
        loaded = isolated_db.load_lines(did)
        assert len(loaded) == 1
        assert loaded[0]["zh"] == "b"

    def test_dub_filename_persists(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        ln = Line(idx=0, start=0, end=1, zh="a", en="a")
        ln.dub_filename = "clip_0.wav"
        isolated_db.save_lines(did, [ln])
        loaded = isolated_db.load_lines(did)
        assert loaded[0]["dub_filename"] == "clip_0.wav"

    def test_flag_and_flag_note_persist(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        ln = Line(idx=0, start=0, end=1, zh="a", en="b",
                  flag="ambiguous_reference", flag_note="'her' unresolved")
        isolated_db.save_lines(did, [ln])
        loaded = isolated_db.load_lines(did)
        assert loaded[0]["flag"] == "ambiguous_reference"
        assert loaded[0]["flag_note"] == "'her' unresolved"

    def test_unflagged_line_has_no_flag(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="b")])
        loaded = isolated_db.load_lines(did)
        assert loaded[0]["flag"] is None

    def test_resaving_clears_a_previously_set_flag(self, isolated_db):
        """A line whose flag was dismissed (or cleared by editing) and
        re-saved shouldn't have the old flag reappear."""
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="b", flag="name_uncertain")])
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="b", flag=None)])
        loaded = isolated_db.load_lines(did)
        assert loaded[0]["flag"] is None


class TestReviewedClearsWithNewEnglish:
    """Every writer of `en` goes through save_lines or the compare-and-set
    update, so the uncheck rule lives in the SQL (db.lines._UNCHECK_ON_NEW_EN)
    rather than in each service (find-and-replace, accept-tm, shorten, the
    translate and retranscribe jobs, a version restore)."""

    def _checked(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello"),
                                     Line(idx=1, start=1, end=2, zh="再见", en="Bye")])
        rows = isolated_db.load_line_objects(did)
        for ln in rows:
            ln.reviewed = True
        isolated_db.save_lines(did, rows, fields=("reviewed",))
        assert [r["reviewed"] for r in isolated_db.load_lines(did)] == [1, 1]
        return did, isolated_db.load_line_objects(did)

    def test_a_field_scoped_en_write_unchecks_only_the_line_whose_english_changed(self, isolated_db):
        did, rows = self._checked(isolated_db)
        rows[0].en = "Hi"
        rows[1].orig = None  # a job's copy without `orig` writes every field: the same text keeps the mark
        isolated_db.save_lines(did, rows, fields=("en", "flag", "flag_note"))
        assert [r["reviewed"] for r in isolated_db.load_lines(did)] == [0, 1]
        # The saved object follows the row, so a later save can't put the mark back.
        assert rows[0].reviewed is False and rows[1].reviewed is True

    def test_only_if_unchanged_and_a_full_sync_uncheck_too(self, isolated_db):
        did, rows = self._checked(isolated_db)
        rows[0].en = "Hi"
        isolated_db.save_lines(did, rows[:1], fields=("en",), only_if_unchanged=True)
        rows = isolated_db.load_line_objects(did)
        assert [ln.reviewed for ln in rows] == [False, True]
        rows[1].en = "Goodbye"
        isolated_db.save_lines(did, rows)
        assert [r["reviewed"] for r in isolated_db.load_lines(did)] == [0, 0]

    def test_compare_and_set_unchecks_on_a_new_english_and_keeps_it_on_the_same(self, isolated_db):
        did, rows = self._checked(isolated_db)
        assert isolated_db.update_line_fields_if(did, rows[0].id, {"en": "Hello"}, {"en": "Hello"})
        assert isolated_db.update_line_fields_if(did, rows[1].id, {"en": "Later"}, {"en": "Bye"})
        assert [r["reviewed"] for r in isolated_db.load_lines(did)] == [1, 0]

    def test_a_write_that_sets_reviewed_itself_wins(self, isolated_db):
        did, rows = self._checked(isolated_db)
        rows[0].en, rows[0].reviewed = "Hi", True
        isolated_db.save_lines(did, rows[:1], fields=("en", "reviewed"))
        assert isolated_db.load_lines(did)[0]["reviewed"] == 1


class TestLineMarks:
    """A person's mark (lines.mark) follows the rules db/lines.py applies to every
    write: a new English clears it unless the write sets it, setting it unchecks the
    line, checking clears it, and a write storing both is refused."""

    def _seed(self, db_, mark="wrong", reviewed=False):
        did = db_.create_drama(title_en="Test")
        db_.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello", mark=mark,
                                  reviewed=reviewed),
                             Line(idx=1, start=1, end=2, zh="再见", en="Bye", mark=mark,
                                  reviewed=reviewed)])
        return did, db_.load_line_objects(did)

    def _state(self, db_, did):
        return [(r["en"], r["reviewed"], r["mark"]) for r in db_.load_lines(did)]

    def test_a_fresh_database_has_the_column_and_stores_no_mark_as_null(self, isolated_db):
        with contextlib.closing(isolated_db.get_conn()) as conn:
            assert "mark" in {r[1] for r in conn.execute("PRAGMA table_info(lines)")}
        did, rows = self._seed(isolated_db, mark="")
        assert [r["mark"] for r in isolated_db.load_lines(did)] == [None, None]
        assert [ln.mark for ln in rows] == [None, None]

    def test_a_write_of_an_unknown_mark_is_refused(self, isolated_db):
        did, rows = self._seed(isolated_db, mark=None)
        rows[0].mark = "maybe"
        with pytest.raises(ValueError):
            isolated_db.save_lines(did, rows, fields=("mark",))
        with pytest.raises(ValueError):
            isolated_db.update_line_fields_if(did, rows[1].id, {"mark": "maybe"}, {})
        assert [r["mark"] for r in isolated_db.load_lines(did)] == [None, None]

    def test_an_unknown_stored_mark_reads_as_none_everywhere(self, isolated_db):
        did, rows = self._seed(isolated_db, mark=None)
        with contextlib.closing(isolated_db.get_conn()) as conn:
            conn.execute("UPDATE lines SET mark = 'Bogus' WHERE id = ?", (rows[0].id,))
            conn.commit()
        assert isolated_db.load_line_objects(did)[0].mark is None
        assert isolated_db.load_line_texts(did)[0].mark is None
        assert isolated_db.update_line_fields_if(did, rows[0].id, {"en": "Hi"}, {"mark": ""})

    # Rule 1: a new English clears the mark, in each write path.

    def test_compare_and_set_clears_the_mark_on_a_new_english_only(self, isolated_db):
        did, rows = self._seed(isolated_db)
        assert isolated_db.update_line_fields_if(did, rows[0].id, {"en": "Hello"}, {"en": "Hello"})
        assert isolated_db.update_line_fields_if(did, rows[1].id, {"en": "Later"}, {"en": "Bye"})
        assert self._state(isolated_db, did) == [("Hello", 0, "wrong"), ("Later", 0, None)]

    def test_compare_and_set_that_sets_the_mark_itself_keeps_it(self, isolated_db):
        did, rows = self._seed(isolated_db)
        assert isolated_db.update_line_fields_if(did, rows[0].id, {"en": "Hi", "mark": "mixed"},
                                                 {"en": "Hello"})
        assert self._state(isolated_db, did)[0] == ("Hi", 0, "mixed")

    def test_a_field_scoped_en_save_clears_it_and_the_object_follows(self, isolated_db):
        did, rows = self._seed(isolated_db)
        rows[0].en = "Hi"
        isolated_db.save_lines(did, rows, fields=("en", "flag", "flag_note"))
        assert self._state(isolated_db, did) == [("Hi", 0, None), ("Bye", 0, "wrong")]
        assert (rows[0].mark, rows[0].orig["mark"]) == (None, None)
        assert (rows[1].mark, rows[1].orig["mark"]) == ("wrong", "wrong")
        # The object follows the row, so a later full sync of it can't put the mark back.
        isolated_db.save_lines(did, rows)
        assert self._state(isolated_db, did)[0] == ("Hi", 0, None)

    def test_a_field_scoped_save_naming_mark_keeps_the_writers_value(self, isolated_db):
        did, rows = self._seed(isolated_db)
        rows[0].en = "Hi"
        isolated_db.save_lines(did, rows[:1], fields=("en", "mark"))
        assert self._state(isolated_db, did)[0] == ("Hi", 0, "wrong")

    def test_only_if_unchanged_clears_it_too(self, isolated_db):
        did, rows = self._seed(isolated_db)
        rows[0].en = "Hi"
        isolated_db.save_lines(did, rows[:1], fields=("en",), only_if_unchanged=True)
        assert self._state(isolated_db, did)[0] == ("Hi", 0, None)

    def test_a_full_sync_clears_it_and_the_object_follows(self, isolated_db):
        did, rows = self._seed(isolated_db)
        rows[1].en = "Goodbye"
        isolated_db.save_lines(did, rows)
        assert self._state(isolated_db, did) == [("Hello", 0, "wrong"), ("Goodbye", 0, None)]
        assert (rows[1].mark, rows[1].orig["mark"]) == (None, None)

    def test_a_full_sync_that_sets_a_new_mark_with_the_english_keeps_it(self, isolated_db):
        did, rows = self._seed(isolated_db)
        rows[1].en, rows[1].mark = "Goodbye", "mixed"
        isolated_db.save_lines(did, rows)
        assert self._state(isolated_db, did)[1] == ("Goodbye", 0, "mixed")

    # Rule 2: a marked line is not checked.

    def test_compare_and_set_mark_unchecks_and_check_unmarks(self, isolated_db):
        did, rows = self._seed(isolated_db, mark=None, reviewed=True)
        assert isolated_db.update_line_fields_if(did, rows[0].id, {"mark": "wrong"}, {"en": "Hello"})
        assert self._state(isolated_db, did)[0] == ("Hello", 0, "wrong")
        assert isolated_db.update_line_fields_if(did, rows[0].id, {"reviewed": 1}, {"en": "Hello"})
        assert self._state(isolated_db, did)[0] == ("Hello", 1, None)
        # Clearing a mark or unchecking says nothing about the other.
        assert isolated_db.update_line_fields_if(did, rows[1].id, {"mark": None}, {})
        assert self._state(isolated_db, did)[1] == ("Bye", 1, None)

    def test_a_field_scoped_mark_save_unchecks_and_the_object_follows(self, isolated_db):
        did, rows = self._seed(isolated_db, mark=None, reviewed=True)
        rows[0].mark = "mixed"
        isolated_db.save_lines(did, rows[:1], fields=("mark",))
        assert self._state(isolated_db, did)[0] == ("Hello", 0, "mixed")
        assert (rows[0].reviewed, rows[0].orig["reviewed"]) == (False, False)

    def test_a_field_scoped_check_unmarks_and_the_object_follows(self, isolated_db):
        did, rows = self._seed(isolated_db)
        rows[0].reviewed = True
        isolated_db.save_lines(did, rows[:1], fields=("reviewed",))
        assert self._state(isolated_db, did)[0] == ("Hello", 1, None)
        assert (rows[0].mark, rows[0].orig["mark"]) == (None, None)

    def test_a_full_sync_mark_unchecks_and_a_check_unmarks(self, isolated_db):
        did, rows = self._seed(isolated_db, mark=None, reviewed=True)
        rows[0].mark = "wrong"
        isolated_db.save_lines(did, rows)
        assert self._state(isolated_db, did)[0] == ("Hello", 0, "wrong")
        assert (rows[0].reviewed, rows[0].orig["reviewed"]) == (False, False)
        rows[0].reviewed = True
        isolated_db.save_lines(did, rows)
        assert self._state(isolated_db, did)[0] == ("Hello", 1, None)
        assert (rows[0].mark, rows[0].orig["mark"]) == (None, None)

    def test_a_write_storing_both_is_refused_and_writes_nothing(self, isolated_db):
        did, rows = self._seed(isolated_db, mark=None)
        with pytest.raises(ValueError):
            isolated_db.update_line_fields_if(did, rows[0].id, {"reviewed": 1, "mark": "wrong"}, {})
        with pytest.raises(ValueError):
            isolated_db.update_lines_fields_if_many(
                did, [(rows[1].id, {"en": "x"}, {}), (rows[0].id, {"reviewed": 1, "mark": "wrong"}, {})])
        rows[0].reviewed, rows[0].mark = True, "wrong"
        rows[1].en = "Edited"
        for fields in (("en", "reviewed", "mark"), None):
            with pytest.raises(ValueError):
                # The other line's edit comes first: the refusal rolls it back.
                isolated_db.save_lines(did, rows[::-1], fields=fields)
        with pytest.raises(ValueError):
            isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="A", reviewed=True,
                                              mark="mixed")])
        assert self._state(isolated_db, did) == [("Hello", 0, None), ("Bye", 0, None)]

    # Rule 3: only a person sets a mark; a job only clears one through rule 1.

    def test_a_job_may_not_declare_or_write_the_mark(self, isolated_db):
        did, rows = self._seed(isolated_db, mark=None)
        with pytest.raises(ValueError):
            isolated_db.check_line_fields(("en", "mark"))
        rows[0].mark = "wrong"
        with isolated_db.job_line_fields("job1", ("en", "flag", "flag_note")):
            with pytest.raises(isolated_db.UndeclaredLineWrite):
                isolated_db.save_lines(did, rows[:1], fields=("mark",))
            with pytest.raises(isolated_db.UndeclaredLineWrite):
                isolated_db.update_line_fields_if(did, rows[0].id, {"mark": "wrong"}, {})
        assert self._state(isolated_db, did)[0] == ("Hello", 0, None)

    def test_a_flag_only_job_save_keeps_the_mark(self, isolated_db):
        from services import export_service
        did, rows = self._seed(isolated_db)
        rows[0].flag, rows[0].flag_note = "timing_overlap", "overlaps"
        with isolated_db.job_line_fields("timing", ("flag", "flag_note")):
            assert not export_service.save_flags(did, rows, ("start", "end"))
        assert [(r["flag"], r["mark"]) for r in isolated_db.load_lines(did)] == [
            ("timing_overlap", "wrong"), (None, "wrong")]


class TestSaveLinesExpectUnchanged:
    """A full sync computed from an earlier read refuses, inside its own
    transaction, when the lines or a removed line's notes changed since."""

    def _seed(self, db):
        did = db.create_drama(title_en="Test")
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a"), Line(idx=1, start=1, end=2, zh="b")])
        return did, db.load_line_objects(did)

    def test_an_edit_since_the_read_refuses_and_writes_nothing(self, isolated_db):
        from lib.errors import ConflictError
        did, read = self._seed(isolated_db)
        edited = isolated_db.load_line_objects(did)
        edited[1].en = "typed"
        isolated_db.save_lines(did, edited, fields=("en",))
        with pytest.raises(ConflictError):
            isolated_db.save_lines(did, read[:1], expect_unchanged=read)
        assert [r["en"] for r in isolated_db.load_lines(did)] == ["", "typed"]

    def test_unchanged_lines_save(self, isolated_db):
        did, read = self._seed(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="new")], expect_unchanged=read)
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["new"]

    def test_a_new_note_on_a_removed_line_refuses(self, isolated_db):
        from lib.errors import ConflictError
        did, read = self._seed(isolated_db)
        isolated_db.save_translation_notes(did, [{"line_id": read[1].id, "line_idx": 1,
                                                  "term": "t", "note": "n"}])
        with pytest.raises(ConflictError):
            isolated_db.save_lines(did, read[:1], known_note_ids=set())
        assert len(isolated_db.load_lines(did)) == 2
        known = {n["id"] for n in isolated_db.list_translation_notes(did)}
        isolated_db.save_lines(did, read[:1], known_note_ids=known)
        assert len(isolated_db.load_lines(did)) == 1

    def test_a_new_emotion_tag_on_a_removed_line_refuses(self, isolated_db):
        from lib.errors import ConflictError
        did, read = self._seed(isolated_db)
        isolated_db.save_emotions(did, {1: {"emotion": "angry", "intensity": 0.9, "note": ""}})
        with pytest.raises(ConflictError):
            isolated_db.save_lines(did, read[:1], known_emotion_ids=set())
        assert len(isolated_db.load_lines(did)) == 2
        known = isolated_db.line_emotion_ids(did)
        isolated_db.save_lines(did, read[:1], known_emotion_ids=known)
        assert len(isolated_db.load_lines(did)) == 1

    def test_field_scoped_saves_refuse_the_checks(self, isolated_db):
        did, read = self._seed(isolated_db)
        with pytest.raises(ValueError):
            isolated_db.save_lines(did, read, fields=("en",), expect_unchanged=read)


class TestLoadLineIds:
    """Step 6f: the cheap id-set check a caller uses to confirm a
    snapshot it computed earlier isn't stale relative to the database
    before committing it as a full replacement (see resegment Apply's
    own safety check)."""

    def test_returns_the_current_id_set(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a"),
                                     Line(idx=1, start=1, end=2, zh="b")])
        loaded = isolated_db.load_line_objects(did)
        assert isolated_db.load_line_ids(did) == {ln.id for ln in loaded}

    def test_empty_for_a_drama_with_no_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        assert isolated_db.load_line_ids(did) == set()

    def test_reflects_a_change_made_by_another_save(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a")])
        before = isolated_db.load_line_ids(did)

        lines = isolated_db.load_line_objects(did)
        lines.append(Line(idx=1, start=1, end=2, zh="b"))
        isolated_db.save_lines(did, lines)

        assert isolated_db.load_line_ids(did) != before
        assert isolated_db.load_line_ids(did) == {ln.id for ln in isolated_db.load_line_objects(did)}


class TestLoadLineTexts:
    """The slim read the counts and filters use: the same lines, order and
    values as load_lines, without the timing and dub columns."""

    def test_matches_load_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=1, start=1, end=2, zh="b", en="B", speaker="S", flag="x",
                                          flag_note="why"),
                                     Line(idx=0, start=0, end=1, zh="a")])
        full = isolated_db.load_lines(did)
        texts = isolated_db.load_line_texts(did)
        assert [t._asdict() for t in texts] == [
            {f: r[f] for f in isolated_db.LineText._fields} for r in full]
        assert [t.zh for t in texts] == ["a", "b"]

    def test_empty_for_a_drama_with_no_lines(self, isolated_db):
        assert isolated_db.load_line_texts(isolated_db.create_drama(title_en="Test")) == []

    def test_load_lines_by_ids_keeps_line_order(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=str(i)) for i in range(4)])
        ids = [r["id"] for r in isolated_db.load_lines(did)]
        assert [r["zh"] for r in isolated_db.load_lines(did, ids=[ids[3], ids[1]])] == ["1", "3"]
        assert [ln.zh for ln in isolated_db.load_line_objects(did, ids=[ids[2]])] == ["2"]
        assert isolated_db.load_lines(did, ids=[]) == []


class TestTranslateHistory:
    """Step 26b: history for the standalone translate tool -- a
    library-level table (no drama_id), same shape as presets. Exit
    condition: a completed translation is saved to history and appears
    in a history listing."""

    def test_save_and_list_roundtrips_every_field(self, isolated_db):
        isolated_db.save_translate_history("zh", "en", "claude", "你好", "Hello")
        history = isolated_db.list_translate_history()
        assert len(history) == 1
        row = history[0]
        assert row["source_language"] == "zh"
        assert row["target_language"] == "en"
        assert row["engine"] == "claude"
        assert row["source_text"] == "你好"
        assert row["translated_text"] == "Hello"
        assert row["created_at"]

    def test_list_is_most_recent_first(self, isolated_db):
        isolated_db.save_translate_history("zh", "en", "claude", "first", "First")
        isolated_db.save_translate_history("en", "ja", "deepl", "second", "Second")
        history = isolated_db.list_translate_history()
        assert [h["source_text"] for h in history] == ["second", "first"]

    def test_list_respects_limit(self, isolated_db):
        for i in range(5):
            isolated_db.save_translate_history("zh", "en", "claude", f"src{i}", f"out{i}")
        assert len(isolated_db.list_translate_history(limit=3)) == 3

    def test_clear_removes_every_row(self, isolated_db):
        isolated_db.save_translate_history("zh", "en", "claude", "a", "A")
        isolated_db.save_translate_history("ja", "en", "deepseek", "b", "B")
        isolated_db.clear_translate_history()
        assert isolated_db.list_translate_history() == []


# Tables with a line_id that a full sync deliberately leaves alone when a line
# is removed or merged. A new per-line table must go here or in
# db.lines._LINE_REF_TABLES, so a merge moves its rows and a delete cleans them.
_LINE_ID_TABLES_NOT_FOLLOWED = {
    "bug_reports": "a frozen debugging bundle; its line_id is only a hint",
    "bulk_job_lines": "results are applied by line id and skipped once the line is gone",
    "line_provenance": "keyed by line id and read against the current lines",
}


def test_every_line_id_table_is_followed_or_listed(isolated_db):
    with contextlib.closing(db.get_conn()) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        with_line_id = {t for t in tables
                        if "line_id" in {c[1] for c in conn.execute(f'PRAGMA table_info("{t}")')}}
    # bug_reports exists only in libraries made before it was dropped from the schema.
    legacy = {"bug_reports"}
    assert with_line_id - legacy == (set(db.lines._LINE_REF_TABLES)
                                     | set(_LINE_ID_TABLES_NOT_FOLLOWED)) - legacy
    assert set(db.lines._LINE_REF_TABLES.values()) <= {"delete", "unlink"}


def test_line_refs_follow_a_merge_and_go_with_a_delete(isolated_db):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 0.5, zh=z) for i, z in enumerate("甲乙丙")])
    a, b, c = db.load_line_objects(did)
    db.save_translation_notes(did, [{"line_id": b.id, "line_idx": 1, "term": "乙",
                                     "note_type": "idiom", "note": "n"}])
    db.save_emotions(did, {2: {"emotion": "joy"}}, id_by_idx={2: c.id})
    db.save_progress(did, last_line_idx=1)

    def history():
        with contextlib.closing(db.get_conn()) as conn:
            return [r[0] for r in conn.execute(
                "SELECT line_id FROM reading_history WHERE drama_id = ?", (did,))]
    a.merged_ids = [b.id]
    db.save_lines(did, [a, c])
    assert [n["line_id"] for n in db.list_translation_notes(did)] == [a.id]
    assert history() == [a.id]
    # a reading position never blocks: it is only unlinked
    assert db.line_ids_with_refs(did, [a.id, c.id]) == {a.id, c.id}
    db.save_lines(did, [ln for ln in db.load_line_objects(did) if ln.id == c.id])
    assert db.list_translation_notes(did) == [] and history() == [None]
    assert db.line_ids_with_refs(did, [c.id]) == {c.id}


def test_line_ids_with_refs_takes_more_ids_than_sqlite_binds(isolated_db):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="甲")])
    [line] = db.load_line_objects(did)
    db.save_emotions(did, {0: {"emotion": "joy"}}, id_by_idx={0: line.id})
    assert db.line_ids_with_refs(did, range(line.id, line.id + 40_000)) == {line.id}
    assert db.line_ids_with_refs(did + 1, [line.id]) == set()


class TestLineHistoryCompression:
    def _snapshots(self, did, n):
        ids = []
        for i in range(n):
            lines = [Line(idx=0, start=0.0, end=1.0, zh=f"第{i}版", en=f"take {i}")]
            ids.append(db.save_line_history_snapshot(did, lines, f"s{i}"))
        return ids

    def _stored(self, did):
        with contextlib.closing(db.get_conn()) as conn:
            return {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT id, typeof(snapshot_json), snapshot_json FROM line_history "
                "WHERE drama_id = ?", (did,))}

    def test_newest_three_stay_text_and_older_ones_are_gzip(self, isolated_db):
        did = db.create_drama(title_zh="D")
        ids = self._snapshots(did, 6)
        stored = self._stored(did)
        assert [stored[i][0] for i in ids] == ["blob"] * 3 + ["text"] * 3
        assert all(stored[i][1][:2] == b"\x1f\x8b" for i in ids[:3])

    def test_every_kept_snapshot_reads_back_whatever_its_form(self, isolated_db):
        did = db.create_drama(title_zh="D")
        ids = self._snapshots(did, 22)
        kept = [h["id"] for h in db.list_line_history(did)]
        assert sorted(kept) == ids[2:]          # the last 20 are kept
        for n, hid in enumerate(ids[2:], start=2):
            rows = db.get_line_history_snapshot(hid)
            assert rows[0]["zh"] == f"第{n}版" and rows[0]["en"] == f"take {n}"

    def test_a_plain_snapshot_from_before_compression_still_reads(self, isolated_db):
        did = db.create_drama(title_zh="D")
        with contextlib.closing(db.get_conn()) as conn:
            hid = conn.execute(
                "INSERT INTO line_history (drama_id, label, snapshot_json, created_at) "
                "VALUES (?, 'old', ?, '2000-01-01')", (did, '[{"zh": "旧"}]')).lastrowid
            conn.commit()
        self._snapshots(did, 4)
        assert self._stored(did)[hid][0] == "blob"
        assert db.get_line_history_snapshot(hid) == [{"zh": "旧"}]

    def test_a_clock_set_back_never_prunes_the_snapshot_just_written(self, isolated_db):
        did = db.create_drama(title_zh="D")
        older = self._snapshots(did, 2)
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("UPDATE line_history SET created_at = '2999-01-01' WHERE drama_id = ?",
                         (did,))
            conn.commit()
        lines = [Line(idx=0, start=0.0, end=1.0, zh="新", en="new")]
        hid = db.save_line_history_snapshot(did, lines, "after the clock", keep_last=2)
        assert db.get_line_history_snapshot(hid)[0]["zh"] == "新"
        assert [h["id"] for h in db.list_line_history(did)] == [hid, older[1]]


class TestLineRefIndexesAndOrphans:
    def test_the_per_line_tables_are_indexed_by_line_id(self, isolated_db):
        import contextlib
        with contextlib.closing(isolated_db.get_conn()) as conn:
            for table in ("translation_notes", "line_emotions", "reading_history"):
                plan = " ".join(r[3] for r in conn.execute(
                    f"EXPLAIN QUERY PLAN DELETE FROM {table} WHERE line_id = 1"))
                assert "USING INDEX" in plan or "USING COVERING INDEX" in plan, table

    def test_notes_and_emotions_for_a_removed_line_are_not_saved(self, isolated_db):
        from core import Line
        did = isolated_db.create_drama(title_en="T")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="A")])
        gone = isolated_db.load_lines(did)[0]["id"]
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="b", en="B")], fields=None)
        assert isolated_db.load_lines(did)[0]["id"] != gone
        isolated_db.save_translation_notes(
            did, [{"line_id": gone, "line_idx": 0, "term": "t", "note": "n"}])
        isolated_db.save_emotions(did, {0: {"emotion": "sad"}}, id_by_idx={0: gone})
        assert isolated_db.list_translation_notes(did) == []
        assert isolated_db.line_emotion_ids(did) == set()

    def test_an_old_library_without_line_id_columns_upgrades_and_gets_the_indexes(self, isolated_db):
        import contextlib
        with contextlib.closing(isolated_db.get_conn()) as conn:
            for table in ("translation_notes", "line_emotions"):
                conn.execute(f"DROP TABLE {table}")
            conn.execute("DROP INDEX IF EXISTS idx_reading_history_line")
            conn.execute("ALTER TABLE reading_history DROP COLUMN line_id")
            conn.execute("""CREATE TABLE translation_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, drama_id INTEGER NOT NULL, line_idx INTEGER,
                term TEXT, note_type TEXT, note TEXT, created_at TEXT, UNIQUE(drama_id, line_idx, term))""")
            conn.execute("""CREATE TABLE line_emotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, drama_id INTEGER NOT NULL, line_idx INTEGER,
                emotion TEXT, intensity REAL, note TEXT, created_at TEXT, UNIQUE(drama_id, line_idx))""")
            conn.commit()
        isolated_db.init_db()
        with contextlib.closing(isolated_db.get_conn()) as conn:
            for table in ("translation_notes", "line_emotions", "reading_history"):
                assert "line_id" in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                assert f"idx_{table}_line" in {r[1] for r in conn.execute(f"PRAGMA index_list({table})")}


def test_confidence_survives_saves_and_clears_when_the_source_text_is_edited(isolated_db):
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="x", confidence=0.42),
                                 Line(idx=1, start=1, end=2, zh="b", en="y", confidence=0.8)])
    lines = isolated_db.load_line_objects(did)
    assert [ln.confidence for ln in lines] == [0.42, 0.8]

    lines[0].en = "changed"
    isolated_db.save_lines(did, lines, fields=("en",))
    assert isolated_db.load_line_objects(did)[0].confidence == 0.42

    lines = isolated_db.load_line_objects(did)
    lines[0].zh = "edited"
    isolated_db.save_lines(did, lines, fields=("zh",))
    assert lines[0].confidence is None
    after = isolated_db.load_line_objects(did)
    assert [ln.confidence for ln in after] == [None, 0.8]

    isolated_db.update_line_fields_if(did, after[1].id, {"zh": "c"}, {"zh": "b"})
    assert isolated_db.load_line_objects(did)[1].confidence is None


def test_retranscription_writes_new_confidence_with_new_text(isolated_db):
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", confidence=0.2)])
    ln = isolated_db.load_line_objects(did)[0]
    ln.zh, ln.confidence = "b", 0.9
    isolated_db.save_lines(did, [ln], fields=("zh", "confidence"))
    assert isolated_db.load_line_objects(did)[0].confidence == 0.9
