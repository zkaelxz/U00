"""Streamlit widget, AppTest and tab-source tests split out of tests/test_dub.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_dub.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFillMissingVoices:
    """Step 25g item 4: the fallback both `cli.py dub` and Workspace
    section 8 use for characters with no voice picked."""

    def test_workspace_dub_button_uses_it_for_both_engines(self):
        import inspect
        from tabs import workspace_tab
        src = inspect.getsource(workspace_tab)
        # Step 26c: the pool is now the drama's source-language pool in
        # original-narration mode, English otherwise -- no longer a bare
        # call with the implicit (always-English) default pool.
        assert "fill_missing_voices(voice_map, speakers, _default_voice_pool)" in src
        assert "fill_missing_voices(\n                    offline_voice_map, speakers, " \
               "dub_module.DEFAULT_OFFLINE_VOICE_POOL)" in src


class TestDubWorkerArgumentBinding:
    """background_jobs appends result_queue as the LAST positional argument
    (`args=(*args, result_queue)`), but build_track_subprocess_worker
    declares result_queue right after offline_voice_map, before its
    keyword-default parameters. The Streamlit tab used to pass
    narrate_original/source_language positionally, which put the real queue
    in the wrong slot and broke dub generation from the tab (Step 26c
    onward). Callers must bind those two by keyword (functools.partial)."""

    def test_workspace_tab_binds_them_by_keyword(self):
        import re
        src = open(os.path.join(os.path.dirname(__file__), "..", "tabs", "workspace_tab.py"),
                   encoding="utf-8").read()
        assert re.search(r"functools\.partial\(dub_module\.build_track_subprocess_worker,\s*"
                         r"narrate_original=_narrate_original,\s*source_language=_source_lang\)", src)
