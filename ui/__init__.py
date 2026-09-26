"""
ui/ -- shared UI component layer (Step 13).

Scaffolding for the information-architecture redesign (Steps 14-19), not a
rebuild of any existing tab. Nothing in tabs/*.py imports from here yet --
each tab wires these components in as it's rebuilt. Until then:

- ui.project_state:   the unified `st.session_state.project` model.
- ui.project_header:  the always-visible project name / stage / settings
                       header.
- ui.workflow:        the pipeline-stage stepper (checkmark / dot / circle
                       per stage).
- ui.status:          the shared background-job status block, including
                       the friendly-failure-card state.

These only read/render; the actual business logic (core.py,
translate_engines.py, dub.py, scanlate.py, background_jobs.py, ...) is
untouched.
"""
