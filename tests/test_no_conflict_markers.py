"""tests/test_no_conflict_markers.py -- no tracked text file keeps a git merge-conflict marker."""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only the two arrow markers: a bare `=======` is a valid Markdown heading underline.
_MARKER = re.compile(r"^(<{7}|>{7})( |$)", re.M)
_BINARY = (".png", ".jpg", ".ico", ".wav", ".mp3", ".mp4", ".ttf", ".woff2", ".onnx", ".zip", ".jar")
_SKIP = ("package-lock.json", ".test_durations", "tests/test_no_conflict_markers.py")


def test_no_tracked_file_has_a_conflict_marker():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    bad = []
    for name in names:
        if not name or name.endswith(_BINARY) or name.endswith(_SKIP):
            continue
        try:
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        for m in _MARKER.finditer(text):
            bad.append(f"{name}:{text.count(chr(10), 0, m.start()) + 1}")
    assert not bad, "git conflict markers left in: " + ", ".join(bad)
