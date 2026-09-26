"""
tests/test_line_ids.py -- Step 2 (R0): permanent line ids.

Lines used to be saved by deleting and re-inserting every row, and
everything attached to a line (translation notes, emotion tags, reading
history) pointed at it by position (idx). A merge or split renumbers
positions, so a note silently ended up on a different line; and two
writers (a background job and the page) each saving the whole list meant
the last save wiped the other's work. These pin the fix.
"""
import sqlite3

import pytest

import db
from core import Line, merge_adjacent_short_lines, adopt_ids, restore_saved_lines


def _lines(*texts, short=True):
    # 0.5s lines 0.1s apart with the same (empty) speaker: merge candidates
    step = 0.6 if short else 5.0
    return [Line(idx=i, start=i * step, end=i * step + (0.5 if short else 4.0), zh=t, en=t.upper())
            for i, t in enumerate(texts)]


class TestIdsArePermanent:
    def test_ids_are_assigned_and_survive_a_resave(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        lines = _lines("a", "b", "c", short=False)
        isolated_db.save_lines(did, lines)
        ids = [ln.id for ln in lines]
        assert all(ids)
        loaded = isolated_db.load_line_objects(did)
        loaded[1].en = "edited"
        isolated_db.save_lines(did, loaded)
        assert [ln.id for ln in isolated_db.load_line_objects(did)] == ids

    def test_a_line_removed_from_the_list_is_deleted_with_its_note(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, _lines("a", "b", short=False))
        isolated_db.save_translation_notes(did, [{"line_idx": 1, "term": "b", "note": "on b"}])
        kept = isolated_db.load_line_objects(did)[:1]
        isolated_db.save_lines(did, kept)
        assert len(isolated_db.load_lines(did)) == 1
        assert isolated_db.list_translation_notes(did) == []

    def test_a_note_follows_its_line_when_positions_shift(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, _lines("a", "b", short=False))
        isolated_db.save_translation_notes(did, [{"line_idx": 1, "term": "b", "note": "on b"}])
        lines = isolated_db.load_line_objects(did)
        lines.insert(0, Line(idx=0, start=-5, end=-4, zh="new first line"))
        for i, ln in enumerate(lines):
            ln.idx = i
        isolated_db.save_lines(did, lines)
        [note] = isolated_db.list_translation_notes(did)
        assert note["line_idx"] == 2  # line "b" is now third
        assert isolated_db.load_lines(did)[2]["zh"] == "b"

    def test_a_note_for_a_line_that_no_longer_exists_is_not_saved_as_an_orphan(self, isolated_db):
        """Step 25d item 11: this used to INSERT with line_id = NULL when
        the target line couldn't be resolved -- save_emotions already
        skipped that case, but save_translation_notes didn't, and since
        SQLite treats every NULL as distinct, the (drama_id, line_id,
        term) uniqueness this app relies on never caught the duplicates,
        so they piled up indefinitely."""
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, _lines("a", short=False))
        # line_idx 5 doesn't exist on this one-line drama.
        isolated_db.save_translation_notes(did, [{"line_idx": 5, "term": "ghost", "note": "orphan"}])
        isolated_db.save_translation_notes(did, [{"line_idx": 5, "term": "ghost", "note": "orphan again"}])
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT * FROM translation_notes WHERE drama_id = ? AND line_id IS NULL",
            (did,)).fetchall()
        conn.close()
        assert rows == []
        assert isolated_db.list_translation_notes(did) == []


