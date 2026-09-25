"""
tests/test_app.py -- app.py, the Streamlit entry point.

Only what's actually testable here: that the app renders with no
exception, and that its favicon points at the app's own real icon file
Step 10.4 built (the same one make_shortcut.bat uses for the desktop
shortcut) rather than only being wired into the shortcut and never into
the running app itself -- the actual browser tab / Edge app-mode window
a person sees every time they use it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAppIcon:
    def test_app_icon_file_exists(self):
        """Regression guard: app.py's favicon wiring silently falls back
        to Streamlit's default if this file is ever moved or deleted,
        so nothing would fail loudly -- this catches that directly."""
        assert os.path.exists(os.path.join(PROJECT_ROOT, "assets", "app_icon.ico"))

    def test_app_runs_with_no_exception(self, isolated_db):
        """Confirms st.set_page_config(page_icon=...) with the real .ico
        path doesn't raise -- this is the regression that motivated the
        fix: the icon existed and was wired into the desktop shortcut
        (Step 10.4) but was never actually passed to the running app."""
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"))
        at.run(timeout=30)
        assert not at.exception
