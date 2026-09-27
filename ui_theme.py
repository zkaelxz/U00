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


# Step 12: a type scale for the tabs that aren't being rebuilt by the
# redesign steps (Library, Scanlate, Live). 16px body and a ~1.25 ratio
# between steps: caption -> body/sub-label -> tab heading. Scoped to
# those tabs via type_scale_scope() for now; Step 13 applies the same
# figures app-wide.
TYPE_SCALE = {"caption": "0.875rem", "body": "1rem", "heading": "1.25rem"}


def type_scale_scope():
    """Opts the current tab into TYPE_SCALE. Drops an invisible marker the
    stylesheet keys on with :has(), since a Streamlit tab panel has no
    per-tab class of its own to target. st.html, not st.markdown: an
    empty-looking markdown block is rendered as a placeholder and the
    span never reaches the page."""
    import streamlit as st
    st.html('<span class="bh-typescale"></span>')


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
      /* The expander's clickable header bar is a <summary> with its own
         explicit light-theme background -- `background` doesn't inherit
         from the outer div above, so without this the header stayed a
         plain white/light bar even with a dark body underneath it. */
      div[data-testid="stExpander"] summary {{
          background: {d['surface']} !important; color: {d['ink']} !important; }}

      div[data-testid="stMetric"] {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}

      /* Step 68: input/select surfaces keyed on Streamlit's own data-testid
         roots, not on BaseWeb's [data-baseweb] markup alone -- Streamlit 1.59
         replaced the BaseWeb select with a react-aria ComboBox (and dropped
         BaseWeb from these widgets entirely), so a data-baseweb-only rule
         matched nothing on any newer install and left every selectbox white.
         Both markups are listed so either Streamlit generation is covered. */
      .stTextInput input, .stTextArea textarea, .stNumberInput input,
      .stSelectbox div[data-baseweb="select"] > div,
      .stMultiSelect div[data-baseweb="select"] > div,
      [data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"],
      [data-testid="stTextInputRootElement"] > div, [data-testid="stTextAreaRootElement"] > div,
      [data-testid="stNumberInputContainer"]:not(:has([aria-invalid="true"])),
      [data-testid="stNumberInputContainer"]:not(:has([aria-invalid="true"])) > div,
      [data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"],
      [data-testid="stSelectbox"] [role="group"], [data-testid="stMultiSelect"] [role="group"],
      [data-testid="stChatInput"] > div, [data-testid="stChatInput"] textarea {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border-color: {d['border']} !important; }}
      [data-testid="stSelectbox"] input, [data-testid="stMultiSelect"] input,
      [data-testid="stTextInputRootElement"] input, [data-testid="stTextAreaRootElement"] textarea,
      [data-testid="stNumberInputContainer"] input, [data-testid="stChatInput"] textarea {{
          color: {d['ink']} !important; -webkit-text-fill-color: {d['ink']} !important; }}
      [data-testid="stSelectbox"] input::placeholder, [data-testid="stMultiSelect"] input::placeholder,
      [data-testid="stTextInputRootElement"] input::placeholder,
      [data-testid="stTextAreaRootElement"] textarea::placeholder,
      [data-testid="stChatInput"] textarea::placeholder {{
          color: {d['muted']} !important; -webkit-text-fill-color: {d['muted']} !important; }}
      [data-testid="stSelectbox"] svg, [data-testid="stMultiSelect"] svg,
      [data-testid="stNumberInputStepUp"] svg, [data-testid="stNumberInputStepDown"] svg {{
          color: {d['muted']} !important; fill: currentColor; }}
      /* The small "Press Enter to apply" hint inside text/number inputs. */
      [data-testid="InputInstructions"] {{ color: {d['muted']} !important; }}
      /* A multiselect's chosen-value chips. */
      [data-testid="stMultiSelect"] [data-tag], [data-testid="stMultiSelect"] span[data-baseweb="tag"] {{
          background: {d['accent_soft']} !important; color: {d['ink']} !important; }}

      /* The open option list of a selectbox/multiselect renders in a portal
         outside .stApp (the "Choose an option" dropdown) -- its own testids
         on Streamlit >= 1.59, a [role=listbox] (BaseWeb's ul) before that. */
      [data-testid="stSelectboxVirtualDropdown"], [data-testid="stMultiSelectDropdown"],
      [data-baseweb="popover"] ul[role="listbox"], [data-baseweb="menu"],
      [data-baseweb="popover"] > div, [data-baseweb="popover"] > div > div {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border-color: {d['border']} !important; }}
      [data-testid="stSelectboxVirtualDropdown"] [role="option"],
      [data-testid="stMultiSelectDropdown"] [role="option"],
      [role="listbox"] [role="option"], [role="listbox"] li {{
          background: transparent !important; color: {d['ink']} !important; }}
      [role="listbox"] [role="option"]:hover, [role="listbox"] [role="option"][data-focused],
      [role="listbox"] [role="option"][aria-selected="true"], [role="listbox"] li:hover,
      [role="listbox"] li[aria-selected="true"] {{
          background: {d['accent_soft']} !important; color: {d['ink']} !important; }}
      /* A disabled/readonly input's text is painted via
         -webkit-text-fill-color in Chrome/WebKit, not `color` -- Streamlit
         sets that to its own light-theme ink at reduced opacity for the
         disabled state, which silently wins over the `color` override
         above and left every read-only box's text unreadable (dark text on
         a dark background). */
      .stTextInput input:disabled, .stTextArea textarea:disabled, .stNumberInput input:disabled {{
          -webkit-text-fill-color: {d['ink']} !important; opacity: 1 !important; }}

      .stButton button {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border-color: {d['border']} !important; }}
      .stButton button:hover {{ border-color: {d['accent']} !important; color: {d['accent']} !important; }}
      .stButton button[kind="primary"] {{ background: {d['accent']} !important; color: #17131F !important; }}
      /* Step 68: the other secondary-style buttons -- download, link, form
         submit, the file uploader's "Browse files", and st.pills -- aren't
         inside .stButton, so the rule above never reached them. */
      [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"],
      [data-testid="stBaseLinkButton-secondary"], [data-testid="stBaseButton-pills"],
      [data-testid="stButtonGroup"] button:not([aria-checked="true"]) {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border-color: {d['border']} !important; }}
      [data-testid="stBaseButton-pillsActive"],
      [data-testid="stButtonGroup"] button[aria-checked="true"] {{
          background: {d['accent_soft']} !important; color: {d['accent']} !important;
          border-color: {d['accent']} !important; }}
      [data-testid="stBaseButton-secondary"]:hover, [data-testid="stBaseButton-secondaryFormSubmit"]:hover,
      [data-testid="stBaseLinkButton-secondary"]:hover, [data-testid="stBaseButton-pills"]:hover {{
          border-color: {d['accent']} !important; color: {d['accent']} !important; }}
      [data-testid="stBaseButton-secondary"] *, [data-testid="stBaseButton-secondaryFormSubmit"] *,
      [data-testid="stBaseLinkButton-secondary"] *, [data-testid="stBaseButton-pills"] *,
      [data-testid="stButtonGroup"] button * {{ color: inherit !important; }}

      /* The top toolbar strip (Deploy / main menu) stayed light over a dark page. */
      header[data-testid="stHeader"] {{ background: {d['bg']} !important; }}
      [data-testid="stHeader"] button, [data-testid="stBaseButton-headerNoPadding"],
      [data-testid="stBaseButton-headerNoPadding"] [data-testid="stIconMaterial"],
      [data-testid="stTextInputRootElement"] [data-testid="stIconMaterial"] {{
          color: {d['ink']} !important; }}
      /* The hover toolbar over tables (download/search/fullscreen). */
      [data-testid="stElementToolbarButtonContainer"] {{ background: {d['surface']} !important; }}

      /* Markdown links kept the light theme's dark blue -- low contrast here. */
      [data-testid="stMarkdownContainer"] a {{ color: {d['accent']} !important; }}
      /* st.text() paints its span in the light theme's ink, which was
         invisible on a dark page (Translate tab's History entries). */
      [data-testid="stText"], [data-testid="stText"] * {{ color: {d['ink']} !important; }}
      [data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{ color: {d['ink']} !important; }}
      [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {{ color: {d['muted']} !important; }}

      /* A popover's trigger (e.g. Reader's "Story" button) is its own
         `stPopoverButton` testid, not `.stButton` -- it rendered as a
         plain white button even in dark mode without this. */
      button[data-testid="stPopoverButton"] {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border-color: {d['border']} !important; }}
      button[data-testid="stPopoverButton"]:hover {{
          border-color: {d['accent']} !important; color: {d['accent']} !important; }}

      .stTabs [role="tablist"] {{ border-bottom-color: {d['border']} !important; }}
      .stTabs [data-testid="stTab"] {{ color: {d['muted']} !important; }}
      .stTabs [aria-selected="true"] {{
          background: {d['accent_soft']} !important; color: {d['accent']} !important; }}

      /* Only the frame: st.dataframe/st.data_editor draw their cells on a
         <canvas> (Glide Data Grid) themed from Streamlit's own light theme in
         JavaScript -- no CSS reaches inside it (checked in Step 68: its
         --gdg-* custom properties are outputs, not inputs), so the grid stays
         a readable light panel until Streamlit's native dark theme is used. */
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
      /* Same boxes on Streamlit >= 1.59 (react-aria, no data-baseweb): only
         the unchecked state, so a ticked box keeps its accent fill. */
      [data-testid="stCheckbox"]:has(input[type="checkbox"]:not(:checked):not([role="switch"])) label > div:not([data-testid]),
      [data-testid="stRadioOption"]:not([data-selected]) > div > div,
      [data-testid="stRadioOption"]:not([data-selected]) > div > div > div,
      [data-testid="stCheckbox"]:has(input:not(:checked)) label[data-baseweb="checkbox"] > span,
      [data-baseweb="radio"] > div:first-child > div {{
          background: {d['surface']} !important; border-color: {d['muted']} !important; }}
      [data-testid="stRadioOption"]:not([data-selected]) > div > div {{
          box-shadow: inset 0 0 0 1px {d['muted']} !important; }}

      /* Sliders */
      div[data-testid="stSlider"] {{ color: {d['ink']} !important; }}
      div[data-testid="stSlider"] [data-baseweb="slider"] > div:first-child {{
          background: {d['border']} !important; }}
      div[data-testid="stTickBar"] {{ color: {d['muted']} !important; }}

      /* File uploader -- the drag-and-drop dropzone defaults to a light box */
      /* No tag qualifier: the dropzone is a <section>, not a <div>, so the
         old div[...] form of this rule never matched. */
      [data-testid="stFileUploaderDropzone"] {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}
      [data-testid="stFileUploaderDropzone"] * {{ color: {d['ink']} !important; }}

      /* Progress bar track (the fill already uses the accent colour via
         Streamlit's own theming; only the empty track needed overriding) */
      div[data-testid="stProgress"] > div > div {{ background: {d['border']} !important; }}

      /* Popovers render their panel in a portal that can sit outside the
         normal .stApp subtree, so this needs a broader selector than the
         rest of the sheet to actually reach it. */
      div[data-baseweb="popover"] {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border: 1px solid {d['border']} !important; }}
      /* Step 68: the popover *content panel* (Reader's "Story", Review's
         "Checks"/"AI refinement"/"Restructure lines", ...). Streamlit >= 1.59
         renders it as its own stPopoverBody, not a BaseWeb popover, so only
         the trigger button above was dark and every panel stayed white. */
      [data-testid="stPopoverBody"] {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border: 1px solid {d['border']} !important; }}

      /* Hover tooltips (every help="..." icon) and a number input's
         out-of-range error tooltip. The blanket `p` rule above already turns
         their text light, but their panel kept the light theme's white --
         white text on white. Covers the react-aria tooltips (>= 1.59) and
         BaseWeb's (before). */
      [data-testid="stTooltipContent"], [data-testid="stTooltipErrorContent"],
      [data-baseweb="tooltip"] > div, [data-baseweb="tooltip"] > div > div {{
          background: {d['surface']} !important; color: {d['ink']} !important;
          border: 1px solid {d['border']} !important; }}
      [data-testid="stTooltipErrorContent"], [data-testid="stTooltipErrorContent"] * {{
          color: #FF8A8A !important; }}

      /* Chat elements (Reader's in-app Q&A) */
      div[data-testid="stChatMessage"] {{
          background: {d['surface']} !important; border-color: {d['border']} !important; }}
      div[data-testid="stChatInput"] textarea {{
          background: {d['surface']} !important; color: {d['ink']} !important; }}
      [data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li {{ color: {d['ink']} !important; }}

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


# Step 68: the Reader's line table is its own iframe document, which
# inject_dark_css() can't reach -- it takes a theme name instead. "match app"
# (the default) follows the app's one Dark mode switch, so turning dark mode
# on no longer leaves the Reader light until a second, separate setting is
# found and changed too.
READER_THEME_OPTIONS = ["match app", "light", "sepia", "dark"]


def resolve_reader_theme(choice: str, app_dark_mode: bool) -> str:
    """The reader.THEMES key to render with: an explicit light/sepia/dark
    choice wins; "match app" (or anything unrecognized) follows dark mode."""
    if choice in ("light", "sepia", "dark"):
        return choice
    return "dark" if app_dark_mode else "light"


# Step 68: a popover's content panel (Review's "Checks", "AI refinement",
# "Restructure lines", Reader's "Story") is rendered by Streamlit as a
# position:fixed element attached to <body> -- outside the main area's own
# scroll container. So a mouse wheel over the panel never reaches the page:
# once the panel itself can't scroll any further (or doesn't overflow at
# all), the wheel does nothing, and with a full-width panel covering most of
# the page the user simply "can't scroll down" while it's open. This passes
# that leftover wheel movement on to the page, and only then -- a panel with
# its own overflow still scrolls itself first. Not a dark-mode issue: it's
# injected in both themes.
_POPOVER_WHEEL_JS = """
<span class="bh-hidden"></span>
<script>
(() => {
  if (window.__bhPopoverWheel) return;
  window.__bhPopoverWheel = true;
  const canScroll = (el, dy) => {
    const oy = getComputedStyle(el).overflowY;
    if (oy !== "auto" && oy !== "scroll") return false;
    return dy > 0 ? el.scrollTop + el.clientHeight < el.scrollHeight - 1 : el.scrollTop > 0;
  };
  document.addEventListener("wheel", (e) => {
    const panel = e.target.closest && e.target.closest('[data-testid="stPopoverBody"]');
    if (!panel || e.ctrlKey) return;
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    for (let el = e.target; el && el !== panel.parentElement; el = el.parentElement) {
      if (canScroll(el, dy)) return;
    }
    const main = document.querySelector('[data-testid="stMain"]');
    if (!main) return;
    main.scrollBy({ top: dy });
    e.preventDefault();
  }, { capture: true, passive: false });
})();
</script>
"""


def inject_popover_scroll_passthrough():
    """Installs _POPOVER_WHEEL_JS once per page. Needs st.html's
    unsafe_allow_javascript (Streamlit >= 1.56); skipped quietly on older
    versions rather than taking the whole app down with a TypeError."""
    import streamlit as st
    try:
        st.html(_POPOVER_WHEEL_JS, unsafe_allow_javascript=True)
    except TypeError:
        pass


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
      .stTabs [role="tablist"] {{ gap: 2px; border-bottom: 1px solid {BORDER}; }}
      .stTabs [data-testid="stTab"] {{
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

      /* --- popovers with collapsible sections: a constant height -------
         Step 68: Streamlit re-anchors a popover panel to its trigger on every
         frame while its content animates, and flips it to the other side of
         the trigger once it fits there -- so opening/closing an expander
         inside one (Reader's "Story tools", Review's "Checks") slid the panel
         down and snapped it back up, reading as the page jumping. Reserving
         the height up front means toggling a section scrolls inside the
         panel instead of resizing and repositioning it. Streamlit's own
         inline max-height still caps it where the viewport is shorter. */
      [data-testid="stPopoverBody"]:has([data-testid="stExpander"]) {{
          height: min(70vh, 640px) !important; }}

      /* --- type scale, for tabs that opt in via type_scale_scope() ---- */
      div[data-testid="stElementContainer"]:has(.bh-typescale),
      div[data-testid="stElementContainer"]:has(.bh-hidden) {{ display: none; }}
      [role="tabpanel"]:has(.bh-typescale) h3 {{
          font-size: {TYPE_SCALE['heading']} !important; font-weight: 620 !important; }}
      [role="tabpanel"]:has(.bh-typescale) p,
      [role="tabpanel"]:has(.bh-typescale) li,
      [role="tabpanel"]:has(.bh-typescale) div[data-testid="stExpander"] summary {{
          font-size: {TYPE_SCALE['body']}; }}
      [role="tabpanel"]:has(.bh-typescale) [data-testid="stCaptionContainer"],
      [role="tabpanel"]:has(.bh-typescale) [data-testid="stCaptionContainer"] p {{
          font-size: {TYPE_SCALE['caption']}; }}
    </style>
    """, unsafe_allow_html=True)

    inject_popover_scroll_passthrough()
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