class TestMergeKeepsAttachments:
    """The roadmap's exit condition: after a merge, a note or flag that was
    attached to a line is still attached to the same line."""

    def _setup(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        # "x" stays long (not a merge candidate), then "a"+"b" merge, then "c"
        lines = [Line(idx=0, start=0, end=4, zh="x", en="X"),
                 Line(idx=1, start=5, end=5.5, zh="a", en="A"),
                 Line(idx=2, start=5.6, end=6.1, zh="b", en="B"),
                 Line(idx=3, start=10, end=14, zh="c", en="C")]
        isolated_db.save_lines(did, lines)
        return did

    def test_note_and_flag_survive_a_merge(self, isolated_db):
        did = self._setup(isolated_db)
        isolated_db.save_translation_notes(did, [
            {"line_idx": 1, "term": "a", "note": "on a"},
            {"line_idx": 2, "term": "b", "note": "on b"},
            {"line_idx": 3, "term": "c", "note": "on c"}])
        isolated_db.save_emotions(did, {2: {"emotion": "sad", "intensity": 0.5, "note": ""},
                                        3: {"emotion": "angry", "intensity": 0.9, "note": ""}})
        lines = isolated_db.load_line_objects(did)
        lines[2].flag, lines[2].flag_note = "ambiguous_reference", "who is 'she'?"
        lines[3].flag = "idiom"
        isolated_db.save_lines(did, lines)

        merged = merge_adjacent_short_lines(isolated_db.load_line_objects(did))
        assert [ln.zh for ln in merged] == ["x", "ab", "c"]
        isolated_db.save_lines(did, merged)

        rows = isolated_db.load_lines(did)
        assert [r["zh"] for r in rows] == ["x", "ab", "c"]
        notes = {n["term"]: n["line_idx"] for n in isolated_db.list_translation_notes(did)}
        assert notes == {"a": 1, "b": 1, "c": 2}  # "b"'s note moved onto the merged line
        assert rows[2]["flag"] == "idiom"  # "c" kept its flag at its new position
        assert rows[1]["flag"] == "ambiguous_reference"  # absorbed "b"'s flag carried over
        emotions = isolated_db.load_emotions(did)
        assert emotions[1]["emotion"] == "sad" and emotions[2]["emotion"] == "angry"

    def test_the_surviving_lines_own_emotion_wins_over_the_absorbed_ones(self, isolated_db):
        did = self._setup(isolated_db)
        isolated_db.save_emotions(did, {1: {"emotion": "calm", "intensity": 0.3, "note": ""},
                                        2: {"emotion": "sad", "intensity": 0.5, "note": ""}})
        isolated_db.save_lines(did, merge_adjacent_short_lines(isolated_db.load_line_objects(did)))
        assert isolated_db.load_emotions(did) == {1: {"emotion": "calm", "intensity": 0.3, "note": ""}}


class TestConcurrentWritesByLineId:
    """Why Step 1b's "one job at a time" guard and edit lock could go:
    each writer only writes what it changed."""

    def test_translate_and_flag_jobs_dont_undo_each_other(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="她来了"),
                                     Line(idx=1, start=1, end=2, zh="你好")])
        translate_copy = isolated_db.load_line_objects(did)
        flag_copy = isolated_db.load_line_objects(did)

        flag_copy[0].flag, flag_copy[0].flag_note = "ambiguous_reference", "she?"
        isolated_db.save_lines(did, flag_copy, fields=("flag", "flag_note"))
        translate_copy[0].en, translate_copy[1].en = "She came.", "Hello."
        isolated_db.save_lines(did, translate_copy, fields=("en",))

        rows = isolated_db.load_lines(did)
        assert rows[0]["en"] == "She came." and rows[0]["flag"] == "ambiguous_reference"

    def test_the_pages_save_doesnt_wipe_what_a_job_wrote_meanwhile(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好"),
                                     Line(idx=1, start=1, end=2, zh="再见")])
        page = isolated_db.load_line_objects(did)       # loaded before the job ran
        job = isolated_db.load_line_objects(did)
        job[0].en, job[1].en = "Hello.", "Bye."
        isolated_db.save_lines(did, job, fields=("en",))

        page[1].speaker = "Xiaoling"                    # the user edits something else
        isolated_db.save_lines(did, page)               # full save of stale `en` values

        rows = isolated_db.load_lines(did)
        assert [r["en"] for r in rows] == ["Hello.", "Bye."]
        assert rows[1]["speaker"] == "Xiaoling"

    def test_a_field_scoped_job_save_never_resurrects_or_deletes_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, _lines("a", "b", short=False))
        job = isolated_db.load_line_objects(did)
        page = isolated_db.load_line_objects(did)
        page = page[:1] + [Line(idx=1, start=20, end=21, zh="user added")]
        isolated_db.save_lines(did, page)               # user deletes "b", adds a line

        for ln in job:
            ln.en = "translated " + ln.zh
        isolated_db.save_lines(did, job, fields=("en",))

        rows = isolated_db.load_lines(did)
        assert [r["zh"] for r in rows] == ["a", "user added"]
        assert rows[0]["en"] == "translated a"

    def test_a_later_job_batch_doesnt_rewrite_a_user_edit_made_after_the_first(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        job = isolated_db.load_line_objects(did)
        job[0].en = "Hello."
        isolated_db.save_lines(did, job, fields=("en",))   # batch 1
        page = isolated_db.load_line_objects(did)
        page[0].en = "Hi there."
        isolated_db.save_lines(did, page)                  # user polishes it
        isolated_db.save_lines(did, job, fields=("en",))   # batch 2 re-saves the list
        assert isolated_db.load_lines(did)[0]["en"] == "Hi there."


class TestAdoptIds:
    def test_restoring_an_old_snapshot_keeps_notes(self, isolated_db):
        """Snapshots/versions from before Step 2 have no ids -- restoring
        one must not delete every row (and every note with it)."""
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, _lines("a", "b", short=False))
        isolated_db.save_translation_notes(did, [{"line_idx": 1, "term": "b", "note": "on b"}])
        current = isolated_db.load_line_objects(did)
        current[1].flag = "idiom"
        isolated_db.save_lines(did, current)
        restored = adopt_ids([Line(idx=0, start=0, end=4, zh="a", en="old A"),
                              Line(idx=1, start=5, end=9, zh="b", en="old B")], current)
        isolated_db.save_lines(did, restored)
        rows = isolated_db.load_lines(did)
        assert [r["en"] for r in rows] == ["old A", "old B"]
        assert rows[1]["flag"] == "idiom"
        assert len(isolated_db.list_translation_notes(did)) == 1

    def test_a_merged_away_id_does_not_get_reattached_by_position(self, isolated_db):
        """Step 25l bug: restoring a snapshot taken before a merge must not
        fall back to matching the now-gone line by position -- idx shifts
        after a merge deletes a row, so that would silently reattach the
        snapshot's flag/notes to whatever unrelated line now sits at the
        old position, and shift every later note along with it."""
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=0.5, zh="a", en="A"),
                                      Line(idx=1, start=0.6, end=1.1, zh="b", en="B"),
                                      Line(idx=2, start=10.0, end=14.0, zh="c", en="C"),
                                      Line(idx=3, start=20.0, end=24.0, zh="d", en="D")])
        current = isolated_db.load_line_objects(did)
        current[2].flag, current[2].flag_note = "idiom", "note on c"
        current[3].flag, current[3].flag_note = "ambiguous", "note on d"
        isolated_db.save_lines(did, current)

        pre_merge = isolated_db.load_line_objects(did)
        isolated_db.save_line_history_snapshot(did, pre_merge, "before merge")
        snap_id = isolated_db.list_line_history(did)[0]["id"]

        merged = merge_adjacent_short_lines(isolated_db.load_line_objects(did))
        assert [ln.zh for ln in merged] == ["ab", "c", "d"]   # "a"+"b" merged into one line
        isolated_db.save_lines(did, merged)

        rows = isolated_db.get_line_history_snapshot(snap_id)
        restored = adopt_ids([Line(**r) for r in rows], isolated_db.load_line_objects(did))
        isolated_db.save_lines(did, restored)

        by_zh = {r["zh"]: r for r in isolated_db.load_lines(did)}
        assert by_zh["a"]["flag"] is None
        assert by_zh["b"]["flag"] is None and by_zh["b"]["flag_note"] == ""
        assert by_zh["c"]["flag"] == "idiom" and by_zh["c"]["flag_note"] == "note on c"
        assert by_zh["d"]["flag"] == "ambiguous" and by_zh["d"]["flag_note"] == "note on d"


