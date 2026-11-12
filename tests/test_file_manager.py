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


def test_a_service_refusal_does_not_swallow_the_next_click(monkeypatch):
    """The refusal must not start the repeat window: the second click has to
    get the same answer, not a silent success."""
    monkeypatch.setattr(file_manager, "_has_desktop", lambda: False)
    for _ in range(2):
        with pytest.raises(file_manager.NoDesktopError):
            file_manager.open_folder("/a")


def test_a_failed_launch_does_not_swallow_the_retry(monkeypatch):
    calls = []

    def popen(argv, **kw):
        calls.append(argv)
        if len(calls) == 1:
            raise FileNotFoundError("no file manager")
    monkeypatch.setattr(file_manager, "_has_desktop", lambda: True)
    monkeypatch.setattr(file_manager.subprocess, "Popen", popen)
    with pytest.raises(OSError):
        file_manager.open_folder("/a")
    file_manager.open_folder("/a")
    assert len(calls) == 2
