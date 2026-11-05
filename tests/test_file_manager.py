"""lib/file_manager: how the folder launch is built and guarded."""

import pytest

from lib import file_manager


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(file_manager, "_last_opened", {})


def test_windows_launches_explorer_directly_with_the_folder_as_one_argument(monkeypatch):
    calls = []
    monkeypatch.setattr(file_manager.sys, "platform", "win32")
    monkeypatch.setattr(file_manager, "_has_desktop", lambda: True)
    monkeypatch.setattr(file_manager.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    file_manager.open_folder(r"C:\Data\my titles\3")
    assert calls == [["explorer.exe", r"C:\Data\my titles\3"]]


def test_a_repeat_within_the_window_launches_once(monkeypatch):
    calls = []
    monkeypatch.setattr(file_manager, "_has_desktop", lambda: True)
    monkeypatch.setattr(file_manager.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    file_manager.open_folder("/a")
    file_manager.open_folder("/a")
    file_manager.open_folder("/b")
    assert len(calls) == 2


def test_no_desktop_session_launches_nothing(monkeypatch):
    monkeypatch.setattr(file_manager, "_has_desktop", lambda: False)
    monkeypatch.setattr(file_manager.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not launch without a desktop"))
    with pytest.raises(file_manager.NoDesktopError):
        file_manager.open_folder("/a")