OLD_SCHEMA = """
CREATE TABLE lines (id INTEGER PRIMARY KEY AUTOINCREMENT, drama_id INTEGER NOT NULL, idx INTEGER,
                    start REAL, end REAL, zh TEXT, en TEXT, speaker TEXT, dub_filename TEXT);
CREATE TABLE translation_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, drama_id INTEGER NOT NULL,
    line_idx INTEGER, term TEXT, note_type TEXT, note TEXT, created_at TEXT,
    UNIQUE(drama_id, line_idx, term));
CREATE TABLE line_emotions (id INTEGER PRIMARY KEY AUTOINCREMENT, drama_id INTEGER NOT NULL,
    line_idx INTEGER NOT NULL, emotion TEXT, intensity REAL, note TEXT, created_at TEXT,
    UNIQUE(drama_id, line_idx));
CREATE TABLE reading_history (id INTEGER PRIMARY KEY AUTOINCREMENT, drama_id INTEGER NOT NULL,
    line_idx INTEGER, percent_complete REAL, accessed_at TEXT);
"""


class TestMigrationFromPositionKeys:
    """Existing libraries: notes/emotions/history keyed by line_idx get a
    line_id, with a backup first, and the whole thing safe to interrupt."""

    @pytest.fixture(autouse=True)
    def _restore_db_paths(self, monkeypatch):
        for name in ("LIBRARY_DIR", "DRAMAS_DIR", "DB_PATH", "BENCHMARK_DIR"):
            monkeypatch.setattr(db, name, getattr(db, name))

    def _old_library(self, tmp_path):
        db.configure_library_dir(str(tmp_path))
        conn = sqlite3.connect(db.DB_PATH)
        conn.executescript(OLD_SCHEMA + """
            CREATE TABLE dramas (id INTEGER PRIMARY KEY AUTOINCREMENT, title_en TEXT, title_zh TEXT);
            INSERT INTO dramas (id, title_en) VALUES (1, 'D');
            INSERT INTO lines (id, drama_id, idx, zh) VALUES (10, 1, 0, 'a'), (11, 1, 1, 'b');
            INSERT INTO translation_notes (drama_id, line_idx, term, note)
                VALUES (1, 1, 't', 'on b'), (1, 9, 'gone', 'no such line');
            INSERT INTO line_emotions (drama_id, line_idx, emotion) VALUES (1, 0, 'sad');
            INSERT INTO reading_history (drama_id, line_idx, percent_complete) VALUES (1, 1, 50);
        """)
        conn.commit()
        conn.close()

    def _table_rows(self, table):
        conn = sqlite3.connect(db.DB_PATH)
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        conn.close()
        return rows

    def test_migrates_backs_up_and_is_idempotent(self, tmp_path):
        self._old_library(tmp_path)
        db.init_db()
        notes = {n["term"]: n for n in db.list_translation_notes(1)}
        assert notes["t"]["line_id"] == 11 and notes["t"]["line_idx"] == 1
        assert notes["gone"]["line_id"] is None  # kept, not silently dropped
        assert db.load_emotions(1) == {0: {"emotion": "sad", "intensity": None, "note": ""}}
        assert db.list_reading_history(1)[0]["line_id"] == 11
        for t in ("translation_notes", "line_emotions", "reading_history"):
            assert self._table_rows(f"_backup_step2_{t}")
        db.init_db()  # second start: no-op, nothing duplicated
        assert len(db.list_translation_notes(1)) == 2

    def test_an_interrupted_migration_leaves_the_old_data_and_can_rerun(self, tmp_path, monkeypatch):
        self._old_library(tmp_path)
        real_ddl = dict(db._LINE_REF_TABLE_DDL)
        broken = dict(real_ddl)
        broken["line_emotions"] = "CREATE TABLE {name} (this is not valid sql"
        monkeypatch.setattr(db, "_LINE_REF_TABLE_DDL", broken)
        with pytest.raises(sqlite3.OperationalError):
            db.init_db()
        # translation_notes was rebuilt earlier in the same transaction --
        # rolled back with the rest, so it's still the old shape
        conn = sqlite3.connect(db.DB_PATH)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(translation_notes)")}
        conn.close()
        assert "line_id" not in cols
        assert len(self._table_rows("translation_notes")) == 2

        monkeypatch.setattr(db, "_LINE_REF_TABLE_DDL", real_ddl)
        db.init_db()
        assert {n["term"]: n["line_id"] for n in db.list_translation_notes(1)} == {"t": 11, "gone": None}


