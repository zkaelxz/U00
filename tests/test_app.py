"""
tests/test_app.py -- app.py, the Streamlit entry point.

Only what's actually testable here: that the app renders with no
exception with its favicon wired to the app's own icon file. The check
that the icon file exists moved to tests/test_app_icon.py; delete this file
together with app.py (docs/streamlit-retirement-plan.md section 9).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAppIcon:
    def test_app_runs_with_no_exception(self, isolated_db):
        """Confirms st.set_page_config(page_icon=...) with the real .ico
        path doesn't raise -- this is the regression that motivated the
        fix: the icon existed and was wired into the desktop shortcut
        (Step 10.4) but was never actually passed to the running app."""
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"))
        at.run(timeout=30)
        assert not at.exception
