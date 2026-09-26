"""
ui/project_state.py -- the unified project-state model (Step 13 item 2).

`app.py` and `reader_tab.py` currently each track "which drama is this"
their own way: `st.session_state.active_drama_id` app-wide, plus
`reader_tab.py`'s own `reader_drama_pick` selectbox and its
`reader_resume_pending`/`reader_resume_banner` reconciliation keys just to
stay in sync with it. Two sources of truth for the same concept.

This module introduces one coherent holder, `st.session_state["project"]`,
carrying project-*identity* state: which drama is current, what pipeline
stage it's on, per-stage status, the selected line/segment, the active
background job, and recent errors/warnings. Steps 14-19 migrate each tab
to read/write through this as they're rebuilt -- this step only adds the
model itself and touches nothing else. Narrowly-scoped keys (reader
display prefs, `settings_*` keys, widget-local keys like
`reader_drama_pick`) are deliberately untouched: this consolidates
project-identity state, not every session-state key in the app.

Every function here reads/writes exactly one key,
`st.session_state["project"]`, and nothing else.
"""

PROJECT_STATE_KEY = "project"


def _new_state() -> dict:
    """A fresh, empty project-state dict. Never shared/mutated in place
    across calls -- each caller that needs a default gets its own dict and
    its own nested containers, so two independent sessions (or two calls
    in a test) can't accidentally alias the same list/dict."""
    return {
        "drama_id": None,
        "current_stage": None,
        "stage_status": {},
        "selected_line_id": None,
        "active_job_id": None,
        "recent_errors": [],
        "recent_warnings": [],
    }


def get_project() -> dict:
    """Returns the current project-state dict, creating it with defaults
    on first access. Always the same dict object for the life of the
    session (mutate it in place, or use the setters below)."""
    import streamlit as st
    if PROJECT_STATE_KEY not in st.session_state:
        st.session_state[PROJECT_STATE_KEY] = _new_state()
    return st.session_state[PROJECT_STATE_KEY]


def reset_project():
    """Replaces project state with a fresh default -- e.g. when the app
    wants to fully clear the current project rather than switch to
    another one. Not currently called from any tab (that's Steps 14-19's
    job); provided so the model is complete on its own."""
    import streamlit as st
    st.session_state[PROJECT_STATE_KEY] = _new_state()
    return st.session_state[PROJECT_STATE_KEY]


def set_drama(drama_id):
    """Sets the current drama and clears the per-drama fields that no
    longer make sense once the drama changes (stage, selection, stage
    status) -- callers that want to preserve those across a re-set of the
    same drama_id should check first."""
    project = get_project()
    if project.get("drama_id") != drama_id:
        project["current_stage"] = None
        project["stage_status"] = {}
        project["selected_line_id"] = None
    project["drama_id"] = drama_id
    return project


def set_stage(stage):
    project = get_project()
    project["current_stage"] = stage
    return project


def set_stage_status(stage, status):
    """Records one stage's status (e.g. "done"/"current"/"not_started")
    without touching any other stage's."""
    project = get_project()
    project["stage_status"][stage] = status
    return project


def set_selected_line(line_id):
    project = get_project()
    project["selected_line_id"] = line_id
    return project


def set_active_job(job_id):
    project = get_project()
    project["active_job_id"] = job_id
    return project


def add_error(message: str):
    project = get_project()
    project["recent_errors"].append(message)
    return project


def add_warning(message: str):
    project = get_project()
    project["recent_warnings"].append(message)
    return project


def clear_errors_and_warnings():
    project = get_project()
    project["recent_errors"] = []
    project["recent_warnings"] = []
    return project
