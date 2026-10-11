"""
tests/test_subprocess_encoding.py -- a subprocess call that decodes its output
as text must say which encoding. Without one, Python uses the machine's
locale code page (cp1252/cp932/cp936 on Windows), so a child that prints UTF-8
(nvidia-smi, git, pip, ffmpeg) turns CJK names into mojibake or raises
UnicodeDecodeError on a reader thread.

installer/service.py, installer/service_control.py (the service's command runner)
and installer/smoke_child.py are allowed to omit it: they read the console's OEM
code page on purpose, which is what sc.exe and icacls print in.

Calls that pass their options through **kwargs are not checked; the scan cannot
see what the dict holds.

Retire this guard when every text-mode subprocess call goes through one helper
(lib/proc.py) that sets the encoding itself.
"""
import ast
import os

from tests.test_split_guards import _SKIP_DIRS, _rel, _walk_files
from tests.test_static_analysis import PROJECT_ROOT

_SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}
_TEXT_KEYWORDS = {"text", "universal_newlines"}
_ALLOWED = {"installer/service.py", "installer/service_control.py", "installer/smoke_child.py"}


def _is_true(node):
    return isinstance(node, ast.Constant) and node.value is True


def _text_calls_without_encoding(tree):
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _SUBPROCESS_CALLS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        text_mode = any(_is_true(keywords.get(name)) for name in _TEXT_KEYWORDS)
        if text_mode and "encoding" not in keywords:
            found.append(node.lineno)
    return found


def test_text_mode_subprocess_calls_name_their_encoding():
    offenders = []
    for path in _walk_files(PROJECT_ROOT, (".py",), skip=_SKIP_DIRS | {"tests"}):
        rel = _rel(path)
        if rel in _ALLOWED:
            continue
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        offenders += [f"{rel}:{line}" for line in _text_calls_without_encoding(tree)]
    assert not offenders, (
        "subprocess call decodes text without encoding=\"utf-8\": " + ", ".join(offenders))


def test_the_scan_flags_a_text_call_without_encoding():
    tree = ast.parse("import subprocess\nsubprocess.run(['x'], text=True)\n"
                     "subprocess.run(['x'], text=True, encoding='utf-8')\n"
                     "subprocess.run(['x'], capture_output=True)\n")
    assert _text_calls_without_encoding(tree) == [2]
