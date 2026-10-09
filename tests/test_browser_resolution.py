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
    assert msg == page_fetch.BROWSER_MISSING and "Install browser support" in msg
    assert "/x/" not in msg and e.value.__cause__ is None
    assert page_fetch.browser_status() == {"found": False, "name": None}


def test_other_launch_errors_are_not_masked():
    def boom(**kw):
        raise RuntimeError("something else")
    with pytest.raises(RuntimeError, match="something else"):
        page_fetch._launch_chromium(boom)


def test_error_view_is_a_fixed_503_sentence():
    from services import sources_search_service as svc
    view = svc.error_view(page_fetch.BrowserNotFound("C:\\Users\\bob\\x"))
    assert view["status"] == 503 and view["code"] == "dependency_unavailable"
    assert view["message"] == page_fetch.BROWSER_MISSING and "bob" not in str(view)


def test_ladder_reports_not_installed():
    from sources import ladder
    from sources.models import FailureReason

    def fetch(url):
        raise page_fetch.BrowserNotFound(page_fetch.BROWSER_MISSING)
    out = ladder._browser_outcome("https://x.example/", None, fetch, "Browser")
    assert out.reasons == [FailureReason.NOT_INSTALLED] and "No browser found" in out.detail


def _fake_playwright(monkeypatch, tmp_path, revision="1243", manifest=True):
    """A playwright package folder holding only driver/package/browsers.json."""
    import importlib.util
    import json
    import types
    pkg = tmp_path / "site" / "playwright"
    (pkg / "driver" / "package").mkdir(parents=True)
    if manifest:
        (pkg / "driver" / "package" / "browsers.json").write_text(json.dumps({"browsers": [
            {"name": "chromium", "revision": revision},
            {"name": "chromium-headless-shell", "revision": revision},
            {"name": "firefox", "revision": "9"}]}))
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: types.SimpleNamespace(
        submodule_search_locations=[str(pkg)]) if name == "playwright" else real(name, *a))


def _install_build(cache, revision, headless_shell=True, program=True):
    for folder, exe in (("chromium", "chrome-linux64/chrome"),
                        ("chromium_headless_shell",
                         "chrome-headless-shell-linux64/chrome-headless-shell")):
        if folder == "chromium_headless_shell" and not headless_shell:
            continue
        path = cache / f"{folder}-{revision}" / exe
        path.parent.mkdir(parents=True)
        path.write_text("x")
        path.chmod(0o755 if program else 0o644)


@pytest.fixture
def cache(monkeypatch, tmp_path):
    path = tmp_path / "cache"
    path.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(path))
    return path


def test_the_build_the_installed_playwright_wants_is_found(monkeypatch, tmp_path, cache):
    _fake_playwright(monkeypatch, tmp_path, "1243")
    _install_build(cache, "1243")
    assert page_fetch.browser_status() == {"found": True, "name": "Playwright Chromium"}


def test_only_an_older_build_is_not_found(monkeypatch, tmp_path, cache):
    _fake_playwright(monkeypatch, tmp_path, "1243")
    _install_build(cache, "1200")
    assert page_fetch.browser_status() == {"found": False, "name": None}


def test_an_older_build_falls_to_the_system_browser(monkeypatch, tmp_path, cache):
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("x")
    monkeypatch.setattr(page_fetch, "_system_browser_candidates", lambda: [("Chrome", str(chrome))])
    _fake_playwright(monkeypatch, tmp_path, "1243")
    _install_build(cache, "1200")
    assert page_fetch.browser_status() == {"found": True, "name": "Chrome"}


def test_a_missing_headless_shell_or_program_file_is_not_found(monkeypatch, tmp_path, cache):
    _fake_playwright(monkeypatch, tmp_path, "1243")
    _install_build(cache, "1243", headless_shell=False)
    assert page_fetch.browser_status()["found"] is False
    (cache / "chromium-1243" / "chrome-linux64" / "chrome").unlink()
    assert page_fetch.browser_status()["found"] is False


def test_an_unreadable_manifest_or_missing_playwright_is_not_found(monkeypatch, tmp_path, cache):
    _install_build(cache, "1243")
    _fake_playwright(monkeypatch, tmp_path, "1243", manifest=False)
    assert page_fetch.browser_status() == {"found": False, "name": None}
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None)
    assert page_fetch.browser_status() == {"found": False, "name": None}


def test_explicit_path_still_wins_over_the_playwright_check(monkeypatch, tmp_path, cache):
    exe = tmp_path / "mybrowser"
    exe.write_text("x")
    monkeypatch.setenv(page_fetch.BROWSER_ENV, str(exe))
    _fake_playwright(monkeypatch, tmp_path, "1243")
    assert page_fetch.browser_status() == {"found": True, "name": "custom"}


def _no_playwright(monkeypatch, present):
    import sys
    import types
    import browser_support
    for name in ("playwright", "playwright.sync_api"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    if present:
        pkg, api = types.ModuleType("playwright"), types.ModuleType("playwright.sync_api")
        api.sync_playwright = object()
        monkeypatch.setitem(sys.modules, "playwright", pkg)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    else:
        monkeypatch.setitem(sys.modules, "playwright", None)  # makes the import raise ImportError
    monkeypatch.setattr(browser_support, "package_installed", lambda: present)


@pytest.mark.parametrize("package", [True, False])
@pytest.mark.parametrize("chrome", [True, False])
def test_package_and_browser_are_reported_separately(monkeypatch, tmp_path, package, chrome):
    import diagnostics
    exe = tmp_path / "chrome"
    exe.write_text("x")
    monkeypatch.setattr(page_fetch, "_system_browser_candidates",
                        lambda: [("Chrome", str(exe))] if chrome else [])
    monkeypatch.setattr(page_fetch, "_bundled_browser_present", lambda: False)
    monkeypatch.delenv(page_fetch.BROWSER_ENV, raising=False)
    _no_playwright(monkeypatch, package)
    status = diagnostics.check_browser()
    assert status == {"found": chrome, "name": "Chrome" if chrome else None, "package": package}
    assert "tmp" not in str(status)
    if package:
        assert page_fetch._require_playwright() is not None
    else:
        with pytest.raises(ImportError) as e:
            page_fetch._require_playwright()
        msg = str(e.value)
        assert "\n" not in msg and "playwright install" not in msg
        assert "Diagnostics > Packages" in msg and "pip install playwright" in msg
        assert "no browser download is needed" in msg


def test_package_installed_sees_a_package_added_after_the_first_check(monkeypatch, tmp_path):
    import os
    import sys
    import browser_support
    monkeypatch.delitem(sys.modules, "playwright", raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    assert browser_support.package_installed() is False
    before = os.stat(tmp_path).st_mtime_ns
    (tmp_path / "playwright").mkdir()
    (tmp_path / "playwright" / "__init__.py").write_text("")
    # An install landing within the filesystem's mtime granularity leaves the
    # directory looking unchanged, so the finder keeps its stale listing.
    os.utime(tmp_path, ns=(before, before))
    assert browser_support.package_installed() is True


def test_package_installed_stays_false_when_missing(monkeypatch, tmp_path):
    import sys
    import browser_support
    monkeypatch.delitem(sys.modules, "playwright", raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    assert browser_support.package_installed() is False
