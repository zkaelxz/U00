"""
tests/test_dark_mode_consistency.py -- Step 46: dark mode left several real
UI surfaces on their light-theme colors even with the toggle on.

Confirmed by driving Streamlit 1.64.0 (this project's pinned version) in a
real headless browser rather than guessing from the CSS alone:

- An `st.expander`'s clickable header is a <summary> element with its own
  explicit light-theme background. `background` doesn't inherit from a
  parent, so overriding only the outer `div[data-testid="stExpander"]`
  left the header bar a plain light/white stripe above a dark body -- this
  covers Reader's "Watch / listen", "My notes" and "Vocabulary export".
- An `st.popover` trigger button (Reader's "Story" bar) has its own
  `stPopoverButton` testid, not `.stButton` -- it rendered as a plain
  white button even in dark mode.
- A disabled/readonly `st.text_area`/`st.text_input` (Standalone
  Translate's "Source text (read-only)", among others) paints its text via
  `-webkit-text-fill-color` in Chrome/WebKit, not `color` -- Streamlit sets
  that to its own light-theme ink at reduced opacity for the disabled
  state, which silently won over the plain `color` override and left the
  text unreadable (dark-on-dark).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest


def _dark_css():
    def _render():
        import ui_theme
        ui_theme.inject_dark_css()
    at = AppTest.from_function(_render)
    at.run(timeout=30)
    [md] = at.markdown
    return md.value


def test_expander_header_bar_gets_a_dark_background():
    css = _dark_css()
    assert 'div[data-testid="stExpander"] summary' in css
    # Must actually set a background, not just color -- background is what
    # was missing (it doesn't inherit from the outer div's own override).
    idx = css.index('div[data-testid="stExpander"] summary')
    rule = css[idx: css.index("}", idx)]
    assert "background:" in rule


def test_popover_trigger_button_gets_dark_styling():
    css = _dark_css()
    assert 'stPopoverButton' in css
    idx = css.index('stPopoverButton')
    rule = css[idx: css.index("}", idx)]
    assert "background:" in rule and "color:" in rule


def test_disabled_text_inputs_override_webkit_text_fill_color():
    css = _dark_css()
    for selector in (".stTextInput input:disabled", ".stTextArea textarea:disabled",
                      ".stNumberInput input:disabled"):
        assert selector in css, f"missing dark-mode rule for {selector}"
    idx = css.index(".stTextInput input:disabled")
    rule = css[idx: css.index("}", idx)]
    assert "-webkit-text-fill-color" in rule
    assert "opacity: 1" in rule


def test_standalone_translate_source_box_is_a_plain_disabled_text_area():
    """The specific box from the bug report (`tabs/translate_tab.py`'s
    "Source text (read-only)") has to actually be a plain `st.text_area`
    for the shared `:disabled` selector above to reach it -- if it ever
    became a custom component this fix would silently stop covering it."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tabs", "translate_tab.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    idx = text.index("Source text (read-only)")
    call = text[max(0, idx - 40): idx + 200]
    assert "st.text_area(" in call
    assert "disabled=True" in call
