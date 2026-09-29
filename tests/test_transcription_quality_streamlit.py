"""Streamlit widget, AppTest and tab-source tests split out of tests/test_transcription_quality.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_transcription_quality.py."""



class TestSenseVoiceTags:

    def test_workspace_shows_both_columns(self, isolated_db):
        import sensevoice_tags as sv
        from core import Line
        from streamlit.testing.v1 import AppTest
        did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                       content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="好的", en="OK")])
        isolated_db.save_emotions(did, {0: {"emotion": "sad", "intensity": 0.5, "note": ""}})
        [line] = isolated_db.load_line_objects(did)
        sv.save_audio_tags(isolated_db.drama_dir(did), {line.id: {"emotion": "happy", "events": []}})

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()
        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        tables = [t.value for t in at.dataframe if "Audio emotion" in t.value.columns]
        assert tables, "no side-by-side table"
        row = tables[0].iloc[0]
        assert (row["Text-based"], row["Audio emotion"], bool(row["Disagree?"])) == ("sad", "happy", True)