class TestTranslationIdsAreLineIds:
    def test_the_model_is_asked_for_the_lines_real_ids(self, isolated_db):
        """R5's id-keyed prompts used 1..n within each batch; they now use
        the permanent line id."""
        import translate_engines as te
        did = isolated_db.create_drama(title_en="D")
        untranslated = _lines("你好", "再见", short=False)
        for ln in untranslated:
            ln.en = ""
        isolated_db.save_lines(did, untranslated)
        lines = isolated_db.load_line_objects(did)
        seen = {}

        def call_model(numbered):
            seen["numbered"] = numbered
            return "{" + ", ".join(f'"{ln.id}": "T{ln.idx}"' for ln in lines) + "}"

        class Engine:
            supports_reference = True
            def translate_batch(self, zh_lines, context):
                return te._request_translations_with_retry(
                    zh_lines, context.get("speaker_labels"), call_model,
                    line_ids=context.get("line_ids"))

        te.translate_lines_with_engine(lines, Engine(), drama_meta={})
        assert f"{lines[0].id}. 你好" in seen["numbered"]
        assert [ln.en for ln in lines] == ["T0", "T1"]


def test_translate_job_whose_lines_were_replaced_records_no_version(isolated_db, monkeypatch):
    import background_jobs
    import translate_engines
    from tabs.workspace_tab import run_translate_job
    did = isolated_db.create_drama(title_en="D", status="aligned")
    isolated_db.save_lines(did, _lines("a", short=False))
    job_lines = isolated_db.load_line_objects(did)
    # A new transcription lands while the job runs: every line replaced
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="new")])
    monkeypatch.setattr(translate_engines, "translate_lines_with_engine",
                        lambda lines, engine, **kw: (lines, []))
    job_id = "test_replaced_lines"
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    run_translate_job(job_id, did, job_lines, object(), {"id": did}, "", None, False, "en-US",
                      None, "", "claude", "audio_drama")
    assert background_jobs.get_status(job_id)["result"]["lines_replaced"] is True
    assert isolated_db.list_translation_versions(did) == []
    assert isolated_db.get_drama(did)["status"] == "aligned"
    background_jobs._jobs.pop(job_id, None)


