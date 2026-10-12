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
    deletes = [m.start() for m in re.finditer(r'(rmdir|del |removedir)[^\n]*"library"', bat)]
    assert len(deletes) == 1
    guard = bat.index('if "!DELETE_LIBRARY!"=="DELETE" (')
    keep = bat.index("Keeping library", guard)
    assert guard < deletes[0] < keep
    assert bat[deletes[0]:].startswith("removedir")


def test_library_neighbours_go_only_in_the_delete_branch_and_only_after_the_library_is_gone():
    bat = _bat()
    guard = bat.index('if "!DELETE_LIBRARY!"=="DELETE" (')
    keep = bat.index("Keeping library", guard)
    branch = bat[guard:keep]
    assert branch.index('call :removedir "library"') < branch.index("else (") < branch.index("call :removeneighbours")
    # The names a restore or reset really makes (db/library_folder.py, services/previous_library_service.py).
    body = bat[bat.index("\n:removeneighbours\n"):]
    body = body[:body.index("\nexit /b 0")]
    assert 'removefile "library.restore-lock"' in body
    assert '"library.pre_restore_*"' in body and '".restore_staging_*"' in body
    # A reset copy is not in the owner's list; it is reported, not deleted.
    assert "pre_reset" not in "\n".join(l for l in body.splitlines() if not l.startswith("REM"))
    assert 'removedir "%%D"' in body
    # Only the DELETE branch calls it.
    assert bat.count("call :removeneighbours") == 1


def test_deletes_only_name_items_in_the_app_folder():
    bat = _bat()
    # cd /d "%~dp0" first; every removal then names a fixed relative item or a shortcut, never an
    # environment-chosen location (BAIHE_DATA_DIR, the caches, PATH).
    assert bat.index('cd /d "%~dp0"') < bat.index("call :removefile")
    for line in bat.splitlines():
        if re.search(r"\b(rmdir|del)\b", line) and not line.lstrip().startswith(("REM", "echo")):
            assert not re.search(r"BAIHE_DATA_DIR|HF_|TORCH|OLLAMA|%PATH%|LOCALAPPDATA", line), line


def test_links_are_never_followed():
    bat = _bat()
    helper = bat[bat.index("\n:removedir\n"):]
    helper = helper[:helper.index("\nexit /b 0")]
    # Attribute check first: not a folder -> untouched; a link -> plain rmdir (no /s), only the link goes.
    assert "%%~aF" in helper
    code = "\n".join(l for l in helper.splitlines() if not l.startswith("REM"))
    assert "rmdir /s /q" in code and code.count("rmdir") == 2
    link_branch = helper[helper.index("l=!"):helper.index("rmdir /s /q")]
    assert re.search(r'rmdir "%~1"', link_branch) and "/s" not in link_branch
    file_helper = bat[bat.index("\n:removefile\n"):]
    file_helper = file_helper[:file_helper.index("\nexit /b 0")]
    # A folder named like the file must not be emptied by `del`.
    assert '"d"' in file_helper and file_helper.index('"d"') < file_helper.index("del /f")


def test_vtuber_list_and_part_files_always_go():
    bat = _bat()
    confirm = bat.index('if /i not "%CONFIRM%"=="y"')
    delete_prompt = bat.index("set /p DELETE_LIBRARY")
    removal = bat[confirm:delete_prompt]
    assert 'call :removefile "vtuber_names.json"' in removal
    assert 'for %%F in (*.part) do if /i "%%~xF"==".part" call :removefile "%%F"' in removal
    assert "vtuber_names.json" in bat.split("echo It will NOT touch")[0]


def test_left_list_covers_what_remains_without_printing_secrets():
    bat = _bat()
    listing = bat[bat.index("Left on this PC"):bat.index("echo Done.")]
    for needle in ('"Your library', '"venv folder', '"model_cache folder', "DESKTOP_LNK", "STARTMENU_LNK",
                   "pre_restore", "pre_reset", ".restore_staging_", "restore-lock", "vtuber_names.json",
                   '"Ollama" ollama', "never installs", "BAIHE_DATA_DIR"):
        assert needle in listing, needle
    # Names only: no file is read or typed, and no key-looking variable is echoed.
    assert not re.search(r"\btype\b|\bmore\b|findstr|API_KEY|%[A-Z_]*(KEY|TOKEN|SECRET)", listing)
    assert ".env" in listing and "never shown" in listing


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
    helpers = bat[bat.index("\n:foldersize\n"):bat.index("\n:removefile\n")]
    assert not re.search(r"rmdir|\bdel\b|rd /s|Remove-Item", helpers)


def test_no_path_is_echoed_through_an_unquoted_argument_expansion():
    # A & in a path would split the echo line and run the rest as a command.
    for line in _bat().splitlines():
        # %~1 is always a fixed label; every path argument is %~2 or %~dp0.
        if line.lstrip().lower().startswith("echo ") and ("%~2" in line or "%~dp0" in line):
            raise AssertionError(line)
