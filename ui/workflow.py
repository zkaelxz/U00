"""
ui/workflow.py -- the pipeline-stage stepper (Step 13 item 1).

Shows where a project is in the pipeline -- done / current / not started
per stage -- so it can be reused in Workspace's header (Step 14) and in
Library's per-project list (Step 13 item 4, deferred to a follow-up once
Step 25d lands; not wired in by this step).

It reuses `ui_theme`'s existing
`bh-stage`/`bh-stage-item`/`bh-stage-done`/`bh-stage-current` classes
(already injected by `ui_theme.inject_css()`) rather than duplicating the
CSS, and adds an explicit checkmark / dot / circle marker per stage.
"""

STAGE_ICONS = {"done": "✓", "current": "●", "not_started": "○"}


def stage_statuses_from_index(stages, current_index):
    """Convenience for the common linear case: everything before
    current_index is done, current_index is current, everything after is
    not started. current_index of None means nothing has started yet."""
    if current_index is None:
        return ["not_started"] * len(stages)
    return [
        "done" if i < current_index else "current" if i == current_index else "not_started"
        for i in range(len(stages))
    ]


def render_stepper(stages, statuses):
    """Renders a horizontal pipeline stepper: one marker + label per
    stage. `stages` is a list of labels; `statuses` is a same-length list
    of "done"/"current"/"not_started" (any other value renders as
    not-started)."""
    import streamlit as st
    if len(stages) != len(statuses):
        raise ValueError("stages and statuses must be the same length")
    items = []
    for label, status in zip(stages, statuses):
        icon = STAGE_ICONS.get(status, STAGE_ICONS["not_started"])
        css_class = {
            "done": "bh-stage-item bh-stage-done",
            "current": "bh-stage-item bh-stage-current",
        }.get(status, "bh-stage-item")
        items.append(f'<span class="{css_class}">{icon} {label}</span>')
    st.markdown(f'<div class="bh-stage">{"".join(items)}</div>', unsafe_allow_html=True)


def render_stepper_from_index(stages, current_index):
    """render_stepper(), computing statuses from a single current-stage
    index -- the common case for a strictly linear pipeline."""
    render_stepper(stages, stage_statuses_from_index(stages, current_index))
