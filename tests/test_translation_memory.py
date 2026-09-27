"""
tests/test_translation_memory.py -- Step 24 item 1: translation memory.

A translator's hand-approved translation is remembered per series and
offered back as a suggestion on a later exact or near-identical source
line -- never written to a line until the translator clicks Accept. Also
covers the find-and-replace hook: correcting a translation in bulk must
correct (or drop) the remembered copy too, or memory would keep
suggesting the mistake that was just fixed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Line
import translation_memory as tm


def _entry(source, translation, id_=1, use_count=1):
    return {"id": id_, "source_text": source, "translation": translation, "use_count": use_count}


class TestFindMatch:
    def test_exact_match(self):
        m = tm.find_match("师姐，你回来了", [_entry("师姐，你回来了", "Senior sister, you're back")])
        assert m["exact"] is True and m["similarity"] == 1.0
        assert m["entry"]["translation"] == "Senior sister, you're back"

    def test_whitespace_differences_still_count_as_exact(self):
        m = tm.find_match(" 师姐， 你回来了 ", [_entry("师姐，你回来了", "Senior sister, you're back")])
        assert m["exact"] is True

    def test_near_identical_line_matches(self):
        m = tm.find_match("师姐，你终于回来了", [_entry("师姐，你回来了", "Senior sister, you're back")])
        assert m is not None and m["exact"] is False
        assert tm.MIN_SIMILARITY <= m["similarity"] < 1.0

    def test_genuinely_different_line_gets_nothing(self):
        assert tm.find_match("今天天气很好", [_entry("师姐，你回来了", "Senior sister, you're back")]) is None

    def test_exact_match_beats_a_closer_looking_fuzzy_one(self):
        entries = [_entry("师姐，你回来了吗", "Are you back?", id_=1),
                   _entry("师姐，你回来了", "You're back", id_=2)]
        assert tm.find_match("师姐，你回来了", entries)["entry"]["id"] == 2

    def test_best_fuzzy_match_wins(self):
        entries = [_entry("师姐，今天你终于回来了呀", "far", id_=1),
                   _entry("师姐，你终于回来了呀", "near", id_=2)]
        assert tm.find_match("师姐，你终于回来了", entries)["entry"]["id"] == 2

    def test_empty_source_or_memory(self):
        assert tm.find_match("", [_entry("a", "b")]) is None
        assert tm.find_match("你好", []) is None


class TestSuggestForLines:
    def test_repeated_line_suggested_new_line_not(self):
        entries = [_entry("师姐，你回来了", "Senior sister, you're back")]
        lines = [Line(idx=0, start=0, end=1, zh="师姐，你回来了", en=""),
                 Line(idx=1, start=1, end=2, zh="今天天气很好", en="")]
        s = tm.suggest_for_lines(lines, entries)
        assert list(s) == [0]

    def test_line_already_using_the_remembered_translation_needs_no_suggestion(self):
        entries = [_entry("师姐，你回来了", "Senior sister, you're back")]
        lines = [Line(idx=0, start=0, end=1, zh="师姐，你回来了", en="Senior sister, you're back ")]
        assert tm.suggest_for_lines(lines, entries) == {}

    def test_suggesting_never_touches_the_line(self):
        entries = [_entry("师姐，你回来了", "Senior sister, you're back")]
        line = Line(idx=0, start=0, end=1, zh="师姐，你回来了", en="AI draft")
        tm.suggest_for_lines([line], entries)
        assert line.en == "AI draft"


class TestTranslationMemoryStorage:
    def test_record_then_same_pair_counts_another_use(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.record_translation_memory(sid, " 你好 ", "Hello")
        isolated_db.record_translation_memory(sid, "你好", "Hello")
        [row] = isolated_db.list_translation_memory(sid)
        assert (row["source_text"], row["translation"], row["use_count"]) == ("你好", "Hello", 2)

    def test_new_translation_for_same_source_replaces_and_restarts_count(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.record_translation_memory(sid, "你好", "Hello")
        isolated_db.record_translation_memory(sid, "你好", "Hello")
        isolated_db.record_translation_memory(sid, "你好", "Hi")
        [row] = isolated_db.list_translation_memory(sid)
        assert (row["translation"], row["use_count"]) == ("Hi", 1)

    def test_blank_or_seriesless_input_is_ignored(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.record_translation_memory(sid, "你好", "  ")
        isolated_db.record_translation_memory(sid, "", "Hello")
        isolated_db.record_translation_memory(None, "你好", "Hello")
        assert isolated_db.list_translation_memory(sid) == []

    def test_memory_is_scoped_per_series(self, isolated_db):
        a = isolated_db.get_or_create_series("A")
        b = isolated_db.get_or_create_series("B")
        isolated_db.record_translation_memory(a, "你好", "Hello")
        assert isolated_db.list_translation_memory(b) == []

    def test_bump_use(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.record_translation_memory(sid, "你好", "Hello")
        [row] = isolated_db.list_translation_memory(sid)
        isolated_db.bump_translation_memory_use(row["id"])
        assert isolated_db.list_translation_memory(sid)[0]["use_count"] == 2

    def test_replace_updates_matching_rows_only_in_that_series(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        other = isolated_db.get_or_create_series("Other")
        isolated_db.record_translation_memory(sid, "鲍勃", "Bob")
        isolated_db.record_translation_memory(sid, "你好", "Hello")
        isolated_db.record_translation_memory(other, "鲍勃", "Bob")
        assert isolated_db.update_translation_memory_after_replace(sid, "Bob ", "Alice") == 1
        by_src = {r["source_text"]: r["translation"] for r in isolated_db.list_translation_memory(sid)}
        assert by_src == {"鲍勃": "Alice", "你好": "Hello"}
        assert isolated_db.list_translation_memory(other)[0]["translation"] == "Bob"

    def test_replace_that_empties_the_translation_drops_the_row(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.record_translation_memory(sid, "嗯", "Um")
        isolated_db.update_translation_memory_after_replace(sid, "Um", "")
        assert isolated_db.list_translation_memory(sid) == []


class TestTranslationMemoryInReview:
    """The Review & edit page: suggestions shown, never auto-applied."""

    def _drama(self, isolated_db, lines):
        sid = isolated_db.get_or_create_series("Series")
        did = isolated_db.create_drama(title_en="Ep 2", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated", series_id=sid)
        isolated_db.save_lines(did, lines)
        return sid, did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _tm_infos(self, at):
        return [i.value for i in at.info if "Translation memory" in i.value]

    def _two_lines(self):
        return [Line(idx=0, start=0, end=1, zh="师姐，你终于回来了", en="Big sis, you came back"),
                Line(idx=1, start=1, end=2, zh="今天天气很好", en="Nice weather")]

    def test_suggestion_shown_for_near_identical_line_but_not_applied(self, isolated_db):
        sid, did = self._drama(isolated_db, self._two_lines())
        isolated_db.record_translation_memory(sid, "师姐，你回来了", "Senior sister, you're back")
        at = self._run(did)

        infos = self._tm_infos(at)
        assert len(infos) == 1  # the genuinely new line gets none
        assert "Senior sister, you're back" in infos[0] and "similar" in infos[0]
        assert isolated_db.load_line_objects(did)[0].en == "Big sis, you came back"
        assert at.text_area(key="en_0").value == "Big sis, you came back"

    def test_accept_applies_to_that_line_only_and_counts_a_use(self, isolated_db):
        sid, did = self._drama(isolated_db, self._two_lines())
        isolated_db.record_translation_memory(sid, "师姐，你终于回来了", "Senior sister, you're back")
        at = self._run(did)
        at.button(key=f"tm_accept_{did}_0").click().run()

        lines = isolated_db.load_line_objects(did)
        assert lines[0].en == "Senior sister, you're back"
        assert lines[1].en == "Nice weather"
        assert at.text_area(key="en_0").value == "Senior sister, you're back"
        assert isolated_db.list_translation_memory(sid)[0]["use_count"] == 2
        assert self._tm_infos(at) == []

    def test_dismiss_hides_it_without_changing_anything(self, isolated_db):
        sid, did = self._drama(isolated_db, self._two_lines())
        isolated_db.record_translation_memory(sid, "师姐，你终于回来了", "Senior sister, you're back")
        at = self._run(did)
        at.button(key=f"tm_dismiss_{did}_0").click().run()
        assert self._tm_infos(at) == []
        assert isolated_db.load_line_objects(did)[0].en == "Big sis, you came back"

    def test_no_series_means_no_memory(self, isolated_db):
        did = isolated_db.create_drama(title_en="Loose", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, self._two_lines())
        at = self._run(did)
        assert self._tm_infos(at) == []

    def test_hand_edit_saved_is_remembered(self, isolated_db):
        sid, did = self._drama(isolated_db, self._two_lines())
        at = self._run(did)
        at.text_area(key="en_0").set_value("Senior sister, you're back")
        [b for b in at.button if b.label == "💾 Save edits (this page)"][0].click().run()
        rows = isolated_db.list_translation_memory(sid)
        assert [(r["source_text"], r["translation"]) for r in rows] == [
            ("师姐，你终于回来了", "Senior sister, you're back")]

    def test_untouched_lines_are_not_remembered_on_save(self, isolated_db):
        sid, did = self._drama(isolated_db, self._two_lines())
        at = self._run(did)
        [b for b in at.button if b.label == "💾 Save edits (this page)"][0].click().run()
        assert isolated_db.list_translation_memory(sid) == []

    def test_find_and_replace_corrects_the_remembered_translation(self, isolated_db):
        sid, did = self._drama(isolated_db, [
            Line(idx=0, start=0, end=1, zh="鲍勃来了", en="Bob is here"),
            Line(idx=1, start=1, end=2, zh="今天天气很好", en="Nice weather")])
        isolated_db.record_translation_memory(sid, "鲍勃来了", "Bob is here")
        at = self._run(did)
        at.text_input(key=f"lines_fr_find_{did}").set_value("Bob").run()
        at.text_input(key=f"lines_fr_replace_{did}").set_value("Alice").run()
        [b for b in at.button if b.label == "🔍 Preview matches"][0].click().run()
        [b for b in at.button if b.label == "✅ Apply 1 change(s)"][0].click().run()

        assert isolated_db.load_line_objects(did)[0].en == "Alice is here"
        [row] = isolated_db.list_translation_memory(sid)
        assert row["translation"] == "Alice is here"
        assert self._tm_infos(at) == []  # the fixed mistake isn't suggested back