class TestRestoreSavedLines:
    """Step 25c item 2: the one restore path Restore and Activate share."""

    def _current(self):
        a = Line(idx=0, start=0, end=1, zh="a", en="A now", speaker="Hero", speaker_manual=True, id=10)
        b = Line(idx=1, start=1, end=2, zh="b", en="B now", speaker="SPEAKER_01", id=11)
        return [a, b]

    def test_a_snapshot_records_and_restores_speaker_manual(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, self._current()[:1])
        isolated_db.save_line_history_snapshot(did, isolated_db.load_line_objects(did), "s")
        (h,) = isolated_db.list_line_history(did)
        snap = isolated_db.get_line_history_snapshot(h["id"])
        assert snap[0]["speaker_manual"] is True
        vid = isolated_db.save_translation_version(did, isolated_db.load_line_objects(did), "v")
        assert isolated_db.get_translation_version(vid)["lines"][0]["speaker_manual"] is True

    def test_an_older_snapshot_without_the_field_keeps_a_same_speaker_correction(self):
        old_rows = [{"id": 10, "idx": 0, "start": 0, "end": 1, "zh": "a", "en": "A then",
                     "speaker": "Hero"},
                    {"id": 11, "idx": 1, "start": 1, "end": 2, "zh": "b", "en": "B then",
                     "speaker": "SPEAKER_01"}]
        restored = restore_saved_lines(old_rows, self._current())
        assert [(l.en, l.speaker, l.speaker_manual) for l in restored] == [
            ("A then", "Hero", True), ("B then", "SPEAKER_01", False)]

    def test_activate_on_the_same_lines_changes_only_the_translation(self):
        version = [{"id": 10, "idx": 0, "start": 5, "end": 6, "zh": "old a", "en": "A v1",
                    "speaker": "SPEAKER_00"},
                   {"id": 11, "idx": 1, "start": 6, "end": 7, "zh": "old b", "en": "B v1",
                    "speaker": "SPEAKER_00"}]
        restored = restore_saved_lines(version, self._current(), translation_only=True)
        assert [(l.id, l.start, l.zh, l.en, l.speaker, l.speaker_manual) for l in restored] == [
            (10, 0, "a", "A v1", "Hero", True), (11, 1, "b", "B v1", "SPEAKER_01", False)]

    def test_activate_over_a_different_line_structure_restores_the_whole_version(self):
        version = [{"id": 10, "idx": 0, "start": 0, "end": 2, "zh": "ab", "en": "AB v1",
                    "speaker": "Hero"}]
        restored = restore_saved_lines(version, self._current(), translation_only=True)
        assert [(l.id, l.end, l.zh, l.en, l.speaker_manual) for l in restored] == [
            (10, 2, "ab", "AB v1", True)]
