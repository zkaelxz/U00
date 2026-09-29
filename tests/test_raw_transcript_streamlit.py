"""Streamlit widget, AppTest and tab-source tests split out of tests/test_raw_transcript.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_raw_transcript.py."""

import raw_transcript as rt
from core import Line


SEGMENTS = [{"start": 0.0, "end": 0.5, "text": "你好"},
            {"start": 0.6, "end": 1.1, "text": "世界"},
            {"start": 5.0, "end": 9.0, "text": "再见"}]


def _transcribed_drama(isolated_db):
    did = isolated_db.create_drama(title_en="D")
    lines = [Line(idx=i, start=s["start"], end=s["end"], zh=s["text"]) for i, s in enumerate(SEGMENTS)]
    isolated_db.save_lines(did, lines)
    ddir = isolated_db.drama_dir(did)
    path = rt.write_raw_transcript(ddir, SEGMENTS, lines, backend="whisper", model="small",
                                   language="zh", mode="whisper")
    return did, ddir, path


class TestRestoreOneLineInReview:
    """The roadmap's exit condition, second half: a single line can be
    restored from the raw transcript, in the Review step."""

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

    def test_restore_button_puts_back_only_that_lines_original_text(self, isolated_db):
        did, ddir, path = _transcribed_drama(isolated_db)
        isolated_db.update_drama(did, media_type="audio_drama", content_mode="audio_drama",
                                 status="aligned")
        lines = isolated_db.load_line_objects(did)
        lines[0].zh, lines[2].zh = "typo one", "typo three"
        isolated_db.save_lines(did, lines)

        at = self._run(did)
        restore = [b for b in at.button if b.key == "rvrestore_2"]
        assert restore, "no restore option for an edited line"
        assert not [b for b in at.button if b.key == "rvrestore_1"]  # unchanged line: nothing to restore
        restore[0].click().run(timeout=30)

        rows = isolated_db.load_lines(did)
        assert [r["zh"] for r in rows] == ["typo one", "世界", "再见"]
        assert [t.value for t in at.text_area if t.key == "zh_2"] == ["再见"]
        assert rt.list_raw_transcripts(ddir) == [path]  # restoring never writes a new file
