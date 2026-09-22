"""
ui_theme.py -- the design system: shared CSS and layout primitives so
every tab looks like part of the same application.

Design decisions, and why:

- ONE accent colour (a muted violet) carries emphasis. When five things
  are highlighted, nothing is. Everything else is neutral greys.
- A fixed spacing scale (4/8/16/24/32) instead of ad-hoc margins, so
  vertical rhythm stays consistent down a long page.
- Progressive disclosure: a workflow with ten steps shows the step
  you're on, not all ten at once. Streamlit's default is to render
  everything always, which is exactly what made the Workspace tab feel
  like a wall.
- Status is shown as state, not as prose. A coloured pill reads faster
  than a sentence, and survives being skimmed.
- Generous line-height and a capped content width, because long-form
  text at full monitor width is genuinely harder to read.
"""

ACCENT = "#7C5CBF"
ACCENT_SOFT = "#EFEAFA"
INK = "#1F1F23"
MUTED = "#6B6B76"
BORDER = "#E4E4EA"
SURFACE = "#FFFFFF"

STATUS_COLORS = {
    "not started": ("#8A8A94", "#F2F2F4"),
    "aligned":     ("#2F6FBF", "#E8F1FB"),
    "translated":  ("#7C5CBF", "#EFEAFA"),
    "dubbed":      ("#2E8A6B", "#E6F5EF"),
    "exported":    ("#1F1F23", "#EDEDF0"),
}


DARK = {
    "bg": "#16161A", "surface": "#1E1E24", "ink": "#E8E8EC",
    "muted": "#9A9AA4", "border": "#2E2E36",
    "accent": "#A98BE8", "accent_soft": "#2A2338",
}


def inject_dark_css():
    """Dark theme for the whole app.

    Streamlit's own theme comes from .streamlit/config.toml, which can't be
    changed at runtime -- so this overrides the surfaces directly. Kept as a
    separate injection so light mode stays the clean default with no
    override cost."""
    import streamlit as st
    d = DARK
    st.markdown(f"""
    <style>
      .stApp, .main, .block-container {{ background: {d['bg']} !important; color: {d['ink']} !important; }}
      section[data-testid="stSidebar"] {{ background: {d['surface']} !important; }}
      h1, h2, h3, h4, p, li, label, .stMarkdown {{ color: {d['ink']} !important; }}
      .stCaption, [data-testid="stCaptionContainer"], small {{ color: {d['muted']} !important; }}

      div[data-testid="stExpander"] {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}
      div[data-testid="stMetric"] {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}

      .stTextInput input, .stTextArea textarea, .stNumberInput input,
      .stSelectbox div[data-baseweb="select"] > div,
      .stMultiSelect div[data-baseweb="select"] > div {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border-color: {d['border']} !important; }}

      .stButton button {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border-color: {d['border']} !important; }}
      .stButton button:hover {{ border-color: {d['accent']} !important; color: {d['accent']} !important; }}
      .stButton button[kind="primary"] {{ background: {d['accent']} !important; color: #17131F !important; }}

      .stTabs [data-baseweb="tab-list"] {{ border-bottom-color: {d['border']} !important; }}
      .stTabs [data-baseweb="tab"] {{ color: {d['muted']} !important; }}
      .stTabs [aria-selected="true"] {{
          background: {d['accent_soft']} !important; color: {d['accent']} !important; }}

      div[data-testid="stDataFrame"], div[data-testid="stTable"],
      div[data-testid="stDataEditor"] {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}
      code, pre {{ background: {d['surface']} !important; color: {d['ink']} !important; }}

      /* Alert boxes (success/info/warning/error) -- used constantly throughout
         the app, and had no dark styling at all: this was probably the single
         biggest source of "still looks white" complaints. */
      div[data-testid="stAlert"] {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border: 1px solid {d['border']} !important; }}
      div[data-testid="stAlert"] p {{ color: {d['ink']} !important; }}

      /* Checkboxes, radios, toggles -- including the dark-mode switch itself,
         which was rendering in light styling even while dark mode was on. */
      .stCheckbox, .stRadio, .stToggle {{ color: {d['ink']} !important; }}
      .stCheckbox label, .stRadio label, .stToggle label {{ color: {d['ink']} !important; }}
      [data-baseweb="checkbox"] > div:first-child,
      [data-baseweb="radio"] > div:first-child {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}

      /* Sliders */
      div[data-testid="stSlider"] {{ color: {d['ink']} !important; }}
      div[data-testid="stSlider"] [data-baseweb="slider"] > div:first-child {{
          background: {d['border']} !important; }}
      div[data-testid="stTickBar"] {{ color: {d['muted']} !important; }}

      /* File uploader -- the drag-and-drop dropzone defaults to a light box */
      div[data-testid="stFileUploaderDropzone"] {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}
      div[data-testid="stFileUploaderDropzone"] * {{ color: {d['ink']} !important; }}

      /* Progress bar track (the fill already uses the accent colour via
         Streamlit's own theming; only the empty track needed overriding) */
      div[data-testid="stProgress"] > div > div {{ background: {d['border']} !important; }}

      /* Popovers render their panel in a portal that can sit outside the
         normal .stApp subtree, so this needs a broader selector than the
         rest of the sheet to actually reach it. */
      div[data-baseweb="popover"] {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border: 1px solid {d['border']} !important; }}

      /* Chat elements (Reader's in-app Q&A) */
      div[data-testid="stChatMessage"] {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}
      div[data-testid="stChatInput"] textarea {{
          background: {d['surface']} !important; color: {d['ink']} !important; }}

      .bh-section {{ border-bottom-color: {d['border']} !important; }}
      .bh-section-title {{ color: {d['ink']} !important; }}
      .bh-empty {{
          background: {d['surface']} !important; border-color: {d['border']} !important;
          color: {d['muted']} !important; }}
      .bh-empty-title {{ color: {d['ink']} !important; }}
      .bh-stage-item {{
          background: {d['surface']} !important; border-color: {d['border']} !important;
          color: {d['muted']} !important; }}
      .bh-stage-done {{ background: {d['accent_soft']} !important; color: {d['accent']} !important; }}
      .bh-stage-current {{ background: {d['accent']} !important; color: #17131F !important; }}
    </style>
    """, unsafe_allow_html=True)


