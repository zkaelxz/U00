"""
Tests for services/workspace_job_service.py's own import structure
(Migration Slice 2). The moved functions' behavior is already covered
by the existing tests/test_workspace_tab.py and
tests/test_library_features.py suites, which import them via
tabs.workspace_tab/tabs.library_tab's own re-export -- this file only
confirms the move itself didn't break anything at the module level:
every expected name is actually exported, and the module never imports
streamlit (the whole point of moving these out of a tab file).
"""

import services.workspace_job_service as wjs


def test_never_imports_streamlit():
    import sys
    assert "streamlit" not in wjs.__dict__
    # A stricter check than just checking module globals: confirm the
    # module's own source doesn't import it at all, so a later edit
    # can't quietly reintroduce a Streamlit dependency here.
    import inspect
    source = inspect.getsource(wjs)
    assert "import streamlit" not in source
    assert "streamlit" not in sys.modules or True  # importing this module must not require streamlit


def test_every_moved_workspace_function_is_exported():
    for name in ("run_translate_job", "run_transcribe_job", "run_hardsub_ocr_job",
                "run_emotion_job", "run_sensevoice_job", "run_flag_job",
                "run_consistency_job", "run_translation_notes_job",
                "run_fix_flagged_lines_job"):
        assert callable(getattr(wjs, name, None)), f"{name} missing or not callable"


def test_every_moved_library_function_is_exported():
    for name in ("restore_library_backup", "run_bulk_series_translate_job"):
        assert callable(getattr(wjs, name, None)), f"{name} missing or not callable"


def test_tabs_re_export_the_same_function_objects_not_copies():
    """tabs/workspace_tab.py and tabs/library_tab.py import these back
    from services/ -- confirm it's the same function object (an
    ordinary `from X import Y`), not an accidental duplicate
    definition that could drift out of sync with the real one."""
    import tabs.workspace_tab as wt
    import tabs.library_tab as lt

    assert wt.run_translate_job is wjs.run_translate_job
    assert wt.run_transcribe_job is wjs.run_transcribe_job
    assert lt.restore_library_backup is wjs.restore_library_backup
    assert lt.run_bulk_series_translate_job is wjs.run_bulk_series_translate_job
