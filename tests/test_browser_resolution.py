"""Which browser program page_fetch launches, and the error when none exists."""

import pytest

import page_fetch

MISSING = ("BrowserType.launch: Executable doesn't exist at /x/chromium-1/chrome\\n"
           "Looks like Playwright was just installed or updated. Please run playwright install")


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.delenv(page_fetch.BROWSER_ENV, raising=False)
    monkeypatch.setattr(page_fetch, "_system_browser_candidates", lambda: [])
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "none"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


class Launcher:
    def __init__(self, bundled_ok=True):
        self.calls, self.bundled_ok = [], bundled_ok

    def __call__(self, *args, **kw):
        self.calls.append(kw.get("executable_path"))
        if "executable_path" not in kw and not self.bundled_ok:
            raise RuntimeError(MISSING)
        return "browser"


def test_bundled_build_is_used_when_present():
    launch = Launcher()
    assert page_fetch._launch_chromium(launch, headless=True) == "browser"
    assert launch.calls == [None]


def test_explicit_path_wins_when_the_file_exists(monkeypatch, tmp_path):
    exe = tmp_path / "mybrowser"
    exe.write_text("x")
    monkeypatch.setenv(page_fetch.BROWSER_ENV, str(exe))
    launch = Launcher()
    page_fetch._launch_chromium(launch)
    assert launch.calls == [str(exe)]
    assert page_fetch.browser_status() == {"found": True, "name": "custom"}


def test_explicit_path_that_does_not_exist_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv(page_fetch.BROWSER_ENV, str(tmp_path / "gone"))
    launch = Launcher()
    page_fetch._launch_chromium(launch)
    assert launch.calls == [None]


def test_missing_bundled_build_falls_back_to_system_chrome(monkeypatch, tmp_path):
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("x")
    monkeypatch.setattr(page_fetch, "_system_browser_candidates",
                        lambda: [("Edge", str(tmp_path / "nope")), ("Chrome", str(chrome))])
    launch = Launcher(bundled_ok=False)
    page_fetch._launch_chromium(launch, headless=True)
    assert launch.calls == [None, str(chrome)]
    assert page_fetch.browser_status() == {"found": True, "name": "Chrome"}


def test_no_browser_raises_one_clear_error_without_paths():
    launch = Launcher(bundled_ok=False)
    with pytest.raises(page_fetch.BrowserNotFound) as e:
        page_fetch._launch_chromium(launch)
    msg = str(e.value)
    assert "BAIHE_BROWSER_PATH" in msg and "playwright install chromium" in msg
    assert "/x/" not in msg and e.value.__cause__ is None
    assert page_fetch.browser_status() == {"found": False, "name": None}


def test_other_launch_errors_are_not_masked():
    def boom(**kw):
        raise RuntimeError("something else")
    with pytest.raises(RuntimeError, match="something else"):
        page_fetch._launch_chromium(boom)


def test_a_bundled_chromium_folder_counts_as_found(monkeypatch, tmp_path):
    d = tmp_path / "pw" / "chromium-1194"
    d.mkdir(parents=True)
    (d / "chrome").write_text("x")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "pw"))
    assert page_fetch.browser_status() == {"found": True, "name": "Playwright Chromium"}


def test_error_view_is_a_fixed_503_sentence():
    from services import sources_search_service as svc
    view = svc._error_view(page_fetch.BrowserNotFound("C:\\Users\\bob\\x"))
    assert view["status"] == 503 and view["code"] == "dependency_unavailable"
    assert view["message"] == page_fetch.BROWSER_MISSING and "bob" not in str(view)


def test_ladder_reports_not_installed():
    from sources import ladder
    from sources.models import FailureReason

    def fetch(url):
        raise page_fetch.BrowserNotFound(page_fetch.BROWSER_MISSING)
    out = ladder._browser_outcome("https://x.example/", None, fetch, "Browser")
    assert out.reasons == [FailureReason.NOT_INSTALLED] and "BAIHE_BROWSER_PATH" in out.detail
