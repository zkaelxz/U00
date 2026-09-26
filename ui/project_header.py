"""
ui/project_header.py -- the compact, always-visible project header
(Step 13 item 1).

Project name, pipeline-progress stepper, and a settings shortcut, reading
from the unified project-state model (ui/project_state.py) instead of the
ad-hoc `active_drama_id` lookups each tab currently does on its own.

Not wired into any tab yet -- Step 14 puts this at the top of the rebuilt
Workspace. The settings "shortcut" here is deliberately inert (it has no
tab to jump to until Step 16 consolidates Settings): it renders a button
and reports whether it was clicked, so a later step can act on that, but
takes no navigation action itself.
"""

from ui.workflow import render_stepper_from_index


def render_project_header(project: dict, drama: dict | None = None,
                          stages: list | None = None, key_prefix: str = "project_header"):
    """Renders the header.

    project: a dict shaped like ui.project_state.get_project()'s return
        value (or an equivalent fake in tests) -- only `current_stage` is
        read from it here.
    drama: the current drama's row (a dict with title_en/title_zh/id), or
        None when no project is open yet.
    stages: the ordered list of stage labels to show in the stepper.
        `project["current_stage"]` is looked up in this list by position;
        if it isn't a valid index, the stepper shows nothing started.
        Omit to render the name/settings shortcut without a stepper.

    Returns True if the settings-shortcut button was clicked this run,
    else False.
    """
    import streamlit as st

    name = _project_name(drama)
    cols = st.columns([5, 1])
    with cols[0]:
        st.markdown(f"**{name}**" if name else "*No project open*")
        if stages:
            current_index = project.get("current_stage") if project else None
            if not isinstance(current_index, int) or not (0 <= current_index < len(stages)):
                current_index = None
            render_stepper_from_index(stages, current_index)
    with cols[1]:
        clicked = st.button("⚙️", key=f"{key_prefix}_settings_shortcut",
                            help="Settings")
    return clicked


def _project_name(drama) -> str:
    if not drama:
        return ""
    return drama.get("title_en") or drama.get("title_zh") or (
        f"drama #{drama['id']}" if drama.get("id") is not None else "")