def inject_css():
    """Global stylesheet. Called once per page load from app.py."""
    import streamlit as st
    st.markdown(f"""
    <style>
      /* --- rhythm & density ------------------------------------------- */
      .block-container {{ padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1400px; }}
      h1 {{ font-size: 1.65rem !important; font-weight: 640 !important;
            letter-spacing: -0.02em; margin-bottom: 0.15rem !important; }}
      h2 {{ font-size: 1.18rem !important; font-weight: 620 !important;
            margin-top: 1.6rem !important; letter-spacing: -0.01em; }}
      h3 {{ font-size: 1.0rem !important; font-weight: 600 !important; color: {INK}; }}
      p, .stMarkdown {{ line-height: 1.62; }}

      /* --- tabs: quieter, less chrome --------------------------------- */
      .stTabs [data-baseweb="tab-list"] {{ gap: 2px; border-bottom: 1px solid {BORDER}; }}
      .stTabs [data-baseweb="tab"] {{
          height: 42px; padding: 0 16px; background: transparent;
          border-radius: 8px 8px 0 0; font-size: 0.92rem; font-weight: 520;
          color: {MUTED};
      }}
      .stTabs [aria-selected="true"] {{ background: {ACCENT_SOFT} !important; color: {ACCENT} !important; }}

      /* --- expanders read as cards, not accordions -------------------- */
      div[data-testid="stExpander"] {{
          border: 1px solid {BORDER}; border-radius: 10px; background: {SURFACE};
          margin-bottom: 10px; box-shadow: 0 1px 2px rgba(20,20,30,0.03);
      }}
      div[data-testid="stExpander"] summary {{ font-weight: 560; font-size: 0.94rem; }}

      /* --- restrained buttons ----------------------------------------- */
      .stButton button {{
          border-radius: 8px; font-weight: 540; font-size: 0.9rem;
          border: 1px solid {BORDER}; transition: all .12s ease;
      }}
      .stButton button:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
      .stButton button[kind="primary"] {{ border: none; box-shadow: 0 1px 3px rgba(124,92,191,.28); }}

      /* --- metrics: calmer ------------------------------------------- */
      div[data-testid="stMetric"] {{
          background: {SURFACE}; border: 1px solid {BORDER};
          border-radius: 10px; padding: 12px 14px;
      }}
      div[data-testid="stMetricValue"] {{ font-size: 1.35rem !important; font-weight: 620; }}
      div[data-testid="stMetricLabel"] {{ font-size: .78rem !important; color: {MUTED}; }}

      /* --- inputs ----------------------------------------------------- */
      .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {{
          border-radius: 8px !important; border-color: {BORDER} !important;
      }}
      .stCaption, [data-testid="stCaptionContainer"] {{ color: {MUTED}; }}

      /* --- custom primitives ------------------------------------------ */
      .bh-pill {{
          display: inline-block; padding: 2px 10px; border-radius: 999px;
          font-size: .74rem; font-weight: 600; letter-spacing: .02em;
      }}
      .bh-section {{
          display: flex; align-items: baseline; gap: 10px;
          margin: 26px 0 6px 0; padding-bottom: 6px; border-bottom: 1px solid {BORDER};
      }}
      .bh-section-title {{ font-size: 1.05rem; font-weight: 620; color: {INK}; }}
      .bh-section-hint {{ font-size: .82rem; color: {MUTED}; }}
      .bh-empty {{
          text-align: center; padding: 40px 20px; color: {MUTED};
          border: 1px dashed {BORDER}; border-radius: 12px; background: {SURFACE};
      }}
      .bh-empty-title {{ font-weight: 600; color: {INK}; margin-bottom: 4px; }}
      .bh-stage {{
          display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 18px 0;
      }}
      .bh-stage-item {{
          padding: 5px 12px; border-radius: 8px; font-size: .82rem;
          border: 1px solid {BORDER}; color: {MUTED}; background: {SURFACE};
      }}
      .bh-stage-done {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-color: {ACCENT_SOFT}; }}
      .bh-stage-current {{ background: {ACCENT}; color: white; border-color: {ACCENT}; font-weight: 600; }}
    </style>
    """, unsafe_allow_html=True)

    if st.session_state.get("app_dark_mode"):
        inject_dark_css()


