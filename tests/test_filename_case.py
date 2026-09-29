"""Guards against tracked files whose names collide on a case-insensitive
filesystem (Windows, default macOS). `frontend/src/components/confirmButton.ts`
next to `ConfirmButton.tsx` broke `npm run build` on Windows (2026-09-29):
an extensionless import like `./confirmButton` resolves to either file there.
"""

import os
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Imported without an extension, so these must be unique ignoring case and extension.
SCRIPT_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}


def _tracked_files():
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("git checkout not available")
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def _collisions(paths, key):
    groups = defaultdict(set)
    for p in paths:
        k = key(p)
        if k is not None:
            groups[k].add(p)
    return sorted(sorted(v) for v in groups.values() if len(v) > 1)


def _module_key(path):
    head, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in SCRIPT_EXTS:
        return None
    return (head.lower(), stem.lower())


def test_no_two_tracked_paths_differ_only_by_case():
    assert _collisions(_tracked_files(), str.lower) == []


def test_no_frontend_modules_differ_only_by_case_ignoring_extension():
    frontend = [p for p in _tracked_files() if p.startswith("frontend/")]
    assert _collisions(frontend, _module_key) == []


def test_module_key_catches_the_windows_case():
    paths = ["a/confirmButton.ts", "a/ConfirmButton.tsx", "a/confirmButton.test.ts",
             "a/sheet.css", "a/Sheet.tsx"]
    assert _collisions(paths, _module_key) == [["a/ConfirmButton.tsx", "a/confirmButton.ts"]]
