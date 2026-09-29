"""Streamlit widget, AppTest and tab-source tests split out of tests/test_workspace_job_service.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_workspace_job_service.py."""

import services.workspace_job_service as wjs


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
