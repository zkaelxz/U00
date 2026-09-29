"""Streamlit widget, AppTest and tab-source tests split out of tests/test_export_formats.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_export_formats.py."""

import subtitle_formats as sf
from core import Line


def _lines():
    return [Line(idx=0, start=0.0, end=2.5, zh="你好", en="Hello there.", speaker="SPEAKER_00"),
            Line(idx=1, start=3.0, end=5.25, zh="再见", en="Goodbye.", speaker="SPEAKER_01")]


class TestOverlapClamp:

    def test_export_panel_warns_but_does_not_silently_flag_on_render(self, isolated_db):
        """Step 6d: detecting an overlap on every render is fine (read-only),
        but writing the flag used to happen unconditionally too -- straight
        from whatever st.session_state.lines held, with no user action. That's
        exactly how a stale-lines bug elsewhere could reach the database as a
        bogus flag before anyone noticed. Merely opening the page must not
        write anything."""
        from streamlit.testing.v1 import AppTest
        did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                       content_mode="audio_drama", status="translated")
        lines = _lines()
        lines[0].end = 3.4
        isolated_db.save_lines(did, lines)

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()
        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)

        row = isolated_db.load_lines(did)[0]
        assert row["flag"] is None  # not written just from rendering the page
        assert row["end"] == 3.4
        assert any("overlap the next one" in w.value for w in at.warning)

        [b for b in at.button if b.key == f"flag_overlaps_{did}"][0].click()
        at.run(timeout=30)
        row = isolated_db.load_lines(did)[0]
        assert row["flag"] == sf.OVERLAP_FLAG and "3.00s" in row["flag_note"]
        assert row["end"] == 3.4
