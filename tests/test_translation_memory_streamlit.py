"""Streamlit widget, AppTest and tab-source tests split out of tests/test_translation_memory.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_translation_memory.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Line


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
