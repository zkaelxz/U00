"""Launchers turn on the PC-only API-key form (user decision 2026-09-29)
while still forcing a loopback bind. Static text checks, like
tests/test_launcher_python_detection.py: the scripts are Windows-only."""
import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(PROJECT_ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_start_bat_enables_key_writes_and_keeps_loopback():
    text = _read("start.bat")
    assert re.search(r"^set BAIHE_API_HOST=127\.0\.0\.1\s*$", text, re.M)
    # `if not defined` respects an explicit BAIHE_API_ALLOW_KEY_WRITES=0.
    assert re.search(
        r"^if not defined BAIHE_API_ALLOW_KEY_WRITES set BAIHE_API_ALLOW_KEY_WRITES=1\s*$",
        text, re.M)
    assert "BAIHE_API_HOST=0.0.0.0" not in text


def test_start_ps1_enables_key_writes_and_keeps_loopback():
    text = _read("start.ps1")
    assert re.search(r'^\$env:BAIHE_API_HOST = "127\.0\.0\.1"\s*$', text, re.M)
    assert re.search(
        r'^if \(-not \$env:BAIHE_API_ALLOW_KEY_WRITES\) '
        r'\{ \$env:BAIHE_API_ALLOW_KEY_WRITES = "1" \}\s*$',
        text, re.M)
    assert '"0.0.0.0"' not in text
