"""
tests/test_step20_ux_polish.py -- Step 20: UX polish (keyboard shortcuts,
toast feedback, transcript search) in Review & edit.

Shortcuts themselves aren't automated here (that's Step 20's own manual
check -- Streamlit's shortcut= dispatch is a frontend/browser concern
AppTest doesn't drive), but the logic each shortcut/feature is built on
is: the shared page-jump math (_page_for_line), next/previous-flagged
navigation (now tests/test_review_lines_service.py), transcript search (_search_transcript),
and the toast-vs-message split for confirmations.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Line
from tabs.workspace_tab import _page_for_line, _search_transcript


def _lines(n):
    return [Line(idx=i, start=float(i), end=float(i) + 1.0, zh="你好", en="Hello")
            for i in range(n)]


class TestPageForLine:
    def test_first_page(self):
        lines = _lines(25)
        assert _page_for_line(0, lines, page_size=10) == 1
        assert _page_for_line(9, lines, page_size=10) == 1

    def test_later_page(self):
        lines = _lines(25)
        assert _page_for_line(10, lines, page_size=10) == 2
        assert _page_for_line(24, lines, page_size=10) == 3

    def test_uses_position_in_the_list_not_the_idx_value(self):
        # A filtered/reordered list -- idx values don't have to be
        # contiguous or in list order for the page math to still work off
        # position, matching how _jump_to_review_page always looks this up
        # against the full, unfiltered list.
        lines = [Line(idx=5, start=0.0, end=1.0, zh="a", en="A"),
                 Line(idx=2, start=1.0, end=2.0, zh="b", en="B"),
                 Line(idx=9, start=2.0, end=3.0, zh="c", en="C")]
        assert _page_for_line(2, lines, page_size=1) == 2
        assert _page_for_line(9, lines, page_size=1) == 3

    def test_missing_idx_defaults_to_first_page(self):
        lines = _lines(5)
        assert _page_for_line(999, lines, page_size=10) == 1



class TestSearchTranscript:
    def test_finds_a_term_in_the_translated_text(self):
        lines = _lines(5)
        lines[3].en = "The zebraword appears here."
        matches = _search_transcript(lines, "zebraword")
        assert [ln.idx for ln in matches] == [3]

    def test_finds_a_term_in_the_source_text(self):
        lines = _lines(5)
        lines[1].zh = "独特词汇"
        matches = _search_transcript(lines, "独特")
        assert [ln.idx for ln in matches] == [1]

    def test_case_insensitive(self):
        lines = _lines(3)
        lines[0].en = "Hello ZEBRA there"
        assert [ln.idx for ln in _search_transcript(lines, "zebra")] == [0]
        assert [ln.idx for ln in _search_transcript(lines, "ZEBRA")] == [0]

    def test_empty_term_matches_nothing(self):
        lines = _lines(3)
        assert _search_transcript(lines, "") == []
        assert _search_transcript(lines, "   ") == []

    def test_no_match_returns_empty(self):
        lines = _lines(3)
        assert _search_transcript(lines, "nonexistent-word") == []

    def test_matches_in_line_order(self):
        lines = _lines(5)
        lines[3].en = "marker here"
        lines[1].en = "marker there"
        assert [ln.idx for ln in _search_transcript(lines, "marker")] == [1, 3]


class TestTranscriptSearchJumpsToCorrectPage:
    """Exit criteria: searching for a word/phrase jumps to the correct
    line, using the same page-jump logic (_page_for_line, via
    _jump_to_line_button) as the existing flagged-item jump buttons."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        lines = _lines(25)
        lines[12].en = "The zebraword appears only here."
        isolated_db.save_lines(did, lines)
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["review_page_size"] = 10
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_search_jumps_to_the_matching_lines_page(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        [t for t in at.text_input if t.key == f"rv_search_{did}"][0].set_value("zebraword").run()
        jump_buttons = [b for b in at.button if b.key == "jump_search_12"]
        assert jump_buttons, "expected a jump button next to the matching line"
        jump_buttons[0].click()
        at.run(timeout=30)
        # Line 12 (0-based) is the 13th line; with review_page_size=10
        # that's position 12 -> page (12 // 10) + 1 == 2.
        assert at.session_state["review_page"] == 2

    def test_no_match_shows_no_jump_button(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        [t for t in at.text_input if t.key == f"rv_search_{did}"][0].set_value(
            "nonexistent-term").run()
        assert not [b for b in at.button if (b.key or "").startswith("jump_search_")]


class TestAutoQCToastVsWarning:
    """Exit criteria: a pure-confirmation action shows a toast, while a
    real result the user needs to act on (here: a flagged mismatch) still
    shows a message they have to see, not just a toast."""

    def _drama(self, isolated_db, lines):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, lines)
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_nothing_to_flag_shows_a_toast(self, isolated_db):
        did = self._drama(isolated_db, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        at = self._run(did)
        [b for b in at.button if b.key == f"run_auto_qc_{did}"][0].click()
        at.run(timeout=30)
        assert any("found nothing" in t.value for t in at.toast)
        assert not any("flagged" in w.value.lower() for w in at.warning)

    def test_a_real_mismatch_stays_a_warning_not_a_toast(self, isolated_db):
        did = self._drama(isolated_db, [Line(idx=0, start=0.0, end=1.0,
                                              zh="我有三个苹果", en="I have apples")])
        at = self._run(did)
        [b for b in at.button if b.key == f"run_auto_qc_{did}"][0].click()
        at.run(timeout=30)
        assert any("flagged" in w.value.lower() for w in at.warning)
        assert not any("found nothing" in t.value for t in at.toast)
