"""String-level checks of uninstall.bat. It can't run here (Windows batch), so
these pin the rules that protect the library: only the exact word DELETE
removes it, and the leftovers it never removes are listed, not deleted."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bat():
    with open(os.path.join(ROOT, "uninstall.bat"), encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


def test_library_is_deleted_only_on_the_exact_word_delete():
    bat = _bat()
    # No /i: batch string comparison is case-sensitive without it.
    assert 'if "!DELETE_LIBRARY!"=="DELETE" (' in bat
    assert "if /i" not in bat.split('if "!DELETE_LIBRARY!"')[0].rsplit("set /p DELETE_LIBRARY", 1)[1]
    # The variable is cleared first, because Enter leaves a set /p variable unchanged.
    assert bat.index('set "DELETE_LIBRARY="') < bat.index("set /p DELETE_LIBRARY")


def test_no_other_code_path_deletes_the_library():
    bat = _bat()
    deletes = [m.start() for m in re.finditer(r'rmdir[^\n]*library|del [^\n]*library', bat)]
    assert len(deletes) == 1
    guard = bat.index('if "!DELETE_LIBRARY!"=="DELETE" (')
    keep = bat.index("Keeping library", guard)
    assert guard < deletes[0] < keep


def test_prompt_shows_size_and_says_backups_are_inside():
    bat = _bat()
    assert "call :foldersize" in bat and "Size: !LIB_SIZE!" in bat
    assert "backups are stored inside it by default" in bat
    assert "y/N" not in bat.split("set /p DELETE_LIBRARY", 1)[1].split("\n", 1)[0]


def test_custom_data_dir_is_reported_and_never_deleted():
    bat = _bat()
    block = bat[bat.index("if defined BAIHE_DATA_DIR ("):]
    block = block[:block.index("\n)\n")]
    assert "!BAIHE_DATA_DIR!" in block and "is kept" in block
    assert not re.search(r"rmdir|\bdel\b", block)
    assert not re.search(r'rmdir[^\n]*BAIHE_DATA_DIR', bat)


def test_leftovers_are_listed_and_never_deleted():
    bat = _bat()
    listing = bat[bat.index("Left on this PC"):bat.index("echo Done.")]
    for needle in ("HF_HOME", ".cache\\huggingface", "TORCH_HOME", ".cache\\torch",
                   "%LOCALAPPDATA%\\pip\\Cache", "OLLAMA_MODELS", ".ollama\\models",
                   '"Python" python', '"Git" git', '"ffmpeg" ffmpeg', '"Node.js" node'):
        assert needle in listing, needle
    assert not re.search(r"rmdir|\bdel\b|rd /s", listing)
    # Helpers only measure and print.
    helpers = bat[bat.index("\n:foldersize\n"):]
    assert not re.search(r"rmdir|\bdel\b|rd /s|Remove-Item", helpers)


def test_no_path_is_echoed_through_an_unquoted_argument_expansion():
    # A & in a path would split the echo line and run the rest as a command.
    for line in _bat().splitlines():
        # %~1 is always a fixed label; every path argument is %~2 or %~dp0.
        if line.lstrip().lower().startswith("echo ") and ("%~2" in line or "%~dp0" in line):
            raise AssertionError(line)