def section(title: str, hint: str = ""):
    """A section heading with an optional one-line explanation. Replaces
    the numbered `st.subheader("5. Translation")` pattern, which made a
    long page read as a form to fill out top-to-bottom."""
    import streamlit as st
    hint_html = f'<span class="bh-section-hint">{hint}</span>' if hint else ""
    st.markdown(
        f'<div class="bh-section"><span class="bh-section-title">{title}</span>{hint_html}</div>',
        unsafe_allow_html=True)


def status_pill(status: str) -> str:
    """Returns HTML for a status pill. Colour-coded so a library table
    can be skimmed rather than read."""
    fg, bg = STATUS_COLORS.get(status, STATUS_COLORS["not started"])
    return f'<span class="bh-pill" style="color:{fg};background:{bg};">{status}</span>'


def stage_indicator(stages, current_index: int):
    """Horizontal progress through a multi-step workflow -- shows what's
    done, where you are, and what's ahead, without rendering every step's
    controls at once."""
    import streamlit as st
    items = []
    for i, name in enumerate(stages):
        cls = ("bh-stage-item bh-stage-done" if i < current_index else
               "bh-stage-item bh-stage-current" if i == current_index else
               "bh-stage-item")
        items.append(f'<span class="{cls}">{name}</span>')
    st.markdown(f'<div class="bh-stage">{"".join(items)}</div>', unsafe_allow_html=True)


def empty_state(title: str, hint: str = ""):
    """A deliberate empty state instead of a bare st.info(). An empty
    screen should say what to do next, not just that it's empty."""
    import streamlit as st
    hint_html = f"<div>{hint}</div>" if hint else ""
    st.markdown(
        f'<div class="bh-empty"><div class="bh-empty-title">{title}</div>{hint_html}</div>',
        unsafe_allow_html=True)


def stage_for_drama(drama: dict, has_lines: bool) -> int:
    """Maps a drama's state onto the workflow stages, so the UI can open
    on the step you're actually up to rather than always starting at 1."""
    status = (drama or {}).get("status", "not started")
    if status in ("exported", "dubbed"):
        return 4
    if status == "translated":
        return 3
    if status == "aligned" or has_lines:
        return 2
    return 0 if not (drama or {}).get("audio_filename") else 1
