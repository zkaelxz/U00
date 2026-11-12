"""Child processes that run third-party code never inherit API keys from the
environment, and a backup never holds the raw service logs."""
import os

import pytest

import pending_install
import storage
import upgrade_check
from lib import proc
from services import browser_install_service, library_backup_service as lbs, ytdlp_child

KEY_VARS = {"ANTHROPIC_API_KEY": "k1", "HF_TOKEN": "t1", "BAIHE_DEEPSEEK_KEY": "k2",
            "BAIHE_DISCORD_WEBHOOK_URL": "https://example.invalid/hook", "BAIHE_NTFY_TOPIC_URL": "https://example.invalid/t"}


@pytest.fixture
def keyed_env(monkeypatch):
    for name, value in KEY_VARS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BAIHE_SOMETHING_PLAIN", "kept")


def _assert_clean(env):
    assert env is not None
    assert not set(KEY_VARS) & set(env)
    assert "PATH" in env or "Path" in env  # the child can still find its tools
    assert env.get("BAIHE_SOMETHING_PLAIN") == "kept"  # only secret-looking names go


def _capture_stream_tree(monkeypatch, target):
    seen = {}

    def fake(cmd, timeout, **kw):
        seen["env"] = kw.get("env")
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(target, "stream_tree", fake)
    return seen


def test_scratch_env_for_pip_has_no_keys(keyed_env, isolated_db):
    with storage.scratch_env() as env:
        _assert_clean(env)
        assert env["TMP"] == env["TEMP"] == env["TMPDIR"]


def test_the_pending_install_runner_has_no_keys(keyed_env, monkeypatch):
    seen = _capture_stream_tree(monkeypatch, pending_install)
    pending_install.run_capture(["pip", "--version"], 5)
    _assert_clean(seen["env"])


def test_the_upgrade_check_runner_has_no_keys(keyed_env, monkeypatch):
    seen = _capture_stream_tree(monkeypatch, upgrade_check.proc_run)
    list(upgrade_check._stream_process(["pip", "--version"], 5))
    _assert_clean(seen["env"])


def test_the_ytdlp_child_has_no_keys(keyed_env, monkeypatch, tmp_path):
    seen = _capture_stream_tree(monkeypatch, proc)
    list(ytdlp_child.run_download(str(tmp_path), {"url": "https://example.com/v"}, 5, lambda: False))
    _assert_clean(seen["env"])


def test_the_playwright_install_has_no_keys(keyed_env, tmp_path):
    _assert_clean(browser_install_service._environment(str(tmp_path)))


def test_a_backup_leaves_out_the_raw_logs_folder(tmp_path):
    library = tmp_path / "library"
    (library / "logs" / "service").mkdir(parents=True)
    (library / "logs" / "app.log").write_text("x")
    (library / "logs" / "service" / "out.log").write_text("traceback")
    (library / "media").mkdir()
    (library / "media" / "a.mp3").write_text("audio")
    found = {name.replace(os.sep, "/") for _, name, _ in
             lbs._backup_files(str(library), set(), lbs._backup_excluded_top_level())}
    assert found == {"media/a.mp3"}


def test_the_sources_fetcher_ignores_proxy_environment_variables(monkeypatch):
    from sources import http_transport
    monkeypatch.setattr(http_transport, "_tls", type(http_transport._tls)())
    assert http_transport._thread_session().trust_env is False


def test_the_ytdlp_child_keeps_the_desktop_session_address_for_browser_cookies(keyed_env, monkeypatch, tmp_path):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    seen = _capture_stream_tree(monkeypatch, proc)
    list(ytdlp_child.run_download(str(tmp_path), {"url": "https://example.com/v"}, 5, lambda: False))
    assert seen["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert not set(KEY_VARS) & set(seen["env"])


@pytest.mark.parametrize("probe", ["_can_import", "_dist_version_in"])
def test_the_upgrade_check_probes_of_the_candidate_have_no_keys(keyed_env, monkeypatch, probe):
    seen = {}

    def fake(cmd, timeout, **kw):
        seen["env"] = kw.get("env")
        return proc.CapturedRun(0, "1.0", "", False, False)
    monkeypatch.setattr(upgrade_check.proc_run, "run_captured", fake)
    getattr(upgrade_check, probe)("/venv/python", "xdist")
    _assert_clean(seen["env"])


def test_the_upgrade_check_test_run_has_no_keys(keyed_env, monkeypatch, tmp_path):
    """The suite runs in a real child: it reports whether it can see a key."""
    seen = tmp_path / "seen.txt"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_env_probe.py").write_text(
        "import os\n"
        "def test_probe():\n"
        f"    open({str(seen)!r}, 'w').write(repr([os.environ.get(n) for n in {sorted(KEY_VARS)!r}]))\n")
    items = list(upgrade_check._stream_process(
        [__import__("sys").executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path / "tests")],
        120, cwd=str(tmp_path),
        env=dict(proc.secretless_env(drop_prefixes=()), TMP=str(tmp_path))))
    assert items[-1]["returncode"] == 0
    assert seen.read_text() == repr([None] * len(KEY_VARS))
