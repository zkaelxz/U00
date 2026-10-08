"""Text-mode subprocess calls must name their encoding.

With text=True and no encoding=, Windows decodes with the locale code page
(cp932, cp1252). A byte it can't map raises inside subprocess's reader
thread, so stdout comes back None and the caller fails far from the cause.
"""
import ast
import json
import os
import types

import pytest

import diagnostics
import media_inspect
from services import loaded_models_service

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"tests", "frontend", "node_modules", ".claude", ".git", "venv", ".venv", "__pycache__"}
SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}

# (relative path, enclosing function) -> why it may omit encoding=. These calls
# still pass errors="replace" so a bad byte can't leave stdout as None.
ALLOWED_WITHOUT_ENCODING = {
    ("installer/service.py", "__call__"):
        "Runner runs Windows sc/netsh/icacls, which print in the OEM code page; "
        "UTF-8 would garble that. The output is only tail-logged.",
    ("installer/smoke_child.py", "_running"):
        "tasklist prints in the OEM code page; only checks that a PID appears.",
    ("installer/smoke_child.py", "breakaway_check"):
        "Child is this repo's own Python, which writes in the locale code page; "
        "only the leading ASCII 'ok' word is read.",
}


def _enclosing_functions(tree):
    owners = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                owners[id(node)] = fn.name
    return owners


def find_text_calls_without_encoding(source):
    """(line, enclosing function) of each subprocess call in text mode with no encoding=."""
    tree = ast.parse(source)
    owners = _enclosing_functions(tree)
    found = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in SUBPROCESS_CALLS
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "subprocess"):
            continue
        kws = {kw.arg: kw.value for kw in call.keywords}
        textual = any(k in kws and not (isinstance(kws[k], ast.Constant) and kws[k].value is False)
                      for k in ("text", "universal_newlines"))
        if textual and "encoding" not in kws:
            found.append((call.lineno, owners.get(id(call), "<module>")))
    return found


def _scan_project():
    hits = {}
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as f:
                    found = find_text_calls_without_encoding(f.read())
                if found:
                    hits[os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")] = found
    return hits


def test_text_mode_subprocess_calls_pass_encoding():
    problems = [f"{path}:{line} in {func}" for path, found in _scan_project().items()
                for line, func in found if (path, func) not in ALLOWED_WITHOUT_ENCODING]
    assert problems == [], f"subprocess text mode without encoding=: {problems}"


def test_allowlist_has_no_stale_entries():
    used = {(path, func) for path, found in _scan_project().items() for _, func in found}
    assert set(ALLOWED_WITHOUT_ENCODING) <= used


def test_checker_flags_missing_encoding():
    bad = "import subprocess\ndef f():\n    subprocess.run(['x'], capture_output=True, text=True)\n"
    assert find_text_calls_without_encoding(bad) == [(3, "f")]
    assert find_text_calls_without_encoding(
        bad.replace("text=True", "universal_newlines=True, errors='replace'")) == [(3, "f")]
    assert find_text_calls_without_encoding(bad.replace("text=True", "text=True, encoding='utf-8'")) == []
    assert find_text_calls_without_encoding(bad.replace("text=True", "check=True")) == []
    assert find_text_calls_without_encoding(bad.replace("run", "check_output")) == [(3, "f")]


# ---- decoding: the fake mimics subprocess, which decodes the child's raw bytes
# with encoding/errors from the call, or the (here ASCII-only) locale if absent.

def _fake_run(raw: bytes):
    def run(cmd, **kw):
        assert kw.get("text") or kw.get("universal_newlines") or kw.get("encoding")
        text = raw.decode(kw.get("encoding") or "ascii", kw.get("errors") or "strict")
        return types.SimpleNamespace(stdout=text, stderr="", returncode=0)
    return run


def test_run_ffprobe_decodes_utf8_cjk_filename(monkeypatch):
    name = "白河 第3話「夏祭り」.mp4"
    raw = json.dumps({"format": {"filename": name}}, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(media_inspect.subprocess, "run", _fake_run(raw))
    assert media_inspect.run_ffprobe(name)["format"]["filename"] == name


def test_nvidia_smi_load_decodes_utf8_output(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    raw = "37, 1024, 8192\n".encode("utf-8")
    monkeypatch.setattr(diagnostics.subprocess, "run", _fake_run(raw))
    assert diagnostics.external_gpu_load()["memory_total_mb"] == 8192.0


def test_nvidia_driver_info_keeps_utf8_gpu_name(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    raw = "NVIDIA 显卡 RTX, 551.23\n".encode("utf-8")
    monkeypatch.setattr(diagnostics.subprocess, "run", _fake_run(raw))
    assert diagnostics.nvidia_driver_info() == {"gpu_name": "NVIDIA 显卡 RTX", "driver_version": "551.23"}


def test_loaded_models_nvidia_smi_decodes_utf8_name(monkeypatch):
    monkeypatch.setattr(loaded_models_service.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    raw = "NVIDIA 显卡 RTX, 24564, 8000, 16564\n".encode("utf-8")
    monkeypatch.setattr(loaded_models_service.subprocess, "run", _fake_run(raw))
    assert loaded_models_service._gpu_from_nvidia_smi()["name"] == "NVIDIA 显卡 RTX"


def test_fake_run_would_have_failed_without_encoding():
    with pytest.raises(UnicodeDecodeError):
        _fake_run("白河".encode("utf-8"))(["x"], text=True)
