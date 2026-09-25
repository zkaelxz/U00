"""
app_help.py -- "App Assistant": ask "where is X" or "is this a bug"
questions about Baihe itself, right in the app, instead of grepping the
real source by hand every time (the exact repeated pattern this feature
was built to replace). Modeled on qa.py's ask_about_drama, down to the
same grounded/"say so honestly" discipline and the same multi-engine
dispatch (qa._dispatch_chat) -- the one thing that's genuinely
different here is what grounds it: not a hand-maintained settings doc
(which would drift out of sync with the UI the same way any duplicated
documentation does), but the app's own real tabs/*_tab.py source,
walked fresh every time a question is asked.
"""

import ast
import glob
import os

import qa

TABS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tabs")


def _string_literal(node) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _help_kwarg(call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg == "help":
            text = _string_literal(kw.value)
            if text:
                return text
    return None


def extract_module_sections(source: str) -> list:
    """[{"heading": ..., "captions": [...]}, ...] for one tabs/*_tab.py
    module's source, walked via the real AST (not a regex over the text,
    which would silently mis-parse a multi-line call or an f-string) --
    every `st.subheader(...)`/`st.expander(...)` call starts a new
    section, and every `st.caption(...)` call or `help=` keyword after it
    (up to the next section) is folded into that section's own
    description. Returns [] on a syntax error rather than raising --
    grounding should degrade gracefully, not break every question just
    because one tab module fails to parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "st"]
    calls.sort(key=lambda n: (n.lineno, n.col_offset))

    sections = []
    current = None
    for call in calls:
        attr = call.func.attr
        if attr in ("subheader", "header", "title", "expander"):
            text = call.args[0] if call.args else None
            text = _string_literal(text) if text is not None else None
            if text:
                current = {"heading": text, "captions": []}
                sections.append(current)
                continue
        if current is None:
            continue
        if attr == "caption" and call.args:
            text = _string_literal(call.args[0])
            if text:
                current["captions"].append(text)
        help_text = _help_kwarg(call)
        if help_text:
            current["captions"].append(help_text)
    return sections


def build_grounding_context(tabs_dir: str = None) -> str:
    """Regenerated from the real tab-module source every time it's
    called -- never cached, so a section added to a tab shows up here
    with no change to this module itself."""
    tabs_dir = tabs_dir or TABS_DIR
    parts = []
    for path in sorted(glob.glob(os.path.join(tabs_dir, "*_tab.py"))):
        module_name = os.path.splitext(os.path.basename(path))[0]
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        sections = extract_module_sections(source)
        if not sections:
            continue
        parts.append(f"## {module_name}")
        for s in sections:
            desc = " ".join(s["captions"])
            parts.append(f"- {s['heading']}" + (f": {desc}" if desc else ""))
    return "\n".join(parts)


def ask_about_app(question: str, engine, chat_history=None, tabs_dir: str = None) -> str:
    """chat_history: same {"role", "content"} list shape as
    qa.ask_about_drama. Grounded in build_grounding_context()'s current
    output -- never invents a setting/tab/button name that isn't in it."""
    grounding = build_grounding_context(tabs_dir)
    system_prompt = (
        "You help someone find a setting or feature inside this app (a "
        "subtitle/translation tool called Baihe), or judge whether "
        "something they describe sounds like a real bug versus expected "
        "behavior. Answer ONLY using the section list below, extracted "
        "directly from the app's own current UI code -- never invent a "
        "setting, tab, or button name that isn't in this list. If nothing "
        "plausibly matches what's being asked about, say so plainly rather "
        "than guessing, and suggest using the 'Copy a report for the "
        "developer' action instead.\n\n"
        f"App sections (tab module -- section heading -- what it does):\n\n{grounding}"
    )
    messages = list(chat_history or [])
    messages.append({"role": "user", "content": question})
    return qa._dispatch_chat(system_prompt, messages, engine)


def format_help_report(question: str, answer: str, diagnostics_report: str) -> str:
    """One paste-able block combining this exchange with the existing
    'Copy diagnostics for support' report -- extends that report rather
    than inventing a second, separate support channel (this is a
    personal-use, two-Claude-session project with no issue tracker)."""
    return (
        "App Assistant exchange:\n"
        f"Q: {question}\n"
        f"A: {answer}\n\n"
        "Diagnostics:\n"
        f"{diagnostics_report}"
    )
