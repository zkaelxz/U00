"""setup.bat is only ever run by hand on Windows, so these pin what a Linux CI can see."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, name), "rb") as fh:
        return fh.read().decode("ascii")


def test_setup_bat_is_crlf_ascii_batch():
    # cmd.exe can fail to find a goto label in a file with bare LF endings.
    text = _read("setup.bat")
    assert "\r\n" in text and text.count("\n") == text.count("\r\n")


def test_setup_bat_labels_are_defined_once_and_all_targets_exist():
    import re
    text = _read("setup.bat")
    defined = re.findall(r"^:(\w+)", text, re.M)
    assert len(defined) == len(set(defined))
    targets = re.findall(r"goto :(\w+)", text)
    assert set(targets) <= set(defined)


def test_setup_bat_stub_wording_matches_start_bat():
    # The two scripts print the same App execution aliases instructions.
    start = _read("start.bat")
    setup = _read("setup.bat")
    for line in ("not a real Python install.", 'turn OFF both "App Installer python.exe"',
                 "Settings -^> Apps -^> Advanced app settings -^> App execution aliases"):
        assert line in start and line in setup


def test_start_bat_does_not_run_setup_bat():
    start = _read("start.bat")
    assert "call setup" not in start.lower() and "call \"%~dp0setup" not in start.lower()
