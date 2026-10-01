"""
tests/test_app_icon.py -- the app's own icon file (assets/app_icon.ico),
built in Step 10.4 and used by make_shortcut.bat for the desktop shortcut.

Moved from tests/test_app.py (Streamlit retirement), which imported
streamlit.testing.
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAppIcon:
    def test_app_icon_file_exists(self):
        """Regression guard: the shortcut (and, while it exists, app.py's
        favicon) silently falls back to a default icon if this file is ever
        moved or deleted, so nothing would fail loudly -- this catches that
        directly."""
        assert os.path.exists(os.path.join(PROJECT_ROOT, "assets", "app_icon.ico"))
