"""
Baihe Audio Drama Subtitler -- Library Edition
------------------------------------------------
Local tool for translating, subtitling, and (optionally) AI-dubbing many
Chinese/Japanese/Korean audio dramas, novels, and comics at scale.

This file just wires up the tabs; each tab's actual UI logic lives in
tabs/*.py. Shared imports/names live in common.py.

For 50-100+ dramas, see cli.py for headless/unattended batch runs.

Run with:  streamlit run app.py
"""

from common import st
import ui_theme

# Imported individually so one tab failing to import (a syntax error, a
# missing module) doesn't prevent every other tab from loading.
_tab_import_errors = {}
for _name in ("settings_tab", "library_tab", "reader_tab", "scanlate_tab",
              "navigator_tab", "discover_tab", "workspace_tab", "diagnostics_tab"):
    try:
        __import__(f"tabs.{_name}")
    except Exception as _exc:
        _tab_import_errors[_name] = _exc
import tabs

st.set_page_config(page_title="Baihe Audio Drama Subtitler", layout="wide")
# The settings sidebar must render BEFORE the stylesheet is injected: the
# dark-mode toggle lives there, and injecting CSS first would always paint
# with the previous value -- which is why it used to take two flips.
try:
    tabs.settings_tab.render_settings_sidebar()
except Exception as exc:
    st.sidebar.error(f"Settings panel failed to load: {exc}")

ui_theme.inject_css()
st.title("Baihe Studio")
st.caption("Translate, subtitle, dub, and read Chinese, Japanese, and Korean works.")

def _safe_render(render_fn, tab_name: str, module_key: str):
    """Runs one tab's renderer with its failure contained.

    Streamlit executes every tab's render function on each page load, so an
    unhandled exception anywhere takes the whole page down -- Library,
    Workspace and Reader all become unusable because Scanlate raised. This
    keeps a failure inside the tab that caused it, and shows the traceback
    there so it's still diagnosable rather than silently swallowed."""
    if module_key in _tab_import_errors:
        st.error(f"The {tab_name} tab could not be loaded.")
        st.caption(f"{type(_tab_import_errors[module_key]).__name__}: "
                  f"{_tab_import_errors[module_key]}")
        st.caption("Check the Diagnostics tab for missing files or dependencies.")
        return
    try:
        render_fn()
    except Exception as exc:
        st.error(f"The {tab_name} tab hit an error. The rest of the app is unaffected.")
        st.caption(f"{type(exc).__name__}: {exc}")
        with st.expander("Details (useful if reporting this)"):
            import traceback
            st.code(traceback.format_exc(), language="text")
        st.caption("If this mentions a missing module, check the Diagnostics tab -- "
                  "it lists which optional dependencies are installed.")


if "active_drama_id" not in st.session_state:
    st.session_state.active_drama_id = None
if "lines" not in st.session_state:
    st.session_state.lines = None

tab_library, tab_workspace, tab_reader, tab_scanlate, tab_navigator, tab_discover, tab_diagnostics = st.tabs(
    ["📚 Library", "🛠️ Workspace", "📖 Read & Watch", "🖼️ Scanlate", "🧭 Navigator", "🔎 Discover", "🩺 Diagnostics"])

with tab_library:
    _safe_render(tabs.library_tab.render_library_tab, "Library", "library_tab")

with tab_reader:
    _safe_render(tabs.reader_tab.render_reader_tab, "Read & Watch", "reader_tab")

with tab_scanlate:
    _safe_render(tabs.scanlate_tab.render_scanlate_tab, "Scanlate", "scanlate_tab")

with tab_navigator:
    _safe_render(tabs.navigator_tab.render_navigator_tab, "Navigator", "navigator_tab")

with tab_discover:
    _safe_render(tabs.discover_tab.render_discover_tab, "Discover", "discover_tab")

with tab_workspace:
    _safe_render(tabs.workspace_tab.render_workspace_tab, "Workspace", "workspace_tab")

with tab_diagnostics:
    _safe_render(tabs.diagnostics_tab.render_diagnostics_tab, "Diagnostics", "diagnostics_tab")
