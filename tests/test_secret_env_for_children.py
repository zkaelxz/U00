"""Child processes that run third-party code never inherit API keys from the
environment, and a backup never holds the raw service logs."""
import os

import pytest

import pending_install
import storage
import upgrade_check
from lib import proc
from services import browser_install_service, library_backup_service as lbs, ytdlp_child

KEY_VARS = {"ANTHROPIC_API_KEY": "k1", "HF_TOKEN": "t1", "BAIHE_DEEPSEEK_KEY": "k2"}


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
